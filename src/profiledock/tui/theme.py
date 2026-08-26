"""Design tokens and theme registration for the ProfileDock TUI.

Custom themes are registered with exact palettes (Obsidian, Tokyo Night,
Catppuccin Mocha, plus a variable-extended Nord) and shared component
variables (``$pd-*``) used across the TUI stylesheet: border slate,
selection pill, muted text, and accent colors.
"""

from __future__ import annotations

import os
from dataclasses import replace as _dataclass_replace
from typing import Any

from textual.app import App
from textual.theme import BUILTIN_THEMES, Theme

THEME_CYCLE = ["obsidian", "tokyo-night", "catppuccin-mocha", "nord"]
DEFAULT_THEME = "obsidian"

_OBSIDIAN = Theme(
    name="obsidian",
    primary="#8f8ff8",
    secondary="#b48ead",
    accent="#e0af68",
    warning="#e0af68",
    error="#f7768e",
    success="#9ece6a",
    foreground="#d6d6e0",
    background="#0a0a0d",
    surface="#0f0f13",
    panel="#15151b",
    dark=True,
    variables={
        "pd-border": "#2a2a33",
        "pd-selection": "#232330",
        "pd-muted": "#5d5d6a",
        "pd-amber": "#e0af68",
        "pd-green": "#9ece6a",
        "pd-red": "#f7768e",
        "pd-cyan": "#7dcfff",
        "pd-mauve": "#b48ead",
    },
)

_TOKYO_NIGHT = Theme(
    name="tokyo-night",
    primary="#7aa2f7",
    secondary="#bb9af7",
    accent="#e0af68",
    warning="#e0af68",
    error="#f7768e",
    success="#9ece6a",
    foreground="#c0caf5",
    background="#1a1b26",
    surface="#16161e",
    panel="#1f2335",
    dark=True,
    variables={
        "pd-border": "#414868",
        "pd-selection": "#3d3856",
        "pd-muted": "#565f89",
        "pd-amber": "#e0af68",
        "pd-green": "#9ece6a",
        "pd-red": "#f7768e",
        "pd-cyan": "#7dcfff",
        "pd-mauve": "#bb9af7",
    },
)

_CATPPUCCIN_MOCHA = Theme(
    name="catppuccin-mocha",
    primary="#89b4fa",
    secondary="#cba6f7",
    accent="#f9e2af",
    warning="#f9e2af",
    error="#f38ba8",
    success="#a6e3a1",
    foreground="#cdd6f4",
    background="#1e1e2e",
    surface="#181825",
    panel="#313244",
    dark=True,
    variables={
        "pd-border": "#45475a",
        "pd-selection": "#45475a",
        "pd-muted": "#6c7086",
        "pd-amber": "#f9e2af",
        "pd-green": "#a6e3a1",
        "pd-red": "#f38ba8",
        "pd-cyan": "#94e2d5",
        "pd-mauve": "#cba6f7",
    },
)

_NORD = _dataclass_replace(
    BUILTIN_THEMES["nord"],
    variables={
        **BUILTIN_THEMES["nord"].variables,
        "pd-border": "#434c5e",
        "pd-selection": "#434c5e",
        "pd-muted": "#7b88a1",
        "pd-amber": "#ebcb8b",
        "pd-green": "#a3be8c",
        "pd-red": "#bf616a",
        "pd-cyan": "#88c0d0",
        "pd-mauve": "#b48ead",
    },
)

_CUSTOM_THEMES = (_OBSIDIAN, _TOKYO_NIGHT, _CATPPUCCIN_MOCHA, _NORD)


def register_profiledock_themes(app: App[Any]) -> None:
    for theme in _CUSTOM_THEMES:
        app.register_theme(theme)


def configured_theme() -> str:
    requested = os.environ.get("PROFILEDOCK_THEME", "").strip().lower()
    if requested in THEME_CYCLE:
        return requested
    return DEFAULT_THEME


def next_theme(current: str) -> str:
    if current not in THEME_CYCLE:
        return DEFAULT_THEME
    index = (THEME_CYCLE.index(current) + 1) % len(THEME_CYCLE)
    return THEME_CYCLE[index]


def theme_label(theme_name: str) -> str:
    return {"tokyo-night": "tokyo-night", "catppuccin-mocha": "catppuccin-mocha", "nord": "nord"}.get(
        theme_name, theme_name
    )


def variable(name: str, theme_name: str | None = None) -> str:
    """Resolve a ``$pd-*`` token to its hex value for markup strings."""
    themes = {theme.name: theme for theme in _CUSTOM_THEMES}
    selected = theme_name or configured_theme()
    theme = themes.get(selected)
    if theme is None:
        return "#565f89"
    value = theme.variables.get(name) if theme.variables else None
    return str(value or "#565f89")
