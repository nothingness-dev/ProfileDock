"""Design tokens and theme registration for the ProfileDock TUI.

Exactly two classic themes are registered — ``dark`` and ``light`` — plus the
shared ``$pd-*`` component variables (border, selection, muted, accents) used
across the TUI stylesheet.
"""

from __future__ import annotations

import os
from typing import Any

from textual.app import App
from textual.theme import Theme

THEME_CYCLE = ["dark", "light"]
DEFAULT_THEME = "dark"

_DARK = Theme(
    name="dark",
    primary="#5f87af",
    secondary="#8a8a8a",
    accent="#c8963c",
    warning="#c8963c",
    error="#cc6666",
    success="#7f9f6f",
    foreground="#d0d0d0",
    background="#111111",
    surface="#161616",
    panel="#1d1d1d",
    dark=True,
    variables={
        "pd-border": "#3a3a3a",
        "pd-selection": "#262626",
        "pd-muted": "#808080",
        "pd-amber": "#c8963c",
        "pd-green": "#7f9f6f",
        "pd-red": "#cc6666",
        "pd-cyan": "#6f9a9a",
        "pd-mauve": "#9a7fa8",
    },
)

_LIGHT = Theme(
    name="light",
    primary="#2f5f8f",
    secondary="#6f6f6f",
    accent="#8a5a00",
    warning="#8a5a00",
    error="#a03030",
    success="#2f6b3a",
    foreground="#1a1a1a",
    background="#f2f2f2",
    surface="#fafafa",
    panel="#e4e4e4",
    dark=False,
    variables={
        "pd-border": "#b8b8b8",
        "pd-selection": "#dcdcdc",
        "pd-muted": "#6f6f6f",
        "pd-amber": "#8a5a00",
        "pd-green": "#2f6b3a",
        "pd-red": "#a03030",
        "pd-cyan": "#22687f",
        "pd-mauve": "#6d4b8f",
    },
)

_CUSTOM_THEMES = (_DARK, _LIGHT)


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
    return theme_name


def is_dark(theme_name: str | None = None) -> bool:
    """Whether the given (or configured) theme uses a dark background."""
    selected = theme_name or configured_theme()
    for theme in _CUSTOM_THEMES:
        if theme.name == selected:
            return bool(theme.dark)
    return True


def variable(name: str, theme_name: str | None = None) -> str:
    """Resolve a ``$pd-*`` token to its hex value for markup strings."""
    themes = {theme.name: theme for theme in _CUSTOM_THEMES}
    selected = theme_name or configured_theme()
    theme = themes.get(selected)
    if theme is None:
        return "#808080"
    value = theme.variables.get(name) if theme.variables else None
    return str(value or "#808080")
