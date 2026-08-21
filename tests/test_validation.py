import pytest

from profiledock.models import LaunchConfig, Profile
from profiledock.validation import (
    ValidationError,
    validate_launch_config,
    validate_required_fields,
    validate_url,
)


def test_validate_required_fields_engine_valid():
    p1 = Profile("abc123", "Name", "2026-01-01T00:00:00+00:00", "/path", engine=None)
    validate_required_fields(p1)

    p2 = Profile("abc123", "Name", "2026-01-01T00:00:00+00:00", "/path", engine="direct")
    validate_required_fields(p2)

    p3 = Profile("abc123", "Name", "2026-01-01T00:00:00+00:00", "/path", engine="playwright")
    validate_required_fields(p3)


def test_validate_required_fields_engine_invalid():
    p = Profile("abc123", "Name", "2026-01-01T00:00:00+00:00", "/path", engine="custom")
    with pytest.raises(ValidationError, match="invalid engine 'custom'"):
        validate_required_fields(p)


def test_validate_url_rejections():
    validate_url("https://example.com")
    validate_url("http://example.com/test")
    validate_url("about:blank")

    with pytest.raises(ValidationError, match="invalid URL scheme 'javascript'"):
        validate_url("javascript:alert(1)")

    with pytest.raises(ValidationError, match="invalid URL scheme 'file'"):
        validate_url("file:///etc/passwd")

    with pytest.raises(ValidationError, match="URL must be a non-empty string"):
        validate_url("   ")


def test_validate_launch_config():
    cfg = LaunchConfig(
        default_tabs=2,
        start_urls=["https://github.com"],
        engine="direct",
        window_width=1280,
        window_height=800,
    )
    validate_launch_config(cfg)

    cfg_invalid_tabs = LaunchConfig(default_tabs=0)
    with pytest.raises(ValidationError, match="default_tabs must be at least 1"):
        validate_launch_config(cfg_invalid_tabs)

    cfg_partial_window = LaunchConfig(window_width=1280)
    with pytest.raises(ValidationError, match="both window_width and window_height must be specified"):
        validate_launch_config(cfg_partial_window)

    cfg_invalid_channel_direct = LaunchConfig(engine="direct", browser="chrome-beta")
    with pytest.raises(ValidationError, match="is not supported on engine 'direct'"):
        validate_launch_config(cfg_invalid_channel_direct)
