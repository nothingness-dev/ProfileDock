from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Protocol

from .cli_support import redact_proxy
from .version import __version__

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "profile_list",
        "description": "List profiles with effective engines and runtime statuses.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "profile_launch",
        "description": "Launch a profile session or attach to the running instance.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "profile_id": {"type": "string"},
                "headless": {"type": "boolean"},
                "tabs": {"type": "integer"},
            },
            "required": ["profile_id"],
        },
    },
    {
        "name": "profile_read_state",
        "description": "Read a tab URL, title, and Markdown content.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "profile_id": {"type": "string"},
                "tab_index": {"type": "integer"},
            },
            "required": ["profile_id"],
        },
    },
    {
        "name": "profile_get_snapshot",
        "description": "Compact accessibility-tree snapshot with stable @eN refs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "profile_id": {"type": "string"},
                "tab_index": {"type": "integer"},
            },
            "required": ["profile_id"],
        },
    },
    {
        "name": "profile_interact",
        "description": "Click, fill, press, or select an @eN snapshot ref.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "profile_id": {"type": "string"},
                "action": {"type": "string"},
                "ref": {"type": "string"},
                "value": {"type": "string"},
                "tab_index": {"type": "integer"},
            },
            "required": ["profile_id", "action", "ref"],
        },
    },
    {
        "name": "profile_eval",
        "description": "Evaluate JavaScript in the authenticated page with a timeout.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "profile_id": {"type": "string"},
                "script": {"type": "string"},
                "tab_index": {"type": "integer"},
            },
            "required": ["profile_id", "script"],
        },
    },
    {
        "name": "profile_close",
        "description": "Gracefully terminate the session and free locks.",
        "inputSchema": {
            "type": "object",
            "properties": {"profile_id": {"type": "string"}},
            "required": ["profile_id"],
        },
    },
]

_TOOL_NAMES = frozenset(spec["name"] for spec in TOOL_SPECS)


class ToolDispatcher(Protocol):
    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


def list_tools() -> list[dict[str, Any]]:
    return deepcopy(TOOL_SPECS)


def render_snapshot(ax_tree: dict[str, Any]) -> str:
    from .ax_snapshot import render_snapshot as render

    return render(ax_tree)


def validate_tool_arguments(name: str, arguments: Any) -> dict[str, Any]:
    if not isinstance(name, str) or name not in _TOOL_NAMES:
        raise ValueError("unknown tool")
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be an object")
    schema = next(spec["inputSchema"] for spec in TOOL_SPECS if spec["name"] == name)
    properties = schema["properties"]
    if set(arguments) - set(properties):
        raise ValueError("unknown tool argument")
    if any(key not in arguments for key in schema.get("required", [])):
        raise ValueError("missing required tool argument")
    types = {"string": str, "integer": int, "boolean": bool}
    for key, value in arguments.items():
        if type(value) is not types[properties[key]["type"]]:
            raise ValueError(f"invalid type for {key}")
    if "profile_id" in arguments and not arguments["profile_id"].strip():
        raise ValueError("profile_id must not be empty")
    if "tabs" in arguments and arguments["tabs"] < 1:
        raise ValueError("tabs must be at least 1")
    if "tab_index" in arguments and arguments["tab_index"] < 0:
        raise ValueError("tab_index must not be negative")
    return dict(arguments)


def _handle_request(dispatcher: Any, request: Any) -> dict[str, Any] | None:
    envelope: dict[str, Any] = {"jsonrpc": "2.0", "id": None}
    if not isinstance(request, dict):
        return {**envelope, "error": {"code": -32600, "message": "invalid request"}}
    if "id" in request and type(request["id"]) not in (int, str):
        return {**envelope, "error": {"code": -32600, "message": "invalid request ID"}}
    envelope["id"] = request.get("id")
    method = request.get("method")
    if request.get("jsonrpc") != "2.0" or not isinstance(method, str):
        return {**envelope, "error": {"code": -32600, "message": "invalid request"}}
    if "id" not in request:
        return None
    params = request.get("params", {})
    if not isinstance(params, dict):
        return {**envelope, "error": {"code": -32602, "message": "params must be an object"}}
    if method == "initialize":
        return {
            **envelope,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "profiledock", "version": __version__},
            },
        }
    if method == "ping":
        return {**envelope, "result": {}}
    if method == "tools/list":
        return {**envelope, "result": {"tools": list_tools()}}
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or name not in _TOOL_NAMES:
            return {**envelope, "error": {"code": -32602, "message": "unknown tool"}}
        try:
            arguments = validate_tool_arguments(name, params.get("arguments", {}))
        except ValueError as exc:
            return {**envelope, "error": {"code": -32602, "message": str(exc)}}
        try:
            result = dispatcher.call_tool(name, arguments)
            content = json.dumps(result, allow_nan=False)
            failed = False
        except Exception as exc:
            content = redact_proxy(str(exc)) or "tool execution failed"
            failed = True
        return {
            **envelope,
            "result": {
                "content": [{"type": "text", "text": content}],
                "isError": failed,
            },
        }
    return {**envelope, "error": {"code": -32601, "message": "unknown method"}}


def serve_stdio(dispatcher: Any, stdin: Any = None, stdout: Any = None) -> None:
    import sys

    if stdin is None:
        stdin = sys.stdin
        if hasattr(stdin, "reconfigure"):
            stdin.reconfigure(encoding="utf-8")
    if stdout is None:
        stdout = sys.stdout
        if hasattr(stdout, "reconfigure"):
            stdout.reconfigure(encoding="utf-8")
    limit = 65536
    while True:
        raw = stdin.readline(limit + 1)
        if not raw:
            break
        if len(raw) > limit:
            while raw and not raw.endswith("\n"):
                raw = stdin.readline(limit + 1)
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32600,
                    "message": "request exceeds maximum size",
                },
            }
        elif not raw.strip():
            continue
        else:
            try:
                request = json.loads(raw)
                response = _handle_request(dispatcher, request)
            except (json.JSONDecodeError, ValueError, TypeError, UnicodeError, RecursionError):
                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32700,
                        "message": "parse error",
                    },
                }
        if response is not None:
            stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            stdout.flush()
