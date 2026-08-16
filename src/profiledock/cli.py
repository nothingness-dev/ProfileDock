import json
from pathlib import Path
from typing import List, Optional

import typer

from .models import Profile
from .process_manager import (
    BrowserLaunchError,
    ProfileRunningError,
    close_controller,
    error_path,
    get_status,
    is_running,
    start_controller,
    state_path,
    _read_error,
    _read_state,
)
from .profile_manager import AmbiguousProfileError, ProfileManager, ProfileNotFoundError
from .storage import StorageError
from .version import __version__

app = typer.Typer(add_completion=False, help="Manage isolated persistent Chromium profiles.")

EXIT_SUCCESS = 0
EXIT_USER_ERROR = 1
EXIT_SYSTEM_ERROR = 2


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"profiledock {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-V",
        help="Show version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    pass


def manager() -> ProfileManager:
    return ProfileManager(Path.cwd())


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
            status = get_status(profile.data_dir)
            items.append(_safe_profile_dict(profile, status=status))
        typer.echo(json.dumps(items, indent=2))
        return
    if not profiles:
        typer.echo("No profiles found.")
        return
    table = [["ID", "NAME", "STATUS"]]
    for profile in profiles:
        status = get_status(profile.data_dir)
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
    status = get_status(profile.data_dir)
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
            st = get_status(prof.data_dir)
            items.append({"id": prof.id, "name": prof.name, "status": st})
        typer.echo(json.dumps(items, indent=2))
        return
    if not profiles:
        typer.echo("No profiles found.")
        return
    if single:
        prof = profiles[0]
        st = get_status(prof.data_dir)
        typer.echo(f"{prof.id}\t{prof.name}\t{st}")
    else:
        table = [["ID", "NAME", "STATUS"]]
        for prof in profiles:
            st = get_status(prof.data_dir)
            table.append([prof.id, prof.name, st])
        typer.echo(_render_table(table))


@app.command()
def launch(profile_id: str, tabs: int = typer.Option(None, "--tabs", "-t")) -> None:
    try:
        profile = manager().resolve(profile_id)
        if tabs is None:
            tabs = typer.prompt("How many tabs do you want to open?", type=int)
        if tabs < 1:
            fail("tab count must be at least 1")
        if not Path(profile.data_dir).exists():
            fail("profile data directory is missing")
        start_controller(profile.data_dir, tabs)
        manager().mark_launched(profile.id)
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError, ProfileRunningError, BrowserLaunchError, ValueError) as exc:
        fail(str(exc))
    typer.echo(f"Launched '{profile.name}' with {tabs} tab(s).")


@app.command()
def close(profile_id: str) -> None:
    try:
        profile = manager().resolve(profile_id)
        close_controller(profile.data_dir)
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError, ProfileRunningError, BrowserLaunchError) as exc:
        fail(str(exc))
    typer.echo(f"Closed '{profile.name}'.")


@app.command()
def delete(profile_id: str, yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation.")) -> None:
    try:
        profile = manager().resolve(profile_id)
        if is_running(profile.data_dir):
            fail("profile is running; close it first")
        if not yes and not typer.confirm(f"Delete profile '{profile.name}' and all browser data?"):
            raise typer.Abort()
        manager().delete(profile.id)
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError, OSError) as exc:
        fail(str(exc))
    typer.echo(f"Deleted '{profile.name}'.")


if __name__ == "__main__":
    app()
