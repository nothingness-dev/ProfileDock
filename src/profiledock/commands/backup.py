"""Backup, restore, and structured logging commands: backup, restore, logs."""

from pathlib import Path
from typing import Optional

import typer

from ..cli_support import (
    emit_json,
    fail,
    fail_exception,
    selected_paths,
)
from ..profile_manager import AmbiguousProfileError, ProfileManager, ProfileNotFoundError
from ..storage import StorageError


def _get_manager() -> ProfileManager:
    from ..cli import manager

    return manager()


def backup_command(
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
    from ..backup import (
        BackupError,
        FileLockedError,
        ProfileNotStoppedError,
        TargetExistsError,
        create_backup_archive,
    )

    paths = selected_paths()
    profile_manager = _get_manager()

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


def restore_command(
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
    from ..restore import (
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


def show_logs_command(
    profile_id: Optional[str] = typer.Argument(None, help="Profile ID, prefix, or name to filter logs."),
    last: Optional[int] = typer.Option(None, "--last", "-n", help="Show last N log entries."),
    json_output: bool = typer.Option(False, "--json", help="Output logs in JSON format."),
) -> None:
    """Read structured ProfileDock logs, optionally for one profile.

    Controller tokens and known secrets are redacted before storage.
    """
    from ..logger import read_profile_logs

    if last is not None and last < 1:
        fail("--last must be a positive integer")
    paths = selected_paths()
    prof_id = None
    if profile_id is not None:
        try:
            profile = _get_manager().resolve(profile_id)
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
