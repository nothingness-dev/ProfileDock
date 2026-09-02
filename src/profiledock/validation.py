import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from .browser_detection import DIRECT_BROWSER_ALIASES
from .data_root import _is_link
from .models import LaunchConfig, Profile


class ValidationError(ValueError):
    pass


_ALLOWED_URL_SCHEMES = frozenset({"http", "https", "about"})
_ALLOWED_PROXY_SCHEMES = frozenset({"http", "https", "socks5"})
_ALLOWED_PLAYWRIGHT_CHANNELS = frozenset({"chromium", "chrome"})
_ALLOWED_DIRECT_BROWSERS = DIRECT_BROWSER_ALIASES
_LOCALE_RE = re.compile(r"^[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*$")


def validate_url(url: str) -> None:
    if not isinstance(url, str) or not url.strip():
        raise ValidationError("URL must be a non-empty string")
    clean = url.strip()
    if len(clean) > 8192 or any(ord(character) < 32 for character in clean):
        raise ValidationError("URL contains invalid characters or is too long")
    if clean.lower().startswith("about:"):
        return
    parsed = urlparse(clean)
    if parsed.scheme.lower() not in _ALLOWED_URL_SCHEMES:
        raise ValidationError(f"invalid URL scheme '{parsed.scheme}' (allowed: http, https, about)")
    if parsed.scheme.lower() in ("http", "https") and not parsed.netloc:
        raise ValidationError(f"invalid URL format: '{clean}'")


def validate_proxy(proxy: str | None) -> None:
    """Validate a proxy URL: scheme://[user:pass@]host[:port].

    Deliberately strict: a bare ``host:port`` (no scheme) is rejected because
    the engines disagree on how to interpret it, and ``socks4`` is rejected
    because neither Playwright nor Chromium flags document reliable support.
    Credentials are allowed in the stored value but must be redacted before
    display (see cli_support.redact_proxy).
    """
    if proxy is None:
        return
    if not isinstance(proxy, str) or not proxy.strip():
        raise ValidationError("proxy must be a non-empty string or null")
    clean = proxy.strip()
    if len(clean) > 512 or any(ord(character) < 32 for character in clean):
        raise ValidationError("proxy contains invalid characters or is too long")
    parsed = urlparse(clean)
    scheme = parsed.scheme.lower()
    if not scheme:
        raise ValidationError(f"proxy must include a scheme (http, https, or socks5): '{clean}'")
    if scheme not in _ALLOWED_PROXY_SCHEMES:
        raise ValidationError(f"unsupported proxy scheme '{scheme}' (allowed: http, https, socks5)")
    # urlparse raises ValueError when .hostname/.port are accessed on a
    # malformed netloc; normalize every failure into ValidationError.
    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValidationError(f"proxy has an invalid host or port: '{clean}'") from exc
    if hostname is None or not hostname.strip():
        raise ValidationError(f"proxy is missing a host: '{clean}'")
    if any(character.isspace() for character in hostname):
        raise ValidationError(f"proxy host contains whitespace: '{clean}'")
    if any(character in hostname for character in ("/", "?", "#", "@")):
        raise ValidationError(f"proxy host contains invalid characters: '{clean}'")
    if port is not None and not (1 <= port <= 65535):
        raise ValidationError(f"proxy port out of range: '{clean}'")


def validate_user_agent(user_agent: str | None) -> None:
    if user_agent is None:
        return
    if not isinstance(user_agent, str) or not user_agent.strip():
        raise ValidationError("user agent must be a non-empty string or null")
    if len(user_agent) > 512 or any(ord(character) < 32 for character in user_agent):
        raise ValidationError("user agent contains invalid characters or is too long")


def validate_locale(locale: str | None) -> None:
    if locale is None:
        return
    if not isinstance(locale, str) or not locale.strip():
        raise ValidationError("locale must be a non-empty string or null")
    clean = locale.strip()
    if len(clean) > 35 or not _LOCALE_RE.match(clean):
        raise ValidationError(f"invalid locale '{clean}' (expected forms like 'en' or 'en-GB')")


def validate_time_zone(time_zone: str | None) -> None:
    if time_zone is None:
        return
    if not isinstance(time_zone, str) or not time_zone.strip():
        raise ValidationError("timezone must be a non-empty string or null")
    clean = time_zone.strip()
    if len(clean) > 64 or any(ord(character) < 32 for character in clean):
        raise ValidationError("timezone contains invalid characters or is too long")
    if clean.lower() == "host":
        raise ValidationError("timezone 'host' is not a valid IANA timezone")
    # Accept any IANA-shaped value; deep validation happens in the browser.


def validate_browser(browser: str, engine: str, require_executable: bool = False) -> None:
    clean_browser = browser.strip()
    if not clean_browser:
        raise ValidationError("browser must be a non-empty channel, alias, or executable path")
    normalized = clean_browser.lower()
    candidate = Path(clean_browser).expanduser()
    if candidate.is_absolute():
        if require_executable and not candidate.is_file():
            raise ValidationError(f"browser executable does not exist or is not a file: {clean_browser}")
        return
    if engine == "direct" and normalized not in _ALLOWED_DIRECT_BROWSERS:
        raise ValidationError(f"unsupported direct browser alias '{browser}'")
    if engine == "playwright" and normalized not in _ALLOWED_PLAYWRIGHT_CHANNELS:
        raise ValidationError(f"unsupported Playwright browser channel '{browser}'")


VALID_ENGINES = frozenset({"direct", "playwright"})


def validate_engine(engine: str | None) -> str | None:
    if engine is not None and engine not in VALID_ENGINES:
        raise ValueError(f"invalid engine '{engine}', must be 'direct' or 'playwright'")
    return engine


def validate_launch_config(
    config: LaunchConfig,
    profile_engine: str | None = None,
    require_browser_executable: bool = False,
) -> None:
    effective_engine = config.engine or profile_engine or "direct"

    if config.engine is not None and config.engine not in {"direct", "playwright"}:
        raise ValidationError(f"invalid engine '{config.engine}', must be 'direct' or 'playwright'")

    if config.default_tabs is not None and config.default_tabs < 1:
        raise ValidationError("default_tabs must be at least 1")

    if len(config.start_urls) > 64:
        raise ValidationError("start_urls cannot contain more than 64 URLs")

    for u in config.start_urls:
        validate_url(u)

    if config.browser is not None:
        validate_browser(config.browser, effective_engine, require_browser_executable)

    if config.window_width is not None and config.window_width < 100:
        raise ValidationError("window_width must be at least 100")

    if config.window_height is not None and config.window_height < 100:
        raise ValidationError("window_height must be at least 100")

    if (config.window_width is None) != (config.window_height is None):
        raise ValidationError("both window_width and window_height must be specified together")


def validate_timestamp(timestamp_str: str, field_name: str) -> None:
    try:
        value = datetime.fromisoformat(timestamp_str)
    except (ValueError, TypeError) as exc:
        raise ValidationError(f"{field_name} must be a valid ISO-8601 timestamp: {exc}") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"{field_name} must include a timezone offset")


def validate_required_fields(profile: Profile) -> None:
    if not profile.id or not profile.id.strip():
        raise ValidationError("profile id must not be empty")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", profile.id) is None:
        raise ValidationError("profile id contains unsafe characters")
    if not profile.name or not profile.name.strip():
        raise ValidationError("profile name must not be empty")
    if not profile.created_at or not profile.created_at.strip():
        raise ValidationError("profile created_at must not be empty")
    if not profile.data_dir or not profile.data_dir.strip():
        raise ValidationError("profile data_dir must not be empty")
    validate_timestamp(profile.created_at, "created_at")
    if profile.last_launched_at:
        validate_timestamp(profile.last_launched_at, "last_launched_at")
    if profile.engine is not None and profile.engine not in {"direct", "playwright"}:
        raise ValidationError(f"invalid engine '{profile.engine}', must be 'direct' or 'playwright'")
    if profile.launch_config is not None:
        validate_launch_config(profile.launch_config, profile.engine)


def validate_duplicate_ids(profiles: list[Profile]) -> None:
    seen_ids: set[str] = set()
    for profile in profiles:
        if profile.id in seen_ids:
            raise ValidationError(f"duplicate profile id: {profile.id}")
        seen_ids.add(profile.id)


def validate_duplicate_directories(profiles: list[Profile]) -> None:
    seen_dirs: set[str] = set()
    for profile in profiles:
        normalized = os.path.normcase(str(Path(profile.data_dir).resolve()))
        if normalized in seen_dirs:
            raise ValidationError(f"duplicate data directory: {profile.data_dir}")
        seen_dirs.add(normalized)


def validate_path_safety(data_dir: str, profile_root: Path) -> None:
    data_path = Path(data_dir)
    try:
        resolved = data_path.resolve()
    except (OSError, ValueError) as exc:
        raise ValidationError(f"cannot resolve data directory path: {data_dir}: {exc}") from exc
    try:
        resolved.relative_to(profile_root.resolve())
    except ValueError as exc:
        raise ValidationError(
            f"data directory must be under profile root ({profile_root}): {data_dir}"
        ) from exc
    root_absolute = profile_root.absolute()
    path_absolute = data_path.absolute()
    try:
        relative = path_absolute.relative_to(root_absolute)
    except ValueError as exc:
        raise ValidationError(
            f"data directory must be under profile root ({profile_root}): {data_dir}"
        ) from exc
    current = root_absolute
    for part in relative.parts:
        current = current / part
        if _is_link(current):
            raise ValidationError(f"path contains symlink at {current}: {data_dir}")


def validate_metadata_document(profiles: list[Profile], profile_root: Path) -> None:
    for profile in profiles:
        validate_required_fields(profile)
    validate_duplicate_ids(profiles)
    validate_duplicate_directories(profiles)
    for profile in profiles:
        validate_path_safety(profile.data_dir, profile_root)
        expected = (profile_root / profile.id / "browser-data").resolve()
        if Path(profile.data_dir).resolve() != expected:
            raise ValidationError(
                f"profile data directory must match profiles/<id>/browser-data: {profile.data_dir}"
            )
