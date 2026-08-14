import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from profiledock.process_manager import (
    BrowserLaunchError,
    ProfileRunningError,
    _alive,
    _context_alive,
    _launch_context,
    _read_error,
    _wait_for_close,
    close_controller,
    error_path,
    is_running,
    start_controller,
    state_path,
)
from profiledock.profile_manager import ProfileManager

pytestmark = pytest.mark.browser


def _process_alive(pid: int) -> bool:
    return _alive(pid)


def _wait_until(predicate, timeout: float = 10) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


def _terminate_owned_tree(pid: int) -> None:
    if not _process_alive(pid):
        return
    if sys.platform == "win32":
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode != 0 and _process_alive(pid):
            raise RuntimeError(f"could not terminate test controller {pid}")
    else:
        try:
            os.kill(pid, 9)
        except (OSError, ProcessLookupError):
            pass
    if not _wait_until(lambda: not _process_alive(pid), timeout=5):
        raise RuntimeError(f"test controller {pid} did not exit")


@pytest.fixture(scope="session")
def browser_available(tmp_path_factory):
    playwright = pytest.importorskip("playwright.sync_api")
    probe = tmp_path_factory.mktemp("browser-probe") / "browser-data"
    try:
        with playwright.sync_playwright() as instance:
            context, _ = _launch_context(instance, str(probe), True)
            context.close()
    except playwright.Error as exc:
        pytest.skip(f"no supported browser found: {exc}")


@pytest.fixture
def controller_env(tmp_path, browser_available):
    manager = ProfileManager(tmp_path)
    profile = manager.create("Test")
    data_dir = Path(profile.data_dir)
    owned_pids: set[int] = set()
    yield manager, profile, data_dir, owned_pids
    if is_running(str(data_dir)):
        try:
            close_controller(str(data_dir), timeout=10)
        except Exception:
            pass
    for pid in owned_pids:
        if _process_alive(pid):
            _terminate_owned_tree(pid)
    state_path(str(data_dir)).unlink(missing_ok=True)
    shutil.rmtree(tmp_path, ignore_errors=True)


def _start(data_dir: Path, owned_pids: set[int], tabs: int = 1):
    state = start_controller(str(data_dir), tabs, headless=True)
    owned_pids.add(state["pid"])
    return state


def _close(data_dir: Path, owned_pids: set[int], pid: int) -> None:
    close_controller(str(data_dir), timeout=10)
    assert _wait_until(lambda: not _process_alive(pid))
    assert not state_path(str(data_dir)).exists()
    assert not is_running(str(data_dir))
    owned_pids.discard(pid)


def test_start_reports_ready_with_actual_page_count(controller_env):
    manager, profile, data_dir, owned_pids = controller_env
    state = _start(data_dir, owned_pids, tabs=3)
    assert state["port"] > 0
    assert state["pid"] > 0
    assert state["token"]
    assert state["page_count"] == 3
    assert state["channel"] in ("chromium", "chrome")
    assert is_running(str(data_dir))


def test_duplicate_launch_is_rejected(controller_env):
    manager, profile, data_dir, owned_pids = controller_env
    _start(data_dir, owned_pids)
    with pytest.raises(ProfileRunningError):
        start_controller(str(data_dir), 1, headless=True)


def test_stale_running_state_is_cleaned(controller_env):
    manager, profile, data_dir, owned_pids = controller_env
    path = state_path(str(data_dir))
    path.write_text(
        json.dumps({"pid": 999999, "port": 12345, "token": "x", "tabs": 1}),
        encoding="utf-8",
    )
    assert not is_running(str(data_dir))
    assert not path.exists()


def test_close_exits_controller_and_removes_state(controller_env):
    manager, profile, data_dir, owned_pids = controller_env
    state = _start(data_dir, owned_pids, tabs=2)
    _close(data_dir, owned_pids, state["pid"])


def test_relaunch_after_graceful_close(controller_env):
    manager, profile, data_dir, owned_pids = controller_env
    first = _start(data_dir, owned_pids, tabs=2)
    _close(data_dir, owned_pids, first["pid"])
    second = _start(data_dir, owned_pids, tabs=1)
    assert second["page_count"] == 1
    _close(data_dir, owned_pids, second["pid"])


def test_persistent_state_survives_controller_cycles(controller_env):
    playwright = pytest.importorskip("playwright.sync_api")
    manager, profile, data_dir, owned_pids = controller_env
    with playwright.sync_playwright() as instance:
        context, _ = _launch_context(instance, str(data_dir), True)
        context.add_cookies(
            [
                {
                    "name": "profiledock-controller",
                    "value": "persisted",
                    "url": "https://example.com",
                    "expires": time.time() + 3600,
                }
            ]
        )
        context.close()

    first = _start(data_dir, owned_pids, tabs=2)
    _close(data_dir, owned_pids, first["pid"])
    second = _start(data_dir, owned_pids, tabs=1)
    _close(data_dir, owned_pids, second["pid"])

    with playwright.sync_playwright() as instance:
        context, _ = _launch_context(instance, str(data_dir), True)
        cookies = context.cookies("https://example.com")
        context.close()
    assert any(
        cookie["name"] == "profiledock-controller"
        and cookie["value"] == "persisted"
        for cookie in cookies
    )


def test_error_file_not_created_on_success(controller_env):
    manager, profile, data_dir, owned_pids = controller_env
    err = error_path(str(data_dir))
    state = _start(data_dir, owned_pids, tabs=1)
    assert not err.exists()
    _close(data_dir, owned_pids, state["pid"])


def test_channel_included_in_controller_state(controller_env):
    manager, profile, data_dir, owned_pids = controller_env
    state = _start(data_dir, owned_pids, tabs=1)
    assert "channel" in state
    assert state["channel"] in ("chromium", "chrome")
    _close(data_dir, owned_pids, state["pid"])


def test_start_controller_preserves_native_exit_diagnostic(controller_env):
    manager, profile, data_dir, owned_pids = controller_env
    script = data_dir.parent / "exit_script.py"
    script.write_text(
        "import sys\nsys.stderr.write('native startup failure')\nsys.exit(1)\n",
        encoding="utf-8",
    )
    original_popen = subprocess.Popen

    def mock_popen(command, **kwargs):
        if "--controller" in command:
            idx = command.index("--controller")
            new_cmd = [sys.executable, str(script)] + command[idx:]
            return original_popen(new_cmd, **kwargs)
        return original_popen(command, **kwargs)

    with patch("profiledock.process_manager.subprocess.Popen", side_effect=mock_popen):
        with pytest.raises(BrowserLaunchError, match="native startup failure") as raised:
            start_controller(str(data_dir), 1, headless=True)
    assert raised.value.category == "controller_exited"
    error = _read_error(error_path(str(data_dir)))
    assert error["error_type"] == "controller_exited"
    assert "native startup failure" in error["message"]


def test_error_file_cleaned_after_successful_launch(controller_env):
    manager, profile, data_dir, owned_pids = controller_env
    err = error_path(str(data_dir))
    err.write_text(
        json.dumps({"error_type": "old_error", "message": "old failure"}),
        encoding="utf-8",
    )
    state = _start(data_dir, owned_pids, tabs=1)
    assert not err.exists()
    _close(data_dir, owned_pids, state["pid"])
    assert not err.exists()


def test_unexpected_process_exit_cleans_running_state(controller_env):
    manager, profile, data_dir, owned_pids = controller_env
    state = _start(data_dir, owned_pids, tabs=1)
    pid = state["pid"]
    _terminate_owned_tree(pid)
    owned_pids.discard(pid)
    assert _wait_until(lambda: not is_running(str(data_dir)), timeout=5)
    assert not state_path(str(data_dir)).exists()


def test_close_after_manual_closure_reports_not_running(controller_env):
    manager, profile, data_dir, owned_pids = controller_env
    state = _start(data_dir, owned_pids, tabs=1)
    pid = state["pid"]
    _terminate_owned_tree(pid)
    owned_pids.discard(pid)
    with pytest.raises(ProfileRunningError, match="profile is not running"):
        close_controller(str(data_dir))
    assert not state_path(str(data_dir)).exists()


def test_relaunch_after_unexpected_controller_exit(controller_env):
    manager, profile, data_dir, owned_pids = controller_env
    first = _start(data_dir, owned_pids, tabs=2)
    _terminate_owned_tree(first["pid"])
    owned_pids.discard(first["pid"])
    second = _start(data_dir, owned_pids, tabs=1)
    assert second["page_count"] == 1
    _close(data_dir, owned_pids, second["pid"])


def test_close_racing_with_manual_closure(controller_env):
    import threading

    manager, profile, data_dir, owned_pids = controller_env
    state = _start(data_dir, owned_pids, tabs=1)
    pid = state["pid"]
    errors = []
    close_entered = threading.Event()

    original_is_running = is_running

    def spying_is_running(dd):
        result = original_is_running(dd)
        if result:
            close_entered.set()
        return result

    def kill_process():
        close_entered.wait(timeout=5)
        try:
            _terminate_owned_tree(pid)
        except Exception as exc:
            errors.append(exc)

    def call_close():
        try:
            with patch("profiledock.process_manager.is_running", side_effect=spying_is_running):
                close_controller(str(data_dir), timeout=10)
        except ProfileRunningError:
            pass
        except Exception as exc:
            errors.append(exc)

    t_kill = threading.Thread(target=kill_process)
    t_close = threading.Thread(target=call_close)
    t_close.start()
    t_kill.start()
    t_kill.join(timeout=10)
    t_close.join(timeout=10)
    owned_pids.discard(pid)
    assert not errors
    assert not state_path(str(data_dir)).exists()
    assert not is_running(str(data_dir))


def test_missing_state_file_during_close(tmp_path):
    data_dir = tmp_path / "browser-data"
    data_dir.mkdir()
    with pytest.raises(ProfileRunningError, match="profile is not running"):
        close_controller(str(data_dir))


def test_context_alive_returns_true_for_valid_context():
    class FakeContext:
        pages = [1, 2]

    assert _context_alive(FakeContext()) is True


def test_context_alive_returns_false_on_exception():
    class DeadContext:
        @property
        def pages(self):
            raise RuntimeError("browser closed")

    assert _context_alive(DeadContext()) is False


def test_context_alive_returns_false_without_pages():
    context = type("EmptyContext", (), {"pages": []})()
    assert _context_alive(context) is False


def test_controller_wait_stops_when_context_closes():
    class ClosingContext:
        def __init__(self):
            self.checks = 0

        @property
        def pages(self):
            self.checks += 1
            return [1] if self.checks == 1 else []

    class TimeoutServer:
        def accept(self):
            raise socket.timeout

    context = ClosingContext()
    _wait_for_close(TimeoutServer(), context, "token")
    assert context.checks == 2


def test_list_shows_stopped_after_manual_closure(controller_env):
    from typer.testing import CliRunner
    from profiledock.cli import app

    runner = CliRunner()
    manager, profile, data_dir, owned_pids = controller_env
    state = _start(data_dir, owned_pids, tabs=1)
    pid = state["pid"]
    _terminate_owned_tree(pid)
    owned_pids.discard(pid)
    is_running(str(data_dir))
    with patch("profiledock.cli.manager") as mock_manager:
        mock_manager.return_value.list_profiles.return_value = [profile]
        result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "stopped" in result.output


def test_close_timeout_with_dead_pid(controller_env):
    manager, profile, data_dir, owned_pids = controller_env
    state = _start(data_dir, owned_pids, tabs=1)
    pid = state["pid"]
    path = state_path(str(data_dir))
    _terminate_owned_tree(pid)
    owned_pids.discard(pid)
    _wait_until(lambda: not _alive(pid), timeout=5)
    with pytest.raises(ProfileRunningError, match="profile is not running"):
        close_controller(str(data_dir), timeout=0.3)
    assert not path.exists()


def test_read_state_rejects_non_dict(tmp_path):
    from profiledock.process_manager import _read_state

    path = tmp_path / "running.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert _read_state(path) is None

    path.write_text(json.dumps(42), encoding="utf-8")
    assert _read_state(path) is None


def test_alive_rejects_invalid_pid():
    assert _alive(-1) is False
    assert _alive(0) is False
