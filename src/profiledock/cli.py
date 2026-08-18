import json
from contextvars import ContextVar
from pathlib import Path
from typing import List, Optional

import typer

from .doctor import (
    DiagnosticCheck,
    STATUS_FAILED,
    STATUS_OK,
    STATUS_WARNING,
    repair_environment,
    run_diagnostics,
)
from .migration import (
    ConflictError,
    MigrationError,
    SourceRunningError,
    failure_report,
    migrate_project,
)
from .data_root import DataPaths, DataRootError, resolve_data_root
from .models import Profile
from .process_manager import (
    BrowserLaunchError,
    ProfileRunningError,
    close_controller,
    get_status,
    is_running,
    start_controller,
)
from .profile_manager import AmbiguousProfileError, ProfileManager, ProfileNotFoundError
from .storage import StorageError
from .version import __version__

app = typer.Typer(add_completion=False, help="Manage isolated persistent Chromium profiles.")

EXIT_SUCCESS = 0
EXIT_USER_ERROR = 1
_paths: ContextVar[Optional[DataPaths]] = ContextVar("profiledock_data_paths", default=None)


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"profiledock {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    data_root: Optional[Path] = typer.Option(
        None,
        "--data-root",
        help="Override the ProfileDock application-data directory.",
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
    try:
        _paths.set(resolve_data_root(data_root))
    except DataRootError as exc:
        fail(str(exc))


def manager() -> ProfileManager:
    paths = _paths.get()
    if paths is None:
        paths = resolve_data_root()
        _paths.set(paths)
    return ProfileManager(paths)


def runtime_path(profile: Profile) -> Path:
    return manager().runtime_path(profile.id)


def fail(message: str, code: int = EXIT_USER_ERROR) -> None:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code)


def _safe_profile_dict(profile: Profile, status: Optional[str] = None) -> dict:
    data = {
        "id": profile.id,
        "name": profile.name,
        "created_at": profile.created_at,
        "data_dir": profile.data_dir,
        "last_launched_at": profile.last_launched_at,
    }
    if status is not None:
        data["status"] = status
    return data


def _render_table(rows: List[List[str]]) -> str:
    if not rows:
        return ""
    col_widths = [max(len(row[col]) for row in rows) for col in range(len(rows[0]))]
    lines = []
    for row in rows:
        line = "  ".join(val.ljust(col_widths[col]) for col, val in enumerate(row))
        lines.append(line.rstrip())
    return "\n".join(lines)


@app.command()
def create(name: str) -> None:
    try:
        profile = manager().create(name)
    except (StorageError, ValueError) as exc:
        fail(str(exc))
    typer.echo(f"Created profile '{profile.name}' ({profile.id})")


@app.command("list")
def list_profiles(
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format."),
) -> None:
    try:
        profiles = manager().list_profiles()
    except StorageError as exc:
        fail(str(exc))
    if json_output:
        items = []
        for profile in profiles:
            status = get_status(profile.data_dir, runtime_dir=runtime_path(profile))
            items.append(_safe_profile_dict(profile, status=status))
        typer.echo(json.dumps(items, indent=2))
        return
    if not profiles:
        typer.echo("No profiles found.")
        return
    table = [["ID", "NAME", "STATUS"]]
    for profile in profiles:
        status = get_status(profile.data_dir, runtime_dir=runtime_path(profile))
        table.append([profile.id, profile.name, status])
    typer.echo(_render_table(table))


@app.command()
def show(
    profile_id: str,
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format."),
) -> None:
    try:
        profile = manager().resolve(profile_id)
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError) as exc:
        fail(str(exc))
    status = get_status(profile.data_dir, runtime_dir=runtime_path(profile))
    data = _safe_profile_dict(profile, status=status)
    if json_output:
        typer.echo(json.dumps(data, indent=2))
        return
    rows = [
        ["ID:", profile.id],
        ["Name:", profile.name],
        ["Status:", status],
        ["Created at:", profile.created_at],
        ["Data directory:", profile.data_dir],
        ["Last launched at:", profile.last_launched_at or "Never"],
    ]
    typer.echo(_render_table(rows))


@app.command()
def rename(profile_id: str, new_name: str) -> None:
    clean_name = new_name.strip()
    if not clean_name:
        fail("profile name cannot be empty")
    try:
        profile = manager().rename(profile_id, clean_name)
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError, ValueError) as exc:
        fail(str(exc))
    typer.echo(f"Renamed profile to '{profile.name}' ({profile.id})")


@app.command()
def status(
    profile_id: Optional[str] = typer.Argument(None, help="Profile ID, prefix, or name."),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format."),
) -> None:
    try:
        if profile_id is not None:
            profile = manager().resolve(profile_id)
            profiles = [profile]
            single = True
        else:
            profiles = manager().list_profiles()
            single = False
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError) as exc:
        fail(str(exc))
    if json_output:
        items = []
        for prof in profiles:
            st = get_status(prof.data_dir, runtime_dir=runtime_path(prof))
            items.append({"id": prof.id, "name": prof.name, "status": st})
        typer.echo(json.dumps(items, indent=2))
        return
    if not profiles:
        typer.echo("No profiles found.")
        return
    if single:
        prof = profiles[0]
        st = get_status(prof.data_dir, runtime_dir=runtime_path(prof))
        typer.echo(f"{prof.id}\t{prof.name}\t{st}")
    else:
        table = [["ID", "NAME", "STATUS"]]
        for prof in profiles:
            st = get_status(prof.data_dir, runtime_dir=runtime_path(prof))
            table.append([prof.id, prof.name, st])
        typer.echo(_render_table(table))


@app.command()
def launch(profile_id: str, tabs: int = typer.Option(None, "--tabs", "-t")) -> None:
    try:
        profile_manager = manager()
        profile = profile_manager.resolve(profile_id)
        if tabs is None:
            tabs = typer.prompt("How many tabs do you want to open?", type=int)
        if tabs < 1:
            fail("tab count must be at least 1")
        if not Path(profile.data_dir).exists():
            fail("profile data directory is missing")
        start_controller(profile.data_dir, tabs, runtime_dir=runtime_path(profile))
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError, ProfileRunningError, BrowserLaunchError, ValueError) as exc:
        fail(str(exc))
    try:
        profile_manager.mark_launched(profile.id)
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError, ValueError) as exc:
        typer.echo(f"Warning: browser launched but launch timestamp was not saved: {exc}", err=True)
    typer.echo(f"Launched '{profile.name}' with {tabs} tab(s).")


@app.command()
def close(profile_id: str) -> None:
    try:
        profile = manager().resolve(profile_id)
        close_controller(profile.data_dir, runtime_dir=runtime_path(profile))
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError, ProfileRunningError, BrowserLaunchError) as exc:
        fail(str(exc))
    typer.echo(f"Closed '{profile.name}'.")


@app.command()
def delete(profile_id: str, yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation.")) -> None:
    try:
        profile = manager().resolve(profile_id)
        if is_running(profile.data_dir, runtime_path(profile)):
            fail("profile is running; close it first")
        if not yes and not typer.confirm(f"Delete profile '{profile.name}' and all browser data?"):
            raise typer.Abort()
        manager().delete(profile.id)
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError, OSError, ValueError) as exc:
        fail(str(exc))
    typer.echo(f"Deleted '{profile.name}'.")


@app.command()
def doctor(
    repair: bool = typer.Option(False, "--repair", help="Perform safe repairs where possible."),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format."),
) -> None:
    paths = _paths.get() or resolve_data_root()
    root = paths.root
    repairs: List[DiagnosticCheck] = []
    if repair:
        repairs = repair_environment(root)
    checks = run_diagnostics(root)
    has_failed = any(c.status == STATUS_FAILED for c in checks)
    if json_output:
        payload = {
            "checks": [c.to_dict() for c in checks],
            "repairs": [r.to_dict() for r in repairs],
            "healthy": not has_failed,
        }
        typer.echo(json.dumps(payload, indent=2))
        if has_failed:
            raise typer.Exit(EXIT_USER_ERROR)
        return
    if repairs:
        typer.echo("Repairs performed:")
        for r in repairs:
            typer.echo(f"  [repaired] {r.summary}")
        typer.echo("")
    table = [["CHECK", "STATUS", "SUMMARY"]]
    for c in checks:
        table.append([c.id, c.status.upper(), c.summary])
    typer.echo(_render_table(table))
    has_actions = [c for c in checks if c.action]
    if has_actions:
        typer.echo("\nSuggested Actions:")
        for c in has_actions:
            typer.echo(f"  - {c.id}: {c.action}")
    if has_failed:
        raise typer.Exit(EXIT_USER_ERROR)


@app.command()
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
    paths = _paths.get() or resolve_data_root()
    if remove_source and not yes:
        if json_output:
            report = failure_report(
                from_project,
                paths.root,
                "--remove-source requires --yes when using --json",
            )
            typer.echo(json.dumps(report.to_dict(), indent=2))
            raise typer.Exit(EXIT_USER_ERROR)
        if not typer.confirm(
            f"Are you sure you want to delete source profile data in '{from_project}' after migration?"
        ):
            raise typer.Abort()

    try:
        report = migrate_project(
            source_root=from_project,
            destination_paths=paths,
            remove_source=remove_source,
        )
    except (MigrationError, ConflictError, SourceRunningError, StorageError, ValueError) as exc:
        if json_output:
            report = failure_report(from_project, paths.root, str(exc))
            typer.echo(json.dumps(report.to_dict(), indent=2))
            raise typer.Exit(EXIT_USER_ERROR)
        fail(str(exc))

    if json_output:
        typer.echo(json.dumps(report.to_dict(), indent=2))
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


if __name__ == "__main__":
    app()
