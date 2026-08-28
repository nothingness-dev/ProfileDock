"""Root CLI application and command registration for ProfileDock."""

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import typer

from .cli_contract import CLI_JSON_OUTPUT_VERSION as CLI_JSON_OUTPUT_VERSION
from .cli_contract import EXIT_SUCCESS as EXIT_SUCCESS
from .cli_contract import EXIT_USAGE_ERROR as EXIT_USAGE_ERROR
from .cli_contract import EXIT_USER_ERROR as EXIT_USER_ERROR
from .cli_contract import error_category as error_category
from .cli_support import (
    _HINTS as _HINTS,
)
from .cli_support import (
    _log_level as _log_level,
)
from .cli_support import (
    _non_interactive as _non_interactive,
)
from .cli_support import (
    _paths as _paths,
)
from .cli_support import (
    _paths_prepared as _paths_prepared,
)
from .cli_support import (
    _verbose as _verbose,
)
from .cli_support import (
    compute_profile_size as _compute_profile_size,
)
from .cli_support import (
    confirm as confirm,
)
from .cli_support import (
    emit_json as emit_json,
)
from .cli_support import (
    fail as fail,
)
from .cli_support import (
    fail_exception as fail_exception,
)
from .cli_support import (
    format_size_bytes as _format_size_bytes,
)
from .cli_support import (
    manager as manager,
)
from .cli_support import (
    render_table as _render_table,
)
from .cli_support import (
    resolve_engine as resolve_engine,
)
from .cli_support import (
    runtime_path as runtime_path,
)
from .cli_support import (
    safe_profile_dict as _safe_profile_dict,
)
from .cli_support import (
    selected_paths as selected_paths,
)
from .commands.automation import (
    close_tab_command,
    eval_script_command,
    export_cookies_command,
    list_tabs_command,
    open_tab_command,
    read_page_command,
)
from .commands.backup import (
    backup_command,
    restore_command,
    show_logs_command,
)
from .commands.browser import (
    close_command,
    launch_command,
    set_engine_command,
    status_command,
)
from .commands.config import (
    config_add_url_command,
    config_remove_url_command,
    config_reset_command,
    config_set_command,
    config_show_command,
)
from .commands.doctor import (
    doctor_command,
)
from .commands.migration import (
    migrate_command,
)
from .commands.profiles import (
    create_command,
    delete_command,
    list_command,
    rename_command,
    show_command,
)
from .data_root import DataPaths as DataPaths
from .data_root import DataRootError, resolve_data_root
from .models import LaunchConfig as LaunchConfig
from .models import Profile as Profile
from .process_manager import (
    BrowserLaunchError as BrowserLaunchError,
)
from .process_manager import (
    ProfileRunningError as ProfileRunningError,
)
from .process_manager import (
    close_controller as close_controller,
)
from .process_manager import (
    get_status as get_status,
)
from .process_manager import (
    is_running as is_running,
)
from .process_manager import (
    send_controller_command as send_controller_command,
)
from .process_manager import (
    start_controller as start_controller,
)
from .process_manager import (
    start_direct_chrome as start_direct_chrome,
)
from .profile_manager import AmbiguousProfileError as AmbiguousProfileError
from .profile_manager import ProfileManager as ProfileManager
from .profile_manager import ProfileNotFoundError as ProfileNotFoundError
from .storage import StorageError as StorageError
from .terminal import is_stdout_tty
from .validation import ValidationError as ValidationError
from .version import __version__

__all__ = [
    "CLI_JSON_OUTPUT_VERSION",
    "EXIT_SUCCESS",
    "EXIT_USAGE_ERROR",
    "EXIT_USER_ERROR",
    "AmbiguousProfileError",
    "BrowserLaunchError",
    "DataPaths",
    "LaunchConfig",
    "Profile",
    "ProfileManager",
    "ProfileNotFoundError",
    "ProfileRunningError",
    "StorageError",
    "ValidationError",
    "__version__",
    "_compute_profile_size",
    "_format_size_bytes",
    "_render_table",
    "_safe_profile_dict",
    "app",
    "backup",
    "backup_command",
    "close",
    "close_command",
    "close_controller",
    "close_tab",
    "close_tab_command",
    "config_add_url",
    "config_add_url_command",
    "config_app",
    "config_remove_url",
    "config_remove_url_command",
    "config_reset",
    "config_reset_command",
    "config_set",
    "config_set_command",
    "config_show",
    "config_show_command",
    "confirm",
    "cookies",
    "create",
    "create_command",
    "delete",
    "delete_command",
    "doctor",
    "doctor_command",
    "emit_json",
    "error_category",
    "eval_script",
    "eval_script_command",
    "export_cookies",
    "export_cookies_command",
    "fail",
    "fail_exception",
    "get_status",
    "is_running",
    "launch",
    "launch_command",
    "list_command",
    "list_profiles",
    "list_tabs",
    "list_tabs_command",
    "manager",
    "migrate",
    "migrate_command",
    "open_tab",
    "open_tab_command",
    "read_page",
    "read_page_command",
    "rename",
    "rename_command",
    "resolve_engine",
    "restore",
    "restore_command",
    "runtime_path",
    "selected_paths",
    "send_controller_command",
    "set_engine",
    "set_engine_command",
    "show",
    "show_command",
    "show_logs",
    "show_logs_command",
    "start_controller",
    "start_direct_chrome",
    "status",
    "status_command",
    "tabs",
]

if TYPE_CHECKING:
    from .doctor import (
        STATUS_FAILED as STATUS_FAILED,
    )
    from .doctor import (
        STATUS_OK as STATUS_OK,
    )
    from .doctor import (
        STATUS_WARNING as STATUS_WARNING,
    )
    from .doctor import (
        DiagnosticCheck as DiagnosticCheck,
    )
    from .doctor import (
        repair_environment as repair_environment,
    )
    from .doctor import (
        run_diagnostics as run_diagnostics,
    )

app = typer.Typer(
    invoke_without_command=True,
    add_completion=False,
    help="Manage isolated persistent Chromium profiles.",
)

config_app = typer.Typer(help="Manage launch configuration presets for a profile.")
app.add_typer(config_app, name="config")

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


app.command(name="create")(create_command)
app.command(name="list")(list_command)
app.command(name="show")(show_command)
app.command(name="rename")(rename_command)
app.command(name="set-engine")(set_engine_command)
app.command(name="status")(status_command)
app.command(
    name="launch",
    epilog="""Examples:\n
  profiledock launch Personal --tabs 3\n
  profiledock launch Work -t 2 -u https://example.com\n
  profiledock launch Work --tabs 4 --engine playwright --browser chromium""",
)(launch_command)
app.command(name="tabs")(list_tabs_command)
app.command(name="open-tab")(open_tab_command)
app.command(name="close-tab")(close_tab_command)
app.command(name="read")(read_page_command)
app.command(name="eval")(eval_script_command)
app.command(name="cookies")(export_cookies_command)
app.command(name="close")(close_command)
app.command(
    name="delete",
    epilog="""Examples:\n
  profiledock delete OldProfile\n
  profiledock delete OldProfile --yes\n
  Back up first if the data matters: profiledock backup OldProfile -o old.tar.gz""",
)(delete_command)

config_app.command(name="show")(config_show_command)
config_app.command(name="set")(config_set_command)
config_app.command(name="add-url")(config_add_url_command)
config_app.command(name="remove-url")(config_remove_url_command)
config_app.command(name="reset")(config_reset_command)

app.command(
    name="doctor",
    epilog="""Examples:\n
  profiledock doctor\n
  profiledock doctor --json\n
  profiledock doctor --repair --reattach-orphans --yes""",
)(doctor_command)

app.command(
    name="migrate",
    epilog="""Examples:\n
  profiledock migrate --from-project C:\\path\\to\\legacy\\project\n
  profiledock migrate --from-project ./legacy --json\n
  profiledock migrate --from-project ./legacy --remove-source --yes""",
)(migrate_command)

app.command(
    name="backup",
    epilog="""Examples:\n
  profiledock close Work\n
  profiledock backup Work --output work.tar.gz --exclude-cache\n
  profiledock backup --all --output full-backup.tar.gz""",
)(backup_command)
app.command(
    name="restore",
    epilog="""Examples:\n
  profiledock restore work.tar.gz\n
  profiledock restore work.tar.gz --json\n
  profiledock restore work.tar.gz --force""",
)(restore_command)
app.command(name="logs")(show_logs_command)

create = create_command
list_profiles = list_command
show = show_command
rename = rename_command
delete = delete_command
set_engine = set_engine_command
status = status_command
launch = launch_command
close = close_command
tabs = list_tabs_command
list_tabs = list_tabs_command
open_tab = open_tab_command
close_tab = close_tab_command
read_page = read_page_command
eval_script = eval_script_command
cookies = export_cookies_command
export_cookies = export_cookies_command
config_show = config_show_command
config_set = config_set_command
config_add_url = config_add_url_command
config_remove_url = config_remove_url_command
config_reset = config_reset_command
doctor = doctor_command
migrate = migrate_command
backup = backup_command
restore = restore_command
show_logs = show_logs_command

if __name__ == "__main__":
    app()
