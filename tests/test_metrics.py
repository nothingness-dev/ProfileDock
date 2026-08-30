"""Tests for the resource metrics subsystem (fully mocked OS telemetry)."""

from __future__ import annotations

import pytest

from profiledock import metrics as domain_metrics
from profiledock.metrics import (
    ProfileMetrics,
    get_profile_metrics,
    measure_live_usage,
    storage_usage,
)
from profiledock.process.metrics import (
    PlatformSampler,
    ProcessIdentity,
    ProcessSample,
    classify_role,
    cpu_percent_between,
    make_sample,
)


class FakeSampler(PlatformSampler):
    """Deterministic sampler returning scripted detail snapshots in order."""

    def __init__(
        self,
        identities: dict[int, ProcessIdentity],
        detail_snapshots: list[dict[int, ProcessSample]],
    ) -> None:
        self.identities = identities
        self.detail_snapshots = detail_snapshots
        self.calls: list[list[int]] = []
        self._index = 0
        self.raise_oserror = False

    def enumerate_processes(self) -> dict[int, ProcessIdentity]:
        if self.raise_oserror:
            raise OSError("permission denied")
        return dict(self.identities)

    def sample_details(self, pids: list[int]) -> dict[int, ProcessSample]:
        self.calls.append(list(pids))
        if self.raise_oserror:
            raise OSError("permission denied")
        if self._index >= len(self.detail_snapshots):
            snapshot = self.detail_snapshots[-1]
        else:
            snapshot = self.detail_snapshots[self._index]
            self._index += 1
        return {pid: snapshot[pid] for pid in pids if pid in snapshot}


def _identity(pid: int, ppid: int, name: str = "chrome", create_time: float = 1000.0) -> ProcessIdentity:
    return ProcessIdentity(pid=pid, ppid=ppid, name=name, create_time=create_time)


def _sample(
    pid: int,
    ppid: int,
    cpu_time: float,
    rss: int,
    vms: int = 0,
    name: str = "chrome",
    create_time: float = 1000.0,
    cmdline: tuple[str, ...] = (),
) -> ProcessSample:
    return make_sample(
        pid=pid,
        ppid=ppid,
        name=name,
        cpu_time=cpu_time,
        rss_bytes=rss,
        vms_bytes=vms,
        create_time=create_time,
        cmdline=cmdline,
    )


def _clock(pairs: list[float]):
    iterator = iter(pairs)

    def _next() -> float:
        return next(iterator)

    return _next


def _no_sleep(_seconds: float) -> None:
    return None


# ---------------------------------------------------------------------------
# Tree aggregation


def test_single_process_tree_aggregation():
    identities = {10: _identity(10, 1)}
    first = {10: _sample(10, 1, cpu_time=1.0, rss=100)}
    second = {10: _sample(10, 1, cpu_time=1.5, rss=200)}
    sampler = FakeSampler(identities, [first, second])
    usage = measure_live_usage(
        10,
        1000.0,
        browser_pid=10,
        cpu_sample_interval=0.1,
        sampler=sampler,
        sleep=_no_sleep,
        clock=_clock([0.0, 1.0]),
    )
    assert usage.status == "running"
    assert usage.process_count == 1
    assert usage.total_cpu_percent == pytest.approx(50.0)
    assert usage.total_memory_rss_bytes == 200.0
    assert usage.processes[0].pid == 10
    assert usage.processes[0].name == "browser"
    assert usage.processes[0].cpu_percent == pytest.approx(50.0)


def test_multi_process_tree_aggregation_and_roles():
    identities = {
        10: _identity(10, 1, "chrome"),
        11: _identity(11, 10, "chrome"),
        12: _identity(12, 10, "chrome"),
        99: _identity(99, 1, "unrelated"),
    }
    first = {
        10: _sample(10, 1, 1.0, 100, cmdline=("chrome",)),
        11: _sample(11, 10, 0.2, 300, cmdline=("chrome", "--type=renderer")),
        12: _sample(12, 10, 0.1, 50, cmdline=("chrome", "--type=gpu-process")),
    }
    second = {
        10: _sample(10, 1, 2.0, 120, cmdline=("chrome",)),
        11: _sample(11, 10, 1.2, 340, cmdline=("chrome", "--type=renderer")),
        12: _sample(12, 10, 0.6, 60, cmdline=("chrome", "--type=gpu-process")),
    }
    sampler = FakeSampler(identities, [first, second])
    usage = measure_live_usage(
        10,
        1000.0,
        browser_pid=10,
        cpu_sample_interval=0.1,
        sampler=sampler,
        sleep=_no_sleep,
        clock=_clock([0.0, 1.0]),
    )
    # Unrelated PID 99 must not leak into the tree.
    assert usage.process_count == 3
    assert usage.total_cpu_percent == pytest.approx((1.0 + 1.0 + 0.5) * 100.0)
    assert usage.total_memory_rss_bytes == float(120 + 340 + 60)
    roles = {p.pid: p.name for p in usage.processes}
    assert roles == {10: "browser", 11: "renderer", 12: "gpu"}


def test_controller_role_for_playwright_tree():
    identities = {
        5: _identity(5, 1, "python"),
        6: _identity(6, 5, "chrome"),
        7: _identity(7, 6, "chrome"),
    }
    first = {
        5: _sample(5, 1, 0.1, 40, name="python"),
        6: _sample(6, 5, 0.5, 500, name="chrome"),
        7: _sample(7, 6, 0.1, 100, name="chrome", cmdline=("chrome", "--type=renderer")),
    }
    second = {
        5: _sample(5, 1, 0.2, 40, name="python"),
        6: _sample(6, 5, 0.7, 520, name="chrome"),
        7: _sample(7, 6, 0.2, 110, name="chrome", cmdline=("chrome", "--type=renderer")),
    }
    sampler = FakeSampler(identities, [first, second])
    usage = measure_live_usage(
        5,
        None,
        browser_pid=6,
        controller_pid=5,
        cpu_sample_interval=0.1,
        sampler=sampler,
        sleep=_no_sleep,
        clock=_clock([0.0, 1.0]),
    )
    roles = {p.pid: p.name for p in usage.processes}
    assert roles == {5: "controller", 6: "browser", 7: "renderer"}


def test_child_spawned_mid_interval_gets_zero_cpu():
    identities = {10: _identity(10, 1), 11: _identity(11, 10)}
    first = {10: _sample(10, 1, 1.0, 100)}
    second = {
        10: _sample(10, 1, 1.5, 100),
        11: _sample(11, 10, 9.9, 80),  # not present in the first sample
    }
    sampler = FakeSampler(identities, [first, second])
    usage = measure_live_usage(
        10,
        1000.0,
        browser_pid=10,
        cpu_sample_interval=0.1,
        sampler=sampler,
        sleep=_no_sleep,
        clock=_clock([0.0, 1.0]),
    )
    assert usage.process_count == 2
    assert usage.total_cpu_percent == pytest.approx(50.0)
    spawned = next(p for p in usage.processes if p.pid == 11)
    assert spawned.cpu_percent == 0.0


def test_child_exit_mid_sample_is_skipped_gracefully():
    identities = {10: _identity(10, 1), 11: _identity(11, 10)}
    first = {
        10: _sample(10, 1, 1.0, 100),
        11: _sample(11, 10, 0.5, 80),
    }
    second = {10: _sample(10, 1, 1.5, 100)}  # PID 11 exited during the window
    sampler = FakeSampler(identities, [first, second])
    usage = measure_live_usage(
        10,
        1000.0,
        browser_pid=10,
        cpu_sample_interval=0.1,
        sampler=sampler,
        sleep=_no_sleep,
        clock=_clock([0.0, 1.0]),
    )
    assert usage.status == "running"
    assert usage.process_count == 1
    assert [p.pid for p in usage.processes] == [10]
    assert usage.total_memory_rss_bytes == 100.0


def test_recycled_root_pid_returns_stopped():
    identities = {10: _identity(10, 1, create_time=5000.0)}
    sampler = FakeSampler(identities, [{10: _sample(10, 1, 0.0, 1, create_time=5000.0)}])
    usage = measure_live_usage(
        10,
        1000.0,
        browser_pid=10,
        cpu_sample_interval=0.1,
        sampler=sampler,
        sleep=_no_sleep,
        clock=_clock([0.0, 1.0]),
    )
    assert usage.status == "stopped"
    assert usage.process_count == 0


def test_missing_root_pid_returns_stopped():
    sampler = FakeSampler({}, [])
    usage = measure_live_usage(
        424242,
        None,
        cpu_sample_interval=0.1,
        sampler=sampler,
        sleep=_no_sleep,
        clock=_clock([0.0, 1.0]),
    )
    assert usage.status == "stopped"


def test_os_error_degrades_telemetry():
    sampler = FakeSampler({}, [])
    sampler.raise_oserror = True
    usage = measure_live_usage(
        10,
        None,
        cpu_sample_interval=0.1,
        sampler=sampler,
        sleep=_no_sleep,
        clock=_clock([0.0, 1.0]),
    )
    assert usage.status == "degraded"
    assert usage.processes == []


def test_tree_vanishing_mid_window_returns_stopped():
    identities = {10: _identity(10, 1)}
    first = {10: _sample(10, 1, 1.0, 100)}
    sampler = FakeSampler(identities, [first, {}])
    usage = measure_live_usage(
        10,
        1000.0,
        cpu_sample_interval=0.1,
        sampler=sampler,
        sleep=_no_sleep,
        clock=_clock([0.0, 1.0]),
    )
    assert usage.status == "stopped"


def test_cpu_percent_between_helper():
    prev = {1: _sample(1, 0, 1.0, 0), 2: _sample(2, 1, 0.5, 0)}
    current = [_sample(1, 0, 1.25, 0), _sample(2, 1, 0.25, 0)]  # PID 2 went backwards
    assert cpu_percent_between(prev, current, 1.0) == pytest.approx(25.0)
    assert cpu_percent_between(prev, current, 0.0) == 0.0
    assert cpu_percent_between({}, current, 1.0) == 0.0


def test_classify_role_precedence():
    controller = _sample(5, 1, 0, 0, name="python")
    browser = _sample(6, 5, 0, 0, name="chrome")
    assert classify_role(controller, browser_pid=6, controller_pid=5) == "controller"
    assert classify_role(browser, browser_pid=6, controller_pid=5) == "browser"
    renderer = _sample(7, 6, 0, 0, name="chrome", cmdline=("chrome", "--type=renderer"))
    assert classify_role(renderer) == "renderer"
    gpu = _sample(8, 6, 0, 0, name="chrome", cmdline=("chrome", "--type=gpu-process"))
    assert classify_role(gpu) == "gpu"
    fallback = _sample(9, 6, 0, 0, name="gpu_process")
    assert classify_role(fallback) == "gpu"
    unknown = _sample(10, 6, 0, 0, name="chrome")
    assert classify_role(unknown) == "utility"


# ---------------------------------------------------------------------------
# Disk storage breakdown


def test_storage_usage_buckets_dummy_tree(tmp_path):
    root = tmp_path / "browser-data"
    (root / "Default" / "Cache").mkdir(parents=True)
    (root / "Default" / "Code Cache").mkdir(parents=True)
    (root / "Default" / "IndexedDB").mkdir(parents=True)
    (root / "Default" / "GPUCache").mkdir(parents=True)
    (root / "Default" / "Crashpad").mkdir(parents=True)
    (root / "Default" / "Cache" / "f1").write_bytes(b"x" * 2048)
    (root / "Default" / "Code Cache" / "f2").write_bytes(b"y" * 512)
    (root / "Default" / "IndexedDB" / "db").write_bytes(b"z" * 4096)
    (root / "Default" / "Cookies").write_bytes(b"c" * 256)
    (root / "Default" / "GPUCache" / "data").write_bytes(b"g" * 128)
    (root / "Default" / "Crashpad" / "report").write_bytes(b"r" * 64)
    (root / "debug.log").write_bytes(b"l" * 32)
    (root / "Default" / "Preferences").write_bytes(b"p" * 16)

    usage = storage_usage(root)
    assert usage.total_bytes == 2048 + 512 + 4096 + 256 + 128 + 64 + 32 + 16
    assert usage.cache_bytes == 2048 + 512 + 128
    assert usage.cookies_storage_bytes == 256
    assert usage.logs_bytes == 64 + 32
    assert usage.browser_data_bytes == 4096 + 16


def test_storage_usage_missing_dir_is_zero(tmp_path):
    usage = storage_usage(tmp_path / "does-not-exist")
    assert usage.total_bytes == 0
    assert usage.browser_data_bytes == 0
    assert usage.cache_bytes == 0
    assert usage.cookies_storage_bytes == 0
    assert usage.logs_bytes == 0


def test_storage_skips_symlink_entries(tmp_path):
    root = tmp_path / "browser-data"
    (root / "Default" / "Cache").mkdir(parents=True)
    (root / "Default" / "Cache" / "f1").write_bytes(b"x" * 100)
    target = tmp_path / "outside"
    target.write_bytes(b"evil" * 1000)
    try:
        (root / "Default" / "Cache" / "link").symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    usage = storage_usage(root)
    assert usage.cache_bytes == 100  # symlink target not followed


# ---------------------------------------------------------------------------
# Domain service aggregation


def _fake_profile(profile_id="abc123", name="Work", engine="direct", data_dir="/tmp/pd"):
    from types import SimpleNamespace

    return SimpleNamespace(
        id=profile_id,
        name=name,
        engine=engine,
        created_at="2026-01-01T00:00:00+00:00",
        last_launched_at=None,
        data_dir=data_dir,
        launch_config=None,
    )


def test_get_profile_metrics_stopped_has_live_none(tmp_path):
    profile = _fake_profile(data_dir=str(tmp_path))
    metrics = get_profile_metrics(profile, status="stopped")
    assert isinstance(metrics, ProfileMetrics)
    assert metrics.status == "stopped"
    assert metrics.live is None
    assert metrics.storage.total_bytes == 0
    payload = metrics.to_dict()
    assert payload["live"] is None
    assert payload["storage"]["total_bytes"] == 0


def test_get_profile_metrics_uses_effective_engine_precedence(tmp_path):
    from types import SimpleNamespace

    profile = _fake_profile(engine="direct", data_dir=str(tmp_path))
    profile.launch_config = SimpleNamespace(engine="playwright")
    metrics = get_profile_metrics(profile, status="stopped")
    assert metrics.engine == "playwright"


def test_get_profile_metrics_running_invokes_live_sampler(tmp_path, monkeypatch):
    data_dir = tmp_path / "browser-data"
    data_dir.mkdir()
    profile = _fake_profile(data_dir=str(data_dir))
    identities = {10: _identity(10, 1)}
    first = {10: _sample(10, 1, 1.0, 100)}
    second = {10: _sample(10, 1, 1.5, 200)}
    sampler = FakeSampler(identities, [first, second])
    monkeypatch.setattr(
        domain_metrics,
        "_live_from_state",
        lambda *args, **kwargs: measure_live_usage(
            10,
            1000.0,
            browser_pid=10,
            cpu_sample_interval=0.1,
            sampler=sampler,
            sleep=_no_sleep,
            clock=_clock([0.0, 1.0]),
        ),
    )
    metrics = get_profile_metrics(profile, status="running", cpu_sample_interval=0.1, sampler=sampler)
    assert metrics.status == "running"
    assert metrics.live is not None
    assert metrics.live.status == "running"
    assert metrics.live.process_count == 1
    assert metrics.live.total_cpu_percent == pytest.approx(50.0)
    assert metrics.live.total_memory_rss_bytes == 200.0
    payload = metrics.to_dict()
    assert payload["live"]["processes"][0]["name"] == "browser"


def test_get_profile_metrics_swallows_live_failures(tmp_path, monkeypatch):
    profile = _fake_profile(data_dir=str(tmp_path))

    def boom(*args, **kwargs):
        raise RuntimeError("sampler exploded")

    monkeypatch.setattr(domain_metrics, "_live_from_state", boom)
    metrics = get_profile_metrics(profile, status="running")
    assert metrics.live is None
    assert metrics.storage.total_bytes == 0


def test_live_from_state_direct_uses_state_file(tmp_path, monkeypatch):
    import json

    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    data_dir = tmp_path / "profiles" / "abc" / "browser-data"
    data_dir.mkdir(parents=True)
    (runtime_dir / "running.json").write_text(
        json.dumps(
            {
                "protocol_version": 2,
                "engine": "direct",
                "profile_id": "abc",
                "pid": 10,
                "launcher_pid": 1,
                "process_create_time": 1000.0,
                "tabs": 3,
                "status": "running",
            }
        ),
        encoding="utf-8",
    )
    identities = {10: _identity(10, 1)}
    first = {10: _sample(10, 1, 1.0, 100)}
    second = {10: _sample(10, 1, 1.5, 200)}
    sampler = FakeSampler(identities, [first, second])

    monkeypatch.setattr(
        domain_metrics,
        "measure_live_usage",
        lambda *args, sampler_override=None, **kwargs: measure_live_usage(
            *args,
            sampler=sampler,
            sleep=_no_sleep,
            clock=_clock([0.0, 1.0]),
            **{k: v for k, v in kwargs.items() if k != "sampler"},
        ),
    )
    usage = domain_metrics._live_from_state(str(data_dir), runtime_dir, 0.1, sampler)
    assert usage is not None
    assert usage.tab_count == 3
    assert usage.status == "running"
