"""Shared CLI runtime context, helpers, formatting, and error handling."""

import json
import os
from contextvars import ContextVar
from pathlib import Path
from typing import Any, NoReturn

import typer

from .cli_contract import CLI_JSON_OUTPUT_VERSION, EXIT_USER_ERROR, error_category
from .data_root import DataPaths, DataRootError, resolve_data_root
from .models import Profile
from .profile_manager import AmbiguousProfileError, ProfileManager, ProfileNotFoundError
from .storage import StorageError
from .validation import ValidationError

_paths: ContextVar[DataPaths | None] = ContextVar("profiledock_data_paths", default=None)
_paths_prepared: ContextVar[bool] = ContextVar("profiledock_data_paths_prepared", default=False)
_verbose: ContextVar[bool] = ContextVar("profiledock_verbose", default=False)
_log_level: ContextVar[str] = ContextVar("profiledock_log_level", default="INFO")
_non_interactive: ContextVar[bool] = ContextVar("profiledock_non_interactive", default=False)

_HINTS = {
    "not_found": "run 'profiledock list' to see existing profiles",
    "ambiguous_profile": "use the full profile ID from 'profiledock list'",
    "profile_active": "close the profile first with 'profiledock close <name>'",
    "confirmation_required": "rerun with --yes, or drop --non-interactive",
    "browser_launch_failed": "check browser installation with 'profiledock doctor'",
}


def selected_paths() -> DataPaths:
    paths = _paths.get()
    if paths is None:
        paths = resolve_data_root(prepare=False)
        _paths.set(paths)
    if not _paths_prepared.get():
        try:
            paths.prepare()
        except (DataRootError, OSError) as exc:
            fail(f"cannot prepare data root: {exc}")
        _paths_prepared.set(True)
    return paths


def manager() -> ProfileManager:
    paths = selected_paths()
    return ProfileManager(paths)


def runtime_path(profile: Profile) -> Path:
    return manager().runtime_path(profile.id)


def fail(
    message: str,
    code: int = EXIT_USER_ERROR,
    category: str | None = None,
    hint: str | None = None,
) -> NoReturn:
    selected_category = category or error_category(message)
    typer.echo(f"Error [{selected_category}]: {message}", err=True)
    if hint:
        typer.echo(f"Next steps: {hint}", err=True)
    raise typer.Exit(code)


def fail_exception(error: Exception, code: int = EXIT_USER_ERROR) -> None:
    from .process_manager import BrowserLaunchError, ProfileRunningError

    if isinstance(error, AmbiguousProfileError):
        category = "ambiguous_profile"
    elif isinstance(error, ProfileNotFoundError):
        category = "not_found"
    elif isinstance(error, ProfileRunningError):
        category = "profile_active"
    elif isinstance(error, BrowserLaunchError):
        category = "browser_launch_failed"
    elif isinstance(error, DataRootError):
        # DataRootError covers both mundane environment failures (missing
        # LOCALAPPDATA, invalid root) and genuine path-safety refusals; let the
        # message keywords classify it instead of blanket security_violation.
        category = error_category(str(error))
    elif isinstance(error, ValidationError):
        category = error_category(str(error))
    elif isinstance(error, (StorageError, OSError)):
        category = "storage_error"
    else:
        from .restore import DecompressionSecurityError

        if isinstance(error, DecompressionSecurityError):
            category = "security_violation"
        else:
            category = error_category(str(error))
    # Structural fallback: domain exceptions may carry their own category
    # (BackupError, RestoreError, MigrationError families). When the keyword
    # classifier produced the generic invalid_input but the exception knows
    # better, prefer the exception's attribute.
    error_category_attr = getattr(error, "category", None)
    if category == "invalid_input" and isinstance(error_category_attr, str) and error_category_attr:
        category = error_category_attr
    fail(str(error), code=code, category=category, hint=_HINTS.get(category))


def emit_json(command: str, data: object, err: bool = False) -> None:
    typer.echo(
        json.dumps({"output_version": CLI_JSON_OUTPUT_VERSION, "command": command, "data": data}, indent=2),
        err=err,
    )


def confirm(message: str) -> bool:
    if _non_interactive.get():
        fail("confirmation required; rerun with --yes", category="confirmation_required")
    return typer.confirm(message)


def resolve_engine_strict(cli_engine: str | None, profile: Profile) -> str:
    """Resolve the effective engine, raising ValueError instead of exiting."""
    if cli_engine:
        clean = cli_engine.strip().lower()
        if clean not in ("direct", "playwright"):
            raise ValueError("engine must be 'direct' or 'playwright'")
        return clean
    # getattr keeps duck-typed profile stand-ins (tests) working without the attribute.
    launch_config = getattr(profile, "launch_config", None)
    if launch_config and launch_config.engine:
        return str(launch_config.engine)
    profile_engine = getattr(profile, "engine", None)
    if profile_engine:
        if profile_engine not in ("direct", "playwright"):
            raise ValueError("stored profile engine must be 'direct' or 'playwright'")
        return str(profile_engine)
    env_value = os.environ.get("PROFILEDOCK_DEFAULT_ENGINE", "").strip()
    if env_value:
        env_engine = env_value.lower()
        if env_engine not in ("direct", "playwright"):
            raise ValueError("PROFILEDOCK_DEFAULT_ENGINE must be 'direct' or 'playwright'")
        return env_engine
    return "direct"


def resolve_engine(cli_engine: str | None, profile: Profile) -> str:
    try:
        return resolve_engine_strict(cli_engine, profile)
    except ValueError as exc:
        fail(str(exc))


def redact_proxy(value: str | None) -> str | None:
    """Mask proxy credentials for display: user:secret@ -> user:***@."""
    if not value or "@" not in value:
        return value
    scheme_split = value.split("://", 1)
    if len(scheme_split) != 2:
        return value
    scheme, rest = scheme_split
    host_split = rest.rsplit("@", 1)
    if len(host_split) != 2:
        return value
    userinfo, hostport = host_split
    if ":" in userinfo:
        user, _ = userinfo.split(":", 1)
        redacted = f"{user}:***"
    else:
        redacted = "***"
    return f"{scheme}://{redacted}@{hostport}"


def safe_profile_dict(profile: Profile, status: str | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": profile.id,
        "name": profile.name,
        "created_at": profile.created_at,
        "data_dir": profile.data_dir,
        "last_launched_at": profile.last_launched_at,
        "engine": resolve_engine(None, profile),
    }
    launch_config = getattr(profile, "launch_config", None)
    if launch_config is not None:
        config_dict = launch_config.to_dict()
        config_dict["proxy"] = redact_proxy(config_dict.get("proxy"))
        data["launch_config"] = config_dict
    if status is not None:
        data["status"] = status
    return data


def compute_profile_size(data_dir_str: str) -> int | None:
    data_dir = Path(data_dir_str)
    if not data_dir.is_dir():
        return None
    total = 0
    try:
        stack = [data_dir]
        while stack:
            current = stack.pop()
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
    except OSError:
        return None
    return total


def format_size_bytes(num_bytes: int | None) -> str:
    if num_bytes is None:
        return "Unknown"
    if num_bytes < 1024:
        return f"{num_bytes} B"
    elif num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    elif num_bytes < 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.1f} MB"
    return f"{num_bytes / (1024 * 1024 * 1024):.2f} GB"


def format_cpu_percent(cpu_percent: float | None) -> str:
    if cpu_percent is None:
        return "-"
    return f"{cpu_percent:.1f}%"


def render_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    num_cols = max(len(row) for row in rows)
    col_widths = [
        max((len(row[col]) for row in rows if col < len(row)), default=0)
        for col in range(num_cols)
    ]
    lines = []
    for row in rows:
        line = "  ".join(val.ljust(col_widths[col]) for col, val in enumerate(row))
        lines.append(line.rstrip())
    return "\n".join(lines)
