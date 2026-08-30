"""Low-level cross-platform process resource sampling.

Discover Chromium process trees and sample per-process CPU/memory counters:

- Linux:  ``/proc/[pid]/stat``, ``/proc/[pid]/statm``, ``/proc/[pid]/cmdline``.
- Windows: tool-help snapshot enumeration plus ``GetProcessTimes`` and
  ``GetProcessMemoryInfo`` per sampled PID.
- macOS:  BSD ``ps`` batch queries (cumulative CPU ``TIME``, RSS/VSZ, ``etime``).
- Any platform with the optional ``psutil`` package installed uses it instead.

Design notes:

- Enumeration (cheap: PID, PPID, name) is separated from detail sampling
  (expensive: CPU/memory counters) so a full system scan never opens a handle
  to every process; details are fetched only for tree members.
- Identity validation compares process creation times with a tolerance before
  a PID is trusted, guarding against PID recycling and phantom processes.
- All sampling is best-effort: dead or unreadable processes are skipped, and
  callers decide how to surface partial data (degraded telemetry).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

_CREATE_TIME_TOLERANCE_SECONDS = 2.0
_WINDOWS_FILETIME_EPOCH_DELTA = 116_444_736_000_000_000  # 100ns ticks between 1601 and 1970
_WINDOWS_FILETIME_TICKS_PER_SECOND = 10_000_000
_PS_EXECUTABLE = "/bin/ps"


@dataclass(frozen=True)
class ProcessIdentity:
    """Cheap per-process enumeration record: who the process is, not what it uses."""

    pid: int
    ppid: int
    name: str
    create_time: float | None = None


@dataclass(frozen=True)
class ProcessSample:
    """Full resource sample for one process."""

    identity: ProcessIdentity
    cpu_time: float = 0.0  # cumulative user+system seconds
    rss_bytes: int = 0
    vms_bytes: int = 0
    create_time: float | None = None
    cmdline: tuple[str, ...] = field(default_factory=tuple)

    @property
    def pid(self) -> int:
        return self.identity.pid

    @property
    def ppid(self) -> int:
        return self.identity.ppid

    @property
    def name(self) -> str:
        return self.identity.name


def make_sample(
    pid: int,
    ppid: int = 1,
    name: str = "chrome",
    cpu_time: float = 0.0,
    rss_bytes: int = 0,
    vms_bytes: int = 0,
    create_time: float | None = None,
    cmdline: tuple[str, ...] = (),
) -> ProcessSample:
    """Build a :class:`ProcessSample` directly; primarily for tests."""
    return ProcessSample(
        identity=ProcessIdentity(pid=pid, ppid=ppid, name=name, create_time=create_time),
        cpu_time=cpu_time,
        rss_bytes=rss_bytes,
        vms_bytes=vms_bytes,
        create_time=create_time,
        cmdline=cmdline,
    )


# ---------------------------------------------------------------------------
# Platform samplers


class PlatformSampler:
    """Two-phase sampling interface: cheap enumeration, then batched details."""

    def enumerate_processes(self) -> dict[int, ProcessIdentity]:  # pragma: no cover - abstract
        raise NotImplementedError

    def sample_details(self, pids: Sequence[int]) -> dict[int, ProcessSample]:  # pragma: no cover
        raise NotImplementedError


class PsutilSampler(PlatformSampler):
    """Sampler backed by the optional ``psutil`` package (any platform)."""

    def __init__(self) -> None:
        import psutil  # optional dependency; imported lazily

        self._psutil = psutil

    def enumerate_processes(self) -> dict[int, ProcessIdentity]:
        identities: dict[int, ProcessIdentity] = {}
        for proc in self._psutil.process_iter(attrs=["pid", "ppid", "name"]):
            try:
                info = proc.info
                identities[int(info["pid"])] = ProcessIdentity(
                    pid=int(info["pid"]),
                    ppid=int(info["ppid"] or 0),
                    name=str(info["name"] or "").lower(),
                )
            except Exception:
                continue
        return identities

    def sample_details(self, pids: Sequence[int]) -> dict[int, ProcessSample]:
        samples: dict[int, ProcessSample] = {}
        for pid in pids:
            try:
                proc = self._psutil.Process(pid)
                cpu = proc.cpu_times()
                mem = proc.memory_info()
                samples[pid] = ProcessSample(
                    identity=ProcessIdentity(
                        pid=pid,
                        ppid=proc.ppid(),
                        name=proc.name().lower(),
                        create_time=proc.create_time(),
                    ),
                    cpu_time=float(cpu.user + cpu.system),
                    rss_bytes=int(mem.rss),
                    vms_bytes=int(mem.vms),
                    cmdline=tuple(proc.cmdline()),
                )
            except Exception:
                continue
        return samples


class LinuxSampler(PlatformSampler):
    """Procfs-based sampler."""

    def __init__(self) -> None:
        self._proc = Path("/proc")

    def enumerate_processes(self) -> dict[int, ProcessIdentity]:
        identities: dict[int, ProcessIdentity] = {}
        boot_epoch = self._boot_time()
        clock_ticks = float(os.sysconf("SC_CLK_TCK"))  # type: ignore[attr-defined]
        for entry in os.listdir(self._proc):
            if not entry.isdigit():
                continue
            parsed = self._read_stat(int(entry), clock_ticks, boot_epoch)
            if parsed is None:
                continue
            identity, _ = parsed
            identities[identity.pid] = identity
        return identities

    def sample_details(self, pids: Sequence[int]) -> dict[int, ProcessSample]:
        clock_ticks = float(os.sysconf("SC_CLK_TCK"))  # type: ignore[attr-defined]
        page_size = int(os.sysconf("SC_PAGE_SIZE"))  # type: ignore[attr-defined]
        boot_epoch = self._boot_time()
        samples: dict[int, ProcessSample] = {}
        for pid in pids:
            parsed = self._read_stat(pid, clock_ticks, boot_epoch)
            if parsed is None:
                continue
            identity, cpu_time = parsed
            cmdline = self._read_cmdline(pid)
            rss_bytes = 0
            vms_bytes = 0
            try:
                statm = (self._proc / str(pid) / "statm").read_text(encoding="ascii").split()
                if len(statm) >= 2:
                    vms_bytes = int(statm[0]) * page_size
                    rss_bytes = int(statm[1]) * page_size
            except (OSError, ValueError):
                pass
            samples[pid] = ProcessSample(
                identity=identity,
                cpu_time=cpu_time,
                rss_bytes=rss_bytes,
                vms_bytes=vms_bytes,
                create_time=identity.create_time,
                cmdline=cmdline,
            )
        return samples

    def _boot_time(self) -> float | None:
        try:
            for line in (self._proc / "stat").read_text(encoding="ascii").splitlines():
                if line.startswith("btime"):
                    return float(line.split()[1])
        except (OSError, ValueError, IndexError):
            pass
        return None

    def _read_stat(
        self, pid: int, clock_ticks: float, boot_epoch: float | None
    ) -> tuple[ProcessIdentity, float] | None:
        try:
            stat_text = (self._proc / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            return None
        open_paren = stat_text.find("(")
        close_paren = stat_text.rfind(")")
        if open_paren < 0 or close_paren < open_paren:
            return None
        comm = stat_text[open_paren + 1 : close_paren]
        try:
            rest = stat_text[close_paren + 1 :].split()
            ppid = int(rest[1])
            cpu_time = (int(rest[11]) + int(rest[12])) / clock_ticks
            starttime_ticks = int(rest[19])
        except (IndexError, ValueError):
            return None
        cmdline = self._read_cmdline(pid)
        name = os.path.basename(cmdline[0]) if cmdline else comm
        create_time = boot_epoch + starttime_ticks / clock_ticks if boot_epoch is not None else None
        return ProcessIdentity(pid=pid, ppid=ppid, name=name.lower(), create_time=create_time), cpu_time

    def _read_cmdline(self, pid: int) -> tuple[str, ...]:
        try:
            raw = (self._proc / str(pid) / "cmdline").read_bytes()
        except OSError:
            return ()
        return tuple(part.decode("utf-8", "replace") for part in raw.split(b"\0") if part)


class WindowsSampler(PlatformSampler):
    """Tool-help enumeration plus per-PID Win32 counter queries."""

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def enumerate_processes(self) -> dict[int, ProcessIdentity]:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        TH32CS_SNAPPROCESS = 0x2
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        class _Entry(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        entries: dict[int, ProcessIdentity] = {}
        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot in (None, -1, 2**64 - 1):
            return entries
        try:
            entry = _Entry()
            entry.dwSize = ctypes.sizeof(_Entry)
            ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                entries[int(entry.th32ProcessID)] = ProcessIdentity(
                    pid=int(entry.th32ProcessID),
                    ppid=int(entry.th32ParentProcessID),
                    name=entry.szExeFile.lower(),
                )
                ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
        return entries

    def sample_details(self, pids: Sequence[int]) -> dict[int, ProcessSample]:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        class _MemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        samples: dict[int, ProcessSample] = {}
        identities = self.enumerate_processes()
        for pid in pids:
            identity = identities.get(pid)
            if identity is None:
                continue
            handle = kernel32.OpenProcess(self._PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                continue
            try:
                creation = wintypes.FILETIME()
                exit_time = wintypes.FILETIME()
                kernel_time = wintypes.FILETIME()
                user_time = wintypes.FILETIME()
                cpu_time = 0.0
                create_epoch: float | None = None
                if kernel32.GetProcessTimes(
                    handle,
                    ctypes.byref(creation),
                    ctypes.byref(exit_time),
                    ctypes.byref(kernel_time),
                    ctypes.byref(user_time),
                ):
                    create_epoch = self._filetime_to_epoch(creation)
                    cpu_time = self._filetime_duration_seconds(kernel_time) + self._filetime_duration_seconds(
                        user_time
                    )
                counters = _MemoryCounters()
                counters.cb = ctypes.sizeof(_MemoryCounters)
                rss_bytes = 0
                vms_bytes = 0
                if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                    rss_bytes = int(counters.WorkingSetSize)
                    vms_bytes = int(counters.PagefileUsage)
                enriched = ProcessIdentity(
                    pid=pid, ppid=identity.ppid, name=identity.name, create_time=create_epoch
                )
                samples[pid] = ProcessSample(
                    identity=enriched, cpu_time=cpu_time, rss_bytes=rss_bytes, vms_bytes=vms_bytes
                )
            except OSError:
                continue
            finally:
                kernel32.CloseHandle(handle)
        return samples

    @staticmethod
    def _filetime_to_epoch(filetime: object) -> float:
        low: int = filetime.dwLowDateTime  # type: ignore[attr-defined]
        high: int = filetime.dwHighDateTime  # type: ignore[attr-defined]
        ticks = (high << 32) + low
        return (ticks - _WINDOWS_FILETIME_EPOCH_DELTA) / _WINDOWS_FILETIME_TICKS_PER_SECOND

    @staticmethod
    def _filetime_duration_seconds(filetime: object) -> float:
        """Convert a FILETIME *duration* (kernel/user CPU time) to seconds."""
        low: int = filetime.dwLowDateTime  # type: ignore[attr-defined]
        high: int = filetime.dwHighDateTime  # type: ignore[attr-defined]
        ticks = (high << 32) + low
        return ticks / _WINDOWS_FILETIME_TICKS_PER_SECOND


class MacOSSampler(PlatformSampler):
    """BSD ``ps`` batch-query sampler (best-effort, stdlib only)."""

    def enumerate_processes(self) -> dict[int, ProcessIdentity]:
        try:
            res = subprocess.run(
                [_PS_EXECUTABLE, "-axo", "pid=,ppid=,comm="],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            output = res.stdout
        except (OSError, subprocess.SubprocessError):
            return {}
        identities: dict[int, ProcessIdentity] = {}
        for line in output.splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) < 3 or not parts[0].isdigit():
                continue
            identities[int(parts[0])] = ProcessIdentity(
                pid=int(parts[0]), ppid=int(parts[1]), name=os.path.basename(parts[2]).lower()
            )
        return identities

    def sample_details(self, pids: Sequence[int]) -> dict[int, ProcessSample]:
        if not pids:
            return {}
        pid_args = ",".join(str(pid) for pid in pids)
        try:
            res = subprocess.run(
                [_PS_EXECUTABLE, "-o", "pid=,rss=,vsz=,time=,etime=", "-p", pid_args],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            output = res.stdout
        except (OSError, subprocess.SubprocessError):
            return {}
        wanted = set(pids)
        identities = {pid: ident for pid, ident in self.enumerate_processes().items() if pid in wanted}
        now = time.time()
        samples: dict[int, ProcessSample] = {}
        for line in output.splitlines():
            parts = line.strip().split(None, 4)
            if len(parts) < 5 or not parts[0].isdigit():
                continue
            pid = int(parts[0])
            identity = identities.get(pid, ProcessIdentity(pid=pid, ppid=0, name=""))
            samples[pid] = ProcessSample(
                identity=identity,
                cpu_time=self._parse_ps_time(parts[3]),
                rss_bytes=int(parts[1]) * 1024,
                vms_bytes=int(parts[2]) * 1024,
                create_time=now - self._parse_ps_time(parts[4]),
            )
        return samples

    @staticmethod
    def _parse_ps_time(value: str) -> float:
        """Parse ``ps`` durations like ``MM:SS``, ``HH:MM:SS``, or ``DD-HH:MM:SS``."""
        days_part, separator, rest = value.partition("-")
        try:
            days = float(days_part) if separator else 0.0
            clock = rest if separator else days_part
            seconds = 0.0
            for part in clock.split(":"):
                seconds = seconds * 60 + float(part)
            return days * 86400 + seconds
        except ValueError:
            return 0.0


class _FallbackSampler(PlatformSampler):
    """Empty sampler for unsupported platforms."""

    def enumerate_processes(self) -> dict[int, ProcessIdentity]:
        return {}

    def sample_details(self, pids: Sequence[int]) -> dict[int, ProcessSample]:
        return {}


_sampler_instance: PlatformSampler | None = None


def _psutil_available() -> bool:
    try:
        import psutil  # noqa: F401

        return True
    except ImportError:
        return False


def _get_sampler() -> PlatformSampler:
    """Return the platform sampler; ``psutil`` is preferred when installed."""
    global _sampler_instance
    if _sampler_instance is not None:
        return _sampler_instance
    if sys.platform == "linux":
        _sampler_instance = LinuxSampler()
    elif sys.platform == "win32":
        _sampler_instance = WindowsSampler()
    elif sys.platform == "darwin":
        _sampler_instance = MacOSSampler()
    else:
        _sampler_instance = _FallbackSampler()
    if _psutil_available():
        _sampler_instance = PsutilSampler()
    return _sampler_instance


def reset_sampler_cache() -> None:
    """Forget the cached platform sampler (used after psutil installs, and by tests)."""
    global _sampler_instance
    _sampler_instance = None


def sample_process_tree(
    root_pid: int,
    expected_create_time: float | None = None,
    sampler: PlatformSampler | None = None,
) -> list[ProcessSample]:
    """Sample all processes belonging to ``root_pid``'s descendant tree.

    The root PID is validated against ``expected_create_time`` (when both
    timestamps are available) before it is trusted; a recycled PID yields an
    empty tree. Children that exit or deny access mid-scan are skipped.
    """
    sampler = sampler or _get_sampler()
    identities = sampler.enumerate_processes()
    root = identities.get(root_pid)
    if root is None:
        return []
    create_time = root.create_time
    if create_time is None:
        details = sampler.sample_details([root_pid])
        if root_pid in details:
            create_time = details[root_pid].create_time
    if (
        expected_create_time is not None
        and create_time is not None
        and abs(create_time - expected_create_time) > _CREATE_TIME_TOLERANCE_SECONDS
    ):
        # PID recycled by an unrelated process: refuse to report telemetry.
        return []

    children: dict[int, list[int]] = {}
    for identity in identities.values():
        children.setdefault(identity.ppid, []).append(identity.pid)
    tree_pids = [root_pid]
    seen = {root_pid}
    index = 0
    while index < len(tree_pids):
        for child in children.get(tree_pids[index], ()):
            if child not in seen:
                seen.add(child)
                tree_pids.append(child)
        index += 1

    details = sampler.sample_details(tree_pids)
    return [details[pid] for pid in tree_pids if pid in details]


def total_cpu_time(samples: list[ProcessSample]) -> float:
    return sum(sample.cpu_time for sample in samples)


def cpu_percent_between(
    previous: dict[int, ProcessSample],
    current: list[ProcessSample],
    wall_seconds: float,
) -> float:
    """Aggregate tree CPU usage as a percentage over the wall-clock interval.

    Processes present in both samples contribute their CPU-time delta; processes
    that appeared or vanished mid-interval contribute nothing (conservative).
    """
    if wall_seconds <= 0:
        return 0.0
    delta = sum(
        max(0.0, sample.cpu_time - previous[sample.pid].cpu_time)
        for sample in current
        if sample.pid in previous
    )
    return delta / wall_seconds * 100.0


def classify_role(
    sample: ProcessSample,
    browser_pid: int | None = None,
    controller_pid: int | None = None,
) -> str:
    """Map a sample to a Chromium role: browser, renderer, gpu, utility, controller."""
    if controller_pid is not None and sample.pid == controller_pid:
        return "controller"
    if browser_pid is not None and sample.pid == browser_pid:
        return "browser"
    joined = " ".join(sample.cmdline).lower()
    if "--type=renderer" in joined or "renderer" in sample.name:
        return "renderer"
    if "--type=gpu" in joined or "gpu" in sample.name:
        return "gpu"
    return "utility"
