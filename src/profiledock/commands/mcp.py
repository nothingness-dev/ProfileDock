from __future__ import annotations

from typing import Any

import typer


def _get_manager() -> Any:
    from ..cli import manager

    return manager()


def _resolve(manager: Any, profile_id: str) -> Any:
    return manager.resolve(profile_id)


def _identity_kwargs(profile: Any) -> dict[str, Any]:
    cfg = getattr(profile, "launch_config", None)
    kwargs: dict[str, Any] = {}
    if cfg is not None:
        for field in ("proxy", "user_agent", "locale", "timezone"):
            value = getattr(cfg, field, None)
            if value:
                kwargs[field] = value
    return kwargs


class MCPToolDispatcher:
    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        from ..cli import runtime_path, send_controller_command
        from ..cli_support import resolve_engine_strict
        from ..mcp_server import validate_tool_arguments
        from ..process_manager import get_status

        args = validate_tool_arguments(name, arguments if arguments is not None else {})
        manager = _get_manager()
        if name == "profile_list":
            profiles = [
                {
                    "id": profile.id,
                    "name": profile.name,
                    "engine": resolve_engine_strict(None, profile),
                    "status": get_status(profile.data_dir, runtime_dir=runtime_path(profile)),
                }
                for profile in manager.list_profiles()
            ]
            return {"profiles": profiles}
        if name == "profile_launch":
            profile = _resolve(manager, str(args.get("profile_id", "")))
            from ..cli import start_controller, start_direct_chrome
            from ..launch_service import (
                build_launch_plan,
                controller_launch_options,
                direct_launch_options,
                resolve_launch_tabs,
            )

            runtime = runtime_path(profile)
            status = get_status(profile.data_dir, runtime_dir=runtime)
            if status == "running":
                result = send_controller_command(
                    profile.data_dir,
                    cmd="tabs",
                    runtime_dir=runtime,
                    auto_start_headless=False,
                )
                return {"profile_id": profile.id, "status": "attached", "tabs": result["tabs"]}
            if status in {"starting", "closing"}:
                raise ValueError(f"profile is {status}; retry after the operation finishes")
            headless = args.get("headless", False)
            tabs = resolve_launch_tabs(profile, args.get("tabs")) or 1
            plan = build_launch_plan(profile, tabs=tabs)
            if plan.engine == "direct":
                if headless:
                    raise ValueError("headless requires the Playwright engine")
                start_direct_chrome(
                    profile.data_dir, plan.tabs, runtime_dir=runtime, **direct_launch_options(plan)
                )
            else:
                start_controller(
                    profile.data_dir,
                    plan.tabs,
                    runtime_dir=runtime,
                    headless=headless,
                    **controller_launch_options(plan),
                )
            result = {"profile_id": profile.id, "status": "started", "tabs": plan.tabs, "engine": plan.engine}
            try:
                manager.mark_launched(profile.id)
            except Exception:
                result["warning"] = "browser launched but launch timestamp was not saved"
            return result
        if name in {"profile_read_state", "profile_get_snapshot", "profile_eval", "profile_interact"}:
            profile = _resolve(manager, str(args.get("profile_id", "")))
            tab = args.get("tab_index", 0)
            if name == "profile_read_state":
                return send_controller_command(
                    profile.data_dir,
                    cmd="read_page",
                    args={"tab": tab},
                    runtime_dir=runtime_path(profile),
                    auto_start_headless=False,
                    **_identity_kwargs(profile),
                )
            if name == "profile_get_snapshot":
                return send_controller_command(
                    profile.data_dir,
                    cmd="snapshot",
                    args={"tab": tab},
                    runtime_dir=runtime_path(profile),
                    auto_start_headless=False,
                    **_identity_kwargs(profile),
                )
            if name == "profile_eval":
                return send_controller_command(
                    profile.data_dir,
                    cmd="eval",
                    args={"script": args.get("script", ""), "tab": tab},
                    runtime_dir=runtime_path(profile),
                    auto_start_headless=False,
                    **_identity_kwargs(profile),
                )
            return send_controller_command(
                profile.data_dir,
                cmd="interact",
                args={
                    "action": args.get("action", ""),
                    "ref": args.get("ref", ""),
                    "value": args.get("value"),
                    "tab": tab,
                },
                runtime_dir=runtime_path(profile),
                auto_start_headless=False,
                **_identity_kwargs(profile),
            )
        if name == "profile_close":
            from ..cli import close_controller

            profile = _resolve(manager, str(args.get("profile_id", "")))
            close_controller(profile.data_dir, runtime_dir=runtime_path(profile))
            return {"profile_id": profile.id, "status": "closed"}
        raise KeyError(name)


def mcp_serve_command() -> None:
    """Serve the MCP tool contract over stdio (one JSON-RPC line per message)."""
    from ..mcp_server import serve_stdio

    serve_stdio(MCPToolDispatcher())


mcp_app = typer.Typer(help="Model Context Protocol server for AI agents.")
mcp_app.command(name="serve")(mcp_serve_command)
