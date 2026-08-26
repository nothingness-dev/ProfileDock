import json
import os
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, Optional

import typer

from .cli_contract import CLI_JSON_OUTPUT_VERSION, EXIT_USER_ERROR, error_category
from .cli_contract import EXIT_SUCCESS as EXIT_SUCCESS
from .cli_contract import EXIT_USAGE_ERROR as EXIT_USAGE_ERROR
from .data_root import DataPaths, DataRootError, resolve_data_root
from .logger import generate_correlation_id, read_profile_logs, write_log_entry
from .models import LaunchConfig, Profile
from .process_manager import (
    BrowserLaunchError,
    ProfileRunningError,
    close_controller,
    get_status,
    is_running,
    start_controller,
    start_direct_chrome,
)
from .profile_manager import AmbiguousProfileError, ProfileManager, ProfileNotFoundError
from .storage import StorageError
from .terminal import fail_mark, is_stdout_tty, ok_mark, warn_mark
from .validation import ValidationError, validate_browser, validate_url
from .version import __version__

if TYPE_CHECKING:
    from .doctor import (
        STATUS_FAILED,
        STATUS_OK,
        STATUS_WARNING,
        DiagnosticCheck,
        repair_environment,
        run_diagnostics,
    )

app = typer.Typer(
    invoke_without_command=True,
    add_completion=False,
    help="Manage isolated persistent Chromium profiles.",
)

config_app = typer.Typer(help="Manage launch configuration presets for a profile.")
app.add_typer(config_app, name="config")

_paths: ContextVar[Optional[DataPaths]] = ContextVar("profiledock_data_paths", default=None)
_paths_prepared: ContextVar[bool] = ContextVar("profiledock_data_paths_prepared", default=False)
_verbose: ContextVar[bool] = ContextVar("profiledock_verbose", default=False)
_log_level: ContextVar[str] = ContextVar("profiledock_log_level", default="INFO")
_non_interactive: ContextVar[bool] = ContextVar("profiledock_non_interactive", default=False)

_DOCTOR_EXPORTS = frozenset(
    {
        "STATUS_FAILED",
        "STATUS_OK",
        "STATUS_WARNING",
        "DiagnosticCheck",
        "repair_environment",
        "run_diagnostics",
    }
)


def __getattr__(name: str) -> Any:
    # Deferred doctor import keeps `list`/`show`/`status` startups light while
    # preserving `patch("profiledock.cli.run_diagnostics")`-style monkeypatching.
    if name in _DOCTOR_EXPORTS:
        from . import doctor

        return getattr(doctor, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _prime_doctor_exports() -> None:
    from . import doctor

    for name in _DOCTOR_EXPORTS:
        if name not in globals():
            globals()[name] = getattr(doctor, name)


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"profiledock {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    context: typer.Context,
    data_root: Optional[Path] = typer.Option(
        None,
        "--data-root",
        help="Override the ProfileDock application-data directory.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose output and trace logging.",
    ),
    log_level: str = typer.Option(
        "INFO",
        "--log-level",
        help="Logging level threshold: DEBUG, INFO, WARNING, ERROR.",
    ),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help="Never prompt; fail when required input or confirmation is missing.",
    ),
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-V",
        help="Show version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    normalized_level = log_level.strip().upper()
    if normalized_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        raise typer.BadParameter("log level must be one of DEBUG, INFO, WARNING, ERROR")
    try:
        _paths.set(resolve_data_root(data_root, prepare=False))
        _paths_prepared.set(False)
        _verbose.set(verbose)
        _log_level.set(normalized_level)
        env_non_interactive = os.environ.get("PROFILEDOCK_NON_INTERACTIVE", "").strip().lower()
        _non_interactive.set(non_interactive or env_non_interactive in {"1", "true", "yes", "on"})
    except DataRootError as exc:
        fail_exception(exc)

    if context.invoked_subcommand is None:
        env_non_interactive = os.environ.get("PROFILEDOCK_NON_INTERACTIVE", "").strip().lower()
        interactive_wanted = (
            is_stdout_tty() and not non_interactive and env_non_interactive not in {"1", "true", "yes", "on"}
        )
        if interactive_wanted:
            from .interactive import TEXTUAL_AVAILABLE, run_interactive

            if TEXTUAL_AVAILABLE:
                run_interactive()
            else:
                typer.echo(
                    'Interactive mode requires the Textual extra: pip install "profiledock[interactive]"'
                )
                typer.echo()
                typer.echo(context.get_help())
            raise typer.Exit(EXIT_SUCCESS)
        typer.echo("Usage: profiledock [OPTIONS] COMMAND [ARGS]...", err=True)
        typer.echo("Try 'profiledock --help' for help.", err=True)
        typer.echo("Error: Missing command.", err=True)
        raise typer.Exit(EXIT_USAGE_ERROR)


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


_HINTS = {
    "not_found": "run 'profiledock list' to see existing profiles",
    "ambiguous_profile": "use the full profile ID from 'profiledock list'",
    "profile_active": "close the profile first with 'profiledock close <name>'",
    "confirmation_required": "rerun with --yes, or drop --non-interactive",
    "browser_launch_failed": "check browser installation with 'profiledock doctor'",
}


def fail_exception(error: Exception, code: int = EXIT_USER_ERROR) -> None:
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


def _safe_profile_dict(profile: Profile, status: Optional[str] = None) -> dict[str, Any]:
    data = {
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


def _compute_profile_size(data_dir_str: str) -> Optional[int]:
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


def _format_size_bytes(num_bytes: Optional[int]) -> str:
    if num_bytes is None:
        return "Unknown"
    if num_bytes < 1024:
        return f"{num_bytes} B"
    elif num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    elif num_bytes < 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.1f} MB"
    return f"{num_bytes / (1024 * 1024 * 1024):.2f} GB"


def _render_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    col_widths = [max(len(row[col]) for row in rows) for col in range(len(rows[0]))]
    lines = []
    for row in rows:
        line = "  ".join(val.ljust(col_widths[col]) for col, val in enumerate(row))
        lines.append(line.rstrip())
    return "\n".join(lines)


@app.command()
def create(
    name: str = typer.Argument(..., help="Display name for the new profile."),
    engine: Optional[str] = typer.Option(
        None,
        "--engine",
        "-e",
        help="Default engine for profile: 'direct' (default) or 'playwright'",
    ),
) -> None:
    """Create a new isolated browser profile.

    Each profile gets its own persistent Chromium user-data directory, so
    cookies, sessions, and login state never leak between profiles.
    """
    if engine is not None:
        clean_engine = engine.strip().lower()
        if clean_engine not in ("direct", "playwright"):
            fail("engine must be 'direct' or 'playwright'")
        engine = clean_engine
    try:
        profile = manager().create(name, engine=engine)
    except (StorageError, ValueError) as exc:
        fail_exception(exc)
    typer.echo(f"Created profile '{profile.name}' ({profile.id})")


@app.command("list")
def list_profiles(
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format."),
) -> None:
    """List all profiles with ID, name, engine, and runtime status."""
    try:
        profiles = manager().list_profiles()
    except StorageError as exc:
        fail_exception(exc)
    if json_output:
        items = []
        for profile in profiles:
            status = get_status(profile.data_dir, runtime_dir=runtime_path(profile))
            items.append(_safe_profile_dict(profile, status=status))
        emit_json("list", items)
        return
    if not profiles:
        typer.echo("No profiles found.")
        return
    table = [["ID", "NAME", "ENGINE", "STATUS"]]
    for profile in profiles:
        status = get_status(profile.data_dir, runtime_dir=runtime_path(profile))
        eng = resolve_engine(None, profile)
        table.append([profile.id, profile.name, eng, status])
    typer.echo(_render_table(table))


@app.command()
def show(
    profile_id: str = typer.Argument(..., help="Profile ID, unique ID prefix, or exact name."),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format."),
) -> None:
    """Show detailed information about one profile.

    Includes identity, effective engine, status, timestamps, data directory,
    disk usage, and the stored launch configuration when present.
    """
    try:
        profile = manager().resolve(profile_id)
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError) as exc:
        fail_exception(exc)
    status = get_status(profile.data_dir, runtime_dir=runtime_path(profile))
    data = _safe_profile_dict(profile, status=status)
    if json_output:
        emit_json("show", data)
        return
    rows = [
        ["ID:", profile.id],
        ["Name:", profile.name],
        ["Engine:", resolve_engine(None, profile)],
        ["Status:", status],
        ["Created at:", profile.created_at],
        ["Data directory:", profile.data_dir],
        ["Disk usage:", _format_size_bytes(_compute_profile_size(profile.data_dir))],
        ["Last launched at:", profile.last_launched_at or "Never"],
    ]
    typer.echo(_render_table(rows))


@app.command()
def rename(
    profile_id: str = typer.Argument(..., help="Profile ID, unique ID prefix, or exact name."),
    new_name: str = typer.Argument(..., help="New display name for the profile."),
) -> None:
    """Rename a profile without touching its data.

    The profile ID, browser-data directory, and running session are unchanged.
    """
    clean_name = new_name.strip()
    if not clean_name:
        fail("profile name cannot be empty")
    try:
        old_name = manager().resolve(profile_id).name
        profile = manager().rename(profile_id, clean_name)
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError, ValueError) as exc:
        fail_exception(exc)
    typer.echo(f"Renamed profile to '{profile.name}' ({profile.id})")
    if old_name != profile.name:
        typer.echo(f"  {old_name} -> {profile.name}")


@config_app.command("show")
def config_show(
    profile_id: str = typer.Argument(..., help="Profile identifier."),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format."),
) -> None:
    """Show a profile's stored launch preset.

    Values left as None are inherited from the profile or defaults at launch time.
    """
    try:
        profile = manager().resolve(profile_id)
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError) as exc:
        fail_exception(exc)
    cfg = getattr(profile, "launch_config", None) or LaunchConfig()
    if json_output:
        emit_json("config show", cfg.to_dict())
        return
    rows = [
        ["Profile:", f"{profile.name} ({profile.id})"],
        ["Default Tabs:", str(cfg.default_tabs) if cfg.default_tabs is not None else "None"],
        ["Engine:", cfg.engine or "None (inherits profile/default)"],
        ["Browser:", cfg.browser or "None (auto-detect)"],
        [
            "Window Size:",
            f"{cfg.window_width}x{cfg.window_height}" if cfg.window_width and cfg.window_height else "None",
        ],
        ["Start URLs:", ", ".join(cfg.start_urls) if cfg.start_urls else "None"],
    ]
    typer.echo(_render_table(rows))


@config_app.command("set")
def config_set(
    profile_id: str = typer.Argument(..., help="Profile identifier."),
    setting: str = typer.Argument(..., help="Setting name (default-tabs, engine, browser, window-size)."),
    value: str = typer.Argument(..., help="Setting value."),
) -> None:
    """Store one launch-preset value for a profile.

    Explicit launch flags override presets for a single launch.
    """
    clean_setting = setting.strip().lower()
    clean_val = value.strip()
    profile_manager = manager()

    try:
        profile = profile_manager.resolve(profile_id)
        old_cfg = getattr(profile, "launch_config", None)

        def _old(field: str) -> str:
            value = getattr(old_cfg, field, None) if old_cfg else None
            return "(unset)" if value is None else str(value)

        if clean_setting == "default-tabs":
            if not clean_val.isdigit() or int(clean_val) < 1:
                fail("default-tabs must be a positive integer >= 1")
            profile_manager.update_launch_config(profile_id, default_tabs=int(clean_val))
            typer.echo(f"Set default-tabs to {clean_val} for profile '{profile.name}' ({profile.id})")
            typer.echo(f"  {_old('default_tabs')} -> {clean_val}")
        elif clean_setting == "engine":
            val_eng = clean_val.lower()
            if val_eng not in ("direct", "playwright"):
                fail("engine must be 'direct' or 'playwright'")
            profile_manager.update_launch_config(profile_id, engine=val_eng)
            typer.echo(f"Set engine to '{val_eng}' for profile '{profile.name}' ({profile.id})")
            typer.echo(f"  {_old('engine')} -> {val_eng}")
        elif clean_setting == "browser":
            effective_engine = resolve_engine(None, profile)
            candidate = Path(clean_val).expanduser()
            stored_browser = str(candidate.resolve()) if candidate.is_file() else clean_val.lower()
            validate_browser(stored_browser, effective_engine, require_executable=True)
            profile_manager.update_launch_config(profile_id, browser=stored_browser)
            typer.echo(f"Set browser to '{stored_browser}' for profile '{profile.name}' ({profile.id})")
            typer.echo(f"  {_old('browser')} -> {stored_browser}")
        elif clean_setting == "window-size":
            parts = clean_val.lower().split("x")
            if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
                fail("window-size must be in format <width>x<height> (e.g. 1280x720)")
            w, h = int(parts[0]), int(parts[1])
            if w < 100 or h < 100:
                fail("width and height must be at least 100")
            profile_manager.update_launch_config(profile_id, window_width=w, window_height=h)
            typer.echo(f"Set window-size to {w}x{h} for profile '{profile.name}' ({profile.id})")
            old_size = (
                f"{old_cfg.window_width}x{old_cfg.window_height}"
                if old_cfg and old_cfg.window_width
                else "(unset)"
            )
            typer.echo(f"  {old_size} -> {w}x{h}")
        else:
            fail(f"unknown setting '{setting}' (valid: default-tabs, engine, browser, window-size)")
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError, ValidationError, ValueError) as exc:
        fail_exception(exc)


@config_app.command("add-url")
def config_add_url(
    profile_id: str = typer.Argument(..., help="Profile identifier."),
    url: str = typer.Argument(..., help="URL to add."),
) -> None:
    """Validate and append a start URL to the launch preset (no duplicates)."""
    try:
        profile = manager().add_start_url(profile_id, url)
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError, ValidationError, ValueError) as exc:
        fail_exception(exc)
    typer.echo(f"Added start URL '{url}' to profile '{profile.name}' ({profile.id})")


@config_app.command("remove-url")
def config_remove_url(
    profile_id: str = typer.Argument(..., help="Profile identifier."),
    url: str = typer.Argument(..., help="URL to remove; must match the stored normalized value."),
) -> None:
    """Remove one start URL from the launch preset."""
    try:
        profile = manager().remove_start_url(profile_id, url)
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError, ValueError) as exc:
        fail_exception(exc)
    typer.echo(f"Removed start URL '{url}' from profile '{profile.name}' ({profile.id})")


@config_app.command("reset")
def config_reset(
    profile_id: str = typer.Argument(..., help="Profile identifier."),
) -> None:
    """Clear the complete launch preset, restoring inherited defaults."""
    try:
        profile = manager().reset_launch_config(profile_id)
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError, ValueError) as exc:
        fail_exception(exc)
    typer.echo(f"Reset launch configuration for profile '{profile.name}' ({profile.id})")


@app.command("set-engine")
def set_engine(
    profile_id: str = typer.Argument(..., help="Profile ID, unique ID prefix, or exact name."),
    engine: str = typer.Argument(..., help="Engine to use: 'direct' or 'playwright'"),
) -> None:
    """Set the profile-level default engine.

    A stored launch-config engine (config set) still overrides this value.
    """
    clean_engine = engine.strip().lower()
    if clean_engine not in ("direct", "playwright"):
        fail("engine must be 'direct' or 'playwright'")
    try:
        old_engine = manager().resolve(profile_id).engine
        profile = manager().set_engine(profile_id, clean_engine)
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError, ValueError) as exc:
        fail_exception(exc)
    typer.echo(f"Set engine to '{clean_engine}' for profile '{profile.name}' ({profile.id})")
    if old_engine != clean_engine:
        typer.echo(f"  {old_engine or '(unset)'} -> {clean_engine}")


@app.command()
def status(
    profile_id: Optional[str] = typer.Argument(None, help="Profile ID, prefix, or name."),
    watch: bool = typer.Option(False, "--watch", "-w", help="Continuously poll and display live status."),
    interval: float = typer.Option(
        1.0, "--interval", "-i", help="Poll interval in seconds when using --watch."
    ),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format."),
) -> None:
    """Report runtime status of one profile or all profiles.

    Status values: stopped, starting, running, closing, stale, error.
    With --watch, refreshes continuously until Ctrl+C.
    """
    if watch and interval <= 0:
        fail("interval must be greater than 0")
    if watch and json_output:
        fail(
            "--watch cannot be combined with --json; poll 'profiledock status --json' from the caller instead"
        )

    def _render_once() -> None:
        if profile_id is not None:
            profile = manager().resolve(profile_id)
            profiles = [profile]
            single = True
        else:
            profiles = manager().list_profiles()
            single = False

        if json_output:
            items = []
            for prof in profiles:
                st = get_status(prof.data_dir, runtime_dir=runtime_path(prof))
                items.append(
                    {
                        "id": prof.id,
                        "name": prof.name,
                        "engine": resolve_engine(None, prof),
                        "status": st,
                    }
                )
            emit_json("status", items)
            return

        if not profiles:
            typer.echo("No profiles found.")
            return

        if single:
            prof = profiles[0]
            st = get_status(prof.data_dir, runtime_dir=runtime_path(prof))
            eng = resolve_engine(None, prof)
            typer.echo(f"{prof.id}\t{prof.name}\t{eng}\t{st}")
        else:
            table = [["ID", "NAME", "ENGINE", "STATUS"]]
            for prof in profiles:
                st = get_status(prof.data_dir, runtime_dir=runtime_path(prof))
                eng = resolve_engine(None, prof)
                table.append([prof.id, prof.name, eng, st])
            typer.echo(_render_table(table))

    try:
        if not watch:
            _render_once()
        else:
            import time

            interactive = is_stdout_tty()
            first_frame = True
            while True:
                if not json_output:
                    if interactive:
                        typer.echo("\033[2J\033[H", nl=False)
                    elif not first_frame:
                        typer.echo()
                first_frame = False
                _render_once()
                time.sleep(interval)
    except KeyboardInterrupt:
        pass
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError) as exc:
        fail_exception(exc)


@app.command(
    epilog="""Examples:\n
  profiledock launch Personal --tabs 3\n
  profiledock launch Work -t 2 -u https://example.com\n
  profiledock launch Work --tabs 4 --engine playwright --browser chromium"""
)
def launch(
    profile_id: str = typer.Argument(..., help="Profile ID, unique ID prefix, or exact name."),
    tabs: Optional[int] = typer.Option(
        None, "--tabs", "-t", help="Number of tabs to open (at least 1). Prompts if no preset exists."
    ),
    engine: Optional[str] = typer.Option(
        None,
        "--engine",
        "-e",
        help="One-launch engine override: 'direct' or 'playwright'",
    ),
    browser: Optional[str] = typer.Option(
        None,
        "--browser",
        "-b",
        help="Browser name (chrome, chromium, brave) or executable path for this launch.",
    ),
    url: Optional[list[str]] = typer.Option(
        None,
        "--url",
        "-u",
        help="Start URL for one tab; repeat for multiple pages. Overrides stored start URLs.",
    ),
) -> None:
    """Launch a profile's browser with its persistent data.

    Login is always manual. Relaunching the same profile reuses its saved
    cookies, sessions, and history. A duplicate launch is refused while the
    profile is already running.
    """
    corr_id = generate_correlation_id()
    paths = selected_paths()
    try:
        profile_manager = manager()
        profile = profile_manager.resolve(profile_id)
        cfg = getattr(profile, "launch_config", None)
        active_engine = resolve_engine(engine, profile)

        target_tabs = tabs
        if target_tabs is None and cfg and cfg.default_tabs is not None:
            target_tabs = cfg.default_tabs
        if target_tabs is None:
            if _non_interactive.get():
                fail("tab count is required in non-interactive mode; use --tabs")
            target_tabs = typer.prompt("How many tabs do you want to open?", type=int)
        if target_tabs < 1:
            fail("tab count must be at least 1")

        target_urls = list(url) if url else list(cfg.start_urls if cfg and cfg.start_urls else [])
        target_urls = [item.strip() for item in target_urls]
        for target_url in target_urls:
            validate_url(target_url)
        if len(target_urls) > target_tabs:
            fail("number of start URLs cannot exceed the requested tab count")

        target_browser = browser if browser is not None else (cfg.browser if cfg else None)
        if target_browser is not None:
            candidate = Path(target_browser).expanduser()
            target_browser = (
                str(candidate.resolve()) if candidate.is_file() else target_browser.strip().lower()
            )
            validate_browser(target_browser, active_engine, require_executable=True)
        width = cfg.window_width if cfg else None
        height = cfg.window_height if cfg else None

        if not Path(profile.data_dir).is_dir():
            fail("profile data directory is missing")

        write_log_entry(
            log_dir=paths.logs_dir,
            level="INFO",
            event="browser_launch_requested",
            profile_id=profile.id,
            correlation_id=corr_id,
            engine=active_engine,
            command="launch",
            browser_path=target_browser,
            details={
                "tabs": target_tabs,
                "url_count": len(target_urls),
                "window_width": width,
                "window_height": height,
            },
        )

        if active_engine == "direct":
            exec_path = Path(target_browser) if target_browser and Path(target_browser).is_file() else None
            direct_options: dict[str, Any] = {"runtime_dir": runtime_path(profile)}
            if exec_path is not None:
                direct_options["executable_path"] = exec_path
            elif target_browser is not None:
                direct_options["browser"] = target_browser
            if target_urls:
                direct_options["start_urls"] = target_urls
            if width is not None and height is not None:
                direct_options["window_width"] = width
                direct_options["window_height"] = height
            state = start_direct_chrome(profile.data_dir, target_tabs, **direct_options)
            write_log_entry(
                log_dir=paths.logs_dir,
                level="INFO",
                event="browser_process_spawned",
                profile_id=profile.id,
                correlation_id=corr_id,
                engine="direct",
                pid=state.get("pid"),
                result="success",
            )
        else:
            controller_options: dict[str, Any] = {"runtime_dir": runtime_path(profile)}
            if target_browser is not None:
                controller_options["browser_channel"] = target_browser
            if target_urls:
                controller_options["start_urls"] = target_urls
            if width is not None and height is not None:
                controller_options["window_width"] = width
                controller_options["window_height"] = height
            state = start_controller(profile.data_dir, target_tabs, **controller_options)
            write_log_entry(
                log_dir=paths.logs_dir,
                level="INFO",
                event="controller_spawned",
                profile_id=profile.id,
                correlation_id=corr_id,
                engine="playwright",
                pid=state.get("controller_pid") or state.get("pid"),
                result="success",
            )
    except (
        ProfileNotFoundError,
        AmbiguousProfileError,
        StorageError,
        ProfileRunningError,
        BrowserLaunchError,
        ValidationError,
        ValueError,
    ) as exc:
        write_log_entry(
            log_dir=paths.logs_dir,
            level="ERROR",
            event="browser_launch_failed",
            correlation_id=corr_id,
            result="failed",
            error_category=getattr(exc, "category", type(exc).__name__),
            details={"error": str(exc)},
        )
        fail_exception(exc)

    try:
        profile_manager.mark_launched(profile.id)
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError, ValueError) as exc:
        typer.echo(f"Warning: browser launched but launch timestamp was not saved: {exc}", err=True)
    typer.echo(f"Launched '{profile.name}' (engine: {active_engine}) with {target_tabs} tab(s).")


@app.command()
def close(
    profile_id: str = typer.Argument(..., help="Profile ID, unique ID prefix, or exact name."),
) -> None:
    """Close a running profile's browser cleanly.

    Requests graceful shutdown and removes runtime state afterwards.
    Safe to run on stale state; refuses ambiguous or unverified processes.
    """
    try:
        profile = manager().resolve(profile_id)
        close_controller(profile.data_dir, runtime_dir=runtime_path(profile))
    except ProfileRunningError as exc:
        if exc.stopped:
            fail(
                str(exc),
                category="profile_active",
                hint="the profile is already stopped; use 'profiledock status' to confirm",
            )
        fail_exception(exc)
    except (
        ProfileNotFoundError,
        AmbiguousProfileError,
        StorageError,
        BrowserLaunchError,
    ) as exc:
        fail_exception(exc)
    typer.echo(f"Closed '{profile.name}'.")


@app.command(
    epilog="""Examples:\n
  profiledock delete OldProfile\n
  profiledock delete OldProfile --yes\n
  Back up first if the data matters: profiledock backup OldProfile -o old.tar.gz"""
)
def delete(
    profile_id: str = typer.Argument(..., help="Profile ID, unique ID prefix, or exact name."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Delete a profile and permanently remove its browser data.

    Running profiles must be closed first. Deletion is permanent unless an
    independent backup archive exists.
    """
    try:
        profile = manager().resolve(profile_id)
        if is_running(profile.data_dir, runtime_path(profile)):
            fail("profile is running; close it first")
        if not yes and not confirm(f"Delete profile '{profile.name}' and all browser data?"):
            raise typer.Abort()
        manager().delete(profile.id)
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError, OSError, ValueError) as exc:
        fail_exception(exc)
    typer.echo(f"Deleted '{profile.name}'.")


@app.command(
    epilog="""Examples:\n
  profiledock doctor\n
  profiledock doctor --json\n
  profiledock doctor --repair --reattach-orphans --yes"""
)
def doctor(
    repair: bool = typer.Option(False, "--repair", help="Perform safe repairs where possible."),
    reattach_orphans: bool = typer.Option(
        False,
        "--reattach-orphans",
        help="Reattach orphan profile directories to metadata (requires --repair).",
    ),
    recreate_missing: bool = typer.Option(
        False,
        "--recreate-missing",
        help="Recreate missing profile browser-data directories (requires --repair).",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Confirm actions without interactive prompt.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format."),
) -> None:
    """Check installation and data health; repair safe issues with --repair.

    Verifies Python compatibility, data-root writability, metadata integrity,
    browser availability, runtime state, orphan directories, and version
    consistency. Run this after crashes or forced termination.
    """
    _prime_doctor_exports()
    paths = selected_paths()
    root = paths.root

    if (reattach_orphans or recreate_missing) and not repair:
        fail("--reattach-orphans and --recreate-missing require --repair flag")

    destructive_actions: list[tuple[str, str]] = []
    if recreate_missing:
        destructive_actions.append(
            ("--recreate-missing", "Recreate missing empty profile browser-data directories?")
        )
    if reattach_orphans:
        destructive_actions.append(
            ("--reattach-orphans", "Reattach discovered orphan profile directories to metadata?")
        )

    for flag, question in destructive_actions:
        if yes:
            continue
        if json_output:
            payload = {
                "checks": [
                    {
                        "id": "confirmation_required",
                        "status": STATUS_FAILED,
                        "summary": f"{flag} requires --yes when using --json",
                    }
                ],
                "repairs": [],
                "healthy": False,
            }
            emit_json("doctor", payload, err=True)
            raise typer.Exit(EXIT_USER_ERROR)
        if not confirm(question):
            raise typer.Abort()

    repairs: list[DiagnosticCheck] = []
    if repair:
        repairs = repair_environment(
            root,
            reattach_orphans=reattach_orphans,
            recreate_missing_directories=recreate_missing,
        )
    checks = run_diagnostics(root)
    has_failed = any(c.status == STATUS_FAILED for c in checks)
    if json_output:
        payload = {
            "checks": [c.to_dict() for c in checks],
            "repairs": [r.to_dict() for r in repairs],
            "healthy": not has_failed,
        }
        emit_json("doctor", payload)
        if has_failed:
            raise typer.Exit(EXIT_USER_ERROR)
        return
    if repairs:
        typer.echo("Repairs performed:")
        for r in repairs:
            typer.echo(f"  [repaired] {r.summary}")
        typer.echo("")
    table = [["", "CHECK", "STATUS", "SUMMARY"]]
    for c in checks:
        if c.status == STATUS_OK:
            mark = ok_mark()
        elif c.status == STATUS_WARNING:
            mark = warn_mark()
        elif c.status == STATUS_FAILED:
            mark = fail_mark()
        else:
            mark = ""
        table.append([mark, c.id, c.status.upper(), c.summary])
    typer.echo(_render_table(table))
    has_actions = [c for c in checks if c.action]
    if has_actions:
        typer.echo("\nSuggested Actions:")
        for c in has_actions:
            typer.echo(f"  - {c.id}: {c.action}")
    if has_failed:
        raise typer.Exit(EXIT_USER_ERROR)


@app.command(
    epilog="""Examples:\n
  profiledock migrate --from-project C:\\path\\to\\legacy\\project\n
  profiledock migrate --from-project ./legacy --json\n
  profiledock migrate --from-project ./legacy --remove-source --yes"""
)
def migrate(
    from_project: Path = typer.Option(
        ...,
        "--from-project",
        help="Path to the source ProfileDock project directory.",
    ),
    remove_source: bool = typer.Option(
        False,
        "--remove-source",
        help="Remove profile data from source after successful migration.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Confirm deletion of source data without prompt.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output migration report in JSON format.",
    ),
) -> None:
    """Move legacy project-local profiles into the application data root.

    Copies and verifies each profile before committing metadata; incomplete
    changes roll back automatically. The source is left untouched unless
    --remove-source and confirmation are both supplied. Close all source
    profiles first.
    """
    from .migration import ConflictError, MigrationError, SourceRunningError, failure_report, migrate_project

    paths = selected_paths()
    if remove_source and not yes:
        if json_output:
            report = failure_report(
                from_project,
                paths.root,
                "--remove-source requires --yes when using --json",
            )
            emit_json("migrate", report.to_dict(), err=True)
            raise typer.Exit(EXIT_USER_ERROR)
        if not confirm(
            f"Are you sure you want to delete source profile data in '{from_project}' after migration?"
        ):
            raise typer.Abort()

    if not json_output:
        typer.echo(f"Validating and copying from '{from_project}'...")
    try:
        report = migrate_project(
            source_root=from_project,
            destination_paths=paths,
            remove_source=remove_source,
        )
    except (MigrationError, ConflictError, SourceRunningError, StorageError, ValueError) as exc:
        if json_output:
            report = failure_report(from_project, paths.root, str(exc))
            emit_json("migrate", report.to_dict(), err=True)
            raise typer.Exit(EXIT_USER_ERROR) from exc
        fail_exception(exc)

    if json_output:
        emit_json("migrate", report.to_dict())
        return

    typer.echo(f"Migration completed from '{from_project}' to '{paths.root}'.")
    if report.migrated:
        typer.echo(f"Migrated ({len(report.migrated)}):")
        for item in report.migrated:
            typer.echo(f"  + {item.name} ({item.id})")
    if report.skipped:
        typer.echo(f"Skipped ({len(report.skipped)}):")
        for item in report.skipped:
            typer.echo(f"  = {item.name} ({item.id}) - {item.message}")
    if report.source_removed:
        typer.echo("Source data successfully removed.")


@app.command(
    epilog="""Examples:\n
  profiledock close Work\n
  profiledock backup Work --output work.tar.gz --exclude-cache\n
  profiledock backup --all --output full-backup.tar.gz"""
)
def backup(
    profile_id: Optional[str] = typer.Argument(
        None, help="Profile ID, prefix, or name to backup. Omit when using --all."
    ),
    all_profiles: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Backup all configured profiles.",
    ),
    output: Path = typer.Option(
        ...,
        "--output",
        "-o",
        help="Output destination path for the backup archive (.tar.gz).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite existing output file.",
    ),
    exclude_cache: bool = typer.Option(
        False,
        "--exclude-cache",
        "-C",
        help="Exclude transient browser cache directories to reduce archive size.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output backup report in JSON format.",
    ),
) -> None:
    """Create a verified .tar.gz backup of one profile or all profiles.

    Every selected profile must be stopped. The archive includes metadata,
    engine and launch configuration, file sizes, and SHA-256 checksums.
    --exclude-cache skips recreatable browser caches to shrink the archive.
    Existing output requires --force.
    """
    from .backup import (
        BackupError,
        FileLockedError,
        ProfileNotStoppedError,
        TargetExistsError,
        create_backup_archive,
    )

    paths = selected_paths()
    profile_manager = manager()

    if not all_profiles and profile_id is None:
        fail("must specify a profile identifier or use --all to backup all profiles")
    if all_profiles and profile_id is not None:
        fail("cannot specify both a profile identifier and --all")

    try:
        if all_profiles:
            profiles = profile_manager.list_profiles()
            if not profiles:
                fail("no profiles found to backup")
        else:
            assert profile_id is not None  # guarded above; narrows Optional for resolve()
            profile = profile_manager.resolve(profile_id)
            profiles = [profile]

        if not json_output:
            typer.echo(f"Archiving {len(profiles)} profile(s)...")
        report = create_backup_archive(
            profiles=profiles,
            data_paths=paths,
            output_file=output,
            force=force,
            exclude_cache=exclude_cache,
        )
    except (
        ProfileNotFoundError,
        AmbiguousProfileError,
        StorageError,
        ProfileNotStoppedError,
        FileLockedError,
        TargetExistsError,
        BackupError,
        ValueError,
    ) as exc:
        fail_exception(exc)

    if json_output:
        emit_json("backup", report.to_dict())
        return

    typer.echo(f"Backup created successfully: {report.output_path}")
    typer.echo(f"Format version: {report.format_version} (ProfileDock {report.profiledock_version})")
    typer.echo(
        f"Total profiles: {report.total_profiles} | Files: {report.total_files}"
        f" | Size: {report.total_bytes} bytes"
    )
    for p in report.profiles:
        eng_label = p.engine or "default (direct)"
        typer.echo(
            f"  + {p.name} ({p.id}) [engine: {eng_label}] - {p.file_count} files ({p.total_bytes} bytes)"
        )


@app.command(
    epilog="""Examples:\n
  profiledock restore work.tar.gz\n
  profiledock restore work.tar.gz --json\n
  profiledock restore work.tar.gz --force"""
)
def restore(
    archive: Path = typer.Argument(..., help="Path to backup archive (.tar.gz) to restore."),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Replace existing profiles with conflicting IDs. Never overwrites active profiles.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output restore report in JSON format.",
    ),
) -> None:
    """Restore profiles from a verified backup archive.

    The complete archive is validated (manifest, paths, sizes, checksums)
    before anything is committed. Conflicting IDs or names are refused unless
    --force is given; running profiles are never overwritten.
    """
    from .restore import (
        DecompressionSecurityError,
        InvalidArchiveError,
        RestoreConflictError,
        RestoreError,
        restore_backup_archive,
    )

    paths = selected_paths()

    try:
        report = restore_backup_archive(
            archive_path=archive,
            data_paths=paths,
            overwrite=force,
        )
    except InvalidArchiveError as exc:
        if getattr(exc, "category", None) == "not_found":
            fail(
                str(exc),
                category="not_found",
                hint="check the archive path and try again",
            )
        fail_exception(exc)
    except (
        DecompressionSecurityError,
        RestoreConflictError,
        RestoreError,
        StorageError,
        ValueError,
    ) as exc:
        fail_exception(exc)

    if json_output:
        emit_json("restore", report.to_dict())
        return

    typer.echo(f"Restore completed from archive: {report.archive_path}")
    typer.echo(f"Format version: {report.format_version} (ProfileDock {report.profiledock_version})")
    typer.echo(
        f"Total restored: {report.total_restored} | Files: {report.total_files}"
        f" | Size: {report.total_bytes} bytes"
    )
    if report.restored:
        typer.echo("Restored profiles:")
        for p in report.restored:
            eng_label = p.engine or "default (direct)"
            typer.echo(
                f"  + {p.name} ({p.id}) [engine: {eng_label}] - {p.file_count} files ({p.total_bytes} bytes)"
            )
    if report.skipped:
        typer.echo("Skipped profiles:")
        for p in report.skipped:
            eng_label = p.engine or "default (direct)"
            typer.echo(f"  = {p.name} ({p.id}) [engine: {eng_label}] - {p.message}")


@app.command("logs")
def show_logs(
    profile_id: Optional[str] = typer.Argument(None, help="Profile ID, prefix, or name to filter logs."),
    last: Optional[int] = typer.Option(None, "--last", "-n", help="Show last N log entries."),
    json_output: bool = typer.Option(False, "--json", help="Output logs in JSON format."),
) -> None:
    """Read structured ProfileDock logs, optionally for one profile.

    Controller tokens and known secrets are redacted before storage.
    """
    if last is not None and last < 1:
        fail("--last must be a positive integer")
    paths = selected_paths()
    prof_id = None
    if profile_id is not None:
        try:
            profile = manager().resolve(profile_id)
            prof_id = profile.id
        except (ProfileNotFoundError, AmbiguousProfileError, StorageError) as exc:
            fail_exception(exc)

    entries = read_profile_logs(paths.logs_dir, profile_id=prof_id, last_n=last)

    if json_output:
        emit_json("logs", entries)
        return

    if not entries:
        typer.echo("No log entries found.")
        return

    for entry in entries:
        ts = entry.get("timestamp", "")
        lvl = entry.get("level", "INFO")
        evt = entry.get("event", "")
        cid = entry.get("correlation_id", "")
        pid = entry.get("profile_id", "-")
        eng = entry.get("engine", "-")
        typer.echo(f"[{ts}] [{lvl}] [{evt}] (cid: {cid}, profile: {pid}, engine: {eng})")
        details = entry.get("details")
        if details and isinstance(details, dict):
            for k, v in details.items():
                typer.echo(f"    {k}: {v}")


if __name__ == "__main__":
    app()
