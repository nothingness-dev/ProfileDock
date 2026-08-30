"""Resource monitoring command: ``profiledock top``.

Presents a live (or one-shot) resource snapshot across profiles: aggregated
process-tree CPU %, resident memory, process counts, active tabs, and disk
footprint. Mirrors ``status`` conventions for --watch/--json and TTY handling.
"""

import json
import time
from dataclasses import dataclass
from typing import Any

import typer

from ..cli_contract import CLI_JSON_OUTPUT_VERSION
from ..cli_support import (
    _non_interactive,
    fail,
    fail_exception,
    format_cpu_percent,
    format_size_bytes,
)
from ..profile_manager import AmbiguousProfileError, ProfileNotFoundError
from ..storage import StorageError
from ..terminal import is_stdout_tty


@dataclass
class _MetricsRow:
    profile_id: str
    name: str
    engine: str
    status: str
    cpu_percent: float | None
    memory_rss_bytes: int | None
    process_count: int | None
    tab_count: int | None
    disk_bytes: int


def _collect_row(profile: Any, runtime_path_fn: Any, cpu_sample_interval: float) -> _MetricsRow:
    from ..metrics import get_profile_metrics

    metrics = get_profile_metrics(
        profile,
        runtime_dir=runtime_path_fn(profile),
        cpu_sample_interval=cpu_sample_interval,
    )
    live = metrics.live
    return _MetricsRow(
        profile_id=metrics.profile_id,
        name=metrics.name,
        engine=metrics.engine,
        status=metrics.status,
        cpu_percent=live.total_cpu_percent if live is not None else None,
        memory_rss_bytes=int(live.total_memory_rss_bytes) if live is not None else None,
        process_count=live.process_count if live is not None else None,
        tab_count=live.tab_count if live is not None else None,
        disk_bytes=metrics.storage.total_bytes,
    )


def _row_to_payload(row: _MetricsRow) -> dict[str, Any]:
    return {
        "profile_id": row.profile_id,
        "name": row.name,
        "engine": row.engine,
        "status": row.status,
        "cpu_percent": row.cpu_percent,
        "memory_rss_bytes": row.memory_rss_bytes,
        "process_count": row.process_count,
        "tab_count": row.tab_count,
        "disk_total_bytes": row.disk_bytes,
    }


def _get_manager() -> Any:
    """Late-bound so tests can patch ``profiledock.cli.manager``."""
    from ..cli import manager

    return manager()


def _resolve_profiles(profile_id: str | None) -> tuple[list[Any], bool]:
    if profile_id is None:
        return _get_manager().list_profiles(), False
    return [_get_manager().resolve(profile_id)], True


def _render_rows_table(rows: list[_MetricsRow]) -> str:
    table: list[list[str]] = [["ID", "NAME", "ENGINE", "STATUS", "CPU%", "RSS", "PROCS", "TABS", "DISK"]]
    for row in rows:
        table.append(
            [
                row.profile_id,
                row.name,
                row.engine,
                row.status,
                format_cpu_percent(row.cpu_percent),
                format_size_bytes(row.memory_rss_bytes) if row.memory_rss_bytes is not None else "-",
                str(row.process_count) if row.process_count is not None else "-",
                str(row.tab_count) if row.tab_count is not None else "-",
                format_size_bytes(row.disk_bytes),
            ]
        )
    from ..cli import _render_table  # late-bound: tests patch profiledock.cli._render_table

    return _render_table(table)


def top_command(
    profile_id: str | None = typer.Argument(
        None, help="Profile ID, unique ID prefix, or exact name. Omit to monitor all profiles."
    ),
    watch: bool | None = typer.Option(
        None,
        "--watch",
        "-w",
        help="Continuously refresh the resource view until Ctrl+C. "
        "Defaults to on for interactive TTYs and off for pipes.",
    ),
    interval: float = typer.Option(1.0, "--interval", "-i", help="Refresh interval in seconds."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON telemetry."),
) -> None:
    """Show live CPU, memory, process, and disk metrics for profiles.

    Like docker stats: running profiles report process-tree CPU % and RSS;
    stopped profiles report their disk footprint. With --json, prints one
    structured snapshot (or a stream of snapshots with --watch).
    """
    from ..cli import runtime_path

    if interval <= 0:
        fail("interval must be greater than 0")
    try:
        profiles, _single = _resolve_profiles(profile_id)
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError) as exc:
        fail_exception(exc)

    effective_watch = (
        watch if watch is not None else (is_stdout_tty() and not json_output and not _non_interactive.get())
    )
    cpu_sample_interval = min(max(interval, 0.1), 0.3)

    def _snapshot() -> list[_MetricsRow]:
        return [_collect_row(profile, runtime_path, cpu_sample_interval) for profile in profiles]

    def _emit_json_frame(rows: list[_MetricsRow]) -> None:
        payload: dict[str, Any] = {
            "interval_seconds": interval,
            "watch": effective_watch,
            "profiles": [_row_to_payload(row) for row in rows],
        }
        typer.echo(
            json.dumps(
                {"output_version": CLI_JSON_OUTPUT_VERSION, "command": "top", "data": payload},
                indent=None if effective_watch else 2,
            )
        )

    try:
        if not effective_watch:
            rows = _snapshot()
            if json_output:
                _emit_json_frame(rows)
            else:
                if not rows:
                    typer.echo("No profiles found.")
                    return
                typer.echo(_render_rows_table(rows))
            return
        interactive = is_stdout_tty() and not json_output
        first_frame = True
        while True:
            frame_started = time.monotonic()
            if not json_output:
                if interactive:
                    typer.echo("\033[2J\033[H", nl=False)
                elif not first_frame:
                    typer.echo()
            first_frame = False
            try:
                rows = _snapshot()
            except StorageError as exc:
                # A transient lock or I/O blip should not kill the monitor.
                typer.echo(f"top refresh skipped: {exc}", err=True)
                time.sleep(interval)
                continue
            if json_output:
                _emit_json_frame(rows)
            else:
                typer.echo(_render_rows_table(rows))
            time.sleep(max(0.0, interval - (time.monotonic() - frame_started)))
    except KeyboardInterrupt:
        pass
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError) as exc:
        fail_exception(exc)
