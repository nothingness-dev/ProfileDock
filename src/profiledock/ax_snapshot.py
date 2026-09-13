from __future__ import annotations

import re
from typing import Any

MAX_SNAPSHOT_DEPTH = 12
MAX_SNAPSHOT_NODES = 200
TRUNCATION_NOTE = f"... (truncated: showing first {MAX_SNAPSHOT_NODES} nodes)"
_REGISTRY = "profiledock.snapshot.refs"


def _snapshot_entries(ax_tree: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    stack = [(ax_tree, 0)]
    seen: set[int] = set()
    while stack and len(entries) <= MAX_SNAPSHOT_NODES:
        node, depth = stack.pop()
        if not isinstance(node, dict) or depth > MAX_SNAPSHOT_DEPTH or id(node) in seen:
            continue
        seen.add(id(node))
        if str(node.get("name", "")).strip():
            entries.append(node)
        children = node.get("children", [])
        if isinstance(children, list):
            stack.extend((child, depth + 1) for child in reversed(children))
    return entries


def render_snapshot(ax_tree: dict[str, Any]) -> str:
    entries = _snapshot_entries(ax_tree)
    lines = []
    for index, node in enumerate(entries[:MAX_SNAPSHOT_NODES], 1):
        name = str(node.get("name", "")).strip().replace("\\", "\\\\").replace("'", "\\'")
        name = name.replace("\n", " ").replace("\r", " ")
        lines.append(f"[@e{index}: {node.get('role') or 'unknown'} '{name}']")
    if len(entries) > MAX_SNAPSHOT_NODES:
        lines.append(TRUNCATION_NOTE)
    return "\n".join(lines)


def resolve_ref(ax_tree: dict[str, Any], ref: str) -> dict[str, Any] | None:
    if not isinstance(ref, str) or re.fullmatch(r"@e[1-9][0-9]*", ref) is None:
        return None
    index = int(ref[2:]) - 1
    entries = _snapshot_entries(ax_tree)[:MAX_SNAPSHOT_NODES]
    return entries[index] if index < len(entries) else None


def _capture_tree(page: Any) -> dict[str, Any]:
    session = page.context.new_cdp_session(page)
    try:
        nodes = session.send("Accessibility.getFullAXTree")["nodes"]
        by_id = {
            node["nodeId"]: {
                "role": node.get("role", {}).get("value", "unknown"),
                "name": "" if node.get("ignored") else node.get("name", {}).get("value", ""),
                "backend_id": node.get("backendDOMNodeId"),
                "children": [],
            }
            for node in nodes
        }
        for node in nodes:
            by_id[node["nodeId"]]["children"] = [
                by_id[child] for child in node.get("childIds", []) if child in by_id
            ]
        tree = by_id[nodes[0]["nodeId"]] if nodes else {}
        page.evaluate("(key) => { globalThis[Symbol.for(key)] = new Map(); }", _REGISTRY)
        for index, node in enumerate(_snapshot_entries(tree)[:MAX_SNAPSHOT_NODES], 1):
            if not node.get("backend_id"):
                continue
            obj = session.send("DOM.resolveNode", {"backendNodeId": node["backend_id"]})["object"]
            object_id = obj.get("objectId")
            if not object_id:
                continue
            try:
                session.send(
                    "Runtime.callFunctionOn",
                    {
                        "objectId": object_id,
                        "functionDeclaration": (
                            "function(key, ref) { const registry = this.ownerDocument?.defaultView"
                            "?.[Symbol.for(key)]; if (registry) registry.set(ref, this); }"
                        ),
                        "arguments": [{"value": _REGISTRY}, {"value": f"@e{index}"}],
                    },
                )
            finally:
                session.send("Runtime.releaseObject", {"objectId": object_id})
        return tree
    finally:
        session.detach()


def execute_snapshot_command(page: Any, cmd: str, args: dict[str, Any]) -> dict[str, Any]:
    if cmd == "snapshot":
        tree = _capture_tree(page)
        return {"status": "ok", "url": page.url, "title": page.title(), "snapshot": render_snapshot(tree)}
    action, ref, value = args.get("action"), args.get("ref"), args.get("value")
    if not isinstance(action, str) or action not in {"click", "fill", "press", "select"}:
        raise ValueError("unsupported interaction action")
    if not isinstance(ref, str) or re.fullmatch(r"@e[1-9][0-9]*", ref) is None:
        raise ValueError("ref must be a snapshot @eN reference")
    if action in {"fill", "select"} and not isinstance(value, str):
        raise ValueError(f"{action} requires a string value")
    if action == "press":
        value = "Enter" if value is None else value
        if not isinstance(value, str) or not value:
            raise ValueError("press requires a non-empty string value")
    handle = page.evaluate_handle(
        "([key, ref]) => { const node = globalThis[Symbol.for(key)]?.get(ref);"
        " return node?.isConnected ? node : null; }",
        [_REGISTRY, ref],
    )
    try:
        target = handle.as_element()
        if target is None:
            raise ValueError(f"unknown or stale snapshot ref: {ref}; request a new snapshot")
        if action == "click":
            target.click(timeout=10000)
        elif action == "fill":
            target.fill(value, timeout=10000)
        elif action == "press":
            target.press(value, timeout=10000)
        else:
            target.select_option(value, timeout=10000)
        return {"status": "ok", "action": action, "ref": ref}
    finally:
        handle.dispose()
