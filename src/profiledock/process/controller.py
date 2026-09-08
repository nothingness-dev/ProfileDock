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
import queue
import socket
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .identity import _find_browser_pid
from .ipc import _IPC_COMMANDS
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
                from ..validation import validate_cookie_url_filter

                for url in urls:
                    validate_cookie_url_filter(url)

            if urls is not None and not urls:
                cookie_list: list[Any] = []
            elif urls:
                cookie_list = context.cookies(urls)
            else:
                cookie_list = context.cookies()
            return ({"status": "ok", "cookies": cookie_list}, False)
        except Exception as exc:
            return ({"status": "error", "message": str(exc)}, False)

    if cmd == "set_cookies":
        set_cookies = args.get("set_cookies")
        if not isinstance(set_cookies, list) or not all(isinstance(c, dict) for c in set_cookies):
            return ({"status": "error", "message": "set_cookies must be a list of cookie objects"}, False)
        for cookie in set_cookies:
            if not str(cookie.get("name", "")).strip() or not isinstance(cookie.get("value"), str):
                return (
                    {"status": "error", "message": "each cookie needs a non-empty name and a string value"},
                    False,
                )
            if not cookie.get("domain") and not cookie.get("url"):
                return (
                    {
                        "status": "error",
                        "message": f"cookie '{cookie.get('name')}' needs a 'domain' or 'url'",
                    },
                    False,
                )
        try:
            context.add_cookies(set_cookies)
            total = len(context.cookies())
            return ({"status": "ok", "added": len(set_cookies), "total_cookies": total}, False)
        except Exception as exc:
            return ({"status": "error", "message": str(exc)}, False)

    if cmd == "screenshot":
        tab_index = args.get("tab", 0)
        url = args.get("url")
        output_path = args.get("output", "")
        full_page = bool(args.get("full_page", False))
        if not isinstance(output_path, str) or not output_path.strip():
            return ({"status": "error", "message": "output path must be a non-empty string"}, False)
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
            if not full_page:
                page.bring_to_front()
            path = output_path.strip()
            page.screenshot(path=path, full_page=full_page)
            size = 0
            try:
                size = int(os.path.getsize(path))
            except OSError:
                pass
            return (
                {
                    "status": "ok",
                    "output": path,
                    "url": page.url,
                    "title": page.title(),
                    "bytes": size,
                },
                False,
            )
        except Exception as exc:
            return ({"status": "error", "message": str(exc)}, False)

    if cmd == "pdf":
        tab_index = args.get("tab", 0)
        url = args.get("url")
        output_path = args.get("output", "")
        if not isinstance(output_path, str) or not output_path.strip():
            return ({"status": "error", "message": "output path must be a non-empty string"}, False)
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
            path = output_path.strip()
            page.pdf(path=path)
            size = 0
            try:
                size = int(os.path.getsize(path))
            except OSError:
                pass
            return (
                {
                    "status": "ok",
                    "output": path,
                    "url": page.url,
                    "title": page.title(),
                    "bytes": size,
                },
                False,
            )
        except Exception as exc:
            message = str(exc)
            if "only supported for Headless" in message or "PDF generation" in message:
                message = "PDF export requires a headless Chromium session; close the profile and retry"
            return ({"status": "error", "message": message}, False)

    return ({"status": "error", "message": f"unknown command '{cmd}'"}, False)


def _encode_ipc_response(response: dict[str, Any]) -> bytes:

    from profiledock.process_manager import _MAX_RESPONSE_BYTES as _max_response_bytes

    encoded = (json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8")
    if len(encoded) <= _max_response_bytes:
        return encoded
    return b'{"status":"error","message":"controller response exceeds the maximum size"}\n'


def _wait_for_close(
    server: socket.socket,
    context: "BrowserContext",
    token: str,
    startup: Callable[[], None] | None = None,
) -> None:
    """Serve the IPC protocol until a close command or browser death.

    A listener thread accepts connections immediately and answers probe and
    close lines without touching the browser, so local liveness checks never
    starve behind a long command or the startup navigation. JSON commands
    that need the browser context are handed to the main thread (the sync
    Playwright API is single-threaded) through a queue.
    """
    from profiledock.process_manager import _MAX_COMMAND_BYTES as max_command_bytes

    command_queue: queue.Queue[tuple[socket.socket, str] | None] = queue.Queue()
    shutdown = threading.Event()
    workers: list[threading.Thread] = []

    def serve(connection: socket.socket) -> None:
        try:
            connection.settimeout(30.0)
            command_raw = b""
            while True:
                chunk = connection.recv(max_command_bytes)
                if not chunk:
                    break
                command_raw += chunk
                if b"\n" in chunk:
                    break
                if len(command_raw) > max_command_bytes:
                    break
        except (TimeoutError, OSError):
            _close_quietly(connection)
            return
        if len(command_raw) > max_command_bytes or not command_raw:
            _send_line(connection, b"error\n")
            return
        supplied = command_raw.decode("utf-8", errors="replace").strip()

        if supplied.startswith("{") and supplied.endswith("}"):
            try:
                cmd_obj = json.loads(supplied)
            except json.JSONDecodeError as exc:
                _send_line(
                    connection,
                    _encode_ipc_response({"status": "error", "message": str(exc)}),
                )
                return
            cmd = cmd_obj.get("cmd")
            req_token = cmd_obj.get("token")
            if not isinstance(req_token, str) or not hmac.compare_digest(
                req_token.encode("utf-8"), token.encode("utf-8")
            ):
                _send_line(connection, b'{"status":"error","message":"unauthorized command token"}\n')
                return
            if cmd == "probe":
                _send_line(connection, b'{"status":"ok"}\n')
                return
            if cmd == "close":
                _send_line(connection, b'{"status":"ok"}\n')
                shutdown.set()
                command_queue.put(None)
                return

            command_queue.put((connection, supplied))
            return

        matched = None
        for candidate in ("probe:" + token, "close:" + token):
            try:
                if hmac.compare_digest(supplied, candidate):
                    matched = candidate
                    break
            except TypeError:
                continue
        if matched is None:
            _send_line(connection, b"error\n")
        elif matched.startswith("probe:"):
            _send_line(connection, b"ok\n")
        else:
            _send_line(connection, b"ok\n")
            shutdown.set()
            command_queue.put(None)

    def listener() -> None:
        while not shutdown.is_set():
            try:
                connection, _ = server.accept()
            except TimeoutError:
                continue
            except OSError:
                shutdown.set()
                command_queue.put(None)
                return
            workers[:] = [worker for worker in workers if worker.is_alive()]
            if len(workers) >= 32 or command_queue.qsize() >= 32:
                _close_quietly(connection)
                continue
            worker = threading.Thread(target=serve, args=(connection,), daemon=True)
            workers.append(worker)
            worker.start()

    def listener_guarded() -> None:
        try:
            listener()
        except BaseException:
            if not shutdown.is_set():
                shutdown.set()
                command_queue.put(None)

    listener_thread = threading.Thread(target=listener_guarded, daemon=True)
    listener_thread.start()
    try:
        if startup is not None:
            startup()
        while not shutdown.is_set() and _context_alive(context):
            try:
                item = command_queue.get(timeout=0.5)
            except queue.Empty:
                pages = context.pages
                if not pages:
                    return
                page = pages[0]
                pump = getattr(page, "wait_for_timeout", None)
                if pump is not None:
                    try:
                        pump(50)
                    except Exception:
                        pass
                continue
            if item is None:
                return
            connection, supplied = item
            try:
                cmd_obj = json.loads(supplied)
                resp, should_exit = _execute_ipc_command(cmd_obj, context, token)
                _send_line(connection, _encode_ipc_response(resp))
                if should_exit:
                    return
            except Exception as exc:
                _send_line(
                    connection,
                    _encode_ipc_response({"status": "error", "message": str(exc)}),
                )
    finally:
        shutdown.set()
        try:
            server.close()
        except (OSError, AttributeError):
            pass
        listener_thread.join(timeout=0.6)
        for worker in workers:
            worker.join(timeout=0.05)
        while True:
            try:
                pending = command_queue.get_nowait()
            except queue.Empty:
                break
            if pending is not None:
                _close_quietly(pending[0])


def _close_quietly(connection: socket.socket) -> None:
    try:
        connection.close()
    except (OSError, AttributeError):
        pass


def _send_line(connection: socket.socket, payload: bytes) -> None:
    try:
        connection.sendall(payload)
    except OSError:
        pass
    _close_quietly(connection)


def _playwright_proxy_options(proxy_url: str | None) -> dict[str, Any]:
    """Map a validated proxy URL onto Playwright's proxy option.

    Credentials embedded in the URL are split out into username/password;
    they are never logged — _write_error redacts the token, and the proxy
    string itself must be run through cli_support.redact_proxy before display.
    """
    if not proxy_url:
        return {}
    from urllib.parse import unquote, urlparse

    parsed = urlparse(proxy_url)
    options: dict[str, Any] = {"server": f"{parsed.scheme}://{parsed.hostname}"}
    if parsed.port:
        options["server"] = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
    if parsed.username:
        options["username"] = unquote(parsed.username)
    if parsed.password:
        options["password"] = unquote(parsed.password)
    return options


def _launch_context(
    playwright: "Playwright",
    data_dir: str,
    headless: bool,
    channel_override: str | None = None,
    window_width: int | None = None,
    window_height: int | None = None,
    proxy: str | None = None,
    user_agent: str | None = None,
    locale: str | None = None,
    timezone: str | None = None,
) -> tuple["BrowserContext", str]:
    from playwright.sync_api import Error as PlaywrightError

    kwargs: dict[str, Any] = {"headless": headless}
    if window_width is not None and window_height is not None:
        kwargs["viewport"] = {"width": window_width, "height": window_height}
        kwargs["args"] = [f"--window-size={window_width},{window_height}"]
    proxy_options = _playwright_proxy_options(proxy)
    if proxy_options:
        kwargs["proxy"] = proxy_options
    if user_agent:
        kwargs["user_agent"] = user_agent
    if locale:
        kwargs["locale"] = locale
    if timezone:
        kwargs["timezone_id"] = timezone

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
    browser_channel: str | None = None,
    window_width: int | None = None,
    window_height: int | None = None,
    start_urls: list[str] | None = None,
    proxy: str | None = None,
    user_agent: str | None = None,
    locale: str | None = None,
    timezone: str | None = None,
    _install_signal_handlers: bool = True,
) -> int:

    from profiledock.process_manager import (
        _atomic_private_json as _atomic_private_json_impl,
    )
    from profiledock.process_manager import (
        _get_process_create_time as _get_process_create_time_impl,
    )

    err = path.parent / "controller.error"

    if _install_signal_handlers:
        import signal as signal_module

        def _terminate(_signum: int, _frame: Any) -> None:
            raise SystemExit(f"controller terminated by signal {_signum}")

        for _sig_name in ("SIGTERM", "SIGINT"):
            _sig = getattr(signal_module, _sig_name, None)
            if _sig is not None:
                try:
                    signal_module.signal(_sig, _terminate)
                except (OSError, ValueError):
                    pass

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
    channel = browser_channel or "chromium"
    server = None
    try:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name == "nt":
            server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(128)
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
                proxy=proxy,
                user_agent=user_agent,
                locale=locale,
                timezone=timezone,
            )
            try:
                urls = list(start_urls or [])
                target_pages = tabs

                while len(context.pages) < target_pages:
                    context.new_page()
                while len(context.pages) > target_pages:
                    context.pages[-1].close()

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

                def _navigate_start_urls() -> None:

                    for idx, url in enumerate(urls):
                        if idx < len(context.pages):
                            try:
                                context.pages[idx].goto(url, wait_until="domcontentloaded", timeout=15000)
                            except Exception:
                                pass

                _wait_for_close(server, context, token, startup=_navigate_start_urls)
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
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--proxy", type=str, default=None)
    parser.add_argument("--user-agent", type=str, default=None)
    parser.add_argument("--locale", type=str, default=None)
    parser.add_argument("--timezone", type=str, default=None)
    args = parser.parse_args()

    token = os.environ.get("PROFILEDOCK_CONTROLLER_TOKEN", "")
    if not token:
        parser.error("PROFILEDOCK_CONTROLLER_TOKEN environment variable is required")
    proxy = os.environ.get("PROFILEDOCK_CONTROLLER_PROXY") or args.proxy
    user_agent = os.environ.get("PROFILEDOCK_CONTROLLER_USER_AGENT") or args.user_agent
    locale = os.environ.get("PROFILEDOCK_CONTROLLER_LOCALE") or args.locale
    timezone = os.environ.get("PROFILEDOCK_CONTROLLER_TIMEZONE") or args.timezone

    raise SystemExit(
        _controller(
            args.controller,
            args.data_dir,
            args.tabs,
            token,
            args.headless,
            browser_channel=None,
            window_width=None,
            window_height=None,
            start_urls=None,
            proxy=proxy,
            user_agent=user_agent,
            locale=locale,
            timezone=timezone,
        )
    )
