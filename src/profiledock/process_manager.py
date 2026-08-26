import argparse
import ctypes
import hmac
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from subprocess import Popen
from typing import (
    IO,
    TYPE_CHECKING,
    Any,
    Optional,
)
from uuid import uuid4

from .fsops import replace_with_retry as _replace_with_retry
from .fsops import write_all as _write_all

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Playwright

_MAX_ERROR_BYTES = 4096
RUNNING_STATE_PROTOCOL_VERSION = 2
_MAX_COMMAND_BYTES = 512
_DIRECT_STATE_FIELDS = frozenset(
    {
        "protocol_version",
        "engine",
        "profile_id",
        "pid",
        "launcher_pid",
        "process_create_time",
        "tabs",
        "channel",
        "started_at",
        "status",
        "closing",
    }
)
_PLAYWRIGHT_STATE_FIELDS = frozenset(
    {
        "protocol_version",
        "engine",
        "profile_id",
        "controller_pid",
        "controller_started_at",
        "launcher_pid",
        "port",
        "token",
        "tabs",
        "page_count",
        "channel",
        "status",
        "closing",
        "browser_channel",
        "start_urls",
        "window_width",
        "window_height",
        "legacy_controller",
        "pid",
    }
)


class ProfileRunningError(Exception):
    def __init__(self, message: str, stopped: bool = False) -> None:
        super().__init__(message)
        self.stopped = stopped


class BrowserLaunchError(Exception):
    def __init__(self, message: str, category: str = "browser_launch_failed") -> None:
        super().__init__(message)
        self.category = category


def _runtime_dir(data_dir: str, runtime_dir: Optional[Path]) -> Path:
    if runtime_dir is not None:
        selected = runtime_dir
    else:
        data_path = Path(data_dir)
        profile_dir = data_path.parent
        profiles_dir = profile_dir.parent
        if profiles_dir.name == "profiles":
            selected = profiles_dir.parent / "runtime" / profile_dir.name
        else:
            selected = profile_dir
    data_path = Path(data_dir)
    try:
        selected.resolve(strict=False).relative_to(data_path.resolve(strict=False))
    except ValueError:
        return selected
    raise ValueError("runtime directory cannot be inside browser-data")


def state_path(data_dir: str, runtime_dir: Optional[Path] = None) -> Path:
    return _runtime_dir(data_dir, runtime_dir) / "running.json"


def error_path(data_dir: str, runtime_dir: Optional[Path] = None) -> Path:
    return _runtime_dir(data_dir, runtime_dir) / "controller.error"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_private_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    fd = None
    try:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
        fd = os.open(str(temporary), flags, 0o600)
        _write_all(fd, payload)
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.chmod(temporary, 0o600)
        _replace_with_retry(temporary, path)
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _atomic_private_json(path: Path, value: dict[str, Any]) -> None:
    _atomic_private_bytes(path, json.dumps(value).encode("utf-8"))


StateDict = dict[str, Any]


def _valid_state(value: StateDict, profile_id: Optional[str] = None) -> bool:
    if value.get("engine") != "playwright" or set(value) - _PLAYWRIGHT_STATE_FIELDS:
        return False
    if (
        type(value.get("protocol_version")) is not int
        or value["protocol_version"] != RUNNING_STATE_PROTOCOL_VERSION
    ):
        return False
    if not isinstance(value.get("profile_id"), str) or not value["profile_id"]:
        return False
    if profile_id is not None and value["profile_id"] != profile_id:
        return False
    if not isinstance(value.get("token"), str) or len(value["token"]) < 32:
        return False
    if type(value.get("controller_pid")) is not int or value["controller_pid"] < 0:
        return False
    if type(value.get("port")) is not int or not 0 <= value["port"] <= 65535:
        return False
    if type(value.get("tabs")) is not int or value["tabs"] < 1:
        return False
    if value.get("status") not in {"starting", "running", "closing"}:
        return False
    if "launcher_pid" in value and (type(value["launcher_pid"]) is not int or value["launcher_pid"] < 0):
        return False
    if "closing" in value and type(value["closing"]) is not bool:
        return False
    if not isinstance(value.get("controller_started_at"), str):
        return False
    try:
        started_at = datetime.fromisoformat(value["controller_started_at"])
    except (TypeError, ValueError):
        return False
    return started_at.tzinfo is not None and started_at.utcoffset() is not None


def _valid_direct_state(value: StateDict, profile_id: str) -> bool:
    if (
        value.get("engine") != "direct"
        or value.get("profile_id") != profile_id
        or set(value) - _DIRECT_STATE_FIELDS
        or type(value.get("protocol_version")) is not int
        or value["protocol_version"] != RUNNING_STATE_PROTOCOL_VERSION
    ):
        return False
    if type(value.get("pid")) is not int or type(value.get("launcher_pid")) is not int:
        return False
    if value["pid"] < 0 or value["launcher_pid"] < 1:
        return False
    if type(value.get("tabs")) is not int or value["tabs"] < 1:
        return False
    if not isinstance(value.get("channel"), str) or not value["channel"]:
        return False
    if value.get("status") not in {"starting", "running", "closing"}:
        return False
    if "closing" in value and type(value["closing"]) is not bool:
        return False
    pid = value["pid"]
    process_create_time = value.get("process_create_time")
    # None is legal on platforms without process-create-time support (macOS);
    # identity checks degrade to PID liveness for such states.
    if pid > 0 and process_create_time is not None and not isinstance(process_create_time, (int, float)):
        return False
    if not isinstance(value.get("started_at"), str):
        return False
    try:
        started_at = datetime.fromisoformat(value["started_at"])
    except (TypeError, ValueError):
        return False
    return started_at.tzinfo is not None and started_at.utcoffset() is not None


def _upgrade_legacy_state(path: Path, value: StateDict, profile_id: str) -> StateDict:
    version = value.get("protocol_version", 0)
    if type(version) is not int or version < 0 or version > RUNNING_STATE_PROTOCOL_VERSION:
        return value
    upgraded = dict(value)
    try:
        modified_at = path.stat().st_mtime
    except OSError:
        return value
    while version < RUNNING_STATE_PROTOCOL_VERSION:
        if version == 0:
            engine = "direct" if upgraded.get("engine") == "direct" else "playwright"
            if engine == "playwright":
                try:
                    pid = int(upgraded.get("controller_pid", upgraded.get("pid", 0)))
                    port = int(upgraded.get("port", 0))
                except (TypeError, ValueError):
                    return value
                token = upgraded.get("token")
                if pid < 1 or port < 1 or not isinstance(token, str) or len(token) < 32:
                    return value
                upgraded.update(
                    {
                        "profile_id": profile_id,
                        "controller_pid": pid,
                        "controller_started_at": upgraded.get("controller_started_at")
                        or datetime.fromtimestamp(modified_at, timezone.utc).isoformat(),
                        "status": upgraded.get("status", "running"),
                        "legacy_controller": True,
                    }
                )
            upgraded["protocol_version"] = 1
            version = 1
        elif version == 1:
            upgraded["engine"] = "direct" if upgraded.get("engine") == "direct" else "playwright"
            upgraded["protocol_version"] = 2
            version = 2
    if upgraded == value:
        return upgraded
    backup_path = path.with_name(f"{path.name}.v{value.get('protocol_version', 0)}.bak")
    try:
        if not backup_path.exists():
            _atomic_private_bytes(backup_path, json.dumps(value).encode("utf-8"))
        _atomic_private_json(path, upgraded)
    except OSError:
        return value
    return upgraded


def _write_error(
    path: Path,
    error_type: str,
    message: str,
    channel: str = "",
    redactions: Iterable[str] = (),
) -> None:
    for secret in redactions:
        if secret:
            message = message.replace(secret, "[redacted]")
    base: dict[str, Any] = {"error_type": error_type}
    if channel:
        base["channel"] = channel
    low = 0
    high = len(message)
    encoded = b""
    while low <= high:
        middle = (low + high) // 2
        payload = {**base, "message": message[:middle]}
        candidate = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        if len(candidate) <= _MAX_ERROR_BYTES:
            encoded = candidate
            low = middle + 1
        else:
            high = middle - 1
    try:
        _atomic_private_bytes(path, encoded)
    except OSError:
        pass


def _read_error(path: Path) -> Optional[StateDict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "error_type" in data:
            return data
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return None


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
    if pid < 1 or not _alive(pid):
        return False
    if expected_start_time is None:
        return True
    actual_start_time = _get_process_create_time(pid)
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


def _read_state(path: Path) -> Optional[StateDict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return None


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def state_file_is_unreadable(state_file: Path) -> bool:
    """True when a running-state file exists but cannot be parsed as a JSON object.

    An unparseable file cannot verify or protect a live process, so it is a safe
    cleanup candidate; readable-but-invalid files are deliberately refused instead.
    A missing file is not unreadable.
    """
    if not state_file.is_file():
        return False
    return not isinstance(_read_state(state_file), dict)


def get_status(data_dir: str, clean_stale: bool = True, runtime_dir: Optional[Path] = None) -> str:
    path = state_path(data_dir, runtime_dir)
    err = error_path(data_dir, runtime_dir)
    if path.exists():
        state = _read_state(path)
        if not state or not isinstance(state, dict):
            return "error"
        state_version = state.get("protocol_version", 0)
        if type(state_version) is int and state_version > RUNNING_STATE_PROTOCOL_VERSION:
            return "error"
        state = _upgrade_legacy_state(path, state, Path(data_dir).parent.name)
        if state.get("engine") == "direct":
            if not _valid_direct_state(state, Path(data_dir).parent.name):
                return "error"
            pid = state["pid"]
            if pid > 0 and _is_matching_process(pid, state.get("process_create_time")):
                if state.get("closing"):
                    return "closing"
                return "running"
            launcher_pid = state["launcher_pid"]
            if pid == 0 and launcher_pid > 0 and _alive(launcher_pid):
                return "starting"
            if clean_stale:
                _unlink_quietly(path)
            return "stale"
        if not state or not _valid_state(state, Path(data_dir).parent.name):
            return "error"
        if state.get("closing"):
            pid = int(state.get("controller_pid", -1))
            if pid > 0 and _alive(pid):
                return "closing"
            if clean_stale:
                _unlink_quietly(path)
            return "stale"
        pid = int(state.get("controller_pid", -1))
        if pid <= 0:
            launcher_pid = int(state.get("launcher_pid", -1))
            if launcher_pid > 0 and _alive(launcher_pid):
                return "starting"
            if clean_stale:
                _unlink_quietly(path)
            return "stale"
        if not _alive(pid):
            if clean_stale:
                _unlink_quietly(path)
            return "stale"
        port = int(state.get("port", 0))
        if not port:
            return "starting"
        return "running"
    if err.exists():
        err_data = _read_error(err)
        if err_data:
            return "error"
    return "stopped"


def is_running(data_dir: str, runtime_dir: Optional[Path] = None) -> bool:
    return get_status(data_dir, clean_stale=True, runtime_dir=runtime_dir) in (
        "starting",
        "running",
        "closing",
        "error",
    )


def _controller_available(state: StateDict) -> bool:
    try:
        port = int(state.get("port", 0))
        token = state.get("token", "")
        if port < 1 or not isinstance(token, str) or not token:
            return False
        with socket.create_connection(("127.0.0.1", port), timeout=0.5) as connection:
            if state.get("legacy_controller"):
                return True
            connection.settimeout(0.5)
            connection.sendall(("probe:" + token + "\n").encode("utf-8"))
            return connection.recv(16) == b"ok\n"
    except (OSError, TypeError, ValueError):
        return False


def is_active_for_mutation(data_dir: str, runtime_dir: Optional[Path] = None) -> bool:
    path = state_path(data_dir, runtime_dir)
    state = _read_state(path)
    if not state:
        return path.exists()
    profile_id = Path(data_dir).parent.name
    state = _upgrade_legacy_state(path, state, profile_id)
    if state.get("engine") == "direct":
        if not _valid_direct_state(state, profile_id):
            return True
        pid = int(state.get("pid", -1))
        launcher_pid = int(state.get("launcher_pid", -1))
        return _is_matching_process(pid, state.get("process_create_time")) or (
            pid == 0 and _alive(launcher_pid)
        )
    upgraded = dict(state)
    if not _valid_state(upgraded, profile_id):
        return True
    controller_pid = int(upgraded.get("controller_pid", -1))
    launcher_pid = int(upgraded.get("launcher_pid", -1))
    return (
        _alive(controller_pid)
        or _controller_available(upgraded)
        or (controller_pid <= 0 and _alive(launcher_pid))
    )


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


def start_direct_chrome(
    data_dir: str,
    tabs: int,
    runtime_dir: Optional[Path] = None,
    executable_path: Optional[Path] = None,
    browser: Optional[str] = None,
    start_urls: Optional[list[str]] = None,
    window_width: Optional[int] = None,
    window_height: Optional[int] = None,
) -> StateDict:
    if tabs < 1:
        raise ValueError("tab count must be at least 1")
    if executable_path is not None and browser is not None:
        raise ValueError("specify either executable_path or browser, not both")
    if (window_width is None) != (window_height is None):
        raise ValueError("both window_width and window_height must be specified together")
    if window_width is not None and (window_width < 100 or window_height is None or window_height < 100):
        raise ValueError("window width and height must be at least 100")
    urls = list(start_urls or [])
    if len(urls) > tabs:
        raise ValueError("number of start URLs cannot exceed the requested tab count")
    if not Path(data_dir).is_dir():
        raise BrowserLaunchError(
            "profile data directory is missing or invalid",
            "invalid_data_directory",
        )
    browser_bin = executable_path if executable_path is not None else _system_browser_executable(browser)
    if browser_bin is None or not Path(browser_bin).is_file():
        raise BrowserLaunchError(
            "Google Chrome, Chromium, or Brave executable not found on system",
            "browser_not_found",
        )

    path = state_path(data_dir, runtime_dir)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        path.parent.chmod(0o700)

    err = error_path(data_dir, runtime_dir)
    _unlink_quietly(err)

    if state_file_is_unreadable(path):
        raise ProfileRunningError(
            "profile runtime state file is unreadable; run 'profiledock doctor --repair' to clean it up"
        )
    if is_running(data_dir, runtime_dir=runtime_dir):
        raise ProfileRunningError("profile is already running")

    started_at = _utc_now()
    initial = {
        "protocol_version": RUNNING_STATE_PROTOCOL_VERSION,
        "profile_id": Path(data_dir).parent.name,
        "pid": 0,
        "launcher_pid": os.getpid(),
        "engine": "direct",
        "tabs": tabs,
        "channel": Path(browser_bin).stem,
        "started_at": started_at,
        "status": "starting",
    }
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(path), flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(initial, handle)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise ProfileRunningError("profile is already running") from exc

    if len(urls) < tabs:
        urls.extend(["about:blank" for _ in range(tabs - len(urls))])

    args = [
        str(browser_bin),
        f"--user-data-dir={data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
    ]
    if window_width is not None and window_height is not None:
        args.append(f"--window-size={window_width},{window_height}")
    args.extend(urls)

    popen_kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
        )
    else:
        popen_kwargs["start_new_session"] = True

    try:
        process = subprocess.Popen(args, **popen_kwargs)
    except OSError as exc:
        _unlink_quietly(path)
        _write_error(err, "browser_launch_failed", str(exc))
        raise BrowserLaunchError(str(exc), "browser_launch_failed") from exc

    # Platforms without process-create-time support (e.g., macOS, where /proc
    # does not exist) record None; identity checks then degrade to PID liveness
    # instead of failing every launch and close.
    proc_create_time = _get_process_create_time(process.pid)
    state = {
        "protocol_version": RUNNING_STATE_PROTOCOL_VERSION,
        "profile_id": initial["profile_id"],
        "pid": process.pid,
        "launcher_pid": initial["launcher_pid"],
        "process_create_time": proc_create_time,
        "engine": "direct",
        "tabs": tabs,
        "channel": initial["channel"],
        "started_at": started_at,
        "status": "running",
    }

    try:
        _atomic_private_json(path, state)
    except OSError as exc:
        try:
            _stop_process(process)
        finally:
            _unlink_quietly(path)
        _write_error(err, "state_write_failed", str(exc))
        raise BrowserLaunchError(str(exc), "state_write_failed") from exc
    return state


def start_controller(
    data_dir: str,
    tabs: int,
    headless: bool = False,
    startup_timeout: float = 30,
    runtime_dir: Optional[Path] = None,
    browser_channel: Optional[str] = None,
    start_urls: Optional[list[str]] = None,
    window_width: Optional[int] = None,
    window_height: Optional[int] = None,
) -> StateDict:
    if tabs < 1:
        raise ValueError("tab count must be at least 1")
    if (window_width is None) != (window_height is None):
        raise ValueError("both window_width and window_height must be specified together")
    if window_width is not None and (window_width < 100 or window_height is None or window_height < 100):
        raise ValueError("window width and height must be at least 100")
    urls = list(start_urls or [])
    if len(urls) > tabs:
        raise ValueError("number of start URLs cannot exceed the requested tab count")
    if not Path(data_dir).is_dir():
        raise BrowserLaunchError(
            "profile data directory is missing or invalid",
            "invalid_data_directory",
        )
    path = state_path(data_dir, runtime_dir)
    err = error_path(data_dir, runtime_dir)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        path.parent.chmod(0o700)
    _unlink_quietly(err)
    if state_file_is_unreadable(path):
        raise ProfileRunningError(
            "profile runtime state file is unreadable; run 'profiledock doctor --repair' to clean it up"
        )
    if is_running(data_dir, runtime_dir):
        raise ProfileRunningError("profile is already running")
    token = uuid4().hex
    initial = {
        "protocol_version": RUNNING_STATE_PROTOCOL_VERSION,
        "engine": "playwright",
        "profile_id": Path(data_dir).parent.name,
        "controller_pid": 0,
        "controller_started_at": _utc_now(),
        "launcher_pid": os.getpid(),
        "port": 0,
        "token": token,
        "tabs": tabs,
        "status": "starting",
        "browser_channel": browser_channel,
        "start_urls": urls,
        "window_width": window_width,
        "window_height": window_height,
    }
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(path), flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(initial, handle)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise ProfileRunningError("profile is already running") from exc

    command = [
        sys.executable,
        "-m",
        "profiledock.process_manager",
        "--controller",
        str(path),
        data_dir,
        str(tabs),
        token,
    ]
    if headless:
        command.append("--headless")
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + startup_timeout
        poll_interval = 0.02
        try:
            while time.monotonic() < deadline:
                state = _read_state(path)
                profile_id_value = initial["profile_id"]
                if state and _valid_state(state, str(profile_id_value)) and state.get("port"):
                    _unlink_quietly(err)
                    _close_stderr(process)
                    return state
                if process.poll() is not None:
                    break
                time.sleep(poll_interval)
                poll_interval = min(poll_interval * 1.5, 0.1)
        except BaseException:
            # An interrupt during startup must not orphan the freshly spawned
            # controller subprocess or its starting-state file.
            _stop_process(process)
            _unlink_quietly(path)
            raise
    except OSError as exc:
        _unlink_quietly(path)
        _write_error(err, "controller_spawn_failed", str(exc), redactions=(token,))
        raise BrowserLaunchError(str(exc), "controller_spawn_failed") from exc
    _unlink_quietly(path)
    error_info = _read_error(err)
    if error_info:
        _close_stderr(process)
        raise BrowserLaunchError(
            error_info["message"],
            str(error_info["error_type"]),
        )
    if process.poll() is not None:
        stderr = _stderr_message(process, token)
        message = f"Controller process exited unexpectedly (code {process.returncode})"
        if stderr:
            message = f"{message}: {stderr}"
        _write_error(err, "controller_exited", message, redactions=(token,))
        _close_stderr(process)
        raise BrowserLaunchError(
            message,
            "controller_exited",
        )
    _stop_process(process)
    message = f"Controller startup timed out after {startup_timeout:g} seconds"
    _write_error(err, "controller_timeout", message, redactions=(token,))
    _close_stderr(process)
    raise BrowserLaunchError(
        message,
        "controller_timeout",
    )


def _close_direct(path: Path, state: StateDict, timeout: float) -> None:
    state["closing"] = True
    state["status"] = "closing"
    try:
        _atomic_private_json(path, state)
    except OSError:
        pass
    pid = int(state.get("pid", -1))
    expected_create_time = state.get("process_create_time")
    if pid > 0 and _alive(pid):
        if not _is_matching_process(pid, expected_create_time, require_verification=True):
            raise ProfileRunningError("profile process identity could not be verified; refusing to signal it")
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            _signal_posix_process_group(pid, signal.SIGTERM)
        deadline = time.monotonic() + timeout
        poll_interval = 0.02
        while time.monotonic() < deadline:
            if not _alive(pid):
                break
            time.sleep(poll_interval)
            poll_interval = min(poll_interval * 1.5, 0.1)
        if _alive(pid):
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
            while time.monotonic() < force_deadline and _alive(pid):
                time.sleep(force_interval)
                force_interval = min(force_interval * 1.5, 0.05)
        if _alive(pid):
            raise BrowserLaunchError("browser process did not close within the timeout")
    _unlink_quietly(path)


def _close_playwright(path: Path, state: StateDict, timeout: float) -> None:
    port = int(state.get("port", 0))
    if not port:
        raise BrowserLaunchError("profile controller is not ready")
    token = state.get("token", "")
    state["closing"] = True
    state["status"] = "closing"
    try:
        _atomic_private_json(path, state)
    except OSError:
        pass
    close_sent = False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3) as connection:
            if state.get("legacy_controller"):
                connection.sendall(("close:" + token).encode("utf-8"))
                close_sent = True
            else:
                connection.sendall(("close:" + token + "\n").encode("utf-8"))
                response = connection.recv(16)
                close_sent = response == b"ok\n"
    except OSError:
        pass
    deadline = time.monotonic() + timeout
    poll_interval = 0.02
    while path.exists() and time.monotonic() < deadline:
        time.sleep(poll_interval)
        poll_interval = min(poll_interval * 1.5, 0.1)
    if path.exists():
        if not _alive(int(state.get("controller_pid", -1))):
            _unlink_quietly(path)
            raise ProfileRunningError("profile is not running", stopped=True)
        raise BrowserLaunchError("profile did not close within the timeout")
    if not close_sent:
        raise ProfileRunningError("profile is not running", stopped=True)


def close_controller(data_dir: str, timeout: float = 15, runtime_dir: Optional[Path] = None) -> None:
    path = state_path(data_dir, runtime_dir)
    initial_state = _read_state(path)
    if path.exists() and not initial_state:
        raise ProfileRunningError(
            "profile running state is invalid; refusing to remove ambiguous state. "
            "Run 'profiledock doctor --repair' to clean up unreadable state files."
        )
    if initial_state:
        initial_state = _upgrade_legacy_state(path, initial_state, Path(data_dir).parent.name)
    if initial_state and initial_state.get("engine") == "direct":
        if not _valid_direct_state(initial_state, Path(data_dir).parent.name):
            raise ProfileRunningError(
                "profile running state is invalid; refusing to signal an unverified process"
            )
        initial_pid = int(initial_state.get("pid", -1))
        expected_create_time = initial_state.get("process_create_time")
        if initial_pid > 0 and _alive(initial_pid):
            actual_create_time = _get_process_create_time(initial_pid)
            # Enforce identity only when both timestamps are available; on
            # platforms that cannot read create times, PID liveness is the
            # strongest available check.
            if (
                expected_create_time is not None
                and actual_create_time is not None
                and abs(actual_create_time - expected_create_time) >= 2.0
            ):
                _unlink_quietly(path)
                raise ProfileRunningError(
                    "profile process is not running (PID was reused by another process)", stopped=True
                )
    if not is_running(data_dir, runtime_dir):
        raise ProfileRunningError("profile is not running", stopped=True)
    state = _read_state(path)
    if not state:
        raise ProfileRunningError("profile is not running", stopped=True)

    if state.get("engine") == "direct":
        _close_direct(path, state, timeout)
        return

    state = _upgrade_legacy_state(path, state, Path(data_dir).parent.name)
    if not _valid_state(state, Path(data_dir).parent.name):
        raise ProfileRunningError(
            "profile running state is invalid; refusing unauthenticated controller access"
        )
    _close_playwright(path, state, timeout)


def _context_alive(context: "BrowserContext") -> bool:
    try:
        return bool(context.pages)
    except Exception:
        return False


def _wait_for_close(server: socket.socket, context: "BrowserContext", token: str) -> None:
    while _context_alive(context):
        try:
            connection, _ = server.accept()
        except (socket.timeout, OSError):
            continue
        with connection:
            try:
                connection.settimeout(2.0)
                command = connection.recv(_MAX_COMMAND_BYTES + 1)
            except (socket.timeout, OSError):
                continue
            if len(command) > _MAX_COMMAND_BYTES or not command:
                try:
                    connection.sendall(b"error\n")
                except OSError:
                    pass
                continue
            supplied = command.decode("utf-8", errors="replace").rstrip("\r\n")
            close_command = "close:" + token
            probe_command = "probe:" + token
            if hmac.compare_digest(supplied, probe_command):
                try:
                    connection.sendall(b"ok\n")
                except OSError:
                    pass
                continue
            if hmac.compare_digest(supplied, close_command):
                try:
                    connection.sendall(b"ok\n")
                except OSError:
                    pass
                return
            try:
                connection.sendall(b"error\n")
            except OSError:
                pass


def _launch_context(
    playwright: "Playwright",
    data_dir: str,
    headless: bool,
    channel_override: Optional[str] = None,
    window_width: Optional[int] = None,
    window_height: Optional[int] = None,
) -> tuple["BrowserContext", str]:
    from playwright.sync_api import Error as PlaywrightError

    kwargs: dict[str, Any] = {"headless": headless}
    if window_width is not None and window_height is not None:
        kwargs["viewport"] = {"width": window_width, "height": window_height}
        kwargs["args"] = [f"--window-size={window_width},{window_height}"]

    if channel_override:
        if Path(channel_override).is_file():
            return playwright.chromium.launch_persistent_context(
                data_dir, executable_path=channel_override, **kwargs
            ), channel_override
        return playwright.chromium.launch_persistent_context(
            data_dir, channel=channel_override, **kwargs
        ), channel_override

    try:
        return playwright.chromium.launch_persistent_context(data_dir, **kwargs), "chromium"
    except PlaywrightError as chromium_error:
        try:
            return playwright.chromium.launch_persistent_context(
                data_dir, channel="chrome", **kwargs
            ), "chrome"
        except PlaywrightError as chrome_error:
            executable = _system_browser_executable()
            if executable is not None:
                try:
                    return playwright.chromium.launch_persistent_context(
                        data_dir,
                        executable_path=str(executable),
                        **kwargs,
                    ), "system"
                except PlaywrightError as system_error:
                    raise PlaywrightError(
                        f"Playwright Chromium: {chromium_error}\nGoogle Chrome: {chrome_error}"
                        f"\nSystem browser: {system_error}"
                    ) from chromium_error
            raise PlaywrightError(
                f"Playwright Chromium: {chromium_error}\nGoogle Chrome: {chrome_error}"
                f"\nSystem browser: not found"
            ) from chromium_error


def _system_browser_executable(preferred: Optional[str] = None) -> Optional[Path]:
    candidates: dict[str, list[Path]] = {"chrome": [], "chromium": [], "brave": []}
    if sys.platform == "win32":
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(variable)
            if base:
                candidates["chrome"].append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
                candidates["brave"].append(
                    Path(base) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe"
                )
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates["chromium"].append(Path(local_app_data) / "Chromium" / "Application" / "chrome.exe")
        commands = {
            "chrome": ("chrome", "google-chrome", "google-chrome-stable"),
            "chromium": ("chromium", "chromium-browser"),
            "brave": ("brave", "brave-browser"),
        }
        for group, names in commands.items():
            candidates[group].extend(Path(value) for value in (shutil.which(name) for name in names) if value)
    elif sys.platform == "darwin":
        candidates["chrome"].append(Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
        candidates["chromium"].append(Path("/Applications/Chromium.app/Contents/MacOS/Chromium"))
        candidates["brave"].append(Path("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"))
    else:
        commands = {
            "chrome": ("google-chrome", "google-chrome-stable", "chrome"),
            "chromium": ("chromium", "chromium-browser"),
            "brave": ("brave-browser", "brave"),
        }
        for group, names in commands.items():
            candidates[group].extend(Path(value) for value in (shutil.which(name) for name in names) if value)
    aliases = {
        "chrome": "chrome",
        "google-chrome": "chrome",
        "google-chrome-stable": "chrome",
        "chromium": "chromium",
        "chromium-browser": "chromium",
        "brave": "brave",
        "brave-browser": "brave",
    }
    selected_group = aliases.get(preferred.lower()) if preferred else None
    if preferred and selected_group is None:
        return None
    groups = [selected_group] if selected_group else ["chrome", "chromium", "brave"]
    return next(
        (candidate for group in groups for candidate in candidates[group] if candidate.is_file()),
        None,
    )


def _controller(
    path: Path,
    data_dir: str,
    tabs: int,
    token: str,
    headless: bool,
    browser_channel: Optional[str] = None,
    window_width: Optional[int] = None,
    window_height: Optional[int] = None,
    start_urls: Optional[list[str]] = None,
) -> int:
    err = path.parent / "controller.error"
    initial_state = _read_state(path) or {}
    if browser_channel is None and isinstance(initial_state.get("browser_channel"), str):
        browser_channel = initial_state["browser_channel"]
    if window_width is None and type(initial_state.get("window_width")) is int:
        window_width = initial_state["window_width"]
    if window_height is None and type(initial_state.get("window_height")) is int:
        window_height = initial_state["window_height"]
    if start_urls is None and isinstance(initial_state.get("start_urls"), list):
        start_urls = [value for value in initial_state["start_urls"] if isinstance(value, str)]
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        _write_error(err, "playwright_unavailable", str(exc), redactions=(token,))
        return 2

    context = None
    channel = browser_channel or "chromium,chrome"
    server = None
    try:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        server.settimeout(0.5)
        port = server.getsockname()[1]
        with sync_playwright() as playwright:
            context, channel = _launch_context(
                playwright,
                data_dir,
                headless,
                channel_override=browser_channel,
                window_width=window_width,
                window_height=window_height,
            )
            try:
                urls = list(start_urls or [])
                target_pages = tabs

                while len(context.pages) < target_pages:
                    context.new_page()
                while len(context.pages) > target_pages:
                    context.pages[-1].close()

                for idx, url in enumerate(urls):
                    if idx < len(context.pages):
                        try:
                            context.pages[idx].goto(url)
                        except Exception:
                            pass

                _atomic_private_json(
                    path,
                    {
                        "protocol_version": RUNNING_STATE_PROTOCOL_VERSION,
                        "engine": "playwright",
                        "profile_id": Path(data_dir).parent.name,
                        "controller_pid": os.getpid(),
                        "pid": os.getpid(),
                        "controller_started_at": _utc_now(),
                        "port": port,
                        "token": token,
                        "tabs": len(context.pages),
                        "page_count": len(context.pages),
                        "channel": channel,
                        "status": "running",
                    },
                )
                _wait_for_close(server, context, token)
            finally:
                try:
                    context.close()
                except PlaywrightError:
                    pass
        _unlink_quietly(err)
        return 0
    except PlaywrightError as exc:
        _write_error(
            err,
            "browser_unavailable",
            str(exc),
            channel=channel,
            redactions=(token,),
        )
        return 2
    except Exception as exc:
        _write_error(
            err,
            "controller_error",
            str(exc),
            channel=channel,
            redactions=(token,),
        )
        return 2
    finally:
        if server is not None:
            server.close()
        _unlink_quietly(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--controller", type=Path, required=True)
    parser.add_argument("data_dir")
    parser.add_argument("tabs", type=int)
    parser.add_argument("token")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--browser-channel", type=str, default=None)
    parser.add_argument("--window-size", type=str, default=None)
    parser.add_argument("--url", action="append", default=[])
    args = parser.parse_args()

    width = None
    height = None
    if args.window_size:
        parts = args.window_size.split(",")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            width = int(parts[0])
            height = int(parts[1])

    raise SystemExit(
        _controller(
            args.controller,
            args.data_dir,
            args.tabs,
            args.token,
            args.headless,
            browser_channel=args.browser_channel,
            window_width=width,
            window_height=height,
            start_urls=args.url,
        )
    )


if __name__ == "__main__":
    main()
