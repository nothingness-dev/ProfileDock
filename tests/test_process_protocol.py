import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from profiledock.process_manager import (
    RUNNING_STATE_PROTOCOL_VERSION,
    ProfileRunningError,
    _alive,
    _atomic_private_json,
    _read_state,
    _valid_state,
    _wait_for_close,
    _write_all,
    close_controller,
    get_status,
    is_active_for_mutation,
    state_path,
)


def test_alive_reaps_exited_unix_child():
    with (
        patch("profiledock.process_manager.os.name", "posix"),
        patch("profiledock.process_manager.os.waitpid", return_value=(123, 0)),
        patch("profiledock.process_manager.os.kill") as kill,
    ):
        assert not _alive(123)
    kill.assert_not_called()


def test_close_preserves_malformed_runtime_state(tmp_path):
    data_dir = tmp_path / "profile-a" / "browser-data"
    data_dir.mkdir(parents=True)
    path = state_path(str(data_dir))
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(ProfileRunningError, match="ambiguous state"):
        close_controller(str(data_dir))
    assert path.read_text(encoding="utf-8") == "not-json"


def test_start_direct_chrome_reports_unreadable_state_with_repair_hint(tmp_path):
    from profiledock.process_manager import start_direct_chrome

    data_dir = tmp_path / "profile-b" / "browser-data"
    data_dir.mkdir(parents=True)
    path = state_path(str(data_dir))
    path.write_text("{broken json", encoding="utf-8")
    executable = tmp_path / "chrome.exe"
    executable.write_text("dummy", encoding="utf-8")

    with pytest.raises(ProfileRunningError, match="doctor --repair"):
        start_direct_chrome(str(data_dir), tabs=1, executable_path=executable)
    assert path.exists()


def test_start_controller_reports_unreadable_state_with_repair_hint(tmp_path):
    from profiledock.process_manager import start_controller

    data_dir = tmp_path / "profile-c" / "browser-data"
    data_dir.mkdir(parents=True)
    runtime = tmp_path / "runtime" / "profile-c"
    path = state_path(str(data_dir), runtime)
    path.parent.mkdir(parents=True)
    path.write_text("]garbage[", encoding="utf-8")

    with pytest.raises(ProfileRunningError, match="doctor --repair"):
        start_controller(str(data_dir), tabs=1, runtime_dir=runtime)
    assert path.exists()


def test_close_preserves_unsupported_future_runtime_state(tmp_path):
    data_dir = tmp_path / "profile-a" / "browser-data"
    data_dir.mkdir(parents=True)
    path = state_path(str(data_dir))
    value = {
        "protocol_version": RUNNING_STATE_PROTOCOL_VERSION + 1,
        "engine": "playwright",
        "profile_id": "profile-a",
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ProfileRunningError, match="invalid"):
        close_controller(str(data_dir))
    assert json.loads(path.read_text(encoding="utf-8")) == value


class Connection:
    def __init__(self, payload):
        self.payload = payload
        self.response = b""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def recv(self, size):
        return self.payload

    def settimeout(self, timeout):
        return None

    def sendall(self, payload):
        self.response = payload


class Server:
    def __init__(self, connections):
        self.connections = iter(connections)

    def accept(self):
        return next(self.connections), None


def test_close_protocol_rejects_oversized_commands():
    oversized = Connection(b"close:" + b"x" * 1000 + b"\n")
    correct = Connection(b"close:secret\n")
    context = type("Context", (), {"pages": [object()]})()
    _wait_for_close(Server([oversized, correct]), context, "secret")
    assert oversized.response == b"error\n"
    assert correct.response == b"ok\n"


def test_close_protocol_rejects_malformed_non_token_commands():
    malformed = Connection(b"kill:12345\n")
    correct = Connection(b"close:secret\n")
    context = type("Context", (), {"pages": [object()]})()
    _wait_for_close(Server([malformed, correct]), context, "secret")
    assert malformed.response == b"error\n"
    assert correct.response == b"ok\n"


def test_close_protocol_authenticates_availability_probe():
    probe = Connection(b"probe:secret\n")
    close = Connection(b"close:secret\n")
    context = type("Context", (), {"pages": [object()]})()
    _wait_for_close(Server([probe, close]), context, "secret")
    assert probe.response == b"ok\n"
    assert close.response == b"ok\n"


def test_mutation_check_uses_direct_pid_identity(tmp_path):
    data_dir = tmp_path / "direct" / "browser-data"
    data_dir.mkdir(parents=True)
    state_path(str(data_dir)).write_text(
        json.dumps(
            {
                "profile_id": "direct",
                "pid": 123,
                "launcher_pid": 1,
                "process_create_time": 10.0,
                "engine": "direct",
                "tabs": 1,
                "channel": "chromium",
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    with (
        patch("profiledock.process_manager._alive", return_value=True),
        patch("profiledock.process_manager._get_process_create_time", return_value=20.0),
    ):
        assert not is_active_for_mutation(str(data_dir))


def test_mutation_check_uses_playwright_controller_availability(tmp_path):
    data_dir = tmp_path / "playwright" / "browser-data"
    data_dir.mkdir(parents=True)
    state_path(str(data_dir)).write_text(
        json.dumps(
            {
                "protocol_version": 1,
                "profile_id": "playwright",
                "token": "x" * 32,
                "controller_pid": 999999,
                "launcher_pid": 999998,
                "port": 12345,
                "controller_started_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    with (
        patch("profiledock.process_manager._alive", return_value=False),
        patch("profiledock.process_manager._controller_available", return_value=True),
    ):
        assert is_active_for_mutation(str(data_dir))


def test_mutation_check_fails_closed_for_corrupt_runtime_state(tmp_path):
    data_dir = tmp_path / "corrupt" / "browser-data"
    data_dir.mkdir(parents=True)
    state_path(str(data_dir)).write_text("not-json", encoding="utf-8")
    assert is_active_for_mutation(str(data_dir))


def test_direct_close_never_signals_state_without_process_identity(tmp_path):
    from profiledock.process_manager import ProfileRunningError, close_controller

    data_dir = tmp_path / "direct-unverified" / "browser-data"
    data_dir.mkdir(parents=True)
    state_path(str(data_dir)).write_text(
        json.dumps(
            {
                "profile_id": "direct-unverified",
                "pid": 12345,
                "launcher_pid": os.getpid(),
                "engine": "direct",
                "tabs": 1,
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    with (
        patch("profiledock.process_manager._alive", return_value=True),
        patch("profiledock.process_manager.subprocess.run") as signal_process,
    ):
        with pytest.raises(ProfileRunningError, match="unverified process"):
            close_controller(str(data_dir))
    signal_process.assert_not_called()


def test_direct_close_preserves_state_when_live_process_identity_is_unavailable(tmp_path):
    data_dir = tmp_path / "direct-unavailable" / "browser-data"
    data_dir.mkdir(parents=True)
    path = state_path(str(data_dir))
    path.write_text(
        json.dumps(
            {
                "protocol_version": RUNNING_STATE_PROTOCOL_VERSION,
                "profile_id": "direct-unavailable",
                "pid": 12345,
                "launcher_pid": os.getpid(),
                "process_create_time": 100.0,
                "engine": "direct",
                "tabs": 1,
                "channel": "chrome",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "status": "running",
            }
        ),
        encoding="utf-8",
    )
    with (
        patch("profiledock.process_manager._alive", return_value=True),
        patch("profiledock.process_manager._get_process_create_time", return_value=None),
        patch("profiledock.process_manager.subprocess.run") as signal_process,
    ):
        with pytest.raises(ProfileRunningError, match="could not be verified"):
            close_controller(str(data_dir))
    signal_process.assert_not_called()
    assert path.exists()


def test_direct_close_detects_pid_reuse(tmp_path):
    from profiledock.process_manager import (
        ProfileRunningError,
        close_controller,
    )

    data_dir = tmp_path / "profile-pid-reuse" / "browser-data"
    data_dir.mkdir(parents=True)
    state_file = data_dir.parent / "running.json"
    state_file.write_text(
        json.dumps(
            {
                "profile_id": "profile-pid-reuse",
                "pid": 12345,
                "launcher_pid": os.getpid(),
                "process_create_time": 100.0,
                "engine": "direct",
                "tabs": 1,
                "channel": "chrome",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "status": "running",
            }
        ),
        encoding="utf-8",
    )

    with (
        patch("profiledock.process_manager._alive", return_value=True),
        patch("profiledock.process_manager._get_process_create_time", return_value=999.0),
    ):
        with pytest.raises(ProfileRunningError, match="PID was reused"):
            close_controller(str(data_dir), timeout=0.1)

    assert not state_file.exists()


def test_legacy_live_state_is_upgraded(tmp_path):
    data_dir = tmp_path / "profile-a" / "browser-data"
    data_dir.mkdir(parents=True)
    path = state_path(str(data_dir))
    path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "port": 12345,
                "token": "x" * 32,
                "tabs": 1,
            }
        ),
        encoding="utf-8",
    )
    assert get_status(str(data_dir)) == "running"
    state = _read_state(path)
    assert state["protocol_version"] == 2
    assert state["profile_id"] == "profile-a"
    assert state["controller_pid"] == os.getpid()
    assert state["legacy_controller"] is True


def test_state_for_another_profile_is_preserved_as_error(tmp_path):
    data_dir = tmp_path / "profile-a" / "browser-data"
    data_dir.mkdir(parents=True)
    path = state_path(str(data_dir))
    path.write_text(
        json.dumps(
            {
                "protocol_version": 1,
                "profile_id": "profile-b",
                "controller_pid": os.getpid(),
                "controller_started_at": datetime.now(timezone.utc).isoformat(),
                "port": 12345,
                "token": "x" * 32,
            }
        ),
        encoding="utf-8",
    )
    assert get_status(str(data_dir), clean_stale=False) == "error"
    assert path.exists()


def test_private_state_write_retries_transient_replace_failure(tmp_path):
    target = tmp_path / "running.json"
    original_replace = Path.replace
    attempts = 0

    def replace_with_failures(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("temporarily locked")
        return original_replace(source, destination)

    with patch.object(Path, "replace", replace_with_failures):
        _atomic_private_json(target, {"status": "running"})
    assert json.loads(target.read_text(encoding="utf-8")) == {"status": "running"}
    assert attempts == 3


def test_malformed_state_is_preserved_without_unlink_attempt(tmp_path):
    data_dir = tmp_path / "profile-a" / "browser-data"
    data_dir.mkdir(parents=True)
    path = state_path(str(data_dir))
    path.write_text("corrupted", encoding="utf-8")
    with patch.object(Path, "unlink", side_effect=AssertionError("unlink attempted")):
        assert get_status(str(data_dir)) == "error"
    assert path.exists()


def test_private_write_all_handles_partial_writes():
    payloads = []

    def partial_write(fd, payload):
        written = min(3, len(payload))
        payloads.append(payload[:written])
        return written

    with patch("profiledock.process_manager.os.write", side_effect=partial_write):
        _write_all(1, b"abcdefgh")
    assert b"".join(payloads) == b"abcdefgh"


def test_state_rejects_boolean_numeric_fields():
    state = {
        "protocol_version": True,
        "profile_id": "profile-a",
        "controller_pid": True,
        "controller_started_at": datetime.now(timezone.utc).isoformat(),
        "port": True,
        "token": "x" * 32,
    }
    assert not _valid_state(state, "profile-a")


def test_state_rejects_timestamp_without_timezone():
    state = {
        "protocol_version": 1,
        "profile_id": "profile-a",
        "controller_pid": os.getpid(),
        "controller_started_at": "2026-01-01T00:00:00",
        "port": 12345,
        "token": "x" * 32,
    }
    assert not _valid_state(state, "profile-a")


def test_start_direct_chrome_validation_and_launch(tmp_path):
    from profiledock.process_manager import (
        BrowserLaunchError,
        ProfileRunningError,
        close_controller,
        is_running,
        start_direct_chrome,
    )

    data_dir = tmp_path / "profile-direct" / "browser-data"

    with pytest.raises(ValueError, match="tab count must be at least 1"):
        start_direct_chrome(str(data_dir), tabs=0)

    with pytest.raises(BrowserLaunchError, match="profile data directory is missing or invalid"):
        start_direct_chrome(str(data_dir), tabs=1)

    data_dir.mkdir(parents=True)

    with patch("profiledock.process_manager._system_browser_executable", return_value=None):
        with pytest.raises(BrowserLaunchError, match="Google Chrome or Chromium executable not found"):
            start_direct_chrome(str(data_dir), tabs=1)

    dummy_chrome = tmp_path / "chrome.exe"
    dummy_chrome.write_text("dummy", encoding="utf-8")

    class DummyProcess:
        pid = 12345

    with (
        patch("profiledock.process_manager.subprocess.Popen", return_value=DummyProcess()) as popen,
        patch("profiledock.process_manager._get_process_create_time", return_value=100.0),
    ):
        state = start_direct_chrome(str(data_dir), tabs=2, executable_path=dummy_chrome)
        assert state["pid"] == 12345
        assert state["engine"] == "direct"
        assert state["tabs"] == 2
        assert state["channel"] == "chrome"
        assert "--disable-background-mode" in popen.call_args.args[0]

    with (
        patch("profiledock.process_manager._alive", return_value=True),
        patch("profiledock.process_manager._get_process_create_time", return_value=100.0),
    ):
        assert get_status(str(data_dir)) == "running"
        assert is_running(str(data_dir))

        with pytest.raises(ProfileRunningError, match="profile is already running"):
            start_direct_chrome(str(data_dir), tabs=1, executable_path=dummy_chrome)

    with (
        patch(
            "profiledock.process_manager._alive",
            side_effect=[True, True, True, True, False, False],
        ),
        patch("profiledock.process_manager._get_process_create_time", return_value=100.0),
        patch("subprocess.run"),
    ):
        close_controller(str(data_dir), timeout=0)
        assert not (data_dir.parent / "running.json").exists()


def test_incomplete_direct_state_is_preserved_as_error(tmp_path):
    data_dir = tmp_path / "profile-direct-stale" / "browser-data"
    data_dir.mkdir(parents=True)
    state_file = data_dir.parent / "running.json"
    state_file.write_text(
        json.dumps({"pid": 99999, "engine": "direct", "tabs": 1, "channel": "chrome"}),
        encoding="utf-8",
    )

    with patch("profiledock.process_manager._alive", return_value=False):
        assert get_status(str(data_dir), clean_stale=True) == "error"
        assert state_file.exists()


def test_direct_chrome_close_failure_preserves_state(tmp_path):
    from profiledock.process_manager import BrowserLaunchError, close_controller

    data_dir = tmp_path / "profile-direct" / "browser-data"
    data_dir.mkdir(parents=True)
    state_file = data_dir.parent / "running.json"
    state_file.write_text(
        json.dumps(
            {
                "profile_id": "profile-direct",
                "pid": 12345,
                "launcher_pid": os.getpid(),
                "process_create_time": 100.0,
                "engine": "direct",
                "tabs": 1,
                "channel": "chrome",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "status": "running",
            }
        ),
        encoding="utf-8",
    )

    with (
        patch("profiledock.process_manager._alive", return_value=True),
        patch("profiledock.process_manager._get_process_create_time", return_value=100.0),
        patch("profiledock.process_manager.subprocess.run"),
    ):
        with pytest.raises(BrowserLaunchError, match="did not close"):
            close_controller(str(data_dir), timeout=0)
    assert state_file.exists()


def test_direct_state_rejects_invalid_pid(tmp_path):
    data_dir = tmp_path / "profile-direct" / "browser-data"
    data_dir.mkdir(parents=True)
    state_file = data_dir.parent / "running.json"
    state_file.write_text(
        json.dumps(
            {
                "profile_id": "profile-direct",
                "pid": "12345",
                "launcher_pid": os.getpid(),
                "engine": "direct",
                "tabs": 1,
                "channel": "chrome",
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    assert get_status(str(data_dir), clean_stale=False) == "error"
    assert state_file.exists()


def test_direct_launch_state_failure_stops_browser(tmp_path):
    from profiledock.process_manager import BrowserLaunchError, start_direct_chrome

    data_dir = tmp_path / "profile-direct" / "browser-data"
    data_dir.mkdir(parents=True)
    executable = tmp_path / "chrome.exe"
    executable.write_text("browser", encoding="utf-8")
    process = type("Process", (), {"pid": 12345})()

    with (
        patch("profiledock.process_manager.subprocess.Popen", return_value=process),
        patch(
            "profiledock.process_manager._atomic_private_json",
            side_effect=OSError("state unavailable"),
        ),
        patch("profiledock.process_manager._get_process_create_time", return_value=100.0),
        patch("profiledock.process_manager._stop_process") as stop_process,
    ):
        with pytest.raises(BrowserLaunchError, match="state unavailable"):
            start_direct_chrome(str(data_dir), 1, executable_path=executable)

    stop_process.assert_called_once_with(process)
    assert not (data_dir.parent / "running.json").exists()


def test_direct_launch_survives_unavailable_process_identity(tmp_path):
    """Platforms without create-time support (macOS) must still launch and close."""
    from profiledock.process_manager import get_status, start_direct_chrome

    data_dir = tmp_path / "profile-unverified" / "browser-data"
    data_dir.mkdir(parents=True)
    executable = tmp_path / "chrome.exe"
    executable.write_text("browser", encoding="utf-8")
    process = type("Process", (), {"pid": 12345})()

    signaled = {"done": False}

    def fake_run(*args, **kwargs):
        signaled["done"] = True
        return type("Completed", (), {"returncode": 0})()

    def fake_alive(pid):
        # Alive through launch + identity checks, then "exits" once signaled.
        return not signaled["done"]

    with (
        patch("profiledock.process_manager.subprocess.Popen", return_value=process),
        patch("profiledock.process_manager._get_process_create_time", return_value=None),
        patch("profiledock.process_manager._stop_process") as stop_process,
        patch("profiledock.process_manager.subprocess.run", side_effect=fake_run),
        patch("profiledock.process_manager._alive", side_effect=fake_alive),
    ):
        state = start_direct_chrome(str(data_dir), tabs=1, executable_path=executable)
        assert state["pid"] == 12345
        assert state["process_create_time"] is None
        stop_process.assert_not_called()

        assert get_status(str(data_dir)) == "running"

        # Close must not refuse merely because create times are unavailable.
        close_controller(str(data_dir))
        assert not state_path(str(data_dir)).exists()


def test_direct_close_still_detects_pid_reuse_with_matching_platform(tmp_path):
    from profiledock.process_manager import start_direct_chrome

    data_dir = tmp_path / "profile-reuse" / "browser-data"
    data_dir.mkdir(parents=True)
    executable = tmp_path / "chrome.exe"
    executable.write_text("browser", encoding="utf-8")
    process = type("Process", (), {"pid": 12345})()
    with (
        patch("profiledock.process_manager.subprocess.Popen", return_value=process),
        patch("profiledock.process_manager._get_process_create_time", return_value=100.0),
    ):
        start_direct_chrome(str(data_dir), tabs=1, executable_path=executable)

    with (
        patch("profiledock.process_manager._alive", return_value=True),
        patch("profiledock.process_manager._get_process_create_time", return_value=99999.0),
    ):
        with pytest.raises(ProfileRunningError, match="PID was reused"):
            close_controller(str(data_dir))


def test_direct_launch_maps_urls_and_window_size(tmp_path):
    from profiledock.process_manager import start_direct_chrome

    data_dir = tmp_path / "profile-direct-config" / "browser-data"
    data_dir.mkdir(parents=True)
    executable = tmp_path / "chrome.exe"
    executable.write_text("browser", encoding="utf-8")

    captured_args = []

    def mock_popen(args, **kwargs):
        captured_args.extend(args)
        return type("Process", (), {"pid": 12345})()

    with (
        patch("profiledock.process_manager.subprocess.Popen", side_effect=mock_popen),
        patch("profiledock.process_manager._get_process_create_time", return_value=100.0),
    ):
        start_direct_chrome(
            str(data_dir),
            tabs=3,
            executable_path=executable,
            start_urls=["https://github.com"],
            window_width=1280,
            window_height=720,
        )

    assert "--window-size=1280,720" in captured_args
    assert "https://github.com" in captured_args
    assert captured_args.count("about:blank") == 2


def test_system_browser_preference_selects_requested_family(tmp_path):
    from profiledock.process_manager import _system_browser_executable

    chrome = tmp_path / "google-chrome"
    chromium = tmp_path / "chromium"
    chrome.write_text("chrome", encoding="utf-8")
    chromium.write_text("chromium", encoding="utf-8")

    def find_browser(name):
        if name == "google-chrome":
            return str(chrome)
        if name == "chromium":
            return str(chromium)
        return None

    with (
        patch("profiledock.browser_detection.sys.platform", "linux"),
        patch("profiledock.browser_detection.shutil.which", side_effect=find_browser),
    ):
        assert _system_browser_executable("chrome") == chrome
        assert _system_browser_executable("chromium") == chromium
        assert _system_browser_executable("unsupported") is None


def test_start_direct_chrome_operates_without_playwright(tmp_path):
    from profiledock.process_manager import start_direct_chrome

    data_dir = tmp_path / "profile-no-pw" / "browser-data"
    data_dir.mkdir(parents=True)
    executable = tmp_path / "chrome.exe"
    executable.write_text("browser", encoding="utf-8")

    with patch.dict(sys.modules, {"playwright": None, "playwright.sync_api": None}):
        process = type("Process", (), {"pid": 54321})()
        with (
            patch("profiledock.process_manager.subprocess.Popen", return_value=process),
            patch("profiledock.process_manager._get_process_create_time", return_value=100.0),
        ):
            state = start_direct_chrome(str(data_dir), tabs=1, executable_path=executable)
            assert state["pid"] == 54321
            assert state["engine"] == "direct"
