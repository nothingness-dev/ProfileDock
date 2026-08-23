import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from .data_root import _is_link
from .models import LaunchConfig, Profile


class ValidationError(Exception):
    pass


_ALLOWED_URL_SCHEMES = frozenset({"http", "https", "about"})
_ALLOWED_PLAYWRIGHT_CHANNELS = frozenset(
    {"chromium", "chrome", "msedge", "chrome-beta", "msedge-beta", "msedge-dev"}
)
_ALLOWED_DIRECT_BROWSERS = frozenset(
    {
        "chrome",
        "chromium",
        "brave",
        "google-chrome",
        "google-chrome-stable",
        "chromium-browser",
        "brave-browser",
    }
)


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


def validate_launch_config(
    config: LaunchConfig,
    profile_engine: Optional[str] = None,
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
