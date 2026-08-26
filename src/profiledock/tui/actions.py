"""Action registry and field specifications for the ProfileDock TUI.

This module is intentionally Textual-free so the command model can be unit
tested without the interactive extra. Every user-facing command is described
by an :class:`ActionSpec`; every parameter that action can take is described
by a :class:`FieldSpec`. The TUI renders forms directly from these specs, and
the backend executes them from the collected values, so adding a command or
parameter in one place updates the palette, deck, forms, and preview lines.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum

_TRUTHY = {"1", "true", "yes", "on"}


def icons_enabled() -> bool:
    return os.environ.get("PROFILEDOCK_ICONS", "").strip().lower() in _TRUTHY


class FieldKind(str, Enum):
    """Widget kind used to render a field inside the form panel."""

    TEXT = "text"
    NUMBER = "number"
    PATH = "path"
    PROFILE = "profile"
    PROFILE_OR_ALL = "profile_or_all"
    ENGINE = "engine"
    BROWSER = "browser"
    FLAGS = "flags"
    TOGGLE = "toggle"


@dataclass(frozen=True)
class FieldSpec:
    """One parameter of an action, rendered as a labeled form control."""

    name: str
    label: str
    kind: FieldKind
    required: bool = False
    placeholder: str = ""
    default: str = ""
    toggled: bool = False
    options: tuple[str, ...] = ()
    hint: str = ""
    advanced: bool = False


@dataclass(frozen=True)
class ActionSpec:
    """A command exposed by the command deck and palette."""

    id: str
    label: str
    description: str
    group: str
    glyph: str
    glyph_fallback: str
    hotkey: str
    fields: tuple[FieldSpec, ...] = field(default_factory=tuple)
    destructive: bool = False

    @property
    def instant(self) -> bool:
        return not self.fields

    @property
    def icon(self) -> str:
        if icons_enabled():
            return self.glyph
        return self.glyph_fallback


class Group(str, Enum):
    LIFECYCLE = "profile_lifecycle"
    CONFIG = "configuration"
    DATA = "backup_and_data"


GROUP_TITLES: dict[str, tuple[str, str, str]] = {
    Group.LIFECYCLE.value: ("󰈹", "#", "Profile Lifecycle"),
    Group.CONFIG.value: ("󰒓", "*", "Configuration"),
    Group.DATA.value: ("󰆓", "%", "Backup & Data"),
}

ENGINE_OPTIONS: tuple[str, ...] = ("direct", "playwright")

CHROMIUM_FLAGS: tuple[str, ...] = (
    "--incognito",
    "--headless=new",
    "--disable-gpu",
    "--start-maximized",
    '--proxy-server="socks5://127.0.0.1:9050"',
    "--no-first-run",
)

PROFILE_FIELD = FieldSpec(
    name="profile",
    label="Profile",
    kind=FieldKind.PROFILE,
    required=True,
    placeholder="fuzzy-search profiles…",
)

ACTIONS: tuple[ActionSpec, ...] = (
    ActionSpec(
        id="list",
        label="list",
        description="List all profiles",
        group=Group.LIFECYCLE.value,
        glyph="󰃖",
        glyph_fallback="=",
        hotkey="l",
    ),
    ActionSpec(
        id="show",
        label="show",
        description="Show profile details",
        group=Group.LIFECYCLE.value,
        glyph="󰋗",
        glyph_fallback="i",
        hotkey="i",
        fields=(PROFILE_FIELD,),
    ),
    ActionSpec(
        id="launch",
        label="launch",
        description="Launch browser session",
        group=Group.LIFECYCLE.value,
        glyph="󰇄",
        glyph_fallback=">",
        hotkey="o",
        fields=(
            PROFILE_FIELD,
            FieldSpec("tabs", "Tabs", FieldKind.NUMBER, placeholder="tabs to open (preset or 1)"),
            FieldSpec(
                "engine",
                "Engine",
                FieldKind.ENGINE,
                options=ENGINE_OPTIONS,
                hint="one-launch override",
                advanced=True,
            ),
            FieldSpec("browser", "Browser", FieldKind.BROWSER, placeholder="auto-detect", advanced=True),
            FieldSpec("flags", "Chromium flags", FieldKind.FLAGS, hint="space to toggle", advanced=True),
            FieldSpec(
                "urls", "Start URLs", FieldKind.TEXT, placeholder="https://… (comma separated)", advanced=True
            ),
        ),
    ),
    ActionSpec(
        id="close",
        label="close",
        description="Close a running profile",
        group=Group.LIFECYCLE.value,
        glyph="󰈆",
        glyph_fallback="n",
        hotkey="w",
        fields=(PROFILE_FIELD,),
    ),
    ActionSpec(
        id="create",
        label="create",
        description="Create a new profile",
        group=Group.LIFECYCLE.value,
        glyph="󰐭",
        glyph_fallback="+",
        hotkey="c",
        fields=(
            FieldSpec("name", "Name", FieldKind.TEXT, required=True, placeholder="display name"),
            FieldSpec("engine", "Engine", FieldKind.ENGINE, options=ENGINE_OPTIONS),
        ),
    ),
    ActionSpec(
        id="rename",
        label="rename",
        description="Rename a profile",
        group=Group.LIFECYCLE.value,
        glyph="󰑕",
        glyph_fallback="r",
        hotkey="r",
        fields=(
            PROFILE_FIELD,
            FieldSpec("new_name", "New name", FieldKind.TEXT, required=True, placeholder="new display name"),
        ),
    ),
    ActionSpec(
        id="delete",
        label="delete",
        description="Delete profile permanently",
        group=Group.LIFECYCLE.value,
        glyph="󰆴",
        glyph_fallback="x",
        hotkey="x",
        destructive=True,
        fields=(PROFILE_FIELD,),
    ),
    ActionSpec(
        id="set-engine",
        label="set-engine",
        description="Switch launch engine",
        group=Group.CONFIG.value,
        glyph="󰚞",
        glyph_fallback="e",
        hotkey="e",
        fields=(
            PROFILE_FIELD,
            FieldSpec("engine", "Engine", FieldKind.ENGINE, required=True, options=ENGINE_OPTIONS),
        ),
    ),
    ActionSpec(
        id="status",
        label="status",
        description="Show runtime status",
        group=Group.CONFIG.value,
        glyph="󰍹",
        glyph_fallback="o",
        hotkey="s",
    ),
    ActionSpec(
        id="doctor",
        label="doctor",
        description="Check installation health",
        group=Group.CONFIG.value,
        glyph="󰞺",
        glyph_fallback="!",
        hotkey="d",
    ),
    ActionSpec(
        id="logs",
        label="logs",
        description="Read recent logs",
        group=Group.CONFIG.value,
        glyph="󰌚",
        glyph_fallback="~",
        hotkey="g",
        fields=(
            FieldSpec("profile", "Profile", FieldKind.PROFILE_OR_ALL, placeholder="all profiles"),
            FieldSpec("last", "Last N", FieldKind.NUMBER, default="25"),
        ),
    ),
    ActionSpec(
        id="backup",
        label="backup",
        description="Back up profiles",
        group=Group.DATA.value,
        glyph="󰆓",
        glyph_fallback="b",
        hotkey="b",
        fields=(
            FieldSpec(
                "target", "Target", FieldKind.PROFILE_OR_ALL, required=True, placeholder="all profiles"
            ),
            FieldSpec(
                "output",
                "Output archive",
                FieldKind.PATH,
                required=True,
                default="profiledock-backup.tar.gz",
            ),
            FieldSpec("exclude_cache", "Exclude cache", FieldKind.TOGGLE, toggled=True),
            FieldSpec("force", "Overwrite output", FieldKind.TOGGLE),
        ),
    ),
    ActionSpec(
        id="restore",
        label="restore",
        description="Restore from archive",
        group=Group.DATA.value,
        glyph="󰋘",
        glyph_fallback="u",
        hotkey="u",
        destructive=True,
        fields=(
            FieldSpec(
                "archive", "Archive", FieldKind.PATH, required=True, placeholder="path/to/backup.tar.gz"
            ),
            FieldSpec("force", "Replace conflicts", FieldKind.TOGGLE),
        ),
    ),
)

ACTIONS_BY_ID: dict[str, ActionSpec] = {action.id: action for action in ACTIONS}

QUIT_ACTION_ID = "__quit__"


def group_icon(group_id: str) -> str:
    nerd, fallback, _title = GROUP_TITLES[group_id]
    if icons_enabled() and ord(nerd) > 0x100:
        return nerd
    return fallback


def action_for_hotkey(key: str) -> ActionSpec | None:
    for action in ACTIONS:
        if action.hotkey == key:
            return action
    return None


def grouped_actions() -> list[tuple[str, list[ActionSpec]]]:
    """Return actions grouped by their section, preserving registry order."""
    ordered_groups = [Group.LIFECYCLE.value, Group.CONFIG.value, Group.DATA.value]
    return [
        (group_id, [action for action in ACTIONS if action.group == group_id]) for group_id in ordered_groups
    ]


def fuzzy_score(query: str, text: str) -> int | None:
    """Subsequence fuzzy match score; ``None`` when *query* is not contained.

    Scoring favors prefix matches and consecutive character runs so that
    ``se`` ranks ``set-engine`` above ``restore``.
    """
    if not query:
        return 0
    q = query.lower()
    t = text.lower()
    score = 0
    cursor = 0
    streak = 0
    for index, char in enumerate(q):
        found = t.find(char, cursor)
        if found < 0:
            return None
        if found == 0:
            score += 8
        if found == cursor and index > 0:
            streak += 1
            score += 4 + streak
        else:
            streak = 0
        if found == 0 and index == 0:
            score += 12
        cursor = found + 1
    return score


def build_argv(action: ActionSpec, values: dict[str, object]) -> list[str]:
    """Assemble the equivalent CLI argv for the command preview line."""
    argv = [action.id]
    for spec in action.fields:
        value = values.get(spec.name)
        if spec.kind is FieldKind.TOGGLE:
            if value:
                argv.append(f"--{spec.name.replace('_', '-')}")
            continue
        if spec.kind is FieldKind.FLAGS:
            flags = value if isinstance(value, list) else []
            if flags:
                argv.append(" ".join(str(flag) for flag in flags))
            continue
        text = str(value or "").strip()
        if not text:
            continue
        if spec.kind is FieldKind.NUMBER or spec.name in ("output", "archive"):
            argv.extend([f"--{spec.name}", text])
        else:
            argv.append(text)
    return argv
