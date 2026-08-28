import os

import pytest

from profiledock.fsops import write_private_json
from profiledock.models import Profile
from profiledock.storage import load_profiles, save_profiles


def test_json_storage_round_trip(tmp_path):
    path = tmp_path / "profiles.json"
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    data_dir = profiles_dir / "abc123" / "browser-data"
    profiles = [Profile("abc123", "Personal", "2026-01-01T00:00:00+00:00", str(data_dir))]
    save_profiles(profiles, path, profiles_dir)
    assert load_profiles(path) == profiles


def test_corrupt_json_raises(tmp_path):
    from profiledock.storage import StorageError

    path = tmp_path / "profiles.json"
    path.write_text("not json", encoding="utf-8")
    try:
        load_profiles(path)
    except StorageError:
        pass
    else:
        raise AssertionError("expected StorageError")


def test_metadata_lock_reentrancy(tmp_path):
    from profiledock.storage import metadata_lock

    path = tmp_path / "profiles.json"
    with metadata_lock(path):
        assert (tmp_path / "profiles.lock").exists()


def test_private_json_write_is_atomic_and_private(tmp_path):
    path = tmp_path / "cookies.json"
    write_private_json(path, [{"name": "sid", "value": "secret"}])
    assert path.read_text(encoding="utf-8").endswith("\n")
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".cookies.json.*.tmp"))


def test_private_json_write_rejects_directory_target(tmp_path):
    with pytest.raises(OSError, match="unsafe JSON output target"):
        write_private_json(tmp_path, {})
