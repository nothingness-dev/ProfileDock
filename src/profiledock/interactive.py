"""Interactive full-screen shell launched by bare `profiledock` on a TTY.

Built on Textual (optional dependency, extra `interactive`). The modern
multi-pane interface lives in :mod:`profiledock.tui`: a categorized command
deck, a profile rail with live status badges, telemetry cards, interactive
forms (radio engine/browser pickers, fuzzy profile search, Chromium flag
toggles), a destructive-action confirmation modal, and a scrollable output
view that renders results exactly as the equivalent CLI command would.

This module is a compatibility facade: it guards the optional import and
keeps the historical names (`InteractiveApp`, `MENU_ITEMS`, `THEME_CYCLE`,
`MIN_WIDTH`/`MIN_HEIGHT`, `run_interactive`) working for embedders.
"""

from __future__ import annotations

from typing import Any

from .cli_contract import EXIT_SUCCESS
from .tui.actions import ACTIONS

try:
    from .tui.app import MIN_HEIGHT, MIN_WIDTH, ProfileDockApp
    from .tui.theme import DEFAULT_THEME, THEME_CYCLE, configured_theme

    TEXTUAL_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without the extra
    TEXTUAL_AVAILABLE = False
    MIN_HEIGHT = 18
    MIN_WIDTH = 70
    DEFAULT_THEME = "dark"
    THEME_CYCLE = ["dark", "light"]

    def configured_theme() -> str:
        return DEFAULT_THEME


def _glyph(name: str) -> str:
    spec = next((action for action in ACTIONS if action.id == name), None)
    if spec is None:
        return "*"
    return spec.icon


def _menu_items() -> list[tuple[str, str, tuple[str, ...], bool]]:
    """Historical MENU_ITEMS shape: (command, description, prompts, destructive)."""
    items: list[tuple[str, str, tuple[str, ...], bool]] = []
    for action in ACTIONS:
        prompts: list[str] = []
        for field_spec in action.fields:
            label = field_spec.name
            if field_spec.kind.value in ("engine",):
                label = f"{field_spec.name} (direct/playwright)"
            elif field_spec.kind.value == "profile_or_all":
                label = f"{field_spec.name} (empty = all)"
            elif field_spec.kind.value == "path":
                label = f"--{field_spec.name}"
            prompts.append(label)
        items.append((action.id, action.description, tuple(prompts), action.destructive))
    return items


MENU_ITEMS: list[tuple[str, str, tuple[str, ...], bool]] = _menu_items()
QUIT_LABEL = "quit"


def run_interactive() -> int:
    """Run the interactive shell; returns a process-style exit code."""
    if not TEXTUAL_AVAILABLE:
        return 1
    app = InteractiveApp()
    app.run()
    return EXIT_SUCCESS


InteractiveApp: Any = ProfileDockApp if TEXTUAL_AVAILABLE else None
