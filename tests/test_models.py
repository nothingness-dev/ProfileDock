import pytest

from profiledock.models import Profile


def test_profile_to_dict_and_from_dict_with_engine():
    p = Profile("id1", "Name1", "2026-01-01T00:00:00+00:00", "/path", engine="direct")
    d = p.to_dict()
    assert d["engine"] == "direct"

    parsed = Profile.from_dict(d)
    assert parsed.engine == "direct"


def test_profile_from_dict_without_engine():
    d = {
        "id": "id1",
        "name": "Name1",
        "created_at": "2026-01-01T00:00:00+00:00",
        "data_dir": "/path",
    }
    parsed = Profile.from_dict(d)
    assert parsed.engine is None


def test_profile_from_dict_invalid_engine_type():
    d = {
        "id": "id1",
        "name": "Name1",
        "created_at": "2026-01-01T00:00:00+00:00",
        "data_dir": "/path",
        "engine": 123,
    }
    with pytest.raises(ValueError, match="profile field engine must be a string or null"):
        Profile.from_dict(d)
