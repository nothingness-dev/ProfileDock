import http.server
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from profiledock.cli import app
from profiledock.process_manager import (
    BrowserLaunchError,
    ProfileRunningError,
    _alive,
    _find_browser_pid,
    _get_process_create_time,
    _is_matching_process,
    _parse_linux_process_stat,
    _terminate_matching_process,
    _valid_state,
    close_controller,
    get_status,
    is_running,
    send_controller_command,
    start_controller,
    state_path,
)

runner = CliRunner()


def _dead_pid() -> int:
    process = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(0)"])
    process.wait()
    return process.pid


def _sleep_process() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])


def _wait_until(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _write_playwright_state(data_dir: Path, **overrides) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "protocol_version": 2,
        "engine": "playwright",
        "profile_id": "abc123",
        "controller_pid": 0,
        "controller_started_at": datetime.now(timezone.utc).isoformat(),
        "launcher_pid": 0,
        "port": 12345,
        "token": "x" * 40,
        "tabs": 1,
        "status": "running",
    }
    state.update(overrides)
    path = state_path(str(data_dir))
    path.write_text(json.dumps(state), encoding="utf-8")
    return state


def _write_direct_state(data_dir: Path, **overrides) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "protocol_version": 2,
        "engine": "direct",
        "profile_id": "abc123",
        "pid": 0,
        "launcher_pid": 0,
        "tabs": 1,
        "channel": "chrome",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
    }
    state.update(overrides)
    path = state_path(str(data_dir))
    path.write_text(json.dumps(state), encoding="utf-8")
    return state


def test_state_accepts_browser_identity_and_headless_fields():
    base = {
        "protocol_version": 2,
        "engine": "playwright",
        "profile_id": "profile-a",
        "controller_pid": 1,
        "controller_started_at": datetime.now(timezone.utc).isoformat(),
        "port": 1,
        "token": "x" * 32,
        "tabs": 1,
        "status": "running",
    }
    assert _valid_state(
        {**base, "browser_pid": 4321, "browser_create_time": 123.5, "headless": True}, "profile-a"
    )
    assert not _valid_state({**base, "browser_pid": -1}, "profile-a")
    assert not _valid_state({**base, "browser_pid": True}, "profile-a")
    assert not _valid_state({**base, "headless": "yes"}, "profile-a")
    assert not _valid_state({**base, "browser_create_time": "soon"}, "profile-a")


def test_find_browser_pid_resolves_main_browser_process():
    table = [
        (100, 1, "explorer.exe"),
        (200, 100, "python.exe"),
        (300, 200, "node.exe"),
        (400, 300, "chrome.exe"),
        (500, 400, "chrome.exe"),
        (600, 100, "chrome.exe"),
    ]
    with patch("profiledock.process_manager._list_processes", return_value=table):
        assert _find_browser_pid(200) == 400
        assert _find_browser_pid(999) == 0


def test_parse_linux_process_stat_preserves_process_name():
    ppid, name = _parse_linux_process_stat("123 (chrome helper) S 42 0 0 0")
    assert ppid == 42
    assert name == "chrome helper"


def test_process_termination_requires_recorded_create_time():
    with (
        patch("profiledock.process_manager._alive", return_value=True),
        patch("profiledock.process_manager._is_matching_process") as matching,
    ):
        assert not _terminate_matching_process(123, None, 0.1)
    matching.assert_not_called()


def test_get_status_reports_crashed_and_cleans_runtime_state(tmp_path):
    data_dir = tmp_path / "abc123" / "browser-data"
    dead = _dead_pid()
    _write_playwright_state(data_dir, controller_pid=dead)
    path = state_path(str(data_dir))

    assert get_status(str(data_dir), clean_stale=False) == "stale"
    assert path.exists()
    assert get_status(str(data_dir), clean_stale=True) == "crashed"
    assert not path.exists()
    assert get_status(str(data_dir)) == "stopped"


def test_get_status_reports_direct_browser_crash(tmp_path):
    data_dir = tmp_path / "abc123" / "browser-data"
    dead = _dead_pid()
    _write_direct_state(data_dir, pid=dead, launcher_pid=os.getpid(), process_create_time=100.0)
    path = state_path(str(data_dir))

    assert get_status(str(data_dir), clean_stale=False) == "stale"
    assert get_status(str(data_dir), clean_stale=True) == "crashed"
    assert not path.exists()


def test_status_closing_with_dead_controller_is_stopped_not_crashed(tmp_path):
    data_dir = tmp_path / "abc123" / "browser-data"
    dead = _dead_pid()
    _write_playwright_state(data_dir, controller_pid=dead, status="closing", closing=True)
    path = state_path(str(data_dir))

    assert get_status(str(data_dir), clean_stale=True) == "stopped"
    assert not path.exists()


def test_is_running_is_false_after_a_crash(tmp_path):
    data_dir = tmp_path / "abc123" / "browser-data"
    dead = _dead_pid()
    _write_playwright_state(data_dir, controller_pid=dead)
    assert not is_running(str(data_dir))


def test_duplicate_launch_is_blocked_while_starting(tmp_path):
    data_dir = tmp_path / "abc123" / "browser-data"
    _write_playwright_state(data_dir, controller_pid=0, launcher_pid=os.getpid(), port=0, status="starting")
    assert is_running(str(data_dir))
    with pytest.raises(ProfileRunningError, match="profile is already running"):
        start_controller(str(data_dir), tabs=1, headless=True)


def test_failed_startup_rolls_back_all_runtime_artifacts(tmp_path):
    data_dir = tmp_path / "abc123" / "browser-data"
    data_dir.mkdir(parents=True)
    stuck = _sleep_process()
    try:
        with patch("profiledock.process_manager.subprocess.Popen", return_value=stuck):
            with pytest.raises(BrowserLaunchError, match="timed out"):
                start_controller(str(data_dir), tabs=1, headless=True, startup_timeout=0.5)
        assert stuck.poll() is not None
        assert not state_path(str(data_dir)).exists()
    finally:
        if stuck.poll() is None:
            stuck.kill()
            stuck.wait()


def test_close_recovers_crash_and_terminates_identity_matched_orphan(tmp_path):
    data_dir = tmp_path / "abc123" / "browser-data"
    dead = _dead_pid()
    orphan = _sleep_process()
    try:
        _write_playwright_state(
            data_dir,
            controller_pid=dead,
            browser_pid=orphan.pid,
            browser_create_time=_get_process_create_time(orphan.pid),
        )
        with pytest.raises(ProfileRunningError, match="profile is not running"):
            close_controller(str(data_dir), timeout=2)
        assert not state_path(str(data_dir)).exists()
        assert _wait_until(lambda: not _alive(orphan.pid))
    finally:
        if orphan.poll() is None:
            orphan.kill()
            orphan.wait()


def test_close_never_signals_identity_mismatched_browser(tmp_path):
    data_dir = tmp_path / "abc123" / "browser-data"
    dead = _dead_pid()
    orphan = _sleep_process()
    try:
        create_time = _get_process_create_time(orphan.pid)
        wrong_time = (create_time + 100000.0) if create_time is not None else None
        _write_playwright_state(
            data_dir,
            controller_pid=dead,
            browser_pid=orphan.pid,
            browser_create_time=wrong_time,
        )
        with pytest.raises(ProfileRunningError, match="profile is not running"):
            close_controller(str(data_dir), timeout=0.2)
        assert not state_path(str(data_dir)).exists()
        assert _alive(orphan.pid)
    finally:
        if orphan.poll() is None:
            orphan.kill()
            orphan.wait()


def test_close_with_malformed_playwright_state_refuses_without_crash(tmp_path):
    data_dir = tmp_path / "abc123" / "browser-data"
    data_dir.mkdir(parents=True)
    path = state_path(str(data_dir))
    path.write_text(
        json.dumps(
            {
                "protocol_version": 2,
                "engine": "playwright",
                "profile_id": "abc123",
                "controller_pid": "not-a-pid",
                "port": 12345,
                "token": "x" * 40,
                "tabs": 1,
                "status": "running",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ProfileRunningError, match="refusing unauthenticated controller access"):
        close_controller(str(data_dir), timeout=0.2)
    assert path.exists()


def test_is_matching_process_rejects_garbage_identity():
    pid = os.getpid()
    assert _is_matching_process(pid, "not-a-time", require_verification=True) is False
    assert _is_matching_process(pid, "not-a-time", require_verification=False) is False
    assert _is_matching_process(pid, None, require_verification=True) is True


def test_playwright_launch_defaults_to_visible_and_passes_timeouts(tmp_path):
    (tmp_path / "browser-data").mkdir()
    profile = type(
        "Profile",
        (),
        {
            "id": "abc123",
            "name": "Test",
            "data_dir": str(tmp_path / "browser-data"),
            "engine": "playwright",
        },
    )()
    with (
        patch("profiledock.cli.manager") as mock_manager,
        patch("profiledock.cli.start_controller") as mock_start,
    ):
        mock_manager.return_value.resolve.return_value = profile
        mock_manager.return_value.runtime_path.return_value = tmp_path / "runtime"
        result = runner.invoke(app, ["launch", "abc123", "--tabs", "1"])
        assert result.exit_code == 0
        assert mock_start.call_args.kwargs["headless"] is False
        assert mock_start.call_args.kwargs["startup_timeout"] == 30.0
        assert "visible" in result.stdout

        mock_start.reset_mock()
        result = runner.invoke(app, ["launch", "abc123", "--tabs", "1", "--headless", "--wait-timeout", "30"])
        assert result.exit_code == 0
        assert mock_start.call_args.kwargs["headless"] is True
        assert mock_start.call_args.kwargs["startup_timeout"] == 30.0


def test_direct_launch_rejects_headless_option(tmp_path):
    (tmp_path / "browser-data").mkdir()
    profile = type(
        "Profile",
        (),
        {
            "id": "abc123",
            "name": "Test",
            "data_dir": str(tmp_path / "browser-data"),
            "engine": "direct",
        },
    )()
    with (
        patch("profiledock.cli.manager") as mock_manager,
        patch("profiledock.cli.start_direct_chrome") as mock_start,
    ):
        mock_manager.return_value.resolve.return_value = profile
        result = runner.invoke(app, ["launch", "abc123", "--tabs", "1", "--headless"])
    assert result.exit_code == 1
    assert "--headless requires the Playwright engine" in result.output
    mock_start.assert_not_called()


@pytest.mark.browser
def test_full_lifecycle_records_state_and_terminates_all_processes(tmp_path):
    pytest.importorskip("playwright.sync_api")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/"
    data_dir = tmp_path / "abc123" / "browser-data"
    data_dir.mkdir(parents=True)
    state = start_controller(str(data_dir), 1, headless=True, start_urls=[base_url])
    try:
        assert state["headless"] is True
        assert state["browser_pid"] > 0
        assert state["page_count"] == 1
        assert state["channel"]
        assert get_status(str(data_dir)) == "running"
        tabs = send_controller_command(str(data_dir), cmd="tabs")
        assert tabs["tabs"][0]["url"].rstrip("/") == base_url.rstrip("/")
        close_controller(str(data_dir), timeout=10)
        assert not state_path(str(data_dir)).exists()
        assert not is_running(str(data_dir))
        assert _wait_until(lambda: not _alive(state["controller_pid"]))
        assert _wait_until(lambda: not _alive(state["browser_pid"]))
    finally:
        if state.get("pid") and _alive(state["pid"]):
            close_controller(str(data_dir), timeout=5)
        server.shutdown()
        server.server_close()


@pytest.mark.browser
def test_closing_last_page_cleans_runtime_state_and_processes(tmp_path):
    pytest.importorskip("playwright.sync_api")
    data_dir = tmp_path / "abc123" / "browser-data"
    data_dir.mkdir(parents=True)
    state = start_controller(str(data_dir), 1, headless=True)
    send_controller_command(
        str(data_dir),
        cmd="close_tab",
        args={"index": 0},
        auto_start_headless=False,
    )
    assert _wait_until(lambda: not state_path(str(data_dir)).exists())
    assert _wait_until(lambda: not _alive(state["controller_pid"]))
    if state["browser_pid"] > 0:
        assert _wait_until(lambda: not _alive(state["browser_pid"]))


@pytest.mark.browser
def test_relaunch_retains_persistent_session_data(tmp_path):
    pytest.importorskip("playwright.sync_api")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/"
    data_dir = tmp_path / "abc123" / "browser-data"
    data_dir.mkdir(parents=True)
    try:
        first = start_controller(str(data_dir), 1, headless=True, start_urls=[base_url])
        try:
            result = send_controller_command(
                str(data_dir),
                cmd="eval",
                args={
                    "script": "localStorage.setItem('pd', 'v1');"
                    " document.cookie = 'pd=1; max-age=31536000; path=/'; 'ok'"
                },
            )
            assert result["status"] == "ok"
        finally:
            close_controller(str(data_dir), timeout=10)
        assert _wait_until(lambda: not _alive(first["controller_pid"]))

        second = start_controller(str(data_dir), 1, headless=True, start_urls=[base_url])
        try:
            result = send_controller_command(
                str(data_dir),
                cmd="eval",
                args={"script": "localStorage.getItem('pd') + '|' + document.cookie"},
            )
            assert result["status"] == "ok"
            assert result["result"] == "v1|pd=1"
        finally:
            close_controller(str(data_dir), timeout=10)
        assert _wait_until(lambda: not _alive(second["controller_pid"]))
        assert not state_path(str(data_dir)).exists()
    finally:
        server.shutdown()
        server.server_close()


def test_launch_rejects_non_positive_wait_timeout(tmp_path):
    (tmp_path / "browser-data").mkdir()
    profile = type(
        "Profile",
        (),
        {"id": "abc123", "name": "Test", "data_dir": str(tmp_path / "browser-data"), "engine": None},
    )()
    with patch("profiledock.cli.manager") as mock_manager, patch("profiledock.cli.start_controller"):
        mock_manager.return_value.resolve.return_value = profile
        result = runner.invoke(app, ["launch", "abc123", "--tabs", "1", "--wait-timeout", "0"])
    assert result.exit_code == 1
    assert "wait timeout must be greater than 0" in result.output


def test_close_cli_passes_shutdown_timeout(tmp_path):
    profile = type(
        "Profile",
        (),
        {"id": "abc123", "name": "Test", "data_dir": str(tmp_path / "browser-data"), "engine": None},
    )()
    with (
        patch("profiledock.cli.manager") as mock_manager,
        patch("profiledock.cli.close_controller") as mock_close,
    ):
        mock_manager.return_value.resolve.return_value = profile
        mock_manager.return_value.runtime_path.return_value = tmp_path / "runtime"
        result = runner.invoke(app, ["close", "abc123", "--timeout", "20"])
        assert result.exit_code == 0
        assert mock_close.call_args.kwargs["timeout"] == 20.0

        result = runner.invoke(app, ["close", "abc123", "--timeout", "0"])
    assert result.exit_code == 1
    assert "timeout must be greater than 0" in result.output


class _QuietHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<html><body><h1>ProfileDock</h1></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def test_close_all_closes_running_and_counts_stopped(tmp_path):
    from unittest.mock import patch as _patch

    runner.invoke(app, ["--data-root", str(tmp_path), "create", "One"])
    runner.invoke(app, ["--data-root", str(tmp_path), "create", "Two"])

    # Keep the real close path from signalling anything; only count behavior.
    def fake_close_controller(data_dir, timeout=15, runtime_dir=None):
        return None

    with (
        _patch("profiledock.cli.manager") as mock_manager,
        _patch("profiledock.cli.close_controller", side_effect=fake_close_controller),
    ):
        rows = [
            type("Profile", (), {"id": "aaa", "name": "One", "data_dir": str(tmp_path / "a")})(),
            type("Profile", (), {"id": "bbb", "name": "Two", "data_dir": str(tmp_path / "b")})(),
        ]
        mock_manager.return_value.list_profiles.return_value = rows
        result = runner.invoke(app, ["close", "--all"])

    assert result.exit_code == 0, result.output
    assert "Closed 'One'." in result.output
    assert "Closed 'Two'." in result.output
    assert "2 profile(s) already stopped." not in result.output


def test_close_all_counts_already_stopped_profiles(tmp_path):
    from profiledock.process_manager import ProfileRunningError as _PRE

    def fake_close_controller(data_dir, timeout=15, runtime_dir=None):
        raise _PRE("profile is not running", stopped=True)

    with (
        patch("profiledock.cli.manager") as mock_manager,
        patch("profiledock.cli.close_controller", side_effect=fake_close_controller),
    ):
        rows = [
            type("Profile", (), {"id": "aaa", "name": "One", "data_dir": str(tmp_path / "a")})(),
            type("Profile", (), {"id": "bbb", "name": "Two", "data_dir": str(tmp_path / "b")})(),
        ]
        mock_manager.return_value.list_profiles.return_value = rows
        result = runner.invoke(app, ["close", "--all"])

    assert result.exit_code == 0, result.output
    assert "2 profile(s) already stopped." in result.output


def test_close_all_with_no_profiles_is_a_clean_noop(tmp_path):
    with patch("profiledock.cli.manager") as mock_manager:
        mock_manager.return_value.list_profiles.return_value = []
        result = runner.invoke(app, ["close", "--all"])
    assert result.exit_code == 0
    assert "No profiles found." in result.output


def test_close_rejects_profile_and_all_together(tmp_path):
    result = runner.invoke(app, ["--data-root", str(tmp_path), "close", "SomeProfile", "--all"])
    assert result.exit_code == 1
    assert "cannot specify both" in result.output
