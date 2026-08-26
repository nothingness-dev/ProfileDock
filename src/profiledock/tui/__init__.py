"""ProfileDock interactive TUI package.

Heavy modules (app, theme, widgets) require the optional Textual extra and
are imported lazily via module ``__getattr__`` so that importing this package
never fails when the extra is absent. The pure-Python action registry in
:mod:`profiledock.tui.actions` and the service layer in
:mod:`profiledock.tui.backend` are always importable.
"""

from __future__ import annotations

from typing import Any

_LAZY = {
    "MIN_HEIGHT": ".app",
    "MIN_WIDTH": ".app",
    "ProfileDockApp": ".app",
    "DEFAULT_THEME": ".theme",
    "THEME_CYCLE": ".theme",
    "configured_theme": ".theme",
    "next_theme": ".theme",
    "register_profiledock_themes": ".theme",
}

__all__ = [
    "ACTIONS",
    "ACTIONS_BY_ID",
    "DEFAULT_THEME",
    "MIN_HEIGHT",
    "MIN_WIDTH",
    "THEME_CYCLE",
    "ActionSpec",
    "FieldKind",
    "FieldSpec",
    "ProfileDockApp",
    "configured_theme",
    "fuzzy_score",
    "next_theme",
    "register_profiledock_themes",
]


def __getattr__(name: str) -> Any:
    if name in ("ACTIONS", "ACTIONS_BY_ID", "ActionSpec", "FieldKind", "FieldSpec", "fuzzy_score"):
        from . import actions

        return getattr(actions, name)
    if name in _LAZY:
        import importlib

        module = importlib.import_module(_LAZY[name], __name__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
