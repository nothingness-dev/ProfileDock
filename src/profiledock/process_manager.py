import argparse
import hmac
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple
from uuid import uuid4

_MAX_ERROR_BYTES = 4096
RUNNING_STATE_PROTOCOL_VERSION = 1
_MAX_COMMAND_BYTES = 512


class ProfileRunningError(Exception):
    pass


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


def _replace_with_retry(source: Path, target: Path, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            source.replace(target)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.02)


def _write_all(fd: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(fd, payload[offset:])
        if written < 1:
            raise OSError("write returned no data")
        offset += written


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


def _atomic_private_json(path: Path, value: Dict[str, Any]) -> None:
    _atomic_private_bytes(path, json.dumps(value).encode("utf-8"))


def _valid_state(value: Dict[str, Any], profile_id: Optional[str] = None) -> bool:
    if (
        type(value.get("protocol_version")) is not int
        or value["protocol_version"] != RUNNING_STATE_PROTOCOL_VERSION
    ):
        return False
    if profile_id is not None and value.get("profile_id") != profile_id:
        return False
    if not isinstance(value.get("token"), str) or len(value["token"]) < 32:
        return False
    if type(value.get("controller_pid")) is not int or type(value.get("port")) is not int:
        return False
    if not isinstance(value.get("controller_started_at"), str):
        return False
    try:
        started_at = datetime.fromisoformat(value["controller_started_at"])
    except (TypeError, ValueError):
        return False
    if started_at.tzinfo is None or started_at.utcoffset() is None:
        return False
    return True


def _valid_direct_state(value: Dict[str, Any], profile_id: str) -> bool:
    if value.get("engine") != "direct" or value.get("profile_id") != profile_id:
        return False
    if type(value.get("pid")) is not int or type(value.get("launcher_pid")) is not int:
        return False
    if type(value.get("tabs")) is not int or value["tabs"] < 1:
        return False
    if not isinstance(value.get("started_at"), str):
        return False
    try:
        started_at = datetime.fromisoformat(value["started_at"])
    except (TypeError, ValueError):
        return False
    return started_at.tzinfo is not None and started_at.utcoffset() is not None


def _upgrade_legacy_state(path: Path, value: Dict[str, Any], profile_id: str) -> Dict[str, Any]:
    if "protocol_version" in value:
        return value
    try:
        pid = int(value.get("pid", 0))
        port = int(value.get("port", 0))
    except (TypeError, ValueError):
        return value
    token = value.get("token")
    if pid < 1 or port < 1 or not isinstance(token, str) or len(token) < 32:
        return value
    upgraded = dict(value)
    try:
        modified_at = path.stat().st_mtime
    except OSError:
        return value
    upgraded.update(
        {
            "protocol_version": RUNNING_STATE_PROTOCOL_VERSION,
            "profile_id": profile_id,
            "controller_pid": pid,
            "controller_started_at": datetime.fromtimestamp(
                modified_at, timezone.utc
            ).isoformat(),
            "status": "running",
            "legacy_controller": True,
        }
    )
    try:
        _atomic_private_json(path, upgraded)
    except OSError:
        pass
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
    base: Dict[str, Any] = {"error_type": error_type}
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


def _read_error(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "error_type" in data:
            return data
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return None


def _alive(pid: int) -> bool:
    if pid < 1:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        exit_code = wintypes.DWORD()
        try:
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _read_state(path: Path) -> Optional[Dict[str, Any]]:
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


def get_status(data_dir: str, clean_stale: bool = True, runtime_dir: Optional[Path] = None) -> str:
    path = state_path(data_dir, runtime_dir)
    err = error_path(data_dir, runtime_dir)
    if path.exists():
        state = _read_state(path)
        if not state or not isinstance(state, dict):
            if clean_stale:
                _unlink_quietly(path)
            return "stale"
        if state.get("engine") == "direct":
            if not _valid_direct_state(state, Path(data_dir).parent.name):
                if clean_stale:
                    _unlink_quietly(path)
                return "stale"
            pid = state["pid"]
            if pid > 0 and _alive(pid):
                if state.get("closing"):
                    return "closing"
                return "running"
            launcher_pid = state["launcher_pid"]
            if pid == 0 and launcher_pid > 0 and _alive(launcher_pid):
                return "starting"
            if clean_stale:
                _unlink_quietly(path)
            return "stale"
        state = _upgrade_legacy_state(path, state, Path(data_dir).parent.name)
        if not state or not _valid_state(state, Path(data_dir).parent.name):
            if clean_stale:
                _unlink_quietly(path)
            return "stale"
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
    return get_status(data_dir, clean_stale=True, runtime_dir=runtime_dir) in ("starting", "running", "closing")


def _stop_process(process: subprocess.Popen[Any], timeout: float = 5) -> None:
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
        process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout)


def _stderr_message(process: subprocess.Popen[Any], token: str) -> str:
    if process.stderr is None:
        return ""
    try:
        output = process.stderr.read(_MAX_ERROR_BYTES)
    except OSError:
        return ""
    if isinstance(output, bytes):
        message = output.decode("utf-8", errors="replace")
    else:
        message = output
    return message.replace(token, "[redacted]").strip()


def _close_stderr(process: subprocess.Popen[Any]) -> None:
    if process.stderr is not None:
        process.stderr.close()


def start_direct_chrome(
    data_dir: str,
    tabs: int,
    runtime_dir: Optional[Path] = None,
    executable_path: Optional[Path] = None,
) -> Dict[str, Any]:
    if tabs < 1:
        raise ValueError("tab count must be at least 1")
    if not Path(data_dir).is_dir():
        raise BrowserLaunchError(
            "profile data directory is missing or invalid",
            "invalid_data_directory",
        )
    browser_bin = executable_path if executable_path is not None else _system_browser_executable()
    if browser_bin is None or not Path(browser_bin).exists():
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

    if is_running(data_dir, runtime_dir=runtime_dir):
        raise ProfileRunningError("profile is already running")

    started_at = _utc_now()
    initial = {
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

    args = [
        str(browser_bin),
        f"--user-data-dir={data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        *["about:blank" for _ in range(tabs)],
    ]

    popen_kwargs: Dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        )
    else:
        popen_kwargs["start_new_session"] = True

    try:
        process = subprocess.Popen(args, **popen_kwargs)
    except OSError as exc:
        _unlink_quietly(path)
        _write_error(err, "browser_launch_failed", str(exc))
        raise BrowserLaunchError(str(exc), "browser_launch_failed") from exc

    state = {
        "profile_id": initial["profile_id"],
        "pid": process.pid,
        "launcher_pid": initial["launcher_pid"],
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
) -> Dict[str, Any]:
    if tabs < 1:
        raise ValueError("tab count must be at least 1")
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
    if is_running(data_dir, runtime_dir):
        raise ProfileRunningError("profile is already running")
    token = uuid4().hex
    initial = {
        "protocol_version": RUNNING_STATE_PROTOCOL_VERSION,
        "profile_id": Path(data_dir).parent.name,
        "controller_pid": 0,
        "controller_started_at": _utc_now(),
        "launcher_pid": os.getpid(),
        "port": 0,
        "token": token,
        "tabs": tabs,
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

    command = [sys.executable, "-m", "profiledock.process_manager", "--controller", str(path), data_dir, str(tabs), token]
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
        while time.monotonic() < deadline:
            state = _read_state(path)
            if state and _valid_state(state, initial["profile_id"]) and state.get("port"):
                _unlink_quietly(err)
                _close_stderr(process)
                return state
            if process.poll() is not None:
                break
            time.sleep(0.1)
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


def close_controller(data_dir: str, timeout: float = 15, runtime_dir: Optional[Path] = None) -> None:
    path = state_path(data_dir, runtime_dir)
    if not is_running(data_dir, runtime_dir):
        _unlink_quietly(path)
        raise ProfileRunningError("profile is not running")
    state = _read_state(path)
    if not state:
        raise ProfileRunningError("profile is not running")

    if state.get("engine") == "direct":
        state["closing"] = True
        state["status"] = "closing"
        try:
            _atomic_private_json(path, state)
        except OSError:
            pass
        pid = int(state.get("pid", -1))
        if pid > 0 and _alive(pid):
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                try:
                    os.kill(pid, signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    pass
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if not _alive(pid):
                    break
                time.sleep(0.1)
            if _alive(pid):
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                else:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except (OSError, ProcessLookupError):
                        pass
                force_deadline = time.monotonic() + min(max(timeout, 0.1), 2)
                while time.monotonic() < force_deadline and _alive(pid):
                    time.sleep(0.05)
            if _alive(pid):
                raise BrowserLaunchError("browser process did not close within the timeout")
        _unlink_quietly(path)
        return

    state = _upgrade_legacy_state(path, state, Path(data_dir).parent.name)
    port = int(state.get("port", 0))
    if not port:
        raise BrowserLaunchError("profile controller is not ready")
    token = state.get("token", "")
    if not _valid_state(state, Path(data_dir).parent.name):
        _unlink_quietly(path)
        raise ProfileRunningError("profile is not running")
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
    while path.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    if path.exists():
        if not _alive(int(state.get("controller_pid", -1))):
            _unlink_quietly(path)
            raise ProfileRunningError("profile is not running")
        raise BrowserLaunchError("profile did not close within the timeout")
    if not close_sent:
        raise ProfileRunningError("profile is not running")


def _context_alive(context: Any) -> bool:
    try:
        return bool(context.pages)
    except Exception:
        return False


def _wait_for_close(server: socket.socket, context: Any, token: str) -> None:
    while _context_alive(context):
        try:
            connection, _ = server.accept()
        except socket.timeout:
            continue
        with connection:
            command = connection.recv(_MAX_COMMAND_BYTES + 1)
            if len(command) > _MAX_COMMAND_BYTES:
                connection.sendall(b"error\n")
                continue
            supplied = command.decode("utf-8", errors="replace").rstrip("\r\n")
            expected = "close:" + token
            if hmac.compare_digest(supplied, expected):
                connection.sendall(b"ok\n")
                return
            connection.sendall(b"error\n")


def _launch_context(playwright: Any, data_dir: str, headless: bool) -> Tuple[Any, str]:
    from playwright.sync_api import Error as PlaywrightError

    try:
        return playwright.chromium.launch_persistent_context(data_dir, headless=headless), "chromium"
    except PlaywrightError as chromium_error:
        try:
            return playwright.chromium.launch_persistent_context(data_dir, channel="chrome", headless=headless), "chrome"
        except PlaywrightError as chrome_error:
            executable = _system_browser_executable()
            if executable is not None:
                try:
                    return playwright.chromium.launch_persistent_context(
                        data_dir,
                        executable_path=str(executable),
                        headless=headless,
                    ), "system"
                except PlaywrightError as system_error:
                    raise PlaywrightError(
                        f"Playwright Chromium: {chromium_error}\nGoogle Chrome: {chrome_error}\nSystem browser: {system_error}"
                    ) from chromium_error
            raise PlaywrightError(
                f"Playwright Chromium: {chromium_error}\nGoogle Chrome: {chrome_error}\nSystem browser: not found"
            ) from chromium_error


def _system_browser_executable() -> Optional[Path]:
    candidates = []
    if sys.platform == "win32":
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(variable)
            if base:
                candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
                candidates.append(Path(base) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe")
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(Path(local_app_data) / "Chromium" / "Application" / "chrome.exe")
        candidates.extend(
            Path(value)
            for value in filter(
                None,
                (
                    shutil.which("chrome"),
                    shutil.which("google-chrome"),
                    shutil.which("chromium"),
                    shutil.which("chromium-browser"),
                    shutil.which("brave"),
                    shutil.which("brave-browser"),
                ),
            )
        )
    elif sys.platform == "darwin":
        candidates.append(Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
        candidates.append(Path("/Applications/Chromium.app/Contents/MacOS/Chromium"))
        candidates.append(Path("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"))
    else:
        candidates.extend(
            Path(value)
            for value in filter(
                None,
                (
                    shutil.which("google-chrome"),
                    shutil.which("google-chrome-stable"),
                    shutil.which("chromium"),
                    shutil.which("chromium-browser"),
                    shutil.which("brave-browser"),
                    shutil.which("brave"),
                ),
            )
        )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _controller(path: Path, data_dir: str, tabs: int, token: str, headless: bool) -> int:
    err = path.parent / "controller.error"
    try:
        from playwright.sync_api import Error as PlaywrightError, sync_playwright
    except ImportError as exc:
        _write_error(err, "playwright_unavailable", str(exc), redactions=(token,))
        return 2

    context = None
    channel = "chromium,chrome"
    server = None
    try:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        server.settimeout(0.5)
        port = server.getsockname()[1]
        with sync_playwright() as playwright:
            context, channel = _launch_context(playwright, data_dir, headless)
            try:
                while len(context.pages) > tabs:
                    context.pages[-1].close()
                while len(context.pages) < tabs:
                    context.new_page()
                _atomic_private_json(
                    path,
                    {
                        "protocol_version": RUNNING_STATE_PROTOCOL_VERSION,
                        "profile_id": Path(data_dir).parent.name,
                        "controller_pid": os.getpid(),
                        "pid": os.getpid(),
                        "controller_started_at": _utc_now(),
                        "port": port,
                        "token": token,
                        "tabs": tabs,
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
    args = parser.parse_args()
    raise SystemExit(_controller(args.controller, args.data_dir, args.tabs, args.token, args.headless))


if __name__ == "__main__":
    main()
