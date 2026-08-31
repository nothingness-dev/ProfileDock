"""Fixture tests for the TUI action registry and CLI-faithful argv rendering.

The command preview line and the output pane header both go through
:func:`build_argv`; these tests freeze its output against the frozen CLI
contract so the TUI can never again display an invocations that the real CLI
would reject (e.g. engines as bare positionals, ``__all__`` sentinels leaking
into the command line, or ``--url`` collapsing into one flag).
"""

from __future__ import annotations

from profiledock.tui.actions import (
    ACTIONS,
    ALL_PROFILES,
    ENGINE_INHERIT,
    ActionSpec,
    FieldKind,
    FieldSpec,
    build_argv,
)


def _spec(action_id: str) -> ActionSpec:
    for action in ACTIONS:
        if action.id == action_id:
            return action
    raise AssertionError(f"unknown action: {action_id}")


def test_launch_argv_positions_and_flags():
    argv = build_argv(
        _spec("launch"),
        {
            "profile": "Work",
            "tabs": "3",
            "engine": ENGINE_INHERIT,
            "browser": "",
            "flags": [],
            "urls": "https://a.example,https://b.example",
        },
    )
    assert argv == [
        "launch",
        "Work",
        "--tabs",
        "3",
        "--url",
        "https://a.example",
        "--url",
        "https://b.example",
    ]


def test_launch_argv_engine_override_is_a_flag_not_a_positional():
    argv = build_argv(
        _spec("launch"),
        {"profile": "Work", "tabs": "1", "engine": "playwright", "flags": []},
    )
    assert argv == ["launch", "Work", "--tabs", "1", "--engine", "playwright"]
    # Engine must never appear as a bare positional after the profile.
    assert "playwright" not in argv[: argv.index("--engine")]


def test_launch_argv_engine_inherit_is_omitted():
    argv = build_argv(
        _spec("launch"),
        {"profile": "Work", "tabs": "1", "engine": ENGINE_INHERIT, "flags": []},
    )
    assert "--engine" not in argv
    assert ENGINE_INHERIT not in argv


def test_launch_argv_flags_render_individually():
    argv = build_argv(
        _spec("launch"),
        {"profile": "W", "tabs": "1", "engine": ENGINE_INHERIT, "flags": ["--incognito", "--disable-gpu"]},
    )
    assert "--incognito" in argv
    assert "--disable-gpu" in argv
    assert not any("[" in token for token in argv)


def test_backup_argv_all_maps_to_flag():
    argv = build_argv(
        _spec("backup"),
        {
            "target": ALL_PROFILES,
            "output": "out.tar.gz",
            "exclude_cache": True,
            "force": False,
        },
    )
    assert argv == ["backup", "--output", "out.tar.gz", "--exclude-cache", "--all"]


def test_backup_argv_single_profile_is_positional():
    argv = build_argv(
        _spec("backup"),
        {"target": "Work", "output": "out.tar.gz", "exclude_cache": False, "force": True},
    )
    assert argv == ["backup", "Work", "--output", "out.tar.gz", "--force"]


def test_restore_argv_archive_is_positional():
    argv = build_argv(
        _spec("restore"),
        {"archive": "work.tar.gz", "force": True},
    )
    assert argv == ["restore", "work.tar.gz", "--force"]
    assert "--archive" not in argv


def test_logs_argv_omits_all_sentinel():
    argv = build_argv(
        _spec("logs"),
        {"profile": ALL_PROFILES, "last": "25"},
    )
    assert argv == ["logs", "--last", "25"]


def test_logs_argv_single_profile_positional():
    argv = build_argv(_spec("logs"), {"profile": "Work", "last": "5"})
    assert argv == ["logs", "Work", "--last", "5"]


def test_open_tab_and_read_urls_are_positional():
    assert build_argv(_spec("open-tab"), {"profile": "W", "url": "https://x"}) == [
        "open-tab",
        "W",
        "https://x",
    ]
    assert build_argv(_spec("read"), {"profile": "W", "url": ""}) == ["read", "W"]


def test_cookies_output_is_flag():
    argv = build_argv(_spec("cookies"), {"profile": "W", "output": "cookies.json"})
    assert argv == ["cookies", "W", "--output", "cookies.json"]


def test_instant_actions_render_only_the_command():
    assert build_argv(_spec("list"), {}) == ["list"]
    assert build_argv(_spec("doctor"), {}) == ["doctor"]


def test_argv_never_contains_sentinels():
    for action in ACTIONS:
        values: dict[str, object] = {}
        for field_spec in action.fields:
            if field_spec.kind is FieldKind.TOGGLE:
                values[field_spec.name] = field_spec.toggled
            elif field_spec.kind is FieldKind.PROFILE_OR_ALL:
                values[field_spec.name] = ALL_PROFILES
            elif field_spec.kind is FieldKind.ENGINE:
                values[field_spec.name] = field_spec.options[0] if field_spec.options else ""
            elif field_spec.kind is FieldKind.FLAGS:
                values[field_spec.name] = []
            else:
                values[field_spec.name] = field_spec.default or "x"
        argv = build_argv(action, values)
        assert ALL_PROFILES not in argv, f"{action.id}: sentinel leaked into argv"
        assert "__" not in " ".join(t for t in argv if t.startswith("-")), f"{action.id}: sentinel in flags"


def test_instant_actions_and_hotkeys_stay_unique():
    ids = [action.id for action in ACTIONS]
    assert len(ids) == len(set(ids))
    hotkeys = [action.hotkey for action in ACTIONS]
    assert len(hotkeys) == len(set(hotkeys))


def test_field_spec_argv_mode_defaults():
    # TOGGLE defaults to boolean rendering; NUMBER to --name value; flags are
    # emitted in field declaration order.
    toggle = FieldSpec("verbose_out", "Verbose", FieldKind.TOGGLE)
    number = FieldSpec("count", "Count", FieldKind.NUMBER)
    assert build_argv(
        ActionSpec(
            id="probe",
            label="probe",
            description="",
            group="g",
            glyph="x",
            glyph_fallback="x",
            hotkey="z",
            fields=(toggle, number),
        ),
        {"verbose_out": True, "count": "2"},
    ) == ["probe", "--verbose-out", "--count", "2"]
