from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..page_reader import extract_page_markdown
from ..validation import validate_cookie_url_filter, validate_url
from .errors import BrowserLaunchError


def _connect(cdp_port: int) -> tuple[Any, Any, Any, Any]:
    from playwright.sync_api import sync_playwright

    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()
        return playwright, browser, context, page
    except Exception as exc:
        playwright.stop()
        raise BrowserLaunchError(f"failed to connect to the direct browser: {exc}") from exc


def _with_connection(
    cdp_port: int,
    handler: Callable[[Any, Any], dict[str, Any]],
) -> dict[str, Any]:
    playwright, _, context, page = _connect(cdp_port)
    try:
        return handler(context, page)
    except BrowserLaunchError:
        raise
    except Exception as exc:
        raise BrowserLaunchError(str(exc)) from exc
    finally:
        try:
            playwright.stop()
        except Exception:
            pass


def _page_for_index(context: Any, index: Any) -> Any:
    if not context.pages:
        raise BrowserLaunchError("profile has no open tabs; open one with 'profiledock open-tab'")
    if type(index) is not int or not 0 <= index < len(context.pages):
        raise BrowserLaunchError(f"tab index out of range: {index}")
    return context.pages[index]


def _page_title(page: Any) -> str:
    try:
        return str(page.title())
    except Exception:
        return ""


def _capture_output_guard(output_path: str, data_dir: str) -> None:
    try:
        Path(output_path).resolve().relative_to(Path(data_dir).resolve())
    except (OSError, ValueError):
        return
    raise BrowserLaunchError(f"output path is inside the profile data directory: {output_path}")


def _capture_size(path: str) -> int:
    try:
        return int(os.path.getsize(path))
    except OSError:
        return 0


def _navigate(page: Any, url: Any, timeout: int) -> None:
    if url is None:
        return
    if not isinstance(url, str):
        raise BrowserLaunchError("URL must be a string or null")
    if not url:
        return
    validate_url(url)
    page.goto(url, wait_until="domcontentloaded", timeout=timeout)


def run_direct_cdp_command(
    data_dir: str,
    cmd: str,
    args: dict[str, Any] | None = None,
    timeout: float = 30.0,
    cdp_port: int | None = None,
    runtime_dir: Path | None = None,
) -> dict[str, Any]:
    del runtime_dir
    if type(cdp_port) is not int or not 1 <= cdp_port <= 65535:
        raise BrowserLaunchError("direct engine session has no valid DevTools endpoint; relaunch the profile")
    command_args = args or {}

    def handler(context: Any, _: Any) -> dict[str, Any]:
        if cmd == "tabs":
            return {
                "status": "ok",
                "tabs": [
                    {"index": index, "url": page.url, "title": _page_title(page)}
                    for index, page in enumerate(context.pages)
                ],
                "tabs_generation": 0,
            }

        if cmd == "open_tab":
            url = command_args.get("url", "about:blank")
            if not isinstance(url, str):
                raise BrowserLaunchError("URL must be a string")
            validate_url(url)
            page = context.new_page()
            try:
                if url != "about:blank":
                    page.goto(url, wait_until="domcontentloaded", timeout=15000)
                return {
                    "status": "ok",
                    "tab": {"index": len(context.pages) - 1, "url": page.url, "title": _page_title(page)},
                }
            except Exception:
                page.close()
                raise

        if cmd == "close_tab":
            page = _page_for_index(context, command_args.get("index"))
            page.close()
            return {"status": "ok", "remaining_tabs": len(context.pages)}

        if cmd == "read_page":
            page = _page_for_index(context, command_args.get("tab", 0))
            _navigate(page, command_args.get("url"), 20000)
            extracted = extract_page_markdown(page.content(), base_url=page.url)
            return {
                "status": "ok",
                "url": page.url,
                "title": extracted["title"] or _page_title(page),
                "content": extracted["content"],
                "links": extracted["links"],
            }

        if cmd == "eval":
            page = _page_for_index(context, command_args.get("tab", 0))
            script = command_args.get("script", "")
            if not isinstance(script, str) or not script:
                raise BrowserLaunchError("script expression must not be empty")
            session = context.new_cdp_session(page)
            try:
                evaluation = session.send(
                    "Runtime.evaluate",
                    {
                        "expression": (
                            "(async () => {"
                            "const timer = new Promise((_, reject) => {"
                            "setTimeout(() => reject(new Error('JavaScript evaluation timed out')), 10000);"
                            "});"
                            "return await Promise.race([Promise.resolve((0, eval)("
                            + json.dumps(script)
                            + ")), timer]);"
                            "})()"
                        ),
                        "awaitPromise": True,
                        "returnByValue": True,
                        "timeout": 10000,
                    },
                )
            finally:
                try:
                    session.detach()
                except Exception:
                    pass
            exception = evaluation.get("exceptionDetails")
            if isinstance(exception, dict):
                detail = exception.get("text") or "JavaScript evaluation failed"
                raise BrowserLaunchError(str(detail))
            remote = evaluation.get("result", {})
            if not isinstance(remote, dict):
                raise BrowserLaunchError("invalid JavaScript result")
            result = remote.get("value")
            if "unserializableValue" in remote:
                result = {"value": remote["unserializableValue"], "unserializable": True}
            return {"status": "ok", "result": result}

        if cmd == "cookies":
            urls = command_args.get("urls")
            if urls is not None:
                if not isinstance(urls, list) or not all(isinstance(url, str) for url in urls):
                    raise BrowserLaunchError("cookie URLs must be a list of strings")
                for url in urls:
                    validate_cookie_url_filter(url)
            cookies = [] if urls == [] else context.cookies(urls) if urls else context.cookies()
            return {"status": "ok", "cookies": cookies}

        if cmd == "set_cookies":
            cookies = command_args.get("set_cookies")
            if not isinstance(cookies, list) or not all(isinstance(cookie, dict) for cookie in cookies):
                raise BrowserLaunchError("set_cookies must be a list of cookie objects")
            for cookie in cookies:
                if not str(cookie.get("name", "")).strip() or not isinstance(cookie.get("value"), str):
                    raise BrowserLaunchError("each cookie needs a non-empty name and a string value")
                if not cookie.get("domain") and not cookie.get("url"):
                    raise BrowserLaunchError(
                        f"cookie '{cookie.get('name')}' needs a 'domain' or 'url'"
                    )
            context.add_cookies(cookies)
            return {"status": "ok", "added": len(cookies), "total_cookies": len(context.cookies())}

        if cmd in {"screenshot", "pdf"}:
            page = _page_for_index(context, command_args.get("tab", 0))
            output = command_args.get("output", "")
            if not isinstance(output, str) or not output.strip():
                raise BrowserLaunchError("output path must be a non-empty string")
            output = output.strip()
            _capture_output_guard(output, data_dir)
            _navigate(page, command_args.get("url"), 20000)
            if cmd == "screenshot":
                page.screenshot(path=output, full_page=bool(command_args.get("full_page", False)))
            else:
                page.pdf(path=output)
            return {
                "status": "ok",
                "output": output,
                "url": page.url,
                "title": _page_title(page),
                "bytes": _capture_size(output),
            }

        raise BrowserLaunchError(f"unsupported direct-bridge command: {cmd}")

    return _with_connection(cdp_port, handler)
