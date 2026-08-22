import pytest

from profiledock.models import Profile


def test_profile_to_dict_and_from_dict_with_engine():
    p = Profile("id1", "Name1", "2026-01-01T00:00:00+00:00", "/path", engine="direct")
    d = p.to_dict()
    assert d["engine"] == "direct"

    parsed = Profile.from_dict(d)
    assert parsed.engine == "direct"


def test_profile_from_dict_without_engine_is_rejected():
    d = {
        "id": "id1",
        "name": "Name1",
        "created_at": "2026-01-01T00:00:00+00:00",
        "data_dir": "/path",
    }
    with pytest.raises(ValueError, match="missing a required field"):
        Profile.from_dict(d)


def test_profile_from_dict_invalid_engine_type():
    d = {
        "id": "id1",
        "name": "Name1",
        "created_at": "2026-01-01T00:00:00+00:00",
        "data_dir": "/path",
        "engine": 123,
        "last_launched_at": None,
        "launch_config": None,
    }
    with pytest.raises(ValueError, match="profile field engine must be a string or null"):
        Profile.from_dict(d)


def test_launch_config_to_dict_and_from_dict():
    from profiledock.models import LaunchConfig

    cfg = LaunchConfig(
        default_tabs=3,
        start_urls=["https://example.com"],
        engine="direct",
        browser="chrome",
        window_width=1920,
        window_height=1080,
    )
    data = cfg.to_dict()
    assert data["default_tabs"] == 3
    assert data["start_urls"] == ["https://example.com"]
    assert data["window_width"] == 1920

    parsed = LaunchConfig.from_dict(data)
    assert parsed.default_tabs == 3
    assert parsed.start_urls == ["https://example.com"]
    assert parsed.engine == "direct"
    assert parsed.browser == "chrome"
    assert parsed.window_width == 1920
    assert parsed.window_height == 1080


def test_profile_with_launch_config():
    from profiledock.models import LaunchConfig

    cfg = LaunchConfig(default_tabs=2, start_urls=["https://test.com"])
    p = Profile("id1", "Name1", "2026-01-01T00:00:00+00:00", "/path", launch_config=cfg)
    d = p.to_dict()
    assert "launch_config" in d
    assert d["launch_config"]["default_tabs"] == 2

    parsed = Profile.from_dict(d)
    assert parsed.launch_config is not None
    assert parsed.launch_config.default_tabs == 2
    assert parsed.launch_config.start_urls == ["https://test.com"]
