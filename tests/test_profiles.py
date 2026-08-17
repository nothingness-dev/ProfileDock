import json
import os
from datetime import datetime, timezone

from profiledock.process_manager import get_status, is_running, state_path


def test_create_list_delete(manager):
    profile = manager.create("Personal")
    assert manager.list_profiles() == [profile]
    assert profile.id in profile.data_dir
    assert __import__("pathlib").Path(profile.data_dir).is_dir()
    manager.delete(profile.id)
    assert manager.list_profiles() == []
    assert not __import__("pathlib").Path(profile.data_dir).parent.exists()


def test_running_state_stale_file_is_cleaned(manager):
    profile = manager.create("Personal")
    path = state_path(profile.data_dir)
    path.write_text('{"pid": 999999, "port": 1}', encoding="utf-8")
    assert not is_running(profile.data_dir)
    assert not path.exists()


def test_get_status_states(manager):
    profile = manager.create("StatusTest")
    assert get_status(profile.data_dir) == "stopped"

    path = state_path(profile.data_dir)
    starting = {
        "protocol_version": 1,
        "profile_id": profile.id,
        "controller_pid": 0,
        "controller_started_at": datetime.now(timezone.utc).isoformat(),
        "launcher_pid": os.getpid(),
        "port": 0,
        "token": "x" * 32,
    }
    path.write_text(json.dumps(starting), encoding="utf-8")
    assert get_status(profile.data_dir, clean_stale=False) == "starting"

    starting["controller_pid"] = 999999
    starting["port"] = 1
    path.write_text(json.dumps(starting), encoding="utf-8")
    assert get_status(profile.data_dir, clean_stale=False) == "stale"

    err = path.parent / "controller.error"
    err.write_text('{"error_type": "test_err", "message": "fail"}', encoding="utf-8")
    path.unlink(missing_ok=True)
    assert get_status(profile.data_dir) == "error"
