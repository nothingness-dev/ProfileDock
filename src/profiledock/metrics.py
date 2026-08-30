"""Profile resource metrics: live process telemetry plus static disk footprint.

Domain service layer between the low-level samplers in
:mod:`profiledock.process.metrics` and the presentation surfaces (``status
--metrics``, ``show``, ``top``, and the TUI inspector). Aggregates:

- Live usage: full Chromium process-tree CPU %, RSS, and process counts for
  running profiles (both direct and Playwright engines).
- Storage usage: on-disk breakdown of a profile's browser-data directory into
  browser data, cache, cookie storage, and logs.

All aggregates are typed dataclasses with ``to_dict()`` for the JSON envelope.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .data_root import _is_link
from .process.metrics import (
    PlatformSampler,
    classify_role,
    cpu_percent_between,
    sample_process_tree,
)
from .process.state import _read_state, state_path

# ---------------------------------------------------------------------------
# Typed metric models


@dataclass
class ProcessResourceUsage:
    pid: int
    name: str  # "browser", "renderer", "gpu", "utility", "controller"
    cpu_percent: float
    memory_rss_bytes: int
    memory_vms_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "name": self.name,
            "cpu_percent": self.cpu_percent,
            "memory_rss_bytes": self.memory_rss_bytes,
            "memory_vms_bytes": self.memory_vms_bytes,
        }


@dataclass
class LiveResourceUsage:
    status: str  # "running", "stopped", "degraded"
    total_cpu_percent: float
    total_memory_rss_bytes: float
    process_count: int
    processes: list[ProcessResourceUsage]
    tab_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "total_cpu_percent": self.total_cpu_percent,
            "total_memory_rss_bytes": self.total_memory_rss_bytes,
            "process_count": self.process_count,
            "processes": [p.to_dict() for p in self.processes],
            "tab_count": self.tab_count,
        }


@dataclass
class StorageResourceUsage:
    total_bytes: int
    browser_data_bytes: int
    cache_bytes: int
    cookies_storage_bytes: int
    logs_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_bytes": self.total_bytes,
            "browser_data_bytes": self.browser_data_bytes,
            "cache_bytes": self.cache_bytes,
            "cookies_storage_bytes": self.cookies_storage_bytes,
            "logs_bytes": self.logs_bytes,
        }


@dataclass
class ProfileMetrics:
    profile_id: str
    name: str
    engine: str
    status: str
    live: LiveResourceUsage | None
    storage: StorageResourceUsage

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "name": self.name,
            "engine": self.engine,
            "status": self.status,
            "live": self.live.to_dict() if self.live is not None else None,
            "storage": self.storage.to_dict(),
        }


# ---------------------------------------------------------------------------
# Static disk metrics

_LOG_FILE_NAMES = frozenset({"debug.log", "chrome_debug.log"})
_LOG_DIR_NAMES = frozenset({"crashpad"})
_COOKIES_FILE_NAMES = frozenset({"cookies", "cookies-journal"})


def _categorize(relative_parts: tuple[str, ...], file_name: str) -> str:
    """Bucket one file into cache, logs, cookies, or browser data."""
    lowered_parts = [part.lower() for part in relative_parts]
    lowered_name = file_name.lower()
    if any("cache" in part for part in lowered_parts):
        return "cache"
    if any(part in _LOG_DIR_NAMES for part in lowered_parts) or lowered_name in _LOG_FILE_NAMES:
        return "logs"
    if lowered_name in _COOKIES_FILE_NAMES:
        return "cookies"
    return "browser_data"


def storage_usage(data_dir: str | Path) -> StorageResourceUsage:
    """Compute the on-disk footprint breakdown for one profile's browser data.

    Walking is best-effort: unreadable files and symlinked/junctioned entries
    are skipped so a partially accessible profile still yields totals.

    Results are memoized per (path, tree mtime) for a short window: watch loops
    and TUI refreshes re-request the breakdown every frame, but a browser-data
    tree's total only changes when its contents change. The mtime check is
    cheap; a full walk is skipped while nothing underneath has been touched.
    """
    root = Path(data_dir)
    if not root.is_dir():
        return _zero_storage_usage()
    try:
        root_mtime = root.stat().st_mtime
    except OSError:
        root_mtime = 0.0
    cached = _storage_usage_cache.get(root)
    if cached is not None and cached[0] == root_mtime and (time.monotonic() - cached[1]) < _STORAGE_CACHE_TTL:
        return cached[2]
    totals = {"cache": 0, "logs": 0, "cookies": 0, "browser_data": 0}
    for current, dir_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        dir_names[:] = [name for name in dir_names if not _is_link(current_path / name)]
        relative = current_path.relative_to(root)
        relative_parts = relative.parts if relative != Path(".") else ()
        for file_name in file_names:
            file_path = current_path / file_name
            if _is_link(file_path) or not file_path.is_file():
                continue
            try:
                size = file_path.stat().st_size
            except OSError:
                continue
            totals[_categorize(relative_parts, file_name)] += size
    total = sum(totals.values())
    usage = StorageResourceUsage(
        total_bytes=total,
        browser_data_bytes=totals["browser_data"],
        cache_bytes=totals["cache"],
        cookies_storage_bytes=totals["cookies"],
        logs_bytes=totals["logs"],
    )
    if len(_storage_usage_cache) >= 32:
        _storage_usage_cache.clear()
    _storage_usage_cache[root] = (root_mtime, time.monotonic(), usage)
    return usage


_STORAGE_CACHE_TTL = 2.0
_storage_usage_cache: dict[Path, tuple[float, float, StorageResourceUsage]] = {}


def reset_storage_cache() -> None:
    """Forget memoized disk breakdowns (used by tests and long-lived embedders)."""
    _storage_usage_cache.clear()


def _zero_storage_usage() -> StorageResourceUsage:
    return StorageResourceUsage(
        total_bytes=0,
        browser_data_bytes=0,
        cache_bytes=0,
        cookies_storage_bytes=0,
        logs_bytes=0,
    )


# ---------------------------------------------------------------------------
# Live process metrics


def measure_live_usage(
    root_pid: int,
    expected_create_time: float | None = None,
    browser_pid: int | None = None,
    controller_pid: int | None = None,
    cpu_sample_interval: float = 0.25,
    sampler: PlatformSampler | None = None,
    sleep: Any = time.sleep,
    clock: Any = time.monotonic,
) -> LiveResourceUsage:
    """Sample one browser process tree twice over ``cpu_sample_interval`` seconds.

    Returns ``status="stopped"`` when the tree vanished or the root PID was
    recycled, and ``status="degraded"`` when the OS refused metric queries.
    CPU percentages are computed from cumulative CPU-time deltas over the
    measured wall-clock window; memory comes from the second sample.
    """
    try:
        first = sample_process_tree(root_pid, expected_create_time, sampler)
    except OSError:
        return _degraded_usage()
    if not first:
        return _stopped_usage()
    wall_start = clock()
    sleep(cpu_sample_interval)
    try:
        second = sample_process_tree(root_pid, expected_create_time, sampler)
    except OSError:
        return _degraded_usage()
    wall_seconds = max(clock() - wall_start, 1e-6)
    if not second:
        # Every process in the tree exited during the sampling window.
        return _stopped_usage()
    previous = {sample.pid: sample for sample in first}
    total_cpu = round(cpu_percent_between(previous, second, wall_seconds), 2)
    processes = [
        ProcessResourceUsage(
            pid=sample.pid,
            name=classify_role(sample, browser_pid=browser_pid, controller_pid=controller_pid),
            cpu_percent=round(
                cpu_percent_between(
                    {sample.pid: previous[sample.pid]} if sample.pid in previous else {},
                    [sample],
                    wall_seconds,
                ),
                2,
            ),
            memory_rss_bytes=sample.rss_bytes,
            memory_vms_bytes=sample.vms_bytes,
        )
        for sample in sorted(second, key=lambda item: item.pid)
    ]
    return LiveResourceUsage(
        status="running",
        total_cpu_percent=total_cpu,
        total_memory_rss_bytes=float(sum(sample.rss_bytes for sample in second)),
        process_count=len(second),
        processes=processes,
        tab_count=None,
    )


def _stopped_usage() -> LiveResourceUsage:
    return LiveResourceUsage(
        status="stopped",
        total_cpu_percent=0.0,
        total_memory_rss_bytes=0.0,
        process_count=0,
        processes=[],
        tab_count=None,
    )


def _degraded_usage() -> LiveResourceUsage:
    return LiveResourceUsage(
        status="degraded",
        total_cpu_percent=0.0,
        total_memory_rss_bytes=0.0,
        process_count=0,
        processes=[],
        tab_count=None,
    )


def _iso_to_epoch(value: Any) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def _live_from_state(
    data_dir: str,
    runtime_dir: Path | None,
    cpu_sample_interval: float,
    sampler: PlatformSampler | None,
) -> LiveResourceUsage | None:
    state = _read_state(state_path(data_dir, runtime_dir))
    if not state:
        return None
    if state.get("engine") == "direct":
        raw_pid = state.get("pid")
        pid = int(raw_pid) if isinstance(raw_pid, int) and raw_pid > 0 else 0
        if pid <= 0:
            return None
        raw_create = state.get("process_create_time")
        expected = float(raw_create) if isinstance(raw_create, (int, float)) else None
        usage = measure_live_usage(
            pid,
            expected,
            browser_pid=pid,
            cpu_sample_interval=cpu_sample_interval,
            sampler=sampler,
        )
        tabs = state.get("tabs")
        usage.tab_count = tabs if isinstance(tabs, int) and tabs > 0 else None
        return usage
    raw_controller = state.get("controller_pid")
    controller_pid = int(raw_controller) if isinstance(raw_controller, int) and raw_controller > 0 else 0
    if controller_pid <= 0:
        return None
    raw_browser = state.get("browser_pid")
    browser_pid = int(raw_browser) if isinstance(raw_browser, int) and raw_browser > 0 else None
    usage = measure_live_usage(
        controller_pid,
        _iso_to_epoch(state.get("controller_started_at")),
        browser_pid=browser_pid,
        controller_pid=controller_pid,
        cpu_sample_interval=cpu_sample_interval,
        sampler=sampler,
    )
    page_count = state.get("page_count")
    if isinstance(page_count, int) and page_count > 0:
        usage.tab_count = page_count
    else:
        tabs = state.get("tabs")
        usage.tab_count = tabs if isinstance(tabs, int) and tabs > 0 else None
    return usage


def _effective_engine(profile: Any) -> str:
    from .cli_support import resolve_engine

    return resolve_engine(None, profile)


def get_profile_metrics(
    profile: Any,
    runtime_dir: Path | None = None,
    status: str | None = None,
    cpu_sample_interval: float = 0.25,
    sampler: PlatformSampler | None = None,
) -> ProfileMetrics:
    """Build the full metric snapshot for one profile.

    ``status`` may be supplied by callers that already computed it (avoiding a
    duplicate runtime-state read). When the profile is not verifiably running,
    ``live`` is ``None``; live sampling failures degrade to ``None`` as well so
    disk metrics remain available.
    """
    from .process_manager import get_status

    if status is None:
        try:
            status = get_status(str(profile.data_dir), clean_stale=False, runtime_dir=runtime_dir)
        except Exception:
            status = "stopped"
    live: LiveResourceUsage | None = None
    if status == "running":
        try:
            live = _live_from_state(str(profile.data_dir), runtime_dir, cpu_sample_interval, sampler)
        except Exception:
            live = None
    return ProfileMetrics(
        profile_id=str(profile.id),
        name=str(profile.name),
        engine=_effective_engine(profile),
        status=status,
        live=live,
        storage=storage_usage(profile.data_dir),
    )


def collect_profiles_metrics(
    profiles: list[Any],
    runtime_dir_for: Any,
    cpu_sample_interval: float = 0.25,
    sampler: PlatformSampler | None = None,
    max_workers: int = 8,
) -> list[ProfileMetrics]:
    """Build metric snapshots for many profiles concurrently.

    Each profile's live sampling involves a wall-clock sleep, so serial
    collection costs ``interval * running-profiles`` per frame; fan-out keeps
    it near ``interval`` total. The optional ``sampler`` is shared across
    workers (samplers are stateless per call). Results are returned in input
    order.
    """
    if not profiles:
        return []
    statuses = [""] * len(profiles)
    for index, profile in enumerate(profiles):
        try:
            statuses[index] = _effective_status(profile, runtime_dir_for(profile))
        except Exception:
            statuses[index] = "stopped"
    workers = min(max_workers, len(profiles))
    if workers <= 1:
        return [
            get_profile_metrics(
                profile,
                runtime_dir=runtime_dir_for(profile),
                status=status,
                cpu_sample_interval=cpu_sample_interval,
                sampler=sampler,
            )
            for profile, status in zip(profiles, statuses, strict=True)
        ]
    results: list[ProfileMetrics | None] = [None] * len(profiles)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                get_profile_metrics,
                profile,
                runtime_dir_for(profile),
                status,
                cpu_sample_interval,
                sampler,
            ): index
            for index, (profile, status) in enumerate(zip(profiles, statuses, strict=True))
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception:
                results[index] = None
    return [
        result
        if result is not None
        else ProfileMetrics(
            profile_id=str(profile.id),
            name=str(profile.name),
            engine=_effective_engine(profile),
            status="stopped",
            live=None,
            storage=StorageResourceUsage(0, 0, 0, 0, 0),
        )
        for profile, result in zip(profiles, results, strict=True)
    ]


def _effective_status(profile: Any, runtime_dir: Path | None) -> str:
    from .process_manager import get_status

    return get_status(str(profile.data_dir), clean_stale=False, runtime_dir=runtime_dir)
