import pytest

from profiledock.models import LaunchConfig, Profile
from profiledock.validation import (
    ValidationError,
    validate_browser,
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
    with pytest.raises(ValidationError, match="unsupported direct browser alias"):
        validate_launch_config(cfg_invalid_channel_direct)


def test_validate_browser_aliases_and_paths(tmp_path):
    validate_browser("chrome", "direct")
    validate_browser("chrome", "playwright")
    validate_browser("chromium", "playwright")

    executable = tmp_path / "browser.exe"
    executable.write_text("browser", encoding="utf-8")
    validate_browser(str(executable.resolve()), "direct", require_executable=True)

    with pytest.raises(ValidationError, match="unsupported direct browser alias"):
        validate_browser("msedge", "direct")
    with pytest.raises(ValidationError, match="does not exist"):
        validate_browser(str((tmp_path / "missing.exe").resolve()), "direct", True)


def test_validate_proxy_accepts_valid_forms():
    from profiledock.validation import validate_proxy

    validate_proxy(None)
    validate_proxy("http://127.0.0.1:8080")
    validate_proxy("https://proxy.example.com")
    validate_proxy("socks5://127.0.0.1:9050")
    validate_proxy("socks5://user:pass@127.0.0.1:1080")
    validate_proxy("http://user@10.0.0.1:3128")


def test_validate_proxy_rejects_invalid_forms():
    import pytest

    from profiledock.validation import ValidationError, validate_proxy

    with pytest.raises(ValidationError, match="scheme"):
        validate_proxy("127.0.0.1:8080")  # bare host:port
    with pytest.raises(ValidationError, match="unsupported proxy scheme"):
        validate_proxy("socks4://127.0.0.1:1080")
    with pytest.raises(ValidationError, match="missing a host"):
        validate_proxy("http://")
    with pytest.raises(ValidationError, match="invalid host or port"):
        validate_proxy("http://127.0.0.1:99999")
    with pytest.raises(ValidationError, match="invalid host or port"):
        validate_proxy("http://127.0.0.1:abc")
    with pytest.raises(ValidationError):
        validate_proxy("http://host with space:8080")


def test_validate_identity_fields():
    import pytest

    from profiledock.validation import (
        ValidationError,
        validate_locale,
        validate_time_zone,
        validate_user_agent,
    )

    validate_user_agent(None)
    validate_user_agent("Mozilla/5.0 (X11; Linux x86_64) Custom")
    with pytest.raises(ValidationError):
        validate_user_agent("   ")
    with pytest.raises(ValidationError):
        validate_user_agent("x" * 600)

    validate_locale(None)
    validate_locale("en")
    validate_locale("en-GB")
    validate_locale("zh-Hant-TW")
    with pytest.raises(ValidationError):
        validate_locale("not a locale!")
    with pytest.raises(ValidationError):
        validate_locale("e")

    validate_time_zone(None)
    validate_time_zone("Europe/Berlin")
    validate_time_zone("America/New_York")
    with pytest.raises(ValidationError):
        validate_time_zone("")
    with pytest.raises(ValidationError):
        validate_time_zone("host")
