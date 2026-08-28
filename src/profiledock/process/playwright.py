"""Playwright engine launcher lifecycle.

Starts the controller subprocess (``python -m profiledock.process_manager
--controller ...``), waits for it to publish a ready runtime state, and closes
it through the authenticated IPC channel.
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional
from uuid import uuid4

from .errors import BrowserLaunchError, ProfileRunningError
from .identity import _close_stderr, _stderr_message, _terminate_matching_process
from .state import (
    RUNNING_STATE_PROTOCOL_VERSION,
    StateDict,
    _read_error,
    _read_state,
    _unlink_quietly,
    _utc_now,
    _valid_state,
    _write_error,
    error_path,
    state_file_is_unreadable,
    state_path,
)


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
    # Late-bound so patches of profiledock.process_manager.is_running,
    # ._controller_available and ._stop_process keep applying.
    from profiledock.process_manager import _controller_available as _controller_available_impl
    from profiledock.process_manager import _stop_process as _stop_process_impl
    from profiledock.process_manager import is_running as _is_running_impl

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
    if _is_running_impl(data_dir, runtime_dir):
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
                if (
                    state
                    and _valid_state(state, str(profile_id_value))
                    and state.get("port")
                    and _controller_available_impl(state)
                ):
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
            _stop_process_impl(process)
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
        _unlink_quietly(path)
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
        _unlink_quietly(path)
        raise BrowserLaunchError(
            message,
            "controller_exited",
        )
    _stop_process_impl(process)
    _unlink_quietly(path)
    message = f"Controller startup timed out after {startup_timeout:g} seconds"
    _write_error(err, "controller_timeout", message, redactions=(token,))
    _close_stderr(process)
    raise BrowserLaunchError(
        message,
        "controller_timeout",
    )


def _close_playwright(path: Path, state: StateDict, timeout: float) -> None:
    # Late-bound so patches of profiledock.process_manager._atomic_private_json,
    # ._alive and ._is_matching_process keep applying.
    from profiledock.process_manager import _alive as _alive_impl
    from profiledock.process_manager import (
        _atomic_private_json as _atomic_private_json_impl,
    )
    from profiledock.process_manager import (
        _is_matching_process as _is_matching_process_impl,
    )

    port = int(state.get("port", 0))
    if not port:
        raise BrowserLaunchError("profile controller is not ready")
    token = state.get("token", "")
    state["closing"] = True
    state["status"] = "closing"
    try:
        _atomic_private_json_impl(path, state)
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
        browser_pid = int(state.get("browser_pid", 0) or 0)
        if browser_pid > 0:
            # Last-resort cleanup after a stuck close; the browser process is
            # only signalled when its identity matches the recorded one.
            _terminate_matching_process(
                browser_pid, state.get("browser_create_time"), min(max(timeout, 0.1), 5)
            )
            grace_deadline = time.monotonic() + min(max(timeout, 0.1), 5)
            while path.exists() and time.monotonic() < grace_deadline:
                time.sleep(0.05)
        if path.exists():
            if not _alive_impl(int(state.get("controller_pid", -1))):
                _unlink_quietly(path)
                raise ProfileRunningError("profile is not running", stopped=True)
            raise BrowserLaunchError("profile did not close within the timeout")
    if not close_sent:
        raise ProfileRunningError("profile is not running", stopped=True)

    # The controller removes running.json only after context.close() has
    # flushed persistent profile data. Wait for the controller (and browser)
    # processes to fully exit so a follow-up launch never races a dying
    # browser and no Chromium or controller processes survive the command.
    controller_pid = int(state.get("controller_pid", -1))
    if controller_pid > 0:
        while _alive_impl(controller_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
    browser_pid = int(state.get("browser_pid", 0) or 0)
    if browser_pid > 0 and _alive_impl(browser_pid):
        if not _is_matching_process_impl(
            browser_pid, state.get("browser_create_time"), require_verification=True
        ):
            # The recorded PID now belongs to an unrelated process; never signal it.
            return
        _terminate_matching_process(browser_pid, state.get("browser_create_time"), min(max(timeout, 0.1), 5))
