import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple
from uuid import uuid4

_MAX_ERROR_BYTES = 4096


class ProfileRunningError(Exception):
    pass


class BrowserLaunchError(Exception):
    def __init__(self, message: str, category: str = "browser_launch_failed") -> None:
        super().__init__(message)
        self.category = category


def state_path(data_dir: str) -> Path:
    return Path(data_dir).parent / "running.json"


def error_path(data_dir: str) -> Path:
    return Path(data_dir).parent / "controller.error"


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
        path.write_bytes(encoded)
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


def get_status(data_dir: str, clean_stale: bool = True) -> str:
    path = state_path(data_dir)
    err = error_path(data_dir)
    if path.exists():
        state = _read_state(path)
        if not state or not isinstance(state, dict):
            if clean_stale:
                path.unlink(missing_ok=True)
            return "stale"
        if state.get("closing"):
            pid = int(state.get("pid", -1))
            if pid > 0 and _alive(pid):
                return "closing"
            if clean_stale:
                path.unlink(missing_ok=True)
            return "stale"
        pid = int(state.get("pid", -1))
        if pid <= 0:
            return "starting"
        if not _alive(pid):
            if clean_stale:
                path.unlink(missing_ok=True)
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


def is_running(data_dir: str) -> bool:
    return get_status(data_dir, clean_stale=True) in ("starting", "running", "closing")


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


def start_controller(
    data_dir: str,
    tabs: int,
    headless: bool = False,
    startup_timeout: float = 30,
) -> Dict[str, Any]:
    if tabs < 1:
        raise ValueError("tab count must be at least 1")
    if not Path(data_dir).is_dir():
        raise BrowserLaunchError(
            "profile data directory is missing or invalid",
            "invalid_data_directory",
        )
    path = state_path(data_dir)
    err = error_path(data_dir)
    err.unlink(missing_ok=True)
    if is_running(data_dir):
        raise ProfileRunningError("profile is already running")
    token = uuid4().hex
    initial = {"pid": 0, "port": 0, "token": token, "tabs": tabs}
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(path), flags)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(initial, handle)
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
        state = _read_state(path) or initial
        state["pid"] = process.pid
        path.write_text(json.dumps(state), encoding="utf-8")
        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            state = _read_state(path)
            if state and state.get("port"):
                err.unlink(missing_ok=True)
                _close_stderr(process)
                return state
            if process.poll() is not None:
                break
            time.sleep(0.1)
    except OSError as exc:
        path.unlink(missing_ok=True)
        _write_error(err, "controller_spawn_failed", str(exc), redactions=(token,))
        raise BrowserLaunchError(str(exc), "controller_spawn_failed") from exc
    path.unlink(missing_ok=True)
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


def close_controller(data_dir: str, timeout: float = 15) -> None:
    path = state_path(data_dir)
    if not is_running(data_dir):
        path.unlink(missing_ok=True)
        raise ProfileRunningError("profile is not running")
    state = _read_state(path)
    if not state:
        raise ProfileRunningError("profile is not running")
    port = int(state.get("port", 0))
    if not port:
        raise BrowserLaunchError("profile controller is not ready")
    token = state.get("token", "")
    state["closing"] = True
    try:
        path.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass
    close_sent = False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3) as connection:
            connection.sendall(("close:" + token).encode("utf-8"))
        close_sent = True
    except OSError:
        pass
    deadline = time.monotonic() + timeout
    while path.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    if path.exists():
        if not _alive(int(state.get("pid", -1))):
            path.unlink(missing_ok=True)
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
            command = connection.recv(256).decode("utf-8")
            if command == "close:" + token:
                return


def _launch_context(playwright: Any, data_dir: str, headless: bool) -> Tuple[Any, str]:
    from playwright.sync_api import Error as PlaywrightError

    try:
        return playwright.chromium.launch_persistent_context(data_dir, headless=headless), "chromium"
    except PlaywrightError as chromium_error:
        try:
            return playwright.chromium.launch_persistent_context(data_dir, channel="chrome", headless=headless), "chrome"
        except PlaywrightError as chrome_error:
            raise PlaywrightError(
                f"Playwright Chromium: {chromium_error}\nGoogle Chrome: {chrome_error}"
            ) from chromium_error


def _controller(path: Path, data_dir: str, tabs: int, token: str, headless: bool) -> int:
    err = error_path(data_dir)
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
                path.write_text(
                    json.dumps(
                        {
                            "pid": os.getpid(),
                            "port": port,
                            "token": token,
                            "tabs": tabs,
                            "page_count": len(context.pages),
                            "channel": channel,
                        }
                    ),
                    encoding="utf-8",
                )
                _wait_for_close(server, context, token)
            finally:
                try:
                    context.close()
                except PlaywrightError:
                    pass
        err.unlink(missing_ok=True)
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
        path.unlink(missing_ok=True)


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
