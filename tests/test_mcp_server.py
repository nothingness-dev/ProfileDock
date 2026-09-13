import io
import json
import re

EXPECTED_TOOLS = [
    "profile_list",
    "profile_launch",
    "profile_read_state",
    "profile_get_snapshot",
    "profile_interact",
    "profile_eval",
    "profile_close",
]


class FakeDispatcher:
    def __init__(self, results=None):
        self.calls = []
        self.results = results or {}

    def call_tool(self, name, arguments=None, *args, **kwargs):
        if arguments is None:
            arguments = args[0] if args else kwargs.get("arguments", {})
        self.calls.append((name, arguments))
        if name not in EXPECTED_TOOLS:
            raise KeyError(name)
        return self.results.get(name, {"ok": True})


def _sample_tree():
    return {
        "role": "WebArea",
        "name": "root",
        "children": [
            {"role": "button", "name": "Submit"},
            {"role": "link", "name": "Home"},
        ],
    }


def test_list_tools_exposes_exact_contract():
    from profiledock.mcp_server import list_tools

    tools = list_tools()
    assert [t["name"] for t in tools] == EXPECTED_TOOLS
    for tool in tools:
        assert tool["description"]
        schema = tool["inputSchema"]
        assert isinstance(schema, dict)
        assert schema.get("type") == "object"


def test_stdio_framing_one_json_line_per_response():
    from profiledock.mcp_server import serve_stdio

    dispatcher = FakeDispatcher({"profile_list": {"profiles": []}})
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "profile_list", "arguments": {}},
    }
    stdin = io.StringIO(json.dumps(req) + "\n")
    stdout = io.StringIO()
    serve_stdio(dispatcher, stdin, stdout)
    lines = stdout.getvalue().splitlines()
    assert len(lines) == 1
    resp = json.loads(lines[0])
    assert resp["id"] == 1
    assert "result" in resp


def test_stdio_unknown_tool_returns_method_not_found():
    from profiledock.mcp_server import serve_stdio

    dispatcher = FakeDispatcher()
    req = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "no_such_tool", "arguments": {}},
    }
    stdin = io.StringIO(json.dumps(req) + "\n")
    stdout = io.StringIO()
    serve_stdio(dispatcher, stdin, stdout)
    resp = json.loads(stdout.getvalue().splitlines()[0])
    assert resp["id"] == 7
    assert resp["error"]["code"] == -32602


def test_snapshot_renderer_assigns_deterministic_refs():
    from profiledock.mcp_server import render_snapshot

    first = render_snapshot(_sample_tree())
    second = render_snapshot(_sample_tree())
    assert first == second
    assert "[@e1: WebArea 'root']" in first
    assert "[@e2: button 'Submit']" in first
    assert "[@e3: link 'Home']" in first
    assert re.search(r"\[@e\d+: \w+ '.*'\]", first)
