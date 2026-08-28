"""Shared CLI runtime context, helpers, formatting, and error handling."""

import json
import os
from contextvars import ContextVar
from pathlib import Path
from typing import Any, NoReturn, Optional

import typer

from .cli_contract import CLI_JSON_OUTPUT_VERSION, EXIT_USER_ERROR, error_category
from .data_root import DataPaths, DataRootError, resolve_data_root
from .models import Profile
from .profile_manager import AmbiguousProfileError, ProfileManager, ProfileNotFoundError
from .storage import StorageError

_paths: ContextVar[Optional[DataPaths]] = ContextVar("profiledock_data_paths", default=None)
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
    category: Optional[str] = None,
    hint: Optional[str] = None,
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
    elif isinstance(error, (StorageError, OSError)):
        category = "storage_error"
    else:
        from .restore import DecompressionSecurityError

        if isinstance(error, DecompressionSecurityError):
            category = "security_violation"
        else:
            category = error_category(str(error))
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


def resolve_engine(cli_engine: Optional[str], profile: Profile) -> str:
    if cli_engine:
        clean = cli_engine.strip().lower()
        if clean not in ("direct", "playwright"):
            fail("engine must be 'direct' or 'playwright'")
        return clean
    # getattr keeps duck-typed profile stand-ins (tests) working without the attribute.
    launch_config = getattr(profile, "launch_config", None)
    if launch_config and launch_config.engine:
        return str(launch_config.engine)
    profile_engine = getattr(profile, "engine", None)
    if profile_engine:
        if profile_engine not in ("direct", "playwright"):
            fail("stored profile engine must be 'direct' or 'playwright'")
        return str(profile_engine)
    env_value = os.environ.get("PROFILEDOCK_DEFAULT_ENGINE", "").strip()
    if env_value:
        env_engine = env_value.lower()
        if env_engine not in ("direct", "playwright"):
            fail("PROFILEDOCK_DEFAULT_ENGINE must be 'direct' or 'playwright'")
        return env_engine
    return "direct"


def safe_profile_dict(profile: Profile, status: Optional[str] = None) -> dict[str, Any]:
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
        data["launch_config"] = launch_config.to_dict()
    if status is not None:
        data["status"] = status
    return data


def compute_profile_size(data_dir_str: str) -> Optional[int]:
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


def format_size_bytes(num_bytes: Optional[int]) -> str:
    if num_bytes is None:
        return "Unknown"
    if num_bytes < 1024:
        return f"{num_bytes} B"
    elif num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    elif num_bytes < 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.1f} MB"
    return f"{num_bytes / (1024 * 1024 * 1024):.2f} GB"


def render_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    col_widths = [max(len(row[col]) for row in rows) for col in range(len(rows[0]))]
    lines = []
    for row in rows:
        line = "  ".join(val.ljust(col_widths[col]) for col, val in enumerate(row))
        lines.append(line.rstrip())
    return "\n".join(lines)
