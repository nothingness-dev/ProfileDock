import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4


class ProfileRunningError(Exception):
    pass


class BrowserLaunchError(Exception):
    pass


def state_path(data_dir: str) -> Path:
    return Path(data_dir).parent / "running.json"


def _alive(pid: int) -> bool:
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
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def is_running(data_dir: str) -> bool:
    path = state_path(data_dir)
    state = _read_state(path)
    if not state:
        if path.exists():
            path.unlink(missing_ok=True)
        return False
    if _alive(int(state.get("pid", -1))):
        return True
    path.unlink(missing_ok=True)
    return False


def start_controller(data_dir: str, tabs: int, headless: bool = False) -> Dict[str, Any]:
    if tabs < 1:
        raise ValueError("tab count must be at least 1")
    path = state_path(data_dir)
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
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        state = _read_state(path) or initial
        state["pid"] = process.pid
        path.write_text(json.dumps(state), encoding="utf-8")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            state = _read_state(path)
            if state and state.get("port"):
                return state
            if process.poll() is not None:
                break
            time.sleep(0.1)
    except OSError as exc:
        path.unlink(missing_ok=True)
        raise BrowserLaunchError(str(exc)) from exc
    path.unlink(missing_ok=True)
    raise BrowserLaunchError("Chromium or Google Chrome failed to launch")


def close_controller(data_dir: str, timeout: float = 15) -> None:
    path = state_path(data_dir)
    if not is_running(data_dir):
        raise ProfileRunningError("profile is not running")
    state = _read_state(path)
    if not state or not state.get("port"):
        raise BrowserLaunchError("profile controller is not ready")
    try:
        with socket.create_connection(("127.0.0.1", int(state["port"])), timeout=3) as connection:
            connection.sendall(("close:" + state["token"]).encode("utf-8"))
    except OSError as exc:
        raise BrowserLaunchError(f"could not contact profile controller: {exc}") from exc
    deadline = time.monotonic() + timeout
    while path.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    if path.exists():
        raise BrowserLaunchError("profile did not close within the timeout")


def _launch_context(playwright: Any, data_dir: str, headless: bool) -> Any:
    from playwright.sync_api import Error as PlaywrightError

    try:
        return playwright.chromium.launch_persistent_context(data_dir, headless=headless)
    except PlaywrightError as bundled_error:
        try:
            return playwright.chromium.launch_persistent_context(data_dir, channel="chrome", headless=headless)
        except PlaywrightError:
            raise bundled_error


def _controller(path: Path, data_dir: str, tabs: int, token: str, headless: bool) -> int:
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    context = None
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    server.settimeout(0.5)
    port = server.getsockname()[1]
    try:
        with sync_playwright() as playwright:
            context = _launch_context(playwright, data_dir, headless)
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
                    }
                ),
                encoding="utf-8",
            )
            closing = False
            while not closing:
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    continue
                with connection:
                    command = connection.recv(256).decode("utf-8")
                    if command == "close:" + token:
                        closing = True
            context.close()
        return 0
    except PlaywrightError:
        return 2
    finally:
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
