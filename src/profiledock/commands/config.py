"""Launch configuration preset commands: config show, set, add-url, remove-url, reset."""

from pathlib import Path

import typer

from ..cli_support import (
    emit_json,
    fail,
    fail_exception,
    redact_proxy,
    resolve_engine,
)
from ..models import LaunchConfig
from ..profile_manager import AmbiguousProfileError, ProfileManager, ProfileNotFoundError
from ..storage import StorageError
from ..validation import (
    ValidationError,
    validate_browser,
    validate_locale,
    validate_proxy,
    validate_time_zone,
    validate_user_agent,
)


def _get_manager() -> ProfileManager:
    from ..cli import manager

    return manager()


def config_show_command(
    profile_id: str = typer.Argument(..., help="Profile identifier."),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format."),
) -> None:
    """Show a profile's stored launch preset.

    Values left as None are inherited from the profile or defaults at launch time.
    """
    from ..cli import _render_table

    try:
        profile = _get_manager().resolve(profile_id)
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError) as exc:
        fail_exception(exc)
    cfg = getattr(profile, "launch_config", None) or LaunchConfig()
    if json_output:
        config_dict = cfg.to_dict()
        config_dict["proxy"] = redact_proxy(config_dict.get("proxy"))
        emit_json("config show", config_dict)
        return
    rows = [
        ["Profile:", f"{profile.name} ({profile.id})"],
        ["Default Tabs:", str(cfg.default_tabs) if cfg.default_tabs is not None else "None"],
        ["Engine:", cfg.engine or "None (inherits profile/default)"],
        ["Browser:", cfg.browser or "None (auto-detect)"],
        [
            "Window Size:",
            f"{cfg.window_width}x{cfg.window_height}" if cfg.window_width and cfg.window_height else "None",
        ],
        ["Start URLs:", ", ".join(cfg.start_urls) if cfg.start_urls else "None"],
        ["Proxy:", redact_proxy(cfg.proxy) or "None (direct connection)"],
        ["User Agent:", cfg.user_agent or "None (browser default)"],
        ["Locale:", cfg.locale or "None (browser default)"],
        ["Timezone:", cfg.timezone or "None (system)"],
    ]
    typer.echo(_render_table(rows))


def config_set_command(
    profile_id: str = typer.Argument(..., help="Profile identifier."),
    setting: str = typer.Argument(..., help="Setting name (default-tabs, engine, browser, window-size)."),
    value: str = typer.Argument(..., help="Setting value."),
) -> None:
    """Store one launch-preset value for a profile.

    Explicit launch flags override presets for a single launch.
    """
    clean_setting = setting.strip().lower()
    clean_val = value.strip()
    profile_manager = _get_manager()

    try:
        profile = profile_manager.resolve(profile_id)
        old_cfg = getattr(profile, "launch_config", None)

        def _old(field: str) -> str:
            val = getattr(old_cfg, field, None) if old_cfg else None
            return "(unset)" if val is None else str(val)

        if clean_setting == "default-tabs":
            if not clean_val.isdigit() or int(clean_val) < 1:
                fail("default-tabs must be a positive integer >= 1")
            profile_manager.update_launch_config(profile_id, default_tabs=int(clean_val))
            typer.echo(f"Set default-tabs to {clean_val} for profile '{profile.name}' ({profile.id})")
            typer.echo(f"  {_old('default_tabs')} -> {clean_val}")
        elif clean_setting == "engine":
            val_eng = clean_val.lower()
            if val_eng not in ("direct", "playwright"):
                fail("engine must be 'direct' or 'playwright'")
            profile_manager.update_launch_config(profile_id, engine=val_eng)
            typer.echo(f"Set engine to '{val_eng}' for profile '{profile.name}' ({profile.id})")
            typer.echo(f"  {_old('engine')} -> {val_eng}")
        elif clean_setting == "browser":
            effective_engine = resolve_engine(None, profile)
            candidate = Path(clean_val).expanduser()
            stored_browser = str(candidate.resolve()) if candidate.is_file() else clean_val.lower()
            validate_browser(stored_browser, effective_engine, require_executable=True)
            profile_manager.update_launch_config(profile_id, browser=stored_browser)
            typer.echo(f"Set browser to '{stored_browser}' for profile '{profile.name}' ({profile.id})")
            typer.echo(f"  {_old('browser')} -> {stored_browser}")
        elif clean_setting == "window-size":
            parts = clean_val.lower().split("x")
            if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
                fail("window-size must be in format <width>x<height> (e.g. 1280x720)")
            w, h = int(parts[0]), int(parts[1])
            if w < 100 or h < 100:
                fail("width and height must be at least 100")
            profile_manager.update_launch_config(profile_id, window_width=w, window_height=h)
            typer.echo(f"Set window-size to {w}x{h} for profile '{profile.name}' ({profile.id})")
            old_size = (
                f"{old_cfg.window_width}x{old_cfg.window_height}"
                if old_cfg and old_cfg.window_width
                else "(unset)"
            )
            typer.echo(f"  {old_size} -> {w}x{h}")
        elif clean_setting == "proxy":
            if clean_val.lower() in ("none", "unset", "clear"):
                target: str | None = None
            else:
                validate_proxy(clean_val)
                target = clean_val
            profile_manager.update_launch_config(profile_id, proxy=target)
            shown = redact_proxy(target) or "(cleared)"
            typer.echo(f"Set proxy to '{shown}' for profile '{profile.name}' ({profile.id})")
            typer.echo(f"  {redact_proxy(_old('proxy') if _old('proxy') != '(unset)' else None) or '(unset)'}"
                       f" -> {shown}")
        elif clean_setting == "user-agent":
            validate_user_agent(clean_val)
            profile_manager.update_launch_config(profile_id, user_agent=clean_val)
            typer.echo(f"Set user-agent for profile '{profile.name}' ({profile.id})")
            typer.echo(f"  {_old('user_agent')} -> {clean_val}")
        elif clean_setting == "locale":
            validate_locale(clean_val)
            profile_manager.update_launch_config(profile_id, locale=clean_val)
            typer.echo(f"Set locale to '{clean_val}' for profile '{profile.name}' ({profile.id})")
            typer.echo(f"  {_old('locale')} -> {clean_val}")
        elif clean_setting == "timezone":
            validate_time_zone(clean_val)
            profile_manager.update_launch_config(profile_id, timezone=clean_val)
            typer.echo(f"Set timezone to '{clean_val}' for profile '{profile.name}' ({profile.id})")
            typer.echo(f"  {_old('timezone')} -> {clean_val}")
        else:
            fail(
                f"unknown setting '{setting}' (valid: default-tabs, engine, browser, window-size, "
                "proxy, user-agent, locale, timezone)"
            )
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError, ValidationError, ValueError) as exc:
        fail_exception(exc)


def config_add_url_command(
    profile_id: str = typer.Argument(..., help="Profile identifier."),
    url: str = typer.Argument(..., help="URL to add."),
) -> None:
    """Validate and append a start URL to the launch preset (no duplicates)."""
    try:
        profile = _get_manager().add_start_url(profile_id, url)
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError, ValidationError, ValueError) as exc:
        fail_exception(exc)
    typer.echo(f"Added start URL '{url}' to profile '{profile.name}' ({profile.id})")


def config_remove_url_command(
    profile_id: str = typer.Argument(..., help="Profile identifier."),
    url: str = typer.Argument(..., help="URL to remove; must match the stored normalized value."),
) -> None:
    """Remove one start URL from the launch preset."""
    try:
        profile = _get_manager().remove_start_url(profile_id, url)
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError, ValidationError, ValueError) as exc:
        fail_exception(exc)
    typer.echo(f"Removed start URL '{url}' from profile '{profile.name}' ({profile.id})")


def config_reset_command(
    profile_id: str = typer.Argument(..., help="Profile identifier."),
) -> None:
    """Clear the complete launch preset, restoring inherited defaults."""
    try:
        profile = _get_manager().reset_launch_config(profile_id)
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError, ValidationError, ValueError) as exc:
        fail_exception(exc)
    typer.echo(f"Reset launch configuration for profile '{profile.name}' ({profile.id})")
