"""Launch-plan resolution shared by the CLI and the TUI.

Both surfaces must apply identical precedence (CLI flags > launch preset >
profile engine > environment > default) and identical validation for tabs,
start URLs, browser selection, and the data directory. This module holds that
logic once, free of typer and Textual dependencies, and raises
:class:`LaunchPlanError` so each surface can render the failure in its own
style. Launcher invocation itself stays with the caller, preserving the
``profiledock.cli.*`` monkeypatch surface used by the test suite.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cli_support import resolve_engine_strict
from .validation import ValidationError, validate_browser, validate_url


class LaunchPlanError(ValueError):
    """Invalid launch parameters; carries a CLI error category."""

    category = "invalid_input"


@dataclass(frozen=True)
class LaunchPlan:
    """A fully resolved, validated set of launch parameters."""

    engine: str
    tabs: int
    urls: tuple[str, ...]
    browser: str | None
    window_width: int | None
    window_height: int | None


def resolve_launch_engine(engine: str | None, profile: Any) -> str:
    """Resolve the effective engine, raising :class:`LaunchPlanError` on bad input."""
    try:
        return resolve_engine_strict(engine, profile)
    except ValueError as exc:
        raise LaunchPlanError(str(exc)) from exc


def resolve_launch_tabs(profile: Any, tabs: int | None) -> int | None:
    """Fall back to the preset ``default-tabs``; ``None`` when still unresolved."""
    if tabs is not None:
        return tabs
    cfg = getattr(profile, "launch_config", None)
    if cfg is not None and cfg.default_tabs is not None:
        default_tabs = cfg.default_tabs
        if isinstance(default_tabs, int) and default_tabs >= 1:
            return default_tabs
    return None


def build_launch_plan(
    profile: Any,
    *,
    engine: str | None = None,
    tabs: int | None = None,
    urls: list[str] | None = None,
    browser: str | None = None,
) -> LaunchPlan:
    """Validate and resolve every launch parameter; raise before any side effect.

    ``tabs`` must be resolved by the caller first (the CLI prompts; the TUI
    defaults to 1) — pass :func:`resolve_launch_tabs` output or a concrete
    value.
    """
    cfg = getattr(profile, "launch_config", None)
    active_engine = resolve_launch_engine(engine, profile)

    if tabs is None:
        raise LaunchPlanError("tab count is required; use --tabs or set default-tabs in the launch preset")
    if tabs < 1:
        raise LaunchPlanError("tab count must be at least 1")

    target_urls = list(urls) if urls else list(cfg.start_urls if cfg and cfg.start_urls else [])
    target_urls = [item.strip() for item in target_urls]
    try:
        for target_url in target_urls:
            validate_url(target_url)
        if len(target_urls) > tabs:
            raise LaunchPlanError("number of start URLs cannot exceed the requested tab count")
    except ValidationError as exc:
        raise LaunchPlanError(str(exc)) from exc

    target_browser = browser if browser is not None else (cfg.browser if cfg else None)
    if target_browser is not None:
        candidate = Path(target_browser).expanduser()
        target_browser = str(candidate.resolve()) if candidate.is_file() else target_browser.strip().lower()
        try:
            validate_browser(target_browser, active_engine, require_executable=True)
        except ValidationError as exc:
            raise LaunchPlanError(str(exc)) from exc

    if not Path(profile.data_dir).is_dir():
        raise LaunchPlanError("profile data directory is missing")

    return LaunchPlan(
        engine=active_engine,
        tabs=tabs,
        urls=tuple(target_urls),
        browser=target_browser,
        window_width=cfg.window_width if cfg else None,
        window_height=cfg.window_height if cfg else None,
    )


def direct_launch_options(
    plan: LaunchPlan, extra_args: list[str] | None = None
) -> dict[str, Any]:
    """Assemble keyword options for :func:`start_direct_chrome` from a plan."""
    options: dict[str, Any] = {}
    if plan.browser is not None:
        browser_path = Path(plan.browser).expanduser()
        if browser_path.is_file():
            options["executable_path"] = browser_path
        else:
            options["browser"] = plan.browser
    if plan.urls:
        options["start_urls"] = list(plan.urls)
    if plan.window_width is not None and plan.window_height is not None:
        options["window_width"] = plan.window_width
        options["window_height"] = plan.window_height
    if extra_args:
        options["extra_args"] = list(extra_args)
    return options


def controller_launch_options(plan: LaunchPlan) -> dict[str, Any]:
    """Assemble keyword options for :func:`start_controller` from a plan."""
    options: dict[str, Any] = {}
    if plan.browser is not None:
        options["browser_channel"] = plan.browser
    if plan.urls:
        options["start_urls"] = list(plan.urls)
    if plan.window_width is not None and plan.window_height is not None:
        options["window_width"] = plan.window_width
        options["window_height"] = plan.window_height
    return options
