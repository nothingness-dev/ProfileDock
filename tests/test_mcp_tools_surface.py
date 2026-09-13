"""Tests for the M1 MCP tool surface: cookies, tabs, capture, coherence, config.

Dispatcher-level tests inject a fake send_controller_command so no browser
or profile launch happens; schema tests run against the real TOOL_SPECS.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from profiledock.mcp_server import TOOL_SPECS, list_tools, validate_tool_arguments

NEW_TOOLS = [
    "profile_cookies_get",
    "profile_cookies_set",
    "profile_cookies_delete",
    "profile_tabs",
    "profile_open_tab",
    "profile_close_tab",
    "profile_screenshot",
    "profile_pdf",
    "profile_coherence",
    "profile_config_get",
    "profile_config_set",
]


# --- tool spec conformance ---------------------------------------------------


def test_tool_surface_includes_m1_tools():
    names = [spec["name"] for spec in TOOL_SPECS]
    for tool in NEW_TOOLS:
        assert tool in names, f"{tool} missing from TOOL_SPECS"


def test_list_tools_returns_m1_specs():
    tools = {spec["name"]: spec for spec in list_tools()}
    for tool in NEW_TOOLS:
        spec = tools[tool]
        assert spec["description"].strip(), tool
        schema = spec["inputSchema"]
        assert schema.get("type") == "object"
        assert "properties" in schema
        # required keys must all be declared properties
        for key in schema.get("required", []):
            assert key in schema["properties"], f"{tool}: required '{key}' undeclared"


def test_cookie_get_defaults_to_redaction():
    spec = next(s for s in TOOL_SPECS if s["name"] == "profile_cookies_get")
    props = spec["inputSchema"]["properties"]
    assert "redact_values" in props
    assert props["redact_values"].get("default") is True


# --- argument validation -----------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("profile_cookies_get", {"urls": "not-a-list"}),
        ("profile_cookies_get", {"redact_values": "yes"}),
        ("profile_cookies_set", {"cookies": [1, 2, 3]}),
        ("profile_cookies_set", {}),
        ("profile_cookies_delete", {}),
        ("profile_open_tab", {}),
        ("profile_close_tab", {"profile_id": "p", "index": -1}),
        ("profile_close_tab", {"profile_id": "p", "index": 0, "tab_index": 1}),
        ("profile_screenshot", {}),
        ("profile_pdf", {}),
        ("profile_coherence", {}),
        ("profile_config_set", {"profile_id": "p", "setting": "engine", "value": "bogus"}),
        ("profile_config_set", {"profile_id": "p", "setting": "browser", "value": "x"}),
        ("profile_tabs", {"profile_id": " "}),
    ],
)
def test_invalid_arguments_rejected(tool: str, arguments: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        validate_tool_arguments(tool, arguments)


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("profile_cookies_get", {"profile_id": "p"}),
        ("profile_cookies_get", {"profile_id": "p", "urls": ["https://x.com"], "redact_values": False}),
        (
            "profile_cookies_set",
            {"profile_id": "p", "cookies": [{"name": "sid", "value": "v", "url": "https://x.com"}]},
        ),
        ("profile_cookies_delete", {"profile_id": "p", "entries": [{"name": "sid", "domain": "x.com"}]}),
        ("profile_cookies_delete", {"profile_id": "p", "urls": ["https://x.com"]}),
        ("profile_tabs", {"profile_id": "p"}),
        ("profile_open_tab", {"profile_id": "p", "url": "https://example.com"}),
        ("profile_close_tab", {"profile_id": "p", "index": 1}),
        ("profile_screenshot", {"profile_id": "p"}),
        ("profile_pdf", {"profile_id": "p"}),
        ("profile_coherence", {"profile_id": "p"}),
        ("profile_config_get", {"profile_id": "p"}),
        ("profile_config_set", {"profile_id": "p", "setting": "timezone", "value": "Europe/Berlin"}),
        ("profile_config_set", {"profile_id": "p", "setting": "locale", "value": "de-DE"}),
    ],
)
def test_valid_arguments_accepted(tool: str, arguments: dict[str, Any]) -> None:
    assert validate_tool_arguments(tool, dict(arguments)) == arguments


# --- dispatcher routing ------------------------------------------------------


class FakeIPC:
    """Captures send_controller_command calls; returns canned results."""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __call__(
        self, data_dir: str, cmd: str, args: dict[str, Any] | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        self.calls.append((data_dir, cmd, dict(args or {})))
        return self.result


@pytest.fixture()
def mcp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolated data root with one profile; returns (dispatcher, profile)."""
    from profiledock.cli_support import _paths, _paths_prepared
    from profiledock.commands.mcp import MCPToolDispatcher
    from profiledock.data_root import resolve_data_root
    from profiledock.profile_manager import ProfileManager

    monkeypatch.setenv("PROFILEDOCK_DATA_ROOT", str(tmp_path / "data"))
    _paths.set(None)
    _paths_prepared.set(False)
    paths = resolve_data_root(prepare=True)
    manager = ProfileManager(paths)
    profile = manager.create("ToolSurface")
    yield MCPToolDispatcher(), profile


def test_dispatcher_cookies_get_redacts_by_default(mcp_env, monkeypatch: pytest.MonkeyPatch) -> None:
    dispatcher, profile = mcp_env
    from profiledock.commands import mcp as mcp_module

    fake = FakeIPC(
        {"status": "ok", "cookies": [{"name": "sid", "value": "secret123", "domain": "x.com", "path": "/"}]}
    )
    monkeypatch.setattr(mcp_module, "_send_ipc", fake)
    result = dispatcher.call_tool("profile_cookies_get", {"profile_id": profile.id})
    assert result["cookies"][0]["value"] == ""
    assert fake.calls[0][1] == "cookies"


def test_dispatcher_cookies_get_unredacted_when_explicit(mcp_env, monkeypatch: pytest.MonkeyPatch) -> None:
    dispatcher, profile = mcp_env
    from profiledock.commands import mcp as mcp_module

    fake = FakeIPC(
        {"status": "ok", "cookies": [{"name": "sid", "value": "secret123", "domain": "x.com", "path": "/"}]}
    )
    monkeypatch.setattr(mcp_module, "_send_ipc", fake)
    result = dispatcher.call_tool("profile_cookies_get", {"profile_id": profile.id, "redact_values": False})
    assert result["cookies"][0]["value"] == "secret123"


def test_dispatcher_cookies_get_applies_filters(mcp_env, monkeypatch: pytest.MonkeyPatch) -> None:
    dispatcher, profile = mcp_env
    from profiledock.commands import mcp as mcp_module

    fake = FakeIPC(
        {
            "status": "ok",
            "cookies": [
                {"name": "a", "value": "1", "domain": "x.com", "path": "/", "expires": -1},
                {"name": "b", "value": "2", "domain": "y.com", "path": "/", "expires": 9999999999},
            ],
        }
    )
    monkeypatch.setattr(mcp_module, "_send_ipc", fake)
    result = dispatcher.call_tool(
        "profile_cookies_get",
        {"profile_id": profile.id, "domains": ["x.com"], "session_only": True, "redact_values": False},
    )
    assert [c["name"] for c in result["cookies"]] == ["a"]


def test_dispatcher_cookies_set_and_delete_route(mcp_env, monkeypatch: pytest.MonkeyPatch) -> None:
    dispatcher, profile = mcp_env
    from profiledock.commands import mcp as mcp_module

    fake = FakeIPC({"status": "ok", "added": 1, "total_cookies": 2})
    monkeypatch.setattr(mcp_module, "_send_ipc", fake)
    result = dispatcher.call_tool(
        "profile_cookies_set",
        {"profile_id": profile.id, "cookies": [{"name": "sid", "value": "v", "url": "https://x.com"}]},
    )
    assert result["added"] == 1
    assert fake.calls[-1][1] == "set_cookies"

    fake2 = FakeIPC({"status": "ok", "deleted": 1, "total_cookies": 1})
    monkeypatch.setattr(mcp_module, "_send_ipc", fake2)
    result = dispatcher.call_tool(
        "profile_cookies_delete",
        {"profile_id": profile.id, "entries": [{"name": "sid", "domain": "x.com", "path": "/"}]},
    )
    assert result["deleted"] == 1
    assert fake2.calls[-1][1] == "delete_cookies"


def test_dispatcher_tabs_open_close_route(mcp_env, monkeypatch: pytest.MonkeyPatch) -> None:
    dispatcher, profile = mcp_env
    from profiledock.commands import mcp as mcp_module

    fake = FakeIPC({"status": "ok", "tabs": [{"index": 0, "title": "T", "url": "https://x.com"}]})
    monkeypatch.setattr(mcp_module, "_send_ipc", fake)
    result = dispatcher.call_tool("profile_tabs", {"profile_id": profile.id})
    assert result["tabs"][0]["index"] == 0

    fake_open = FakeIPC({"status": "ok", "tab": {"index": 1, "url": "https://example.com", "title": ""}})
    monkeypatch.setattr(mcp_module, "_send_ipc", fake_open)
    result = dispatcher.call_tool(
        "profile_open_tab", {"profile_id": profile.id, "url": "https://example.com"}
    )
    assert result["tab"]["index"] == 1

    fake_close = FakeIPC({"status": "ok", "remaining_tabs": 1})
    monkeypatch.setattr(mcp_module, "_send_ipc", fake_close)
    result = dispatcher.call_tool("profile_close_tab", {"profile_id": profile.id, "index": 1})
    assert result["remaining_tabs"] == 1


def test_dispatcher_screenshot_returns_image_content(
    mcp_env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatcher, profile = mcp_env
    from profiledock.commands import mcp as mcp_module

    png_bytes = b"\x89PNG\r\n\x1a\n" + b"0" * 32

    def fake_send(
        data_dir: str, cmd: str, args: dict[str, Any] | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        Path(args["output"]).write_bytes(png_bytes)
        return {
            "status": "ok",
            "output": args["output"],
            "url": "https://x.com",
            "title": "T",
            "bytes": len(png_bytes),
        }

    monkeypatch.setattr(mcp_module, "_send_ipc", fake_send)
    result = dispatcher.call_tool("profile_screenshot", {"profile_id": profile.id})
    image = result["content"][0]
    assert image["type"] == "image"
    assert base64.b64decode(image["data"]) == png_bytes
    assert image["mimeType"] == "image/png"


def test_dispatcher_pdf_returns_resource_content(
    mcp_env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatcher, profile = mcp_env
    from profiledock.commands import mcp as mcp_module

    pdf_bytes = b"%PDF-1.4 fake"

    def fake_send(
        data_dir: str, cmd: str, args: dict[str, Any] | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        Path(args["output"]).write_bytes(pdf_bytes)
        return {
            "status": "ok",
            "output": args["output"],
            "url": "https://x.com",
            "title": "T",
            "bytes": len(pdf_bytes),
        }

    monkeypatch.setattr(mcp_module, "_send_ipc", fake_send)
    result = dispatcher.call_tool("profile_pdf", {"profile_id": profile.id})
    blob = result["content"][0]
    assert blob["type"] == "resource"
    assert base64.b64decode(blob["resource"]["blob"]) == pdf_bytes
    assert blob["resource"]["mimeType"] == "application/pdf"


def test_dispatcher_config_get_redacts_proxy(mcp_env) -> None:
    dispatcher, profile = mcp_env
    dispatcher.call_tool(
        "profile_config_set",
        {"profile_id": profile.id, "setting": "proxy", "value": "http://user:secret@proxy.example.com:8080"},
    )
    result = dispatcher.call_tool("profile_config_get", {"profile_id": profile.id})
    assert result["launch_config"]["proxy"] == "http://user:***@proxy.example.com:8080"


def test_dispatcher_config_set_allowed_settings_only(mcp_env) -> None:
    dispatcher, profile = mcp_env
    result = dispatcher.call_tool(
        "profile_config_set", {"profile_id": profile.id, "setting": "timezone", "value": "Asia/Tokyo"}
    )
    assert result["launch_config"]["timezone"] == "Asia/Tokyo"
    with pytest.raises(ValueError, match=r"(not supported|invalid value for setting)"):
        dispatcher.call_tool(
            "profile_config_set", {"profile_id": profile.id, "setting": "start_urls", "value": "x"}
        )


def test_dispatcher_config_set_rejects_bad_values(mcp_env) -> None:
    dispatcher, profile = mcp_env
    with pytest.raises(ValueError):
        dispatcher.call_tool(
            "profile_config_set", {"profile_id": profile.id, "setting": "timezone", "value": "Not/AZone"}
        )


def test_dispatcher_coherence_direct_profile(mcp_env) -> None:
    dispatcher, profile = mcp_env
    result = dispatcher.call_tool("profile_coherence", {"profile_id": profile.id})
    assert result["score"] == 100
    assert result["is_coherent"] is True


@pytest.mark.parametrize(
    "tool,block",
    [
        ("profile_screenshot", {"type": "image", "data": "AA==", "mimeType": "image/png"}),
        (
            "profile_pdf",
            {
                "type": "resource",
                "resource": {
                    "uri": "profiledock://p/export.pdf",
                    "blob": "AA==",
                    "mimeType": "application/pdf",
                },
            },
        ),
    ],
)
def test_capture_content_reaches_wire(tool, block):
    import io
    import json

    from profiledock.mcp_server import serve_stdio

    class Dispatcher:
        def call_tool(self, name, arguments):
            return {"profile_id": "p", "content": [block]}

    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": {"profile_id": "p"}},
    }
    output = io.StringIO()
    serve_stdio(Dispatcher(), io.StringIO(json.dumps(request) + "\n"), output)
    result = json.loads(output.getvalue())["result"]
    assert result["isError"] is False
    assert result["content"][1] == block
    assert json.loads(result["content"][0]["text"]) == {"profile_id": "p"}
