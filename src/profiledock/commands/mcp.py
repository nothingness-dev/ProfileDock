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


def _send_ipc(data_dir: str, cmd: str, args: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    from ..cli import send_controller_command

    return send_controller_command(data_dir, cmd=cmd, args=args, **kwargs)


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
        if name == "profile_cookies_get":
            profile = _resolve(manager, str(args.get("profile_id", "")))
            from ..commands.automation import _apply_cookie_filters

            urls = args.get("urls")
            res = _send_ipc(
                profile.data_dir,
                "cookies",
                args={"urls": urls} if urls is not None else {},
                runtime_dir=runtime_path(profile),
                auto_start_headless=True,
                **_identity_kwargs(profile),
            )
            cookies = _apply_cookie_filters(
                res.get("cookies", []),
                domains=args.get("domains"),
                session_only=bool(args.get("session_only", False)),
            )
            if args.get("redact_values", True):
                cookies = [{**c, "value": ""} for c in cookies]
            return {"profile_id": profile.id, "cookies": cookies}
        if name == "profile_cookies_set":
            profile = _resolve(manager, str(args.get("profile_id", "")))
            return _send_ipc(
                profile.data_dir,
                "set_cookies",
                args={"set_cookies": args.get("cookies", [])},
                runtime_dir=runtime_path(profile),
                auto_start_headless=True,
                **_identity_kwargs(profile),
            )
        if name == "profile_cookies_delete":
            profile = _resolve(manager, str(args.get("profile_id", "")))
            delete_args: dict[str, Any] = {}
            if args.get("entries") is not None:
                delete_args["delete_cookies"] = args["entries"]
            if args.get("urls") is not None:
                delete_args["urls"] = args["urls"]
            return _send_ipc(
                profile.data_dir,
                "delete_cookies",
                args=delete_args,
                runtime_dir=runtime_path(profile),
                auto_start_headless=True,
                **_identity_kwargs(profile),
            )
        if name == "profile_tabs":
            profile = _resolve(manager, str(args.get("profile_id", "")))
            return _send_ipc(
                profile.data_dir,
                "tabs",
                runtime_dir=runtime_path(profile),
                auto_start_headless=False,
            )
        if name == "profile_open_tab":
            profile = _resolve(manager, str(args.get("profile_id", "")))
            return _send_ipc(
                profile.data_dir,
                "open_tab",
                args={"url": args.get("url", "about:blank")},
                runtime_dir=runtime_path(profile),
                auto_start_headless=True,
                **_identity_kwargs(profile),
            )
        if name == "profile_close_tab":
            profile = _resolve(manager, str(args.get("profile_id", "")))
            return _send_ipc(
                profile.data_dir,
                "close_tab",
                args={"index": args.get("index", 0)},
                runtime_dir=runtime_path(profile),
                auto_start_headless=False,
            )
        if name in {"profile_screenshot", "profile_pdf"}:
            import base64
            import tempfile
            from pathlib import Path as _Path

            profile = _resolve(manager, str(args.get("profile_id", "")))
            suffix = ".png" if name == "profile_screenshot" else ".pdf"
            handle = tempfile.NamedTemporaryFile(prefix="profiledock-mcp-", suffix=suffix, delete=False)  # noqa: SIM115 - closed immediately; path handed to the controller
            output_path = handle.name
            handle.close()
            ipc_args: dict[str, Any] = {"tab": args.get("tab_index", 0), "output": output_path}
            if args.get("url"):
                ipc_args["url"] = args["url"]
            if name == "profile_screenshot":
                ipc_args["full_page"] = bool(args.get("full_page", False))
            try:
                res = _send_ipc(
                    profile.data_dir,
                    "screenshot" if name == "profile_screenshot" else "pdf",
                    args=ipc_args,
                    runtime_dir=runtime_path(profile),
                    auto_start_headless=True,
                    **_identity_kwargs(profile),
                )
                blob = _Path(output_path).read_bytes()
            finally:
                try:
                    _Path(output_path).unlink(missing_ok=True)
                except OSError:
                    pass
            encoded = base64.b64encode(blob).decode("ascii")
            content: list[dict[str, Any]]
            if name == "profile_screenshot":
                content = [{"type": "image", "data": encoded, "mimeType": "image/png"}]
            else:
                resource_block: dict[str, Any] = {
                    "uri": f"file:///{profile.id}/export.pdf",
                    "mimeType": "application/pdf",
                    "blob": encoded,
                }
                content = [{"type": "resource", "resource": resource_block}]
            return {
                "profile_id": profile.id,
                "url": res.get("url"),
                "title": res.get("title"),
                "bytes": res.get("bytes", len(blob)),
                "content": content,
            }
        if name == "profile_coherence":
            profile = _resolve(manager, str(args.get("profile_id", "")))
            from ..coherence import check_coherence

            cfg = getattr(profile, "launch_config", None)
            proxy = cfg.proxy if cfg else None
            report = check_coherence(
                proxy=proxy,
                configured_timezone=cfg.timezone if cfg else None,
                configured_locale=cfg.locale if cfg else None,
            )
            return report.to_dict(profile=profile.id)
        if name == "profile_config_get":
            profile = _resolve(manager, str(args.get("profile_id", "")))
            from ..cli_support import redact_proxy

            cfg = getattr(profile, "launch_config", None)
            config_dict = cfg.to_dict() if cfg else _default_launch_config_dict()
            config_dict["proxy"] = redact_proxy(config_dict.get("proxy"))
            return {"profile_id": profile.id, "launch_config": config_dict}
        if name == "profile_config_set":
            from ..models import LaunchConfig as _LaunchConfig

            setting = str(args.get("setting", ""))
            value = str(args.get("value", ""))
            allowed = {"default-tabs", "proxy", "user-agent", "locale", "timezone"}
            if setting not in allowed:
                raise ValueError(
                    f"setting '{setting}' is not supported (allowed: {', '.join(sorted(allowed))})"
                )
            updates: dict[str, Any] = {}
            if setting == "default-tabs":
                if not value.isdigit() or int(value) < 1:
                    raise ValueError("default-tabs must be a positive integer >= 1")
                updates["default_tabs"] = int(value)
            elif setting == "proxy":
                if value.lower() in ("none", "unset", "clear"):
                    updates["proxy"] = None
                else:
                    updates["proxy"] = value
            elif setting == "user-agent":
                updates["user_agent"] = value
            elif setting == "locale":
                updates["locale"] = value
            else:
                updates["timezone"] = value
            updated = manager.update_launch_config(str(args.get("profile_id", "")), **updates)
            cfg = getattr(updated, "launch_config", None) or _LaunchConfig()
            from ..cli_support import redact_proxy

            config_dict = cfg.to_dict()
            config_dict["proxy"] = redact_proxy(config_dict.get("proxy"))
            return {"profile_id": updated.id, "launch_config": config_dict}
        raise KeyError(name)


def _default_launch_config_dict() -> dict[str, Any]:
    from ..models import LaunchConfig

    return LaunchConfig().to_dict()


def mcp_serve_command() -> None:
    """Serve the MCP tool contract over stdio (one JSON-RPC line per message)."""
    from ..mcp_server import serve_stdio

    serve_stdio(MCPToolDispatcher())


mcp_app = typer.Typer(help="Model Context Protocol server for AI agents.")
mcp_app.command(name="serve")(mcp_serve_command)
