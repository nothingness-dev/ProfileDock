"""Legacy project migration commands: migrate."""

from pathlib import Path

import typer

from ..cli_contract import EXIT_USER_ERROR
from ..cli_support import (
    confirm,
    emit_json,
    fail_exception,
    selected_paths,
)


def migrate_command(
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
    from ..migration import ConflictError, MigrationError, SourceRunningError, failure_report, migrate_project
    from ..storage import StorageError

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
