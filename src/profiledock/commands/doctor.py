"""Diagnostics and self-repair commands: doctor."""

import typer

from ..cli_contract import EXIT_USER_ERROR
from ..cli_support import (
    confirm,
    emit_json,
    fail,
    selected_paths,
)
from ..terminal import fail_mark, ok_mark, warn_mark


def _get_prime_doctor_exports() -> None:
    from ..cli import _prime_doctor_exports

    _prime_doctor_exports()


def doctor_command(
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
    _get_prime_doctor_exports()
    from ..cli import (
        STATUS_FAILED,
        STATUS_OK,
        STATUS_WARNING,
        _render_table,
        repair_environment,
        run_diagnostics,
    )

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

    repairs = []
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
