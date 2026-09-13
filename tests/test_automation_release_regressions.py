import io
import json
from unittest.mock import MagicMock, patch

import pytest

from profiledock.mcp_server import serve_stdio


def test_mcp_bad_arguments_do_not_kill_stdio_or_dispatch_notifications():
    dispatcher = MagicMock()
    messages = [
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": [1]},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "profile_launch", "arguments": {"profile_id": "A", "tabs": True}},
        },
        {"jsonrpc": "2.0", "id": 3, "method": "initialize"},
        {"jsonrpc": "2.0", "id": 4, "method": "ping"},
    ]
    output = io.StringIO()
    serve_stdio(dispatcher, io.StringIO("\n".join(map(json.dumps, messages))), output)
    results = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [item["id"] for item in results] == [1, 2, 3, 4]
    assert results[0]["error"]["code"] == -32602
    assert results[1]["error"]["code"] == -32602
    assert "tools" in results[2]["result"]["capabilities"]
    assert results[3]["result"] == {}
    dispatcher.call_tool.assert_not_called()


def test_mcp_tool_result_has_content_and_error_semantics():
    dispatcher = MagicMock()
    dispatcher.call_tool.side_effect = [{"profiles": []}, ValueError("failed")]
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "profile_list", "arguments": {}},
    }
    output = io.StringIO()
    serve_stdio(dispatcher, io.StringIO((json.dumps(request) + "\n") * 2), output)
    first, second = [json.loads(line)["result"] for line in output.getvalue().splitlines()]
    assert json.loads(first["content"][0]["text"]) == {"profiles": []}
    assert first["isError"] is False
    assert second["isError"] is True


def test_mcp_launch_honors_tabs_headless_and_preset(tmp_path):
    from profiledock.commands.mcp import MCPToolDispatcher
    from profiledock.models import LaunchConfig, Profile

    profile = Profile(
        "abc",
        "Work",
        "2026-01-01T00:00:00+00:00",
        str(tmp_path),
        engine="playwright",
        launch_config=LaunchConfig(locale="en-GB"),
    )
    manager = MagicMock()
    manager.resolve.return_value = profile
    with (
        patch("profiledock.commands.mcp._get_manager", return_value=manager),
        patch("profiledock.cli.runtime_path", return_value=tmp_path / "runtime"),
        patch("profiledock.process_manager.get_status", return_value="stopped"),
        patch("profiledock.cli.start_controller") as start,
    ):
        result = MCPToolDispatcher().call_tool(
            "profile_launch",
            {
                "profile_id": "abc",
                "tabs": 3,
                "headless": True,
            },
        )
    assert result["tabs"] == 3
    assert start.call_args.args == (str(tmp_path), 3)
    assert start.call_args.kwargs["headless"] is True
    assert start.call_args.kwargs["locale"] == "en-GB"
    manager.mark_launched.assert_called_once_with("abc")


@pytest.mark.parametrize("domains, expected", [(["example.com"], 1), (["missing.test"], 0), (None, 2)])
def test_cli_clear_intersects_filters_and_preserves_empty_matches(tmp_path, domains, expected):
    from typer.testing import CliRunner

    from profiledock.cli import app
    from profiledock.models import Profile

    profile = Profile("abc", "Work", "2026-01-01T00:00:00+00:00", str(tmp_path))
    jar = [
        {"name": "a", "domain": "example.com", "path": "/"},
        {"name": "b", "domain": "other.test", "path": "/"},
    ]
    manager = MagicMock()
    manager.resolve.return_value = profile
    calls = []

    def send(data_dir, cmd, args, **kwargs):
        calls.append((cmd, args))
        if cmd == "cookies":
            assert args == {"urls": ["https://example.com/"]}
            return {"cookies": jar}
        assert len(args["delete_cookies"]) == expected
        return {"deleted": expected, "total_cookies": 2 - expected}

    argv = ["cookies", "abc", "--clear", "--url", "https://example.com/", "--json"]
    for domain in domains or []:
        argv.extend(["--domain", domain])
    with (
        patch("profiledock.commands.automation._get_manager", return_value=manager),
        patch("profiledock.cli.runtime_path", return_value=tmp_path),
        patch("profiledock.cli.send_controller_command", side_effect=send),
    ):
        result = CliRunner().invoke(app, argv)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["deleted"] == expected
    assert len(calls) == 2


def test_mcp_attach_does_not_add_tabs(tmp_path):
    from profiledock.commands.mcp import MCPToolDispatcher
    from profiledock.models import Profile

    profile = Profile("abc", "Work", "2026-01-01T00:00:00+00:00", str(tmp_path), engine="direct")
    manager = MagicMock()
    manager.resolve.return_value = profile
    with (
        patch("profiledock.commands.mcp._get_manager", return_value=manager),
        patch("profiledock.cli.runtime_path", return_value=tmp_path),
        patch("profiledock.process_manager.get_status", return_value="running"),
        patch("profiledock.cli.send_controller_command", return_value={"tabs": [{"index": 0}]}) as send,
        patch("profiledock.cli.start_controller") as start,
    ):
        result = MCPToolDispatcher().call_tool("profile_launch", {"profile_id": "abc", "tabs": 4})
    assert result["status"] == "attached"
    assert send.call_args.kwargs["cmd"] == "tabs"
    start.assert_not_called()


@pytest.mark.browser
def test_real_browser_snapshot_targets_and_cookie_scopes(tmp_path):
    from playwright.sync_api import sync_playwright

    from profiledock.ax_snapshot import execute_snapshot_command
    from profiledock.process.controller import _delete_cookies_live

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(str(tmp_path / "browser"), headless=True)
        try:
            page = context.pages[0]
            page.set_content(
                "<button onclick=\"this.textContent='Clicked'\">Same</button>"
                "<button onclick=\"this.textContent='Second'\">Same</button>"
                '<input aria-label="Name"><select aria-label="Choice">'
                '<option value="a">A</option><option value="b">B</option></select>'
            )
            snapshot = execute_snapshot_command(page, "snapshot", {})["snapshot"]
            buttons = [line.split(":")[0][1:] for line in snapshot.splitlines() if "button 'Same'" in line]
            assert len(buttons) == 2
            execute_snapshot_command(page, "interact", {"action": "click", "ref": buttons[1]})
            assert page.locator("button").nth(0).inner_text() == "Same"
            assert page.locator("button").nth(1).inner_text() == "Second"
            text_ref = next(
                line.split(":")[0][1:] for line in snapshot.splitlines() if "textbox 'Name'" in line
            )
            execute_snapshot_command(page, "interact", {"action": "fill", "ref": text_ref, "value": "Alice"})
            assert page.locator("input").input_value() == "Alice"
            execute_snapshot_command(page, "interact", {"action": "press", "ref": text_ref, "value": "End"})
            select_ref = next(
                line.split(":")[0][1:] for line in snapshot.splitlines() if "combobox 'Choice'" in line
            )
            execute_snapshot_command(page, "interact", {"action": "select", "ref": select_ref, "value": "b"})
            assert page.locator("select").input_value() == "b"
            page.locator("button").nth(1).evaluate("(el) => el.remove()")
            with pytest.raises(ValueError, match="stale"):
                execute_snapshot_command(page, "interact", {"action": "click", "ref": buttons[1]})
            context.add_cookies(
                [
                    {"name": "a", "value": "1", "domain": "example.com", "path": "/"},
                    {"name": "a", "value": "2", "domain": "example.com", "path": "/private"},
                    {"name": "b", "value": "3", "domain": "other.test", "path": "/"},
                ]
            )
            assert (
                _delete_cookies_live(
                    context, [{"name": "a", "domain": "example.com", "path": "/private"}], []
                )
                == 1
            )
            assert len(context.cookies()) == 2
            assert _delete_cookies_live(context, [], ["https://example.com/"]) == 1
            assert [cookie["name"] for cookie in context.cookies()] == ["b"]
            from profiledock.process import direct_bridge

            second = context.new_page()
            second.set_content("<button onclick=\"this.textContent='Done'\">Direct</button>")
            with patch.object(
                direct_bridge, "_with_connection", side_effect=lambda port, handler: handler(context, page)
            ):
                direct = direct_bridge.run_direct_cdp_command(
                    str(tmp_path), "snapshot", {"tab": 1}, cdp_port=9222
                )
                ref = next(
                    line.split(":")[0][1:]
                    for line in direct["snapshot"].splitlines()
                    if "button 'Direct'" in line
                )
                direct_bridge.run_direct_cdp_command(
                    str(tmp_path), "interact", {"tab": 1, "ref": ref, "action": "click"}, cdp_port=9222
                )
            assert second.locator("button").inner_text() == "Done"
        finally:
            context.close()
