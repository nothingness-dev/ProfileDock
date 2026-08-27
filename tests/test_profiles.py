import json
import os
from datetime import datetime, timezone
from unittest.mock import patch

from profiledock.process_manager import get_status, is_running, state_path


class FixedUuid:
    def __init__(self, value):
        self.hex = value


def test_create_list_delete(manager):
    profile = manager.create("Personal")
    assert manager.list_profiles() == [profile]
    assert profile.id in profile.data_dir
    assert __import__("pathlib").Path(profile.data_dir).is_dir()
    manager.delete(profile.id)
    assert manager.list_profiles() == []
    assert not __import__("pathlib").Path(profile.data_dir).parent.exists()


def test_create_retries_profile_id_collision_without_touching_existing_directory(manager):
    existing = manager.profiles_dir / "deadbeef"
    existing.mkdir()
    marker = existing / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    with patch(
        "profiledock.profile_manager.uuid.uuid4",
        side_effect=[FixedUuid("deadbeef00000000"), FixedUuid("cafebabe00000000")],
    ):
        profile = manager.create("Collision Safe")
    assert profile.id == "cafebabe"
    assert marker.read_text(encoding="utf-8") == "keep"


def test_delete_removes_metadata_when_profile_directory_is_missing(manager):
    profile = manager.create("Missing")
    __import__("shutil").rmtree(__import__("pathlib").Path(profile.data_dir).parent)
    manager.delete(profile.id)
    assert manager.list_profiles() == []


def test_malformed_running_state_is_preserved(manager):
    profile = manager.create("Personal")
    path = state_path(profile.data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"pid": 999999, "port": 1}', encoding="utf-8")
    assert is_running(profile.data_dir)
    assert path.exists()


def test_get_status_states(manager):
    profile = manager.create("StatusTest")
    assert get_status(profile.data_dir) == "stopped"

    path = state_path(profile.data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    starting = {
        "protocol_version": 1,
        "profile_id": profile.id,
        "controller_pid": 0,
        "controller_started_at": datetime.now(timezone.utc).isoformat(),
        "launcher_pid": os.getpid(),
        "port": 0,
        "token": "x" * 32,
        "tabs": 1,
        "status": "starting",
    }
    path.write_text(json.dumps(starting), encoding="utf-8")
    assert get_status(profile.data_dir, clean_stale=False) == "starting"

    starting["controller_pid"] = 999999
    starting["port"] = 1
    path.write_text(json.dumps(starting), encoding="utf-8")
    assert get_status(profile.data_dir, clean_stale=False) == "stale"
    assert get_status(profile.data_dir) == "stopped"
    assert not path.exists()

    err = path.parent / "controller.error"
    err.write_text('{"error_type": "test_err", "message": "fail"}', encoding="utf-8")
    path.unlink(missing_ok=True)
    assert get_status(profile.data_dir) == "error"


def test_create_with_engine_and_set_engine(manager):
    profile = manager.create("DirectProfile", engine="direct")
    assert profile.engine == "direct"
    assert manager.get(profile.id).engine == "direct"

    updated = manager.set_engine(profile.id, "playwright")
    assert updated.engine == "playwright"
    assert manager.get(profile.id).engine == "playwright"

    cleared = manager.set_engine(profile.id, None)
    assert cleared.engine is None
    assert manager.get(profile.id).engine is None
