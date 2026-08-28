"""Profile lifecycle and inspection commands: create, list, show, rename, delete."""

from typing import Optional

import typer

from ..cli_support import (
    compute_profile_size,
    confirm,
    emit_json,
    fail,
    fail_exception,
    format_size_bytes,
    resolve_engine,
    safe_profile_dict,
)
from ..profile_manager import AmbiguousProfileError, ProfileManager, ProfileNotFoundError
from ..storage import StorageError


def _get_manager() -> ProfileManager:
    from ..cli import manager

    return manager()


def create_command(
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
        profile = _get_manager().create(name, engine=engine)
    except (StorageError, ValueError) as exc:
        fail_exception(exc)
    typer.echo(f"Created profile '{profile.name}' ({profile.id})")


def list_command(
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format."),
) -> None:
    """List all profiles with ID, name, engine, and runtime status."""
    from ..cli import _render_table, get_status, runtime_path

    try:
        profiles = _get_manager().list_profiles()
    except StorageError as exc:
        fail_exception(exc)
    if json_output:
        items = []
        for profile in profiles:
            status = get_status(profile.data_dir, runtime_dir=runtime_path(profile))
            items.append(safe_profile_dict(profile, status=status))
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


def show_command(
    profile_id: str = typer.Argument(..., help="Profile ID, unique ID prefix, or exact name."),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format."),
) -> None:
    """Show detailed information about one profile.

    Includes identity, effective engine, status, timestamps, data directory,
    disk usage, and the stored launch configuration when present.
    """
    from ..cli import _render_table, get_status, runtime_path

    try:
        profile = _get_manager().resolve(profile_id)
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError) as exc:
        fail_exception(exc)
    status = get_status(profile.data_dir, runtime_dir=runtime_path(profile))
    data = safe_profile_dict(profile, status=status)
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
        ["Disk usage:", format_size_bytes(compute_profile_size(profile.data_dir))],
        ["Last launched at:", profile.last_launched_at or "Never"],
    ]
    typer.echo(_render_table(rows))


def rename_command(
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
        old_name = _get_manager().resolve(profile_id).name
        profile = _get_manager().rename(profile_id, clean_name)
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError, ValueError) as exc:
        fail_exception(exc)
    typer.echo(f"Renamed profile to '{profile.name}' ({profile.id})")
    if old_name != profile.name:
        typer.echo(f"  {old_name} -> {profile.name}")


def delete_command(
    profile_id: str = typer.Argument(..., help="Profile ID, unique ID prefix, or exact name."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Delete a profile and permanently remove its browser data.

    Running profiles must be closed first. Deletion is permanent unless an
    independent backup archive exists.
    """
    from ..cli import is_running, runtime_path

    try:
        profile = _get_manager().resolve(profile_id)
        if is_running(profile.data_dir, runtime_path(profile)):
            fail("profile is running; close it first")
        if not yes and not confirm(f"Delete profile '{profile.name}' and all browser data?"):
            raise typer.Abort()
        _get_manager().delete(profile.id)
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError, OSError, ValueError) as exc:
        fail_exception(exc)
    typer.echo(f"Deleted '{profile.name}'.")
