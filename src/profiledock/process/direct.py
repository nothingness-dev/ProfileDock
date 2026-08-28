"""Direct Chrome engine lifecycle.

Launches a system Chrome/Chromium binary detached from the launcher process
and records its identity (PID plus process creation time) so later closes can
refuse to signal a recycled PID.
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

from ..browser_detection import system_browser_executable as _system_browser_impl
from .errors import BrowserLaunchError, ProfileRunningError
from .identity import _signal_posix_process_group
from .state import (
    RUNNING_STATE_PROTOCOL_VERSION,
    StateDict,
    _unlink_quietly,
    _utc_now,
    _write_error,
    error_path,
    state_file_is_unreadable,
    state_path,
)


def _system_browser_executable(preferred: Optional[str] = None) -> Optional[Path]:
    """Thin delegation kept so callers and tests can patch this name."""
    return _system_browser_impl(preferred)


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
    # Late-bound so patches of profiledock.process_manager._system_browser_executable,
    # .is_running, ._get_process_create_time, ._stop_process and
    # ._atomic_private_json keep applying.
    from profiledock.process_manager import (
        _atomic_private_json as _atomic_private_json_impl,
    )
    from profiledock.process_manager import (
        _get_process_create_time as _get_process_create_time_impl,
    )
    from profiledock.process_manager import (
        _stop_process as _stop_process_impl,
    )
    from profiledock.process_manager import (
        _system_browser_executable as _system_browser_executable_impl,
    )
    from profiledock.process_manager import is_running as _is_running_impl

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
    browser_bin = executable_path if executable_path is not None else _system_browser_executable_impl(browser)
    if browser_bin is None or not Path(browser_bin).is_file():
        raise BrowserLaunchError(
            "Google Chrome or Chromium executable not found on system",
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
    if _is_running_impl(data_dir, runtime_dir=runtime_dir):
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
        "--disable-background-mode",
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
    proc_create_time = _get_process_create_time_impl(process.pid)
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
        _atomic_private_json_impl(path, state)
    except OSError as exc:
        try:
            _stop_process_impl(process)
        finally:
            _unlink_quietly(path)
        _write_error(err, "state_write_failed", str(exc))
        raise BrowserLaunchError(str(exc), "state_write_failed") from exc
    return state


def _close_direct(path: Path, state: StateDict, timeout: float) -> None:
    # Late-bound so patches of profiledock.process_manager._atomic_private_json,
    # ._alive and ._is_matching_process keep applying.
    from profiledock.process_manager import _alive as _alive_impl
    from profiledock.process_manager import (
        _atomic_private_json as _atomic_private_json_impl,
    )
    from profiledock.process_manager import (
        _is_matching_process as _is_matching_process_impl,
    )

    state["closing"] = True
    state["status"] = "closing"
    try:
        _atomic_private_json_impl(path, state)
    except OSError:
        pass
    pid = int(state.get("pid", -1))
    expected_create_time = state.get("process_create_time")
    if pid > 0 and _alive_impl(pid):
        if not _is_matching_process_impl(pid, expected_create_time, require_verification=True):
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
            if not _alive_impl(pid):
                break
            time.sleep(poll_interval)
            poll_interval = min(poll_interval * 1.5, 0.1)
        if _alive_impl(pid):
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
        if _alive_impl(pid):
            raise BrowserLaunchError("browser process did not close within the timeout")
    _unlink_quietly(path)
