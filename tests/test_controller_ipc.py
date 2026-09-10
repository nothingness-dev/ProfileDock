import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from profiledock.cli import app
from profiledock.models import Profile
from profiledock.page_reader import extract_page_markdown
from profiledock.process import controller as controller_module
from profiledock.process_manager import (
    _encode_ipc_response,
    _execute_ipc_command,
    send_controller_command,
)


def test_page_markdown_extractor_headings_and_links():
    html = """
    <html>
        <head><title>Test Page</title></head>
        <body>
            <h1>Main Heading</h1>
            <p>Welcome to <b>ProfileDock</b> terminal reader.</p>
            <h2>Section 1</h2>
            <ul>
                <li>Item A</li>
                <li>Item B</li>
            </ul>
            <p>Visit <a href="https://example.com/docs">Documentation</a> today.</p>
            <script>console.log('should be stripped');</script>
            <style>.hidden { display:none; }</style>
        </body>
    </html>
    """
    res = extract_page_markdown(html, base_url="https://example.com")
    assert res["title"] == "Test Page"
    assert "# Main Heading" in res["content"]
    assert "**ProfileDock**" in res["content"]
    assert "## Section 1" in res["content"]
    assert "Item A" in res["content"]
    assert "should be stripped" not in res["content"]
    assert "[1] Documentation" in res["content"]
    assert len(res["links"]) == 1
    assert res["links"][0]["url"] == "https://example.com/docs"


def test_page_markdown_extractor_removes_terminal_controls():
    res = extract_page_markdown(
        '<title>bad\x1btitle</title><p>safe\x07text</p><a href="JaVaScRiPt:alert(1)">bad</a>'
    )
    assert res["title"] == "badtitle"
    assert "safetext" in res["content"]
    assert "\x1b" not in res["content"]
    assert "\x07" not in res["content"]
    assert res["links"] == []


def test_execute_ipc_command_unauthorized_token():
    mock_context = MagicMock()
    cmd = {"cmd": "tabs", "token": "wrong-token"}
    resp, should_exit = _execute_ipc_command(cmd, mock_context, token="correct-token")
    assert resp["status"] == "error"
    assert "unauthorized" in resp["message"]
    assert not should_exit


def test_execute_ipc_command_tabs_and_close():
    mock_page1 = MagicMock()
    mock_page1.url = "https://github.com"
    mock_page1.title.return_value = "GitHub"

    mock_page2 = MagicMock()
    mock_page2.url = "https://example.com"
    mock_page2.title.return_value = "Example"

    mock_context = MagicMock()
    mock_context.pages = [mock_page1, mock_page2]

    cmd_tabs = {"cmd": "tabs", "token": "secret-token"}
    resp, should_exit = _execute_ipc_command(cmd_tabs, mock_context, token="secret-token")
    assert resp["status"] == "ok"
    assert len(resp["tabs"]) == 2
    assert resp["tabs"][0]["url"] == "https://github.com"
    assert resp["tabs"][1]["title"] == "Example"
    assert not should_exit

    cmd_close = {"cmd": "close", "token": "secret-token"}
    resp_close, should_exit_close = _execute_ipc_command(cmd_close, mock_context, token="secret-token")
    assert resp_close["status"] == "ok"
    assert should_exit_close is True


def test_execute_ipc_command_open_and_close_tab():
    mock_new_page = MagicMock()
    mock_new_page.url = "https://news.ycombinator.com"
    mock_new_page.title.return_value = "Hacker News"

    mock_context = MagicMock()
    mock_context.pages = [MagicMock(), mock_new_page]
    mock_context.new_page.return_value = mock_new_page

    cmd_open = {"cmd": "open_tab", "token": "tok", "args": {"url": "https://news.ycombinator.com"}}
    resp_open, _ = _execute_ipc_command(cmd_open, mock_context, token="tok")
    assert resp_open["status"] == "ok"
    assert resp_open["tab"]["url"] == "https://news.ycombinator.com"

    cmd_close_tab = {"cmd": "close_tab", "token": "tok", "args": {"index": 1}}
    resp_close, _ = _execute_ipc_command(cmd_close_tab, mock_context, token="tok")
    assert resp_close["status"] == "ok"
    mock_new_page.close.assert_called_once()


def test_execute_ipc_command_eval_and_cookies():
    mock_page = MagicMock()
    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    mock_context.new_cdp_session.return_value.send.return_value = {"result": {"value": "evaluated_title"}}
    mock_context.cookies.return_value = [{"name": "session", "value": "xyz123"}]

    cmd_eval = {"cmd": "eval", "token": "tok", "args": {"script": "document.title"}}
    resp_eval, _ = _execute_ipc_command(cmd_eval, mock_context, token="tok")
    assert resp_eval["status"] == "ok"
    assert resp_eval["result"] == "evaluated_title"
    call = mock_context.new_cdp_session.return_value.send.call_args
    assert call[0][0] == "Runtime.evaluate"
    params = call[0][1]
    assert params["awaitPromise"] is True
    assert params["returnByValue"] is True
    assert params["timeout"] == 10000
    assert "Promise.race" in params["expression"]
    assert "document.title" in params["expression"]
    mock_context.new_cdp_session.return_value.detach.assert_called_once()

    cmd_cookies = {"cmd": "cookies", "token": "tok", "args": {}}
    resp_cookies, _ = _execute_ipc_command(cmd_cookies, mock_context, token="tok")
    assert resp_cookies["status"] == "ok"
    assert len(resp_cookies["cookies"]) == 1
    assert resp_cookies["cookies"][0]["name"] == "session"


def test_execute_ipc_command_cookies_empty_url_list_exports_nothing():
    """Regression: an explicit empty URL filter widened to 'export everything'.

    urls=[] is falsy in Python, so `context.cookies(urls) if urls else
    context.cookies()` silently returned the whole cookie jar for an explicit
    empty filter — a credential-safety bug for scripted callers.
    """
    mock_context = MagicMock()
    mock_context.pages = [MagicMock()]
    mock_context.cookies.return_value = [{"name": "all", "value": "leak"}]

    resp, _ = _execute_ipc_command(
        {"cmd": "cookies", "token": "tok", "args": {"urls": []}}, mock_context, token="tok"
    )
    assert resp["status"] == "ok"
    assert resp["cookies"] == [], "empty url filter must export nothing, not everything"
    mock_context.cookies.assert_not_called()


def test_execute_ipc_command_rejects_invalid_arguments_and_urls():
    context = MagicMock()
    context.pages = [MagicMock()]
    malformed, _ = _execute_ipc_command({"cmd": "tabs", "token": "tok", "args": []}, context, token="tok")
    invalid_url, _ = _execute_ipc_command(
        {"cmd": "open_tab", "token": "tok", "args": {"url": "file:///secret"}},
        context,
        token="tok",
    )
    invalid_tab, _ = _execute_ipc_command(
        {"cmd": "eval", "token": "tok", "args": {"script": "1", "tab": -1}},
        context,
        token="tok",
    )
    assert malformed["status"] == "error"
    assert "invalid URL scheme" in invalid_url["message"]
    assert "tab index out of range" in invalid_tab["message"]
    context.new_page.assert_not_called()


def test_controller_command_client_rejects_unknown_command():
    try:
        send_controller_command("unused", "unknown")
    except ValueError as exc:
        assert "unsupported controller command" in str(exc)
    else:
        raise AssertionError("unknown command was accepted")


def test_controller_response_size_is_bounded():
    with patch("profiledock.process_manager._MAX_RESPONSE_BYTES", 32):
        encoded = _encode_ipc_response({"status": "ok", "content": "x" * 100})
    assert len(encoded) < 100
    assert b"exceeds the maximum size" in encoded


def test_cookies_json_file_output_preserves_json_stdout(tmp_path: Path):
    profile = Profile("abc123", "Work", "2026-01-01T00:00:00+00:00", str(tmp_path / "data"))
    output = tmp_path / "cookies.json"
    runner = CliRunner()
    with (
        patch("profiledock.cli.manager") as selected_manager,
        patch("profiledock.cli.send_controller_command", return_value={"cookies": [{"name": "sid"}]}),
    ):
        selected_manager.return_value.resolve.return_value = profile
        selected_manager.return_value.runtime_path.return_value = tmp_path / "runtime"
        result = runner.invoke(app, ["cookies", "abc123", "--output", str(output), "--json"])
    assert result.exit_code == 0
    assert '"command": "cookies"' in result.stdout
    assert '"count": 1' in result.stdout
    assert output.read_text(encoding="utf-8").endswith("\n")


def test_cookies_url_filter_rejects_bare_domain(tmp_path: Path):
    """Cookie --url filters require full URLs, verified against live Chromium.

    Playwright's context.cookies(urls) forwards filters to CDP, and CDP
    rejects bare domains with 'Invalid URL' (confirmed on a real browser —
    see the audit for this fix). Client-side validation must keep rejecting
    them so the user gets a clear error instead of a cryptic controller
    failure. Use --domain for domain-scoped filtering.
    """
    profile = Profile("abc123", "Work", "2026-01-01T00:00:00+00:00", str(tmp_path / "data"))
    runner = CliRunner()
    with (
        patch("profiledock.cli.manager") as selected_manager,
        patch("profiledock.cli.send_controller_command", return_value={"cookies": []}),
    ):
        selected_manager.return_value.resolve.return_value = profile
        selected_manager.return_value.runtime_path.return_value = tmp_path / "runtime"
        result = runner.invoke(app, ["cookies", "abc123", "--url", "example.com"])
    assert result.exit_code == 1
    assert "invalid URL scheme" in result.output

    # Full URL passes and reaches the controller.
    with (
        patch("profiledock.cli.manager") as selected_manager,
        patch(
            "profiledock.cli.send_controller_command",
            return_value={"cookies": [{"name": "sid"}]},
        ) as send_mock,
    ):
        selected_manager.return_value.resolve.return_value = profile
        selected_manager.return_value.runtime_path.return_value = tmp_path / "runtime"
        result = runner.invoke(app, ["cookies", "abc123", "--url", "https://example.com"])
    assert result.exit_code == 0, result.output
    sent_args = send_mock.call_args.kwargs.get("args") or send_mock.call_args[1].get("args")
    assert sent_args == {"urls": ["https://example.com"]}


def test_cookies_session_only_filter_excludes_persistent_cookies(tmp_path: Path):
    """Session cookies (no expiry) can be excluded with --session-only.

    Long-lived tracking cookies persist on disk; a user hardening an export
    wants current-session credentials only. --session-only must drop every
    cookie whose expires is -1 (Playwright's marker for session cookies).
    """
    profile = Profile("abc123", "Work", "2026-01-01T00:00:00+00:00", str(tmp_path / "data"))
    cookies = [
        {"name": "session", "value": "a", "expires": -1, "domain": "example.com"},
        {"name": "persistent", "value": "b", "expires": 1893456000, "domain": "example.com"},
    ]
    runner = CliRunner()
    with (
        patch("profiledock.cli.manager") as selected_manager,
        patch("profiledock.cli.send_controller_command", return_value={"cookies": cookies}),
    ):
        selected_manager.return_value.resolve.return_value = profile
        selected_manager.return_value.runtime_path.return_value = tmp_path / "runtime"
        result = runner.invoke(app, ["cookies", "abc123", "--session-only", "--json"])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["command"] == "cookies"
    names = [c["name"] for c in envelope["data"]]
    assert names == ["session"]


def test_cookies_domain_filter_matches_suffix(tmp_path: Path):
    """--domain filters by domain suffix, matching cookie-domain semantics.

    A cookie set for '.example.com' must match --domain example.com, and
    subdomains must match their parent: the filter mirrors how cookie domains
    actually scope, not exact-string equality.
    """
    profile = Profile("abc123", "Work", "2026-01-01T00:00:00+00:00", str(tmp_path / "data"))
    cookies = [
        {"name": "a", "value": "1", "domain": ".example.com"},
        {"name": "b", "value": "2", "domain": "sub.example.com"},
        {"name": "c", "value": "3", "domain": "other.org"},
    ]
    runner = CliRunner()
    with (
        patch("profiledock.cli.manager") as selected_manager,
        patch("profiledock.cli.send_controller_command", return_value={"cookies": cookies}),
    ):
        selected_manager.return_value.resolve.return_value = profile
        selected_manager.return_value.runtime_path.return_value = tmp_path / "runtime"
        result = runner.invoke(app, ["cookies", "abc123", "--domain", "example.com", "--json"])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["command"] == "cookies"
    names = [c["name"] for c in envelope["data"]]
    assert sorted(names) == ["a", "b"]


# ---------------------------------------------------------------------------
# cookies import (--load), Netscape format, and redaction
# ---------------------------------------------------------------------------

NETSCAPE_SAMPLE = (
    "# Netscape HTTP Cookie File\n"
    "# Generated by ProfileDock\n"
    ".example.com\tTRUE\t/\tFALSE\t1893456000\tsess\tpersistent-value\n"
    ".other.org\tTRUE\t/\tTRUE\t0\tsess2\tsession-value\n"
)


def test_netscape_preserves_cookie_scope_http_only_and_empty_values():
    from profiledock.commands.automation import _cookies_to_netscape, _parse_cookie_file

    cookies = [
        {
            "domain": "example.com",
            "path": "/",
            "secure": True,
            "httpOnly": True,
            "expires": -1,
            "name": "sid",
            "value": "",
        },
        {
            "domain": ".example.com",
            "path": "/",
            "secure": False,
            "httpOnly": False,
            "expires": 1893456000,
            "name": "pref",
            "value": "value",
        },
    ]
    raw = "\n".join(_cookies_to_netscape(cookies))
    assert "#HttpOnly_example.com\tFALSE" in raw
    assert _parse_cookie_file(raw, Path("cookies.txt")) == cookies


def test_cookies_load_json_round_trip(tmp_path: Path):
    """Importing a previously exported JSON file restores the cookie jar."""
    profile = Profile("abc123", "Work", "2026-01-01T00:00:00+00:00", str(tmp_path / "data"))
    jar = [{"name": "sid", "value": "v", "domain": "example.com", "path": "/", "expires": -1}]
    load_file = tmp_path / "jar.json"
    load_file.write_text(json.dumps(jar), encoding="utf-8")
    runner = CliRunner()
    with (
        patch("profiledock.cli.manager") as selected_manager,
        patch(
            "profiledock.cli.send_controller_command",
            return_value={"added": 1, "total_cookies": 1},
        ) as send_mock,
    ):
        selected_manager.return_value.resolve.return_value = profile
        selected_manager.return_value.runtime_path.return_value = tmp_path / "runtime"
        result = runner.invoke(app, ["cookies", "abc123", "--load", str(load_file), "--json"])
    assert result.exit_code == 0, result.output
    assert send_mock.call_args.kwargs.get("args") == {"set_cookies": jar}
    envelope = json.loads(result.stdout)
    assert envelope["data"]["count"] == 1
    assert envelope["data"]["total_cookies"] == 1


def test_cookies_load_netscape(tmp_path: Path):
    """--load auto-detects Netscape cookies.txt and converts to Playwright shape.

    Interop with the wider ecosystem (yt-dlp, curl, scripting) requires the
    classic tab-separated format; expires 0 means a session cookie and must
    map to expires -1.
    """
    profile = Profile("abc123", "Work", "2026-01-01T00:00:00+00:00", str(tmp_path / "data"))
    load_file = tmp_path / "cookies.txt"
    load_file.write_text(NETSCAPE_SAMPLE, encoding="utf-8")
    runner = CliRunner()
    with (
        patch("profiledock.cli.manager") as selected_manager,
        patch(
            "profiledock.cli.send_controller_command",
            return_value={"added": 2, "total_cookies": 2},
        ) as send_mock,
    ):
        selected_manager.return_value.resolve.return_value = profile
        selected_manager.return_value.runtime_path.return_value = tmp_path / "runtime"
        result = runner.invoke(app, ["cookies", "abc123", "--load", str(load_file), "--json"])
    assert result.exit_code == 0, result.output
    sent = send_mock.call_args.kwargs.get("args")["set_cookies"]
    assert sent == [
        {
            "domain": ".example.com",
            "path": "/",
            "secure": False,
            "httpOnly": False,
            "expires": 1893456000,
            "name": "sess",
            "value": "persistent-value",
        },
        {
            "domain": ".other.org",
            "path": "/",
            "secure": True,
            "httpOnly": False,
            "expires": -1,
            "name": "sess2",
            "value": "session-value",
        },
    ]


def test_cookies_load_rejects_malformed_entries(tmp_path: Path):
    """A malformed cookie entry fails the import with a clear message.

    Half-imported authentication state is worse than none: one bad line in a
    Netscape file (wrong column count) or a JSON entry missing name/value
    must abort the whole import before anything reaches the browser.
    """
    profile = Profile("abc123", "Work", "2026-01-01T00:00:00+00:00", str(tmp_path / "data"))
    bad = tmp_path / "bad.txt"
    bad.write_text(
        ".example.com\tTRUE\t/\tFALSE\t1893456000\tonly-five-columns\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    with (
        patch("profiledock.cli.manager") as selected_manager,
        patch("profiledock.cli.send_controller_command", return_value={"added": 0}),
    ):
        selected_manager.return_value.resolve.return_value = profile
        selected_manager.return_value.runtime_path.return_value = tmp_path / "runtime"
        result = runner.invoke(app, ["cookies", "abc123", "--load", str(bad)])
    assert result.exit_code == 1
    assert "malformed cookie entry" in result.output or "line 1" in result.output


def test_cookies_load_missing_file_fails_cleanly(tmp_path: Path):
    profile = Profile("abc123", "Work", "2026-01-01T00:00:00+00:00", str(tmp_path / "data"))
    runner = CliRunner()
    with (
        patch("profiledock.cli.manager") as selected_manager,
        patch("profiledock.cli.send_controller_command", return_value={"added": 0}),
    ):
        selected_manager.return_value.resolve.return_value = profile
        selected_manager.return_value.runtime_path.return_value = tmp_path / "runtime"
        result = runner.invoke(app, ["cookies", "abc123", "--load", str(tmp_path / "nope.json")])
    assert result.exit_code == 1
    assert "cannot read" in result.output or "not found" in result.output


def test_cookies_load_invalid_utf8_fails_cleanly(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_bytes(b"\xff\xfe\x00")
    runner = CliRunner()
    with patch("profiledock.cli.send_controller_command") as send_mock:
        result = runner.invoke(app, ["cookies", "abc123", "--load", str(bad)])
    assert result.exit_code == 1
    assert "invalid cookie file" in result.output
    send_mock.assert_not_called()


def test_cookies_invalid_format_fails_before_browser_start():
    runner = CliRunner()
    with patch("profiledock.cli.send_controller_command") as send_mock:
        result = runner.invoke(app, ["cookies", "abc123", "--format", "yaml"])
    assert result.exit_code == 1
    assert "unsupported format" in result.output
    send_mock.assert_not_called()


def test_cookies_redact_values_masks_secrets(tmp_path: Path):
    """--redact-values exports metadata without the secret values.

    Exporting to stdout dumps raw authentication material into scrollback;
    a redacted preview lets users audit names/domains/expiry before deciding
    to save the real values. Empty strings cannot restore the original secrets.
    """
    profile = Profile("abc123", "Work", "2026-01-01T00:00:00+00:00", str(tmp_path / "data"))
    cookies = [
        {"name": "sid", "value": "super-secret", "domain": "example.com", "expires": -1},
    ]
    runner = CliRunner()
    with (
        patch("profiledock.cli.manager") as selected_manager,
        patch("profiledock.cli.send_controller_command", return_value={"cookies": cookies}),
    ):
        selected_manager.return_value.resolve.return_value = profile
        selected_manager.return_value.runtime_path.return_value = tmp_path / "runtime"
        result = runner.invoke(app, ["cookies", "abc123", "--redact-values", "--json"])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["data"][0]["value"] != "super-secret"
    assert envelope["data"][0]["value"] == ""
    assert envelope["data"][0]["name"] == "sid"


def test_execute_ipc_command_screenshot(tmp_path: Path):
    mock_page = MagicMock()
    mock_page.url = "https://example.com"
    mock_page.title.return_value = "Example"

    def fake_screenshot(path, full_page=False):
        Path(path).write_bytes(b"\x89PNG fake bytes")

    mock_page.screenshot.side_effect = fake_screenshot

    mock_context = MagicMock()
    mock_context.pages = [mock_page]

    out_file = tmp_path / "capture.png"
    cmd = {
        "cmd": "screenshot",
        "token": "tok",
        "args": {"output": str(out_file), "full_page": True},
    }
    resp, should_exit = _execute_ipc_command(cmd, mock_context, token="tok")
    assert resp["status"] == "ok"
    assert resp["output"] == str(out_file)
    assert resp["bytes"] == len(b"\x89PNG fake bytes")
    assert resp["url"] == "https://example.com"
    assert resp["title"] == "Example"
    assert should_exit is False
    # full_page passed through to playwright
    assert mock_page.screenshot.call_args.kwargs["full_page"] is True
    assert out_file.read_bytes() == b"\x89PNG fake bytes"


def test_execute_ipc_command_screenshot_survives_title_failure(tmp_path: Path):
    mock_page = MagicMock()
    mock_page.url = "https://example.com"
    mock_page.title.side_effect = Exception("Execution context was destroyed")

    def fake_screenshot(path, full_page=False):
        Path(path).write_bytes(b"\x89PNG ok")

    mock_page.screenshot.side_effect = fake_screenshot

    mock_context = MagicMock()
    mock_context.pages = [mock_page]

    out_file = tmp_path / "capture.png"
    cmd = {"cmd": "screenshot", "token": "tok", "args": {"output": str(out_file)}}
    resp, _ = _execute_ipc_command(cmd, mock_context, token="tok")
    assert resp["status"] == "ok", resp
    assert resp["bytes"] == len(b"\x89PNG ok")


def test_execute_ipc_command_screenshot_requires_output_path():
    mock_context = MagicMock()
    mock_context.pages = [MagicMock()]
    cmd = {"cmd": "screenshot", "token": "tok", "args": {"output": "  "}}
    resp, _ = _execute_ipc_command(cmd, mock_context, token="tok")
    assert resp["status"] == "error"
    assert "output path" in resp["message"]


def test_execute_ipc_command_screenshot_rejects_bad_url():
    mock_context = MagicMock()
    mock_context.pages = [MagicMock()]
    cmd = {
        "cmd": "screenshot",
        "token": "tok",
        "args": {"output": "x.png", "url": "javascript:alert(1)"},
    }
    resp, _ = _execute_ipc_command(cmd, mock_context, token="tok")
    assert resp["status"] == "error"


def test_execute_ipc_command_screenshot_rejects_out_of_range_tab():
    mock_context = MagicMock()
    mock_context.pages = [MagicMock()]
    cmd = {"cmd": "screenshot", "token": "tok", "args": {"output": "x.png", "tab": 7}}
    resp, _ = _execute_ipc_command(cmd, mock_context, token="tok")
    assert resp["status"] == "error"
    assert "tab index" in resp["message"]


def test_execute_ipc_command_pdf(tmp_path: Path):
    mock_page = MagicMock()
    mock_page.url = "https://example.com"
    mock_page.title.return_value = "Example"

    def fake_pdf(path):
        Path(path).write_bytes(b"%PDF-1.4 fake")

    mock_page.pdf.side_effect = fake_pdf

    mock_context = MagicMock()
    mock_context.pages = [mock_page]

    out_file = tmp_path / "page.pdf"
    cmd = {"cmd": "pdf", "token": "tok", "args": {"output": str(out_file)}}
    resp, should_exit = _execute_ipc_command(cmd, mock_context, token="tok")
    assert resp["status"] == "ok"
    assert resp["bytes"] == len(b"%PDF-1.4 fake")
    assert resp["title"] == "Example"
    assert should_exit is False


def test_execute_ipc_command_pdf_requires_output_path():
    mock_context = MagicMock()
    mock_context.pages = [MagicMock()]
    cmd = {"cmd": "pdf", "token": "tok", "args": {}}
    resp, _ = _execute_ipc_command(cmd, mock_context, token="tok")
    assert resp["status"] == "error"


def test_execute_ipc_command_pdf_refuses_output_inside_profile_data_dir(tmp_path: Path):
    data_dir = tmp_path / "abc123" / "browser-data"
    data_dir.mkdir(parents=True)
    mock_context = MagicMock()
    mock_context.pages = [MagicMock()]

    inside = data_dir / "page.pdf"
    cmd = {"cmd": "pdf", "token": "tok", "args": {"output": str(inside)}}
    resp, _ = _execute_ipc_command(cmd, mock_context, token="tok", data_dir=str(data_dir))
    assert resp["status"] == "error"
    assert "profile data directory" in resp["message"]
    assert not inside.exists()


def test_execute_ipc_command_pdf_rejects_bad_url():
    mock_context = MagicMock()
    mock_context.pages = [MagicMock()]
    cmd = {"cmd": "pdf", "token": "tok", "args": {"output": "x.pdf", "url": "javascript:x()"}}
    resp, _ = _execute_ipc_command(cmd, mock_context, token="tok")
    assert resp["status"] == "error"


def test_execute_ipc_command_pdf_maps_headed_error():
    mock_page = MagicMock()
    mock_page.pdf.side_effect = Exception("PDF generation is only supported for Headless Chromium")
    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    cmd = {"cmd": "pdf", "token": "tok", "args": {"output": "x.pdf"}}
    resp, _ = _execute_ipc_command(cmd, mock_context, token="tok")
    assert resp["status"] == "error"
    assert "headless Chromium session" in resp["message"]


def test_execute_ipc_command_eval_wraps_script_with_deadline():
    mock_page = MagicMock()
    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    mock_context.new_cdp_session.return_value.send.return_value = {
        "result": {"type": "string", "value": "ok"}
    }

    cmd = {"cmd": "eval", "token": "tok", "args": {"script": "document.title"}}
    resp, _ = _execute_ipc_command(cmd, mock_context, token="tok")
    assert resp["status"] == "ok"
    sent = mock_context.new_cdp_session.return_value.send.call_args
    expression = sent[0][1]["expression"]
    assert "Promise.race" in expression, "script must run under a deadline wrapper"
    assert "document.title" in expression, "original expression must be preserved"
    assert sent[0][1]["awaitPromise"] is True


def test_execute_ipc_command_eval_marks_unserializable_result():
    mock_page = MagicMock()
    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    mock_context.new_cdp_session.return_value.send.return_value = {
        "result": {"type": "number", "unserializableValue": "NaN"}
    }

    cmd = {"cmd": "eval", "token": "tok", "args": {"script": "NaN"}}
    resp, _ = _execute_ipc_command(cmd, mock_context, token="tok")
    assert resp["status"] == "ok"
    assert resp["result"] == {"value": "NaN", "unserializable": True}


def test_execute_ipc_command_eval_reports_deadline_expiry():
    mock_page = MagicMock()
    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    mock_context.new_cdp_session.return_value.send.return_value = {
        "exceptionDetails": {
            "text": "Uncaught (in promise)",
            "exception": {"description": "Error: the page evaluation timed out after 10s"},
        }
    }

    cmd = {"cmd": "eval", "token": "tok", "args": {"script": "new Promise(() => {})"}}
    resp, _ = _execute_ipc_command(cmd, mock_context, token="tok")
    assert resp["status"] == "error"
    assert "timed out" in resp["message"]


def test_execute_ipc_command_tabs_uses_browser_level_target_info():
    mock_page1 = MagicMock()
    mock_page1.url = "https://busy.example.com"
    mock_page2 = MagicMock()
    mock_page2.url = "https://example.com"

    target_infos = [
        {"title": "Busy Page", "url": "https://busy.example.com", "type": "page"},
        {"title": "", "url": "https://example.com", "type": "page"},
    ]

    mock_session = MagicMock()
    mock_session.send.return_value = {"targetInfos": target_infos}

    mock_browser = MagicMock()
    mock_browser.new_browser_cdp_session.return_value = mock_session

    mock_context = MagicMock()
    mock_context.pages = [mock_page1, mock_page2]
    mock_context.browser = mock_browser

    cmd = {"cmd": "tabs", "token": "tok"}
    resp, should_exit = _execute_ipc_command(cmd, mock_context, token="tok")
    assert resp["status"] == "ok"
    assert not should_exit
    titles = [t["title"] for t in resp["tabs"]]
    assert titles == ["Busy Page", ""]
    for page in mock_context.pages:
        page.title.assert_not_called(), "page.title() must never be called from tabs"


def test_execute_ipc_command_open_tab_closes_orphan_page_on_goto_failure():
    mock_new_page = MagicMock()
    mock_new_page.url = "https://down.example.invalid"
    mock_new_page.title.return_value = ""
    mock_new_page.goto.side_effect = Exception("net::ERR_CONNECTION_REFUSED")

    mock_context = MagicMock()
    mock_context.pages = [MagicMock(), mock_new_page]
    mock_context.new_page.return_value = mock_new_page

    cmd = {"cmd": "open_tab", "token": "tok", "args": {"url": "https://down.example.invalid"}}
    resp, _ = _execute_ipc_command(cmd, mock_context, token="tok")
    assert resp["status"] == "error"
    assert "CONNECTION_REFUSED" in resp["message"]
    mock_new_page.close.assert_called_once()


def test_execute_ipc_command_close_tab_closes_last_page():
    mock_page = MagicMock()
    mock_context = MagicMock()
    mock_context.pages = [mock_page]

    cmd = {"cmd": "close_tab", "token": "tok", "args": {"index": 0}}
    resp, _ = _execute_ipc_command(cmd, mock_context, token="tok")
    assert resp["status"] == "ok"
    mock_page.close.assert_called_once()


def test_execute_ipc_command_stale_generation_rejected():
    mock_page = MagicMock()
    mock_context = MagicMock()
    mock_context.pages = [mock_page]

    cmd = {"cmd": "close_tab", "token": "tok", "args": {"index": 0, "generation": 1}}
    resp, _ = _execute_ipc_command(cmd, mock_context, token="tok")
    assert resp["status"] == "error"
    assert "stale" in resp["message"]
    mock_page.close.assert_not_called()


def test_execute_ipc_command_current_generation_accepted():
    mock_page = MagicMock()
    mock_context = MagicMock()
    mock_context.pages = [MagicMock(), mock_page]

    cmd = {"cmd": "close_tab", "token": "tok", "args": {"index": 1, "generation": controller_module._tabs_generation}}
    resp, _ = _execute_ipc_command(cmd, mock_context, token="tok")
    assert resp["status"] == "ok"
    mock_page.close.assert_called_once()


def test_execute_ipc_command_omitted_generation_accepted():
    mock_page = MagicMock()
    mock_context = MagicMock()
    mock_context.pages = [MagicMock(), mock_page]

    cmd = {"cmd": "close_tab", "token": "tok", "args": {"index": 1}}
    resp, _ = _execute_ipc_command(cmd, mock_context, token="tok")
    assert resp["status"] == "ok"
    mock_page.close.assert_called_once()


def test_execute_ipc_command_read_page_no_pages_reports_error():
    mock_context = MagicMock()
    mock_context.pages = []

    cmd = {"cmd": "read_page", "token": "tok", "args": {}}
    resp, _ = _execute_ipc_command(cmd, mock_context, token="tok")
    assert resp["status"] == "error"
    assert "no open tabs" in resp["message"]
    mock_context.new_page.assert_not_called()
