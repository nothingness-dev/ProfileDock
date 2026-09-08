import json
import os
import sys
import tempfile
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


@pytest.mark.parametrize("command", ["probe", "close", "tabs"])
@pytest.mark.parametrize("token", [None, "wrong", "\u2603"])
def test_json_protocol_rejects_unauthenticated_commands(command, token):
    request = Connection((json.dumps({"cmd": command, "token": token}) + "\n").encode())
    close = Connection(b"close:secret\n")
    context = type("Context", (), {"pages": [object()]})()
    _wait_for_close(Server([request, close]), context, "secret")
    assert json.loads(request.response)["status"] == "error"
    assert "unauthorized" in json.loads(request.response)["message"]


def test_reap_failure_preserves_runtime_record(tmp_path):
    data_dir = tmp_path / "profile" / "browser-data"
    data_dir.mkdir(parents=True)
    path = state_path(str(data_dir))
    state = {
        "protocol_version": RUNNING_STATE_PROTOCOL_VERSION,
        "engine": "playwright",
        "profile_id": "profile",
        "controller_pid": 999999,
        "controller_started_at": datetime.now(timezone.utc).isoformat(),
        "launcher_pid": 1,
        "port": 12345,
        "token": "x" * 32,
        "tabs": 1,
        "status": "running",
        "browser_pid": 4242,
        "browser_create_time": 100.0,
    }
    path.write_text(json.dumps(state), encoding="utf-8")
    with (
        patch("profiledock.process_manager._alive", return_value=False),
        patch("profiledock.process.manager._terminate_matching_process", return_value=False),
    ):
        assert get_status(str(data_dir)) == "error"
    assert json.loads(path.read_text(encoding="utf-8")) == state


def test_close_protocol_survives_non_ascii_command_without_crashing():
    """Regression: non-ASCII input raised TypeError out of hmac.compare_digest.

    Any local unauthenticated process could send one malformed line (e.g.
    b"\\x80\\n") and the uncaught TypeError would escape _wait_for_close,
    crash the controller, and close the user's browser. Non-ASCII commands
    must be answered with an error response instead.
    """
    non_ascii = Connection(b"\x80\n")
    correct = Connection(b"close:secret\n")
    context = type("Context", (), {"pages": [object()]})()
    _wait_for_close(Server([non_ascii, correct]), context, "secret")
    assert non_ascii.response == b"error\n"
    assert correct.response == b"ok\n"


def test_controller_listener_uses_exclusive_bind_on_windows(tmp_path):
    """Regression: SO_REUSEADDR on Windows allows a second local user to
    double-bind the controller's loopback port and capture probe/close
    traffic carrying the IPC token. Windows must set SO_EXCLUSIVEADDRUSE;
    POSIX keeps no reuse option (the port is ephemeral).
    """
    from profiledock.process import controller as controller_module

    captured: dict = {}
    real_socket = __import__("socket").socket

    class RecordingSocket(real_socket):
        def setsockopt(self, level, optname, value):
            captured[(level, optname)] = value
            return super().setsockopt(level, optname, value)

    import socket as socket_module

    recorded_socket = RecordingSocket()
    with (
        patch.object(socket_module, "socket", return_value=recorded_socket),
        patch("playwright.sync_api.sync_playwright"),
        patch.object(controller_module, "_launch_context", side_effect=RuntimeError("stop after bind")),
    ):
        server_path = tmp_path / "running.json"
        try:
            controller_module._controller(
                server_path,
                "unused-data-dir",
                1,
                "x" * 32,
                headless=True,
                _install_signal_handlers=False,
            )
        except SystemExit:
            pass
        except Exception:
            pass

    reuse_addr = captured.get((socket_module.SOL_SOCKET, socket_module.SO_REUSEADDR))
    if sys.platform == "win32":
        exclusive = captured.get((socket_module.SOL_SOCKET, socket_module.SO_EXCLUSIVEADDRUSE))
        assert exclusive == 1, "controller must set SO_EXCLUSIVEADDRUSE on Windows"
        assert reuse_addr != 1, "SO_REUSEADDR must not be set on Windows"
    else:
        assert reuse_addr != 1


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
        patch("profiledock.process_manager.subprocess.Popen", return_value=DummyProcess()) as popen,
        patch("profiledock.process_manager._get_process_create_time", return_value=100.0),
        patch("profiledock.process_manager._alive", return_value=False),
    ):
        start_direct_chrome(
            str(data_dir),
            tabs=1,
            executable_path=dummy_chrome,
            extra_args=["--incognito", "--start-maximized"],
        )
        launched_args = popen.call_args.args[0]
        assert "--incognito" in launched_args
        assert "--start-maximized" in launched_args

        assert launched_args.index("--incognito") > launched_args.index("--disable-background-mode")
        assert launched_args.index("--incognito") < launched_args.index("about:blank")

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


def test_controller_spawn_detaches_into_its_own_process_group(tmp_path):
    """Regression: the controller inherited the launcher's process group.

    A controller sharing the launcher's group dies together with it (terminal
    SIGHUP / CTRL_CLOSE_EVENT) with no chance to run its finally-block, leaving
    the whole Chromium tree orphaned. It must be spawned detached into a fresh
    session (POSIX) / detached process group (Windows), mirroring the direct
    engine, so teardown signals reach the group and terminal death does not
    propagate.
    """
    import subprocess

    from profiledock.process_manager import BrowserLaunchError, start_controller

    data_dir = tmp_path / "profile-detach" / "browser-data"
    data_dir.mkdir(parents=True)

    captured_kwargs: dict = {}

    class DummyProcess:
        pid = 424242
        returncode = 1
        stderr = None

        def poll(self):
            return 1

    def capture_popen(command, **kwargs):
        captured_kwargs.update(kwargs)
        return DummyProcess()

    with (
        patch("profiledock.process_manager.subprocess.Popen", side_effect=capture_popen),
        patch("profiledock.process_manager._stop_process"),
    ):
        with pytest.raises(BrowserLaunchError):
            start_controller(str(data_dir), 1, headless=True, startup_timeout=2)

    if sys.platform == "win32":
        flags = captured_kwargs.get("creationflags", 0)
        assert flags & getattr(subprocess, "DETACHED_PROCESS", 0x00000008), (
            "controller must be spawned detached on Windows"
        )
        assert flags & getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200), (
            "controller must get its own process group on Windows"
        )
    else:
        assert captured_kwargs.get("start_new_session") is True, (
            "controller must be spawned into a fresh session on POSIX"
        )


def test_start_controller_drains_stderr_during_startup_poll():
    """Regression: the controller's stderr=PIPE was never drained during the
    startup poll. A chatty Playwright driver (GPU/fontconfig error loops,
    DEBUG=pw:api) fills the OS pipe buffer (~4KB on Windows), the controller
    blocks on its next stderr write, never publishes ready state, and the
    launcher reports a misleading controller_timeout. The poll loop must
    drain stderr while waiting.
    """
    import subprocess as subprocess_module

    from profiledock.process_manager import BrowserLaunchError, start_controller

    data_dir = Path(tempfile.mkdtemp(prefix="pd-stderr-")) / "browser-data"
    data_dir.mkdir()

    script = Path(tempfile.mkdtemp(prefix="pd-stderr-script-")) / "chatty.py"
    script.write_text(
        "import sys\n"
        "for i in range(8192):\n"
        "    sys.stderr.write('x' * 1024)\n"
        "sys.stderr.flush()\n"
        "sys.exit(3)\n",
        encoding="utf-8",
    )

    original_popen = subprocess_module.Popen

    def mock_popen(command, **kwargs):
        new_cmd = [sys.executable, str(script)]
        return original_popen(new_cmd, **kwargs)

    with (
        patch("profiledock.process_manager.subprocess.Popen", side_effect=mock_popen),
        patch("profiledock.process_manager.is_running", return_value=False),
    ):
        with pytest.raises(BrowserLaunchError) as excinfo:
            start_controller(str(data_dir), 1, headless=True, startup_timeout=5)

    assert excinfo.value.category == "controller_exited"


def test_automation_autostart_forwards_identity():
    """Regression: send_controller_command's auto-start spawned a bare
    headless Chromium with no proxy, user-agent, locale or timezone. A
    profile configured with a socks5 proxy silently egressed via the real
    IP -- a privacy/identity violation. Auto-start must apply the same
    identity options a manual launch would.
    """
    data_dir = Path(tempfile.mkdtemp(prefix="pd-autostart-")) / "browser-data"
    data_dir.mkdir()

    captured: dict = {}

    def fake_start_controller(data_dir, tabs=1, headless=False, runtime_dir=None, **kwargs):
        captured.update(kwargs)
        captured["headless"] = headless
        captured["tabs"] = tabs
        return {
            "protocol_version": RUNNING_STATE_PROTOCOL_VERSION,
            "engine": "playwright",
            "profile_id": data_dir_justify(data_dir),
            "controller_pid": 999999,
            "controller_started_at": datetime.now(timezone.utc).isoformat(),
            "launcher_pid": 1,
            "port": 12345,
            "token": "x" * 32,
            "tabs": 1,
            "status": "running",
        }

    with (
        patch("profiledock.process.ipc.start_controller", side_effect=fake_start_controller),
        patch("profiledock.process.ipc._controller_available", return_value=False),
    ):
        from profiledock.process.ipc import send_controller_command

        fake_response = json.dumps({"status": "ok"}).encode() + b"\n"

        class FakeSock:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def settimeout(self, t):
                pass

            def sendall(self, data):
                pass

            def recv(self, size):
                return fake_response

        with patch("profiledock.process.ipc.socket.create_connection", return_value=FakeSock()):
            send_controller_command(
                str(data_dir),
                "probe",
                proxy="socks5://user:pass@proxy:1080",
                user_agent="TestUA/1.0",
                locale="en-GB",
                timezone="Europe/Berlin",
            )

    assert captured.get("proxy") == "socks5://user:pass@proxy:1080"
    assert captured.get("user_agent") == "TestUA/1.0"
    assert captured.get("locale") == "en-GB"
    assert captured.get("timezone") == "Europe/Berlin"
    assert captured.get("headless") is True


def data_dir_justify(data_dir):
    return Path(data_dir).parent.name


def test_mutation_check_treats_live_browser_as_active_even_without_controller():
    """Regression: is_active_for_mutation read a controller-dead /
    browser-alive profile as inactive, so a delete or backup could race a
    live Chromium still writing to the browser-data directory. A recorded
    browser whose identity matches is activity, full stop.
    """
    data_dir = Path(tempfile.mkdtemp(prefix="pd-mutation-")) / "browser-data"
    data_dir.mkdir()
    path = state_path(str(data_dir))
    state = {
        "protocol_version": RUNNING_STATE_PROTOCOL_VERSION,
        "engine": "playwright",
        "profile_id": data_dir.parent.name,
        "controller_pid": 999999,
        "controller_started_at": datetime.now(timezone.utc).isoformat(),
        "launcher_pid": 4242,
        "port": 12345,
        "token": "x" * 32,
        "tabs": 1,
        "status": "running",
        "browser_pid": 5150,
        "browser_create_time": 100.0,
        "headless": True,
    }
    path.write_text(json.dumps(state), encoding="utf-8")

    with (
        patch("profiledock.process_manager._alive", return_value=False),
        patch("profiledock.process_manager._controller_available", return_value=False),
        patch("profiledock.process_manager._is_matching_process", return_value=True),
    ):
        assert is_active_for_mutation(str(data_dir)) is True


def test_stale_starting_state_ignores_reused_launcher_pid():
    """Regression: stale 'starting' states (controller_pid=0) were kept alive
    by a bare _alive(launcher_pid) check with no recorded create-time. When
    Windows reused the launcher PID for an unrelated long-lived process,
    every launch failed with 'profile is already running' until that
    unrelated process exited. The launcher's create-time is now recorded and
    verified, exactly like the browser's.
    """
    data_dir = Path(tempfile.mkdtemp(prefix="pd-launcherpid-")) / "browser-data"
    data_dir.mkdir()
    path = state_path(str(data_dir))
    state = {
        "protocol_version": RUNNING_STATE_PROTOCOL_VERSION,
        "engine": "playwright",
        "profile_id": data_dir.parent.name,
        "controller_pid": 0,
        "controller_started_at": datetime.now(timezone.utc).isoformat(),
        "launcher_pid": 4242,
        "launcher_create_time": 111.0,
        "port": 0,
        "token": "x" * 32,
        "tabs": 1,
        "status": "starting",
    }
    path.write_text(json.dumps(state), encoding="utf-8")

    with (
        patch("profiledock.process_manager._alive", return_value=True),
        patch("profiledock.process_manager._get_process_create_time", return_value=999.0),
    ):
        assert get_status(str(data_dir), clean_stale=False) == "stale"
        assert get_status(str(data_dir), clean_stale=True) == "stopped"

    path.write_text(json.dumps(state), encoding="utf-8")
    with (
        patch("profiledock.process_manager._alive", return_value=True),
        patch("profiledock.process_manager._get_process_create_time", return_value=111.0),
    ):
        assert get_status(str(data_dir), clean_stale=False) == "starting"


def test_clean_stale_terminates_recorded_browser_before_unlinking():
    """Regression: get_status's clean_stale paths unlinked running.json
    without terminating a recorded live browser_pid. The relaunch path then
    destroyed the browser identity record while Chromium kept running,
    holding the Windows profile lock; the next launch failed with a cryptic
    browser_unavailable and the orphan was unmanageable.
    """
    data_dir = Path(tempfile.mkdtemp(prefix="pd-cleanstale-")) / "browser-data"
    data_dir.mkdir()
    path = state_path(str(data_dir))
    state = {
        "protocol_version": RUNNING_STATE_PROTOCOL_VERSION,
        "engine": "playwright",
        "profile_id": data_dir.parent.name,
        "controller_pid": 999999,
        "controller_started_at": datetime.now(timezone.utc).isoformat(),
        "launcher_pid": os.getpid(),
        "port": 12345,
        "token": "x" * 32,
        "tabs": 1,
        "status": "running",
        "browser_pid": 4242,
        "browser_create_time": 100.0,
        "headless": True,
    }
    path.write_text(json.dumps(state), encoding="utf-8")

    terminated = []
    with (
        patch("profiledock.process_manager._alive", return_value=False),
        patch("profiledock.process_manager._is_matching_process", return_value=True),
        patch(
            "profiledock.process.manager._terminate_matching_process",
            side_effect=lambda pid, *a, **k: (terminated.append(pid), True)[1],
        ),
    ):
        status = get_status(str(data_dir), clean_stale=True)

    assert status in ("crashed", "stopped")
    assert 4242 in terminated, "recorded live browser must be terminated before unlink"
    assert not path.exists()


def test_controller_rejects_redundant_launch_arguments():
    """Regression: --browser-channel/--window-size/--url were parsed by main()
    but start_controller never sent them -- channel/window/urls travel via the
    running.json state file. Two parallel transport contracts meant a caller
    wiring only argv got silently ignored values. The dead surface is gone;
    the state file is the sole channel for those parameters.
    """
    from profiledock.process import controller as controller_module

    real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

    def blocked_import(name, *args, **kwargs):
        if name == "playwright.sync_api":
            raise ImportError("playwright missing")
        return real_import(name, *args, **kwargs)

    argv_backup = sys.argv
    scratch = Path(tempfile.mkdtemp(prefix="pd-dead-argv-"))
    sys.argv = [
        "controller",
        "--controller",
        str(scratch / "running.json"),
        str(scratch / "browser-data"),
        "1",
        "--browser-channel",
        "chrome",
    ]
    import contextlib
    import io as io_module

    err_stream = io_module.StringIO()
    try:
        with (
            patch("builtins.__import__", blocked_import),
            patch.dict(os.environ, {"PROFILEDOCK_CONTROLLER_TOKEN": "x" * 32}),
            contextlib.redirect_stderr(err_stream),
            pytest.raises(SystemExit),
        ):
            controller_module.main()

        assert "unrecognized arguments" in err_stream.getvalue()
    finally:
        sys.argv = argv_backup
        import shutil

        shutil.rmtree(scratch, ignore_errors=True)


def test_controller_credentials_use_environment():
    """Regression: token and --proxy traveled as command-line arguments.

    argv is world-readable on Linux (/proc/<pid>/cmdline) and readable by
    same-user processes on Windows (WMI). The IPC token and a proxy URL with
    embedded credentials must travel via an environment variable instead.
    """
    from profiledock.process_manager import BrowserLaunchError, start_controller

    data_dir = Path(tempfile.mkdtemp(prefix="pd-argv-")) / "browser-data"
    data_dir.mkdir()

    captured: dict = {}

    class DummyProcess:
        pid = 1
        returncode = 1
        stderr = None

        def poll(self):
            return 1

    def capture_popen(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs.get("env")
        return DummyProcess()

    with (
        patch("profiledock.process_manager.subprocess.Popen", side_effect=capture_popen),
        patch("profiledock.process_manager._stop_process"),
    ):
        with pytest.raises(BrowserLaunchError):
            start_controller(
                str(data_dir), 1, headless=True, startup_timeout=0.5, proxy="http://user:secret@proxy:8080"
            )

    argv = " ".join(captured["command"])
    assert "pd-argv-" in argv
    assert "secret" not in argv, "proxy credentials must not appear in argv"
    env = captured["env"] or {}

    token_env = env.get("PROFILEDOCK_CONTROLLER_TOKEN")
    assert token_env, "token must be passed via PROFILEDOCK_CONTROLLER_TOKEN env var"
    assert token_env not in argv, "token must not appear in argv"
    proxy_env = env.get("PROFILEDOCK_CONTROLLER_PROXY")
    assert proxy_env == "http://user:secret@proxy:8080", "proxy must travel via env"
    assert "--proxy" not in argv


def test_launch_rejects_unsupported_url_schemes():
    """Regression: start_urls bypassed the tool's URL allowlist.

    Every IPC command validates URLs against http/https/about, but
    validate_launch_request counted only len(urls) <= tabs, so the public
    start_controller API could navigate file://, data:, or javascript:
    URLs at startup -- schemes the tool deliberately forbids elsewhere.
    """
    from profiledock.process.launch import validate_launch_request

    data_dir = Path(tempfile.mkdtemp(prefix="pd-url-scheme-"))
    try:
        with pytest.raises(Exception, match=r"scheme|URL"):
            validate_launch_request(str(data_dir), 1, None, None, ["file:///C:/secret.html"])
        with pytest.raises(Exception, match=r"scheme|URL"):
            validate_launch_request(str(data_dir), 1, None, None, ["data:text/html,hello"])

        validate_launch_request(str(data_dir), 1, None, None, ["https://example.com"])
        validate_launch_request(str(data_dir), 2, None, None, ["about:blank", "https://example.com"])
    finally:
        data_dir.rmdir()


def test_launch_rejects_root_parented_data():
    """Regression: Path(data_dir).parent.name == '' for root-parented data dirs.

    start_controller('C:/mydata') derived profile_id='' which _valid_state
    always rejects, so the launcher never accepted the ready state: the poll
    loop timed out and a healthy browser was force-killed. Such a data_dir
    must be rejected up front with a clear error.
    """
    from profiledock.process.launch import validate_launch_request

    probe = Path(Path(tempfile.gettempdir()).anchor) / "profiledock-drive-root-probe"
    with patch.object(Path, "is_dir", return_value=True):
        with pytest.raises(Exception, match=r"data directory|profile"):
            validate_launch_request(str(probe), 1, None, None, None)


def test_stop_process_kills_the_whole_process_group_posix():
    """Regression: _stop_process degraded to single-PID kill on POSIX.

    The controller now runs as its own process-group leader (see
    test_controller_spawn_detaches_into_its_own_process_group), so a
    group-kill must be issued; a lone SIGTERM to the controller PID would
    orphan the node driver and Chromium children.
    """
    from profiledock.process_manager import _stop_process

    process = type("Process", (), {"pid": 4242, "poll": lambda self: None})()
    with (
        patch("profiledock.process.identity.sys.platform", "linux"),
        patch("profiledock.process.identity._signal_posix_process_group") as signal_group,
        patch("profiledock.process_manager.subprocess.run") as run,
    ):
        process.communicate = lambda timeout: (b"", b"")  # type: ignore[method-assign]
        _stop_process(process)  # type: ignore[arg-type]
        signal_group.assert_called_once()
        run.assert_not_called()
