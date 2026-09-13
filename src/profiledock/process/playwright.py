import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from .errors import BrowserLaunchError, ProfileRunningError
from .identity import _terminate_matching_process
from .launch import prepare_runtime_dir, validate_launch_request
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


class _StderrCapture:
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self._process = process
        self._buffer = b""
        self._buffer_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        stream = self._process.stderr
        if stream is None:
            return

        def read_loop() -> None:
            try:
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        break
                    with self._buffer_lock:
                        self._buffer = (self._buffer + chunk)[-_MAX_STDERR_TAIL:]
            except (OSError, ValueError):
                pass

        self._thread = threading.Thread(target=read_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._process.poll() is not None and self._thread is not None:
            self._thread.join(timeout=1)

    def tail(self) -> bytes:
        with self._buffer_lock:
            return self._buffer


_MAX_STDERR_TAIL = 16 * 1024


def start_controller(
    data_dir: str,
    tabs: int,
    headless: bool = False,
    startup_timeout: float = 30,
    runtime_dir: Path | None = None,
    browser_channel: str | None = None,
    start_urls: list[str] | None = None,
    window_width: int | None = None,
    window_height: int | None = None,
    proxy: str | None = None,
    user_agent: str | None = None,
    locale: str | None = None,
    timezone: str | None = None,
) -> StateDict:

    from profiledock.process_manager import _controller_available as _controller_available_impl
    from profiledock.process_manager import (
        _get_process_create_time as _get_process_create_time_impl,
    )
    from profiledock.process_manager import _stop_process as _stop_process_impl
    from profiledock.process_manager import is_running as _is_running_impl

    validate_launch_request(data_dir, tabs, window_width, window_height, start_urls)

    path = state_path(data_dir, runtime_dir)
    err = error_path(data_dir, runtime_dir)
    prepare_runtime_dir(data_dir, runtime_dir)
    urls = list(start_urls or [])
    if state_file_is_unreadable(path):
        raise ProfileRunningError(
            "profile runtime state file is unreadable; run 'profiledock doctor --repair' to clean it up"
        )
    if _is_running_impl(data_dir, runtime_dir):
        raise ProfileRunningError("profile is already running")
    token = uuid4().hex
    launcher_pid = os.getpid()
    initial = {
        "protocol_version": RUNNING_STATE_PROTOCOL_VERSION,
        "engine": "playwright",
        "profile_id": Path(data_dir).parent.name,
        "controller_pid": 0,
        "controller_started_at": _utc_now(),
        "launcher_pid": launcher_pid,
        "launcher_create_time": _get_process_create_time_impl(launcher_pid),
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
    ]

    env = dict(os.environ)
    for key in ("TOKEN", "PROXY", "USER_AGENT", "LOCALE", "TIMEZONE"):
        env.pop("PROFILEDOCK_CONTROLLER_" + key, None)
    env["PROFILEDOCK_CONTROLLER_TOKEN"] = token
    if headless:
        command.append("--headless")
    if proxy:
        env["PROFILEDOCK_CONTROLLER_PROXY"] = proxy
    if user_agent:
        env["PROFILEDOCK_CONTROLLER_USER_AGENT"] = user_agent
    if locale:
        env["PROFILEDOCK_CONTROLLER_LOCALE"] = locale
    if timezone:
        env["PROFILEDOCK_CONTROLLER_TIMEZONE"] = timezone
    try:
        popen_kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.PIPE,
            "env": env,
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
            )
        else:
            popen_kwargs["start_new_session"] = True
        process = subprocess.Popen(command, **popen_kwargs)
        deadline = time.monotonic() + startup_timeout
        poll_interval = 0.02

        stderr_capture = _StderrCapture(process)
        stderr_capture.start()
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
                    stderr_capture.stop()
                    return state
                if process.poll() is not None:
                    break
                time.sleep(poll_interval)
                poll_interval = min(poll_interval * 1.5, 0.1)
        except BaseException:
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
        _stop_process_impl(process)
        stderr_capture.stop()
        _unlink_quietly(path)
        raise BrowserLaunchError(
            error_info["message"],
            str(error_info["error_type"]),
        )
    if process.poll() is not None:
        stderr_capture.stop()
        stderr = stderr_capture.tail().decode("utf-8", errors="replace").replace(token, "[redacted]").strip()
        message = f"Controller process exited unexpectedly (code {process.returncode})"
        if stderr:
            message = f"{message}: {stderr}"
        _write_error(err, "controller_exited", message, redactions=(token,))
        stderr_capture.stop()
        _unlink_quietly(path)
        raise BrowserLaunchError(
            message,
            "controller_exited",
        )
    _stop_process_impl(process)
    _unlink_quietly(path)
    message = f"Controller startup timed out after {startup_timeout:g} seconds"
    _write_error(err, "controller_timeout", message, redactions=(token,))
    stderr_capture.stop()
    raise BrowserLaunchError(
        message,
        "controller_timeout",
    )


def _close_playwright(path: Path, state: StateDict, timeout: float) -> None:

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
    controller_pid = int(state.get("controller_pid", -1))
    while path.exists() and time.monotonic() < deadline:
        if controller_pid > 0 and not _alive_impl(controller_pid):
            browser_pid = int(state.get("browser_pid", 0) or 0)
            if browser_pid > 0:
                _terminate_matching_process(
                    browser_pid,
                    state.get("browser_create_time"),
                    min(max(timeout, 0.1), 5),
                )
            _unlink_quietly(path)
            if close_sent:
                return
            raise ProfileRunningError("profile is not running", stopped=True)
        time.sleep(poll_interval)
        poll_interval = min(poll_interval * 1.5, 0.1)
    if path.exists():
        browser_pid = int(state.get("browser_pid", 0) or 0)
        if browser_pid > 0:
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

    if controller_pid > 0:
        while _alive_impl(controller_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
    browser_pid = int(state.get("browser_pid", 0) or 0)
    if browser_pid > 0 and _alive_impl(browser_pid):
        if not _is_matching_process_impl(
            browser_pid, state.get("browser_create_time"), require_verification=True
        ):
            return
        _terminate_matching_process(browser_pid, state.get("browser_create_time"), min(max(timeout, 0.1), 5))
