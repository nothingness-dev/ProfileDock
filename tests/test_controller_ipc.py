from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from profiledock.cli import app
from profiledock.models import Profile
from profiledock.page_reader import extract_page_markdown
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
    mock_context.pages = [mock_new_page]
    mock_context.new_page.return_value = mock_new_page

    cmd_open = {"cmd": "open_tab", "token": "tok", "args": {"url": "https://news.ycombinator.com"}}
    resp_open, _ = _execute_ipc_command(cmd_open, mock_context, token="tok")
    assert resp_open["status"] == "ok"
    assert resp_open["tab"]["url"] == "https://news.ycombinator.com"

    cmd_close_tab = {"cmd": "close_tab", "token": "tok", "args": {"index": 0}}
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
    mock_context.new_cdp_session.return_value.send.assert_called_once_with(
        "Runtime.evaluate",
        {
            "expression": "document.title",
            "awaitPromise": True,
            "returnByValue": True,
            "timeout": 10000,
        },
    )
    mock_context.new_cdp_session.return_value.detach.assert_called_once()

    cmd_cookies = {"cmd": "cookies", "token": "tok", "args": {}}
    resp_cookies, _ = _execute_ipc_command(cmd_cookies, mock_context, token="tok")
    assert resp_cookies["status"] == "ok"
    assert len(resp_cookies["cookies"]) == 1
    assert resp_cookies["cookies"][0]["name"] == "session"


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
