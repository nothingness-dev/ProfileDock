from profiledock.models import Profile
from profiledock.storage import load_profiles, save_profiles


def test_json_storage_round_trip(tmp_path):
    path = tmp_path / "profiles.json"
    profiles = [Profile("abc123", "Personal", "2026-01-01T00:00:00+00:00", "/tmp/browser")]
    save_profiles(profiles, path)
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

