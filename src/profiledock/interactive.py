

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


def _menu_items() -> list[tuple[str, str, tuple[str, ...], bool]]:

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


def run_interactive() -> int:

    if not TEXTUAL_AVAILABLE:
        return 1
    app = InteractiveApp()
    app.run()
    return EXIT_SUCCESS


InteractiveApp: Any = ProfileDockApp if TEXTUAL_AVAILABLE else None
