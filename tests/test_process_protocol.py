import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from profiledock.process_manager import (
    _atomic_private_json,
    _read_state,
    _valid_state,
    _wait_for_close,
    _write_all,
    get_status,
    state_path,
)


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

    def sendall(self, payload):
        self.response = payload


class Server:
    def __init__(self, connections):
        self.connections = iter(connections)

    def accept(self):
        return next(self.connections), None


def test_close_protocol_rejects_wrong_token_before_accepting_match():
    wrong = Connection(b"close:wrong\n")
    correct = Connection(b"close:secret\n")
    context = type("Context", (), {"pages": [object()]})()
    _wait_for_close(Server([wrong, correct]), context, "secret")
    assert wrong.response == b"error\n"
    assert correct.response == b"ok\n"


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
    assert state["protocol_version"] == 1
    assert state["profile_id"] == "profile-a"
    assert state["controller_pid"] == os.getpid()
    assert state["legacy_controller"] is True


def test_state_for_another_profile_is_stale(tmp_path):
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
    assert get_status(str(data_dir), clean_stale=False) == "stale"


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


def test_stale_cleanup_tolerates_unlink_failure(tmp_path):
    data_dir = tmp_path / "profile-a" / "browser-data"
    data_dir.mkdir(parents=True)
    path = state_path(str(data_dir))
    path.write_text("corrupted", encoding="utf-8")
    with patch.object(Path, "unlink", side_effect=PermissionError("locked")):
        assert get_status(str(data_dir)) == "stale"


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
        with pytest.raises(BrowserLaunchError, match="Google Chrome, Chromium, or Brave executable not found"):
            start_direct_chrome(str(data_dir), tabs=1)

    dummy_chrome = tmp_path / "chrome.exe"
    dummy_chrome.write_text("dummy", encoding="utf-8")

    class DummyProcess:
        pid = 12345

    with patch("profiledock.process_manager.subprocess.Popen", return_value=DummyProcess()):
        state = start_direct_chrome(str(data_dir), tabs=2, executable_path=dummy_chrome)
        assert state["pid"] == 12345
        assert state["engine"] == "direct"
        assert state["tabs"] == 2
        assert state["channel"] == "chrome"

    with patch("profiledock.process_manager._alive", return_value=True):
        assert get_status(str(data_dir)) == "running"
        assert is_running(str(data_dir))

        with pytest.raises(ProfileRunningError, match="profile is already running"):
            start_direct_chrome(str(data_dir), tabs=1, executable_path=dummy_chrome)

    with patch(
        "profiledock.process_manager._alive",
        side_effect=[True, True, True, False, False],
    ), patch("subprocess.run"):
        close_controller(str(data_dir), timeout=0)
        assert not (data_dir.parent / "running.json").exists()


def test_direct_chrome_stale_detection(tmp_path):
    data_dir = tmp_path / "profile-direct-stale" / "browser-data"
    data_dir.mkdir(parents=True)
    state_file = data_dir.parent / "running.json"
    state_file.write_text(
        json.dumps({"pid": 99999, "engine": "direct", "tabs": 1, "channel": "chrome"}),
        encoding="utf-8",
    )

    with patch("profiledock.process_manager._alive", return_value=False):
        assert get_status(str(data_dir), clean_stale=True) == "stale"
        assert not state_file.exists()


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
                "engine": "direct",
                "tabs": 1,
                "channel": "chrome",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "status": "running",
            }
        ),
        encoding="utf-8",
    )

    with patch("profiledock.process_manager._alive", return_value=True), patch(
        "profiledock.process_manager.subprocess.run"
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
    assert get_status(str(data_dir), clean_stale=False) == "stale"


def test_direct_launch_state_failure_stops_browser(tmp_path):
    from profiledock.process_manager import BrowserLaunchError, start_direct_chrome

    data_dir = tmp_path / "profile-direct" / "browser-data"
    data_dir.mkdir(parents=True)
    executable = tmp_path / "chrome.exe"
    executable.write_text("browser", encoding="utf-8")
    process = type("Process", (), {"pid": 12345})()

    with patch("profiledock.process_manager.subprocess.Popen", return_value=process), patch(
        "profiledock.process_manager._atomic_private_json",
        side_effect=OSError("state unavailable"),
    ), patch("profiledock.process_manager._stop_process") as stop_process:
        with pytest.raises(BrowserLaunchError, match="state unavailable"):
            start_direct_chrome(str(data_dir), 1, executable_path=executable)

    stop_process.assert_called_once_with(process)
    assert not (data_dir.parent / "running.json").exists()


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

    with patch("profiledock.process_manager.subprocess.Popen", side_effect=mock_popen):
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
