"""Process identity, discovery and termination primitives.

Platform-specific code (Windows kernel32 calls, Linux /proc reading, POSIX
process groups) is deliberately concentrated in this module. Identity checks
pair each PID with its creation time so a recycled PID is never mistaken for
the recorded process.
"""

import ctypes
import os
import signal
import subprocess
import sys
import time
from functools import lru_cache
from pathlib import Path
from subprocess import Popen
from typing import IO, Any, Optional

from .state import _MAX_ERROR_BYTES

if sys.platform == "win32":

    @lru_cache(maxsize=1)
    def _kernel32() -> tuple[Any, Any, Any]:
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [
                ("dwLowDateTime", wintypes.DWORD),
                ("dwHighDateTime", wintypes.DWORD),
            ]

        library = ctypes.WinDLL("kernel32", use_last_error=True)
        library.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        library.OpenProcess.restype = wintypes.HANDLE
        library.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
        ]
        library.GetProcessTimes.restype = wintypes.BOOL
        library.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        library.GetExitCodeProcess.restype = wintypes.BOOL
        library.CloseHandle.argtypes = [wintypes.HANDLE]
        library.CloseHandle.restype = wintypes.BOOL
        return library, FILETIME, wintypes

else:

    def _kernel32() -> tuple[Any, Any, Any]:
        raise NotImplementedError("kernel32 is only available on Windows")


def _get_process_create_time(pid: int) -> Optional[float]:
    if pid < 1:
        return None
    if sys.platform == "win32":
        kernel32, filetime_type, _wintypes = _kernel32()

        handle = kernel32.OpenProcess(0x1000 | 0x0400, False, pid)
        if not handle:
            return None
        creation_time = filetime_type()
        exit_time = filetime_type()
        kernel_time = filetime_type()
        user_time = filetime_type()
        try:
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation_time),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            ):
                return None
            time_val = (creation_time.dwHighDateTime << 32) + creation_time.dwLowDateTime
            return float((time_val - 116444736000000000) / 10000000.0)
        finally:
            kernel32.CloseHandle(handle)

    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.exists():
        try:
            content = proc_stat.read_text(encoding="utf-8")
            parts = content.split(")")
            if len(parts) >= 2:
                fields = parts[-1].strip().split()
                if len(fields) >= 20:
                    starttime_ticks = float(fields[19])
                    return starttime_ticks
        except (OSError, ValueError, IndexError):
            pass
    return None


def _is_matching_process(
    pid: int,
    expected_start_time: Optional[float],
    require_verification: bool = False,
) -> bool:
    # Late-bound so patches of profiledock.process_manager._alive and
    # ._get_process_create_time keep applying.
    from profiledock.process_manager import _alive as _alive_impl
    from profiledock.process_manager import _get_process_create_time as _get_process_create_time_impl

    if pid < 1 or not _alive_impl(pid):
        return False
    if expected_start_time is None:
        return True
    if not isinstance(expected_start_time, (int, float)):
        return False
    actual_start_time = _get_process_create_time_impl(pid)
    if actual_start_time is None:
        return not require_verification
    return abs(actual_start_time - expected_start_time) < 2.0


def _alive(pid: int) -> bool:
    if pid < 1:
        return False
    if sys.platform == "win32":
        kernel32, _, wintypes = _kernel32()

        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        exit_code = wintypes.DWORD()
        try:
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == 259  # type: ignore[no-any-return]  # DWORD.value is int
        finally:
            kernel32.CloseHandle(handle)
    if os.name != "nt":
        try:
            wpid, _ = os.waitpid(pid, os.WNOHANG)
            if wpid == pid:
                return False
        except (ChildProcessError, OSError):
            pass
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


_CHROMIUM_PROCESS_NAMES = ("chrome", "chromium", "headless_shell", "msedge")


def _is_chromium_process_name(name: str) -> bool:
    lowered = name.lower()
    return any(marker in lowered for marker in _CHROMIUM_PROCESS_NAMES)


def _parse_linux_process_stat(value: str) -> tuple[int, str]:
    name_start = value.find("(")
    name_end = value.rfind(")")
    if name_start < 0 or name_end <= name_start:
        raise ValueError("invalid process stat")
    stat_fields = value[name_end + 1 :].split()
    if len(stat_fields) < 2:
        raise ValueError("invalid process stat")
    return int(stat_fields[1]), value[name_start + 1 : name_end]


def _list_processes() -> list[tuple[int, int, str]]:
    """Return (pid, parent_pid, executable_name) snapshots for all processes."""
    if sys.platform == "win32":
        import ctypes.wintypes as wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class PROCESSENTRY32W(ctypes.Structure):
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

        TH32CS_SNAPPROCESS = 0x2
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot == wintypes.HANDLE(-1).value:
            return []
        entries: list[tuple[int, int, str]] = []
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        try:
            if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                while True:
                    entries.append(
                        (int(entry.th32ProcessID), int(entry.th32ParentProcessID), str(entry.szExeFile))
                    )
                    if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                        break
        finally:
            kernel32.CloseHandle(snapshot)
        return entries

    if not Path("/proc").is_dir():
        return []
    entries = []
    for pid_dir in Path("/proc").iterdir():
        if not pid_dir.name.isdigit():
            continue
        try:
            process_stat = (pid_dir / "stat").read_text(encoding="utf-8")
            ppid, comm = _parse_linux_process_stat(process_stat)
            entries.append((int(pid_dir.name), ppid, comm))
        except (OSError, ValueError, IndexError):
            continue
    return entries


def _find_browser_pid(controller_pid: int) -> int:
    """Locate the main Chromium process spawned by the controller process tree.

    Returns the PID of the root of the Chromium subtree (the browser main
    process), or 0 when it cannot be determined. Callers must treat 0 as
    "unknown" and never signal it.
    """
    if controller_pid < 1:
        return 0
    # Late-bound so patches of profiledock.process_manager._list_processes keep applying.
    from profiledock.process_manager import _list_processes as _list_processes_impl

    try:
        entries = _list_processes_impl()
    except Exception:
        return 0
    by_pid = {pid: (ppid, name) for pid, ppid, name in entries}
    best_pid = 0
    for pid, (_ppid, name) in by_pid.items():
        if not _is_chromium_process_name(name):
            continue
        topmost_chromium = pid
        current = pid
        reached_controller = False
        for _ in range(64):
            entry = by_pid.get(current)
            if entry is None:
                break
            parent_pid, _parent_name = entry
            if parent_pid == controller_pid:
                reached_controller = True
                break
            if parent_pid not in by_pid:
                break
            if _is_chromium_process_name(by_pid[parent_pid][1]):
                topmost_chromium = parent_pid
            current = parent_pid
        if reached_controller and topmost_chromium:
            best_pid = topmost_chromium
            break
    return best_pid


def _terminate_matching_process(pid: int, expected_create_time: Optional[float], timeout: float) -> bool:
    """Terminate a process tree only when its identity matches the recorded one.

    Returns True when the process is gone (or was already absent). A PID whose
    create time does not match the recorded value is never signalled.
    """
    # Late-bound so patches of profiledock.process_manager._alive and
    # ._is_matching_process keep applying.
    from profiledock.process_manager import _alive as _alive_impl
    from profiledock.process_manager import _is_matching_process as _is_matching_process_impl

    if pid < 1 or not _alive_impl(pid):
        return True
    if expected_create_time is None:
        return False
    if not _is_matching_process_impl(pid, expected_create_time, require_verification=True):
        return False
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        _signal_posix_process_group(pid, signal.SIGTERM)
    deadline = time.monotonic() + max(timeout, 0.1)
    poll_interval = 0.02
    while time.monotonic() < deadline and _alive_impl(pid):
        time.sleep(poll_interval)
        poll_interval = min(poll_interval * 1.5, 0.1)
    if _alive_impl(pid):
        if not _is_matching_process_impl(pid, expected_create_time, require_verification=True):
            return False
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            _signal_posix_process_group(pid, signal.SIGKILL)
        force_deadline = time.monotonic() + min(max(timeout, 0.1), 2)
        force_interval = 0.01
        while time.monotonic() < force_deadline and _alive_impl(pid):
            time.sleep(force_interval)
            force_interval = min(force_interval * 1.5, 0.05)
    return not _alive_impl(pid)


def _signal_posix_process_group(pid: int, sig: signal.Signals) -> None:
    try:
        pgid = os.getpgid(pid)  # type: ignore[attr-defined]  # POSIX-only API; guarded by sys.platform callers
        if pgid == pid:
            os.killpg(pgid, sig)  # type: ignore[attr-defined]
            return
    except (OSError, ProcessLookupError):
        pass
    try:
        os.kill(pid, sig)
    except (OSError, ProcessLookupError):
        pass


def _stop_process(process: Popen[bytes], timeout: float = 5) -> None:
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        _signal_posix_process_group(process.pid, signal.SIGTERM)
    # Drain stderr while waiting so a child filling the pipe cannot deadlock
    # the wait; communicate() also closes the pipe deterministically.
    try:
        process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if sys.platform != "win32":
            _signal_posix_process_group(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.communicate(timeout=timeout)


def _stderr_message(process: Popen[bytes], token: str) -> str:
    stderr: IO[bytes] | None = process.stderr
    if stderr is None or stderr.closed:
        return ""
    try:
        output = stderr.read(_MAX_ERROR_BYTES)
    except (OSError, ValueError):
        return ""
    return output.decode("utf-8", errors="replace").replace(token, "[redacted]").strip()


def _close_stderr(process: Popen[bytes]) -> None:
    stderr = process.stderr
    if stderr is not None and not stderr.closed:
        stderr.close()
