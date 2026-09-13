from __future__ import annotations

from unittest.mock import MagicMock


def _ctx_with_cookies(stored):
    context = MagicMock()
    jar = list(stored)
    session = MagicMock()
    sent = []

    def _send(method, params=None):
        sent.append((method, dict(params or {})))
        if method == "Network.deleteCookies":
            before = len(jar)
            jar[:] = [
                c
                for c in jar
                if not (
                    c.get("name") == params.get("name")
                    and (not params.get("domain") or str(c.get("domain", "")) == str(params.get("domain")))
                    and (not params.get("path") or str(c.get("path", "")) == str(params.get("path")))
                )
            ]
            return {"deleted": before - len(jar)}
        if method == "Network.getCookies":
            return {"cookies": list(jar)}
        return {}

    session.send.side_effect = _send
    context.new_cdp_session.return_value = session
    context.cookies.side_effect = lambda urls=None: [
        c for c in jar if not urls or c.get("url") in urls
    ]
    context.pages = [MagicMock(url="https://example.com")]
    context.sent = sent
    return context, jar


def test_controller_delete_cookies_removes_matching_entries():
    from profiledock.process.controller import _execute_ipc_command

    context, jar = _ctx_with_cookies(
        [
            {"name": "a", "value": "1", "domain": ".example.com", "path": "/"},
            {"name": "b", "value": "2", "domain": ".other.test", "path": "/"},
        ]
    )
    resp, _ = _execute_ipc_command(
        {
            "cmd": "delete_cookies",
            "token": "tok",
            "args": {"delete_cookies": [{"name": "a", "domain": ".example.com"}]},
        },
        context,
        token="tok",
    )
    assert resp["status"] == "ok"
    assert resp["deleted"] == 1
    assert resp["total_cookies"] == 1
    assert [c["name"] for c in jar] == ["b"]


def test_controller_delete_cookies_validates_entries():
    from profiledock.process.controller import _execute_ipc_command

    context, _ = _ctx_with_cookies([])
    resp, _ = _execute_ipc_command(
        {"cmd": "delete_cookies", "token": "tok", "args": {"delete_cookies": [{"name": ""}]}},
        context,
        token="tok",
    )
    assert resp["status"] == "error"


def test_controller_delete_cookies_bulk_by_urls():
    from profiledock.process.controller import _execute_ipc_command

    context, jar = _ctx_with_cookies(
        [
            {"name": "a", "value": "1", "domain": ".example.com", "path": "/", "url": "https://example.com/"},
            {"name": "b", "value": "2", "domain": ".other.test", "path": "/", "url": "https://other.test/"},
        ]
    )
    resp, _ = _execute_ipc_command(
        {"cmd": "delete_cookies", "token": "tok", "args": {"urls": ["https://example.com/"]}},
        context,
        token="tok",
    )
    assert resp["status"] == "ok"
    assert resp["deleted"] == 1
    assert [c["name"] for c in jar] == ["b"]


def test_direct_bridge_delete_cookies_parity(monkeypatch):
    from profiledock.process import direct_bridge as bridge

    context, jar = _ctx_with_cookies(
        [{"name": "a", "value": "1", "domain": ".example.com", "path": "/"}]
    )
    playwright = MagicMock()
    browser = MagicMock()
    browser.contexts = [context]
    playwright.chromium.connect_over_cdp.return_value = browser
    play_cls = MagicMock()
    play_cls.return_value.start.return_value = playwright
    monkeypatch.setattr(bridge, "_connect", lambda cdp_port: (playwright, browser, context, context.pages[0]))
    result = bridge.run_direct_cdp_command(
        "/data",
        "delete_cookies",
        {"delete_cookies": [{"name": "a", "domain": ".example.com"}]},
        cdp_port=9222,
    )
    assert result["status"] == "ok"
    assert result["deleted"] == 1
    assert jar == []
