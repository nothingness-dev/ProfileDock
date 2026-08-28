"""Controller subprocess entry point (IPC server side).

This module runs inside the spawned controller process
(``python -m profiledock.process_manager --controller ...``). It launches the
Playwright context, serves the authenticated loopback IPC protocol, and writes
the ready/error state files the launcher polls.
"""

import argparse
import hmac
import json
import os
import socket
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from .identity import _find_browser_pid
from .ipc import _IPC_COMMANDS, _MAX_COMMAND_BYTES
from .state import (
    RUNNING_STATE_PROTOCOL_VERSION,
    _read_state,
    _unlink_quietly,
    _utc_now,
    _write_error,
)

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Playwright


def _context_alive(context: "BrowserContext") -> bool:
    try:
        return bool(context.pages)
    except Exception:
        return False


def _execute_ipc_command(
    cmd_obj: dict[str, Any], context: "BrowserContext", token: str
) -> tuple[dict[str, Any], bool]:
    """Execute a parsed JSON-RPC command against active browser context.

    Returns (response_dict, should_exit_loop).
    """
    req_token = cmd_obj.get("token", "")
    if not isinstance(req_token, str) or not hmac.compare_digest(req_token, token):
        return ({"status": "error", "message": "unauthorized command token"}, False)

    cmd = cmd_obj.get("cmd", "")
    args = cmd_obj.get("args", {})
    if not isinstance(cmd, str) or cmd not in _IPC_COMMANDS:
        return ({"status": "error", "message": f"unknown command '{cmd}'"}, False)
    if not isinstance(args, dict):
        return ({"status": "error", "message": "command arguments must be an object"}, False)

    if cmd == "probe":
        return ({"status": "ok"}, False)

    if cmd == "close":
        return ({"status": "ok"}, True)

    if cmd == "tabs":
        pages_info = []
        for idx, page in enumerate(context.pages):
            try:
                title = page.title()
            except Exception:
                title = ""
            pages_info.append({"index": idx, "url": page.url, "title": title})
        return ({"status": "ok", "tabs": pages_info}, False)

    if cmd == "open_tab":
        url = args.get("url", "about:blank")
        if not isinstance(url, str):
            return ({"status": "error", "message": "URL must be a string"}, False)
        try:
            from ..validation import validate_url

            validate_url(url)
            page = context.new_page()
            if url and url != "about:blank":
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
            return (
                {
                    "status": "ok",
                    "tab": {
                        "index": len(context.pages) - 1,
                        "url": page.url,
                        "title": page.title(),
                    },
                },
                False,
            )
        except Exception as exc:
            return ({"status": "error", "message": str(exc)}, False)

    if cmd == "close_tab":
        index = args.get("index")
        if type(index) is not int or not (0 <= index < len(context.pages)):
            return ({"status": "error", "message": f"tab index out of range: {index}"}, False)
        try:
            context.pages[index].close()
            return ({"status": "ok", "remaining_tabs": len(context.pages)}, False)
        except Exception as exc:
            return ({"status": "error", "message": str(exc)}, False)

    if cmd == "read_page":
        tab_index = args.get("tab", 0)
        url = args.get("url")
        if url is not None and not isinstance(url, str):
            return ({"status": "error", "message": "URL must be a string or null"}, False)
        if url:
            try:
                from ..validation import validate_url

                validate_url(url)
            except Exception as exc:
                return ({"status": "error", "message": str(exc)}, False)
        if not context.pages:
            context.new_page()
        if type(tab_index) is not int or not (0 <= tab_index < len(context.pages)):
            return ({"status": "error", "message": f"tab index out of range: {tab_index}"}, False)
        page = context.pages[tab_index]
        try:
            if url:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
            html = page.content()
            from ..page_reader import extract_page_markdown

            extracted = extract_page_markdown(html, base_url=page.url)
            return (
                {
                    "status": "ok",
                    "url": page.url,
                    "title": extracted["title"] or page.title(),
                    "content": extracted["content"],
                    "links": extracted["links"],
                },
                False,
            )
        except Exception as exc:
            return ({"status": "error", "message": str(exc)}, False)

    if cmd == "eval":
        script = args.get("script", "")
        tab_index = args.get("tab", 0)
        if not isinstance(script, str) or not script:
            return ({"status": "error", "message": "script expression must not be empty"}, False)
        if not context.pages:
            context.new_page()
        if type(tab_index) is not int or not (0 <= tab_index < len(context.pages)):
            return ({"status": "error", "message": f"tab index out of range: {tab_index}"}, False)
        page = context.pages[tab_index]
        session = None
        try:
            session = context.new_cdp_session(page)
            evaluation = session.send(
                "Runtime.evaluate",
                {
                    "expression": script,
                    "awaitPromise": True,
                    "returnByValue": True,
                    "timeout": 10000,
                },
            )
            exception = evaluation.get("exceptionDetails")
            if isinstance(exception, dict):
                detail = exception.get("text") or "JavaScript evaluation failed"
                thrown = exception.get("exception")
                if isinstance(thrown, dict):
                    description = thrown.get("description") or thrown.get("value")
                    if description:
                        detail = f"{detail}: {description}"
                return ({"status": "error", "message": str(detail)}, False)
            remote = evaluation.get("result", {})
            if not isinstance(remote, dict):
                return ({"status": "error", "message": "invalid JavaScript result"}, False)
            result = remote.get("value")
            if "unserializableValue" in remote:
                result = remote["unserializableValue"]
            return ({"status": "ok", "result": result}, False)
        except Exception as exc:
            return ({"status": "error", "message": str(exc)}, False)
        finally:
            if session is not None:
                try:
                    session.detach()
                except Exception:
                    pass

    if cmd == "cookies":
        urls = args.get("urls")
        try:
            if urls is not None:
                if not isinstance(urls, list) or not all(isinstance(url, str) for url in urls):
                    return ({"status": "error", "message": "cookie URLs must be a list of strings"}, False)
                from ..validation import validate_url

                for url in urls:
                    validate_url(url)
            cookie_list = context.cookies(urls) if urls else context.cookies()
            return ({"status": "ok", "cookies": cookie_list}, False)
        except Exception as exc:
            return ({"status": "error", "message": str(exc)}, False)

    return ({"status": "error", "message": f"unknown command '{cmd}'"}, False)


def _encode_ipc_response(response: dict[str, Any]) -> bytes:
    # Late-bound so patches of profiledock.process_manager._MAX_RESPONSE_BYTES keep applying.
    from profiledock.process_manager import _MAX_RESPONSE_BYTES as _max_response_bytes

    encoded = (json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8")
    if len(encoded) <= _max_response_bytes:
        return encoded
    return b'{"status":"error","message":"controller response exceeds the maximum size"}\n'


def _wait_for_close(server: socket.socket, context: "BrowserContext", token: str) -> None:
    while _context_alive(context):
        try:
            connection, _ = server.accept()
        except (socket.timeout, OSError):
            continue
        with connection:
            try:
                connection.settimeout(30.0)
                command_raw = b""
                while True:
                    chunk = connection.recv(_MAX_COMMAND_BYTES)
                    if not chunk:
                        break
                    command_raw += chunk
                    if b"\n" in chunk:
                        break
                    if len(command_raw) > _MAX_COMMAND_BYTES:
                        break
            except (socket.timeout, OSError):
                continue
            if len(command_raw) > _MAX_COMMAND_BYTES or not command_raw:
                try:
                    connection.sendall(b"error\n")
                except OSError:
                    pass
                continue
            supplied = command_raw.decode("utf-8", errors="replace").strip()

            if supplied.startswith("{") and supplied.endswith("}"):
                try:
                    cmd_obj = json.loads(supplied)
                    resp, should_exit = _execute_ipc_command(cmd_obj, context, token)
                    connection.sendall(_encode_ipc_response(resp))
                    if should_exit:
                        return
                    continue
                except Exception as exc:
                    try:
                        err_resp = {"status": "error", "message": str(exc)}
                        connection.sendall(_encode_ipc_response(err_resp))
                    except OSError:
                        pass
                    continue

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
    except PlaywrightError as error:
        raise PlaywrightError(
            f"Playwright Chromium is not available ({error}). "
            "Run 'playwright install chromium', or switch this profile to the "
            "direct engine to use an installed Google Chrome or Chromium."
        ) from error


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
    # Late-bound so patches of profiledock.process_manager._atomic_private_json
    # and ._get_process_create_time keep applying.
    from profiledock.process_manager import (
        _atomic_private_json as _atomic_private_json_impl,
    )
    from profiledock.process_manager import (
        _get_process_create_time as _get_process_create_time_impl,
    )

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

                browser_pid = _find_browser_pid(os.getpid())
                _atomic_private_json_impl(
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
                        "browser_pid": browser_pid,
                        "browser_create_time": _get_process_create_time_impl(browser_pid)
                        if browser_pid > 0
                        else None,
                        "headless": bool(headless),
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
            start_urls=args.url or None,
        )
    )
