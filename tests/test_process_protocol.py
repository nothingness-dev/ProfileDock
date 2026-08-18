import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from profiledock.process_manager import _atomic_private_json, _read_state, _wait_for_close, get_status, state_path


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
