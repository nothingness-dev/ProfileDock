import importlib.util

import pytest
from typer.testing import CliRunner

from profiledock.cli import EXIT_USAGE_ERROR, app
from profiledock.tui.actions import ACTIONS

runner = CliRunner()

TEXTUAL_INSTALLED = importlib.util.find_spec("textual") is not None


def test_bare_invocation_non_tty_reports_missing_command(tmp_path, monkeypatch):
    monkeypatch.setattr("profiledock.cli.is_stdout_tty", lambda: False)
    result = runner.invoke(app, ["--data-root", str(tmp_path)])
    assert result.exit_code == EXIT_USAGE_ERROR
    assert "Missing command" in result.output + (result.stderr or "")


def test_bare_invocation_non_interactive_skips_shell(tmp_path, monkeypatch):
    monkeypatch.setenv("PROFILEDOCK_NON_INTERACTIVE", "1")
    result = runner.invoke(app, ["--data-root", str(tmp_path)])
    assert result.exit_code == EXIT_USAGE_ERROR


def test_commands_still_work_after_callback_change(tmp_path):
    runner.invoke(app, ["--data-root", str(tmp_path), "create", "Work"])
    result = runner.invoke(app, ["--data-root", str(tmp_path), "list"])
    assert result.exit_code == 0
    assert "Work" in result.output


@pytest.mark.skipif(not TEXTUAL_INSTALLED, reason="textual extra not installed")
@pytest.mark.asyncio
class TestInteractiveApp:
    @pytest.fixture(autouse=True)
    def _isolated_data_root(self, tmp_path_factory, monkeypatch):
        from profiledock import cli as pd_cli

        root = tmp_path_factory.mktemp("pd-root")
        monkeypatch.setenv("PROFILEDOCK_DATA_ROOT", str(root))
        pd_cli._paths.set(None)
        pd_cli._paths_prepared.set(False)
        self._data_root = root
        return root

    def _make_profiles(self, count=2):
        from profiledock.data_root import resolve_data_root
        from profiledock.profile_manager import ProfileManager

        paths = resolve_data_root(self._data_root, prepare=True)
        manager = ProfileManager(paths)
        for index in range(count):
            manager.create(f"Profile{index}")

    async def _settle(self, pilot, app_instance, rounds=2):
        for _ in range(rounds):
            await pilot.pause()
            try:
                await app_instance.workers.wait_for_complete()
            except Exception:
                pass
        await pilot.pause()

    @pytest.mark.asyncio
    async def test_deck_lists_all_commands_grouped(self):
        from profiledock.interactive import ProfileDockApp

        app_instance = ProfileDockApp()
        async with app_instance.run_test():
            deck = app_instance.query_one("#deck")
            assert deck.option_count == len(ACTIONS) + 3
            ids = {str(deck.get_option_at_index(i).id) for i in range(deck.option_count)}
            for action in ACTIONS:
                assert action.id in ids

    @pytest.mark.asyncio
    async def test_selecting_list_shows_output(self):
        self._make_profiles()
        from profiledock.interactive import ProfileDockApp

        app_instance = ProfileDockApp()
        async with app_instance.run_test() as pilot:
            deck = app_instance.query_one("#deck")
            assert deck.current_spec is not None
            assert deck.current_spec.id == "list"
            await pilot.press("enter")
            await self._settle(pilot, app_instance)
            output = app_instance.query_one("#output-pane")
            assert output.styles.display == "block"
            body = app_instance.query_one("#output-body")
            body_text = str(getattr(body, "content", "") or getattr(body, "renderable", ""))
            assert "Profile0" in body_text

    @pytest.mark.asyncio
    async def test_quit_binding_exits(self):
        from profiledock.interactive import ProfileDockApp

        app_instance = ProfileDockApp()
        async with app_instance.run_test() as pilot:
            await pilot.press("q")
        assert not app_instance.is_running

    @pytest.mark.asyncio
    async def test_theme_cycle_changes_theme(self):
        from profiledock.interactive import THEME_CYCLE, ProfileDockApp

        app_instance = ProfileDockApp()
        async with app_instance.run_test() as pilot:
            start = app_instance.theme
            await pilot.press("t")
            await pilot.pause()
            expected_index = (THEME_CYCLE.index(start) + 1) % len(THEME_CYCLE) if start in THEME_CYCLE else 1
            assert app_instance.theme == THEME_CYCLE[expected_index]

    @pytest.mark.asyncio
    async def test_configured_theme_env_respected(self, monkeypatch):
        from profiledock.interactive import ProfileDockApp

        monkeypatch.setenv("PROFILEDOCK_THEME", "light")
        app_instance = ProfileDockApp()
        async with app_instance.run_test():
            assert app_instance.theme == "light"

    @pytest.mark.asyncio
    async def test_tiny_terminal_hides_panels(self):
        from profiledock.interactive import MIN_HEIGHT, MIN_WIDTH, ProfileDockApp

        app_instance = ProfileDockApp()
        async with app_instance.run_test(size=(MIN_WIDTH - 10, MIN_HEIGHT - 4)) as pilot:
            await pilot.pause()
            too_small = app_instance.query_one("#too-small")
            assert too_small.styles.display == "block"
            assert app_instance.query_one("#main").styles.display == "none"

    @pytest.mark.asyncio
    async def test_output_pane_is_scrollable(self):
        self._make_profiles()
        from textual.containers import VerticalScroll

        from profiledock.interactive import ProfileDockApp

        app_instance = ProfileDockApp()
        async with app_instance.run_test() as pilot:
            await pilot.press("enter")
            await self._settle(pilot, app_instance)
            scroll = app_instance.query_one("#output-pane", VerticalScroll)
            assert scroll.scroll_offset.y >= 0

    @pytest.mark.asyncio
    async def test_hotkey_opens_form_and_escape_returns(self):
        self._make_profiles()
        from profiledock.interactive import ProfileDockApp
        from profiledock.tui.widgets.forms import FormPanel

        app_instance = ProfileDockApp()
        async with app_instance.run_test() as pilot:
            await self._settle(pilot, app_instance)
            await pilot.press("r")
            await pilot.pause()
            form = app_instance.query_one("#form-pane", FormPanel)
            assert form.styles.display == "block"
            assert form.spec is not None and form.spec.id == "rename"
            await pilot.press("escape")
            await pilot.pause()
            assert app_instance.query_one("#inspect-pane").styles.display == "block"
            assert form.styles.display == "none"

    @pytest.mark.asyncio
    async def test_set_engine_form_updates_profile(self):
        self._make_profiles()
        from profiledock.data_root import resolve_data_root
        from profiledock.interactive import ProfileDockApp
        from profiledock.profile_manager import ProfileManager
        from profiledock.tui.widgets.forms import FormPanel

        app_instance = ProfileDockApp()
        async with app_instance.run_test() as pilot:
            await self._settle(pilot, app_instance)
            await pilot.press("e")
            await pilot.pause()
            form = app_instance.query_one("#form-pane", FormPanel)
            assert form.spec is not None and form.spec.id == "set-engine"
            choice = form.query_one("#choice-engine")
            choice.set_value("playwright")
            await pilot.pause()
            form.submit()
            await self._settle(pilot, app_instance, rounds=3)
            manager = ProfileManager(resolve_data_root(self._data_root, prepare=True))
            engines = {profile.name: profile.engine for profile in manager.list_profiles()}
            assert engines["Profile0"] == "playwright"

    @pytest.mark.asyncio
    async def test_delete_requires_typed_confirmation(self):
        self._make_profiles(count=1)
        from profiledock.data_root import resolve_data_root
        from profiledock.interactive import ProfileDockApp
        from profiledock.profile_manager import ProfileManager
        from profiledock.tui.widgets.forms import FormPanel
        from profiledock.tui.widgets.overlays import ConfirmModal

        app_instance = ProfileDockApp()
        async with app_instance.run_test() as pilot:
            await self._settle(pilot, app_instance)
            await pilot.press("x")
            await pilot.pause()
            form = app_instance.query_one("#form-pane", FormPanel)
            form.submit()
            await pilot.pause()
            modal = app_instance.screen
            assert isinstance(modal, ConfirmModal)
            assert modal.requires_typed_confirmation
            modal.query_one("#confirm-input").value = "WrongName"
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app_instance.screen, ConfirmModal)
            modal.query_one("#confirm-input").value = "Profile0"
            await pilot.pause()
            await pilot.press("enter")
            await self._settle(pilot, app_instance, rounds=3)
            manager = ProfileManager(resolve_data_root(self._data_root, prepare=True))
            assert manager.list_profiles() == []

    @pytest.mark.asyncio
    async def test_double_click_opens_command_form(self):
        """Double click opens the clicked command's form."""
        self._make_profiles()
        from profiledock.interactive import ProfileDockApp

        app_instance = ProfileDockApp()
        async with app_instance.run_test() as pilot:
            await self._settle(pilot, app_instance)
            deck = app_instance.query_one("#deck")
            assert deck.highlighted == 1
            await pilot.double_click("#deck", offset=(6, 2))
            await self._settle(pilot, app_instance)
            assert app_instance._mode == "form"
            assert app_instance.query_one("#form-pane").spec.id == "show"

    @pytest.mark.asyncio
    async def test_form_gates_deck_and_rail_then_restores_them(self):
        self._make_profiles()
        from profiledock.interactive import ProfileDockApp
        from profiledock.tui.widgets.forms import FormPanel

        app_instance = ProfileDockApp()
        async with app_instance.run_test() as pilot:
            await self._settle(pilot, app_instance)
            deck = app_instance.query_one("#deck")
            rail = app_instance.query_one("#rail")
            hl_before = deck.highlighted

            await pilot.press("o")  # open launch form
            await pilot.pause()
            assert deck.disabled
            assert rail.disabled

            # clicks on gated panes must not move their selection mid-form
            await pilot.click("#deck", offset=(6, 2))
            await self._settle(pilot, app_instance)
            assert app_instance.query_one("#form-pane", FormPanel).styles.display == "block"
            assert deck.highlighted == hl_before

            await pilot.press("escape")
            await self._settle(pilot, app_instance)
            assert not deck.disabled
            assert not rail.disabled
            assert deck.has_focus

    @pytest.mark.asyncio
    async def test_form_reopens_after_cancel_without_duplicate_ids(self):
        self._make_profiles()
        from profiledock.interactive import ProfileDockApp
        from profiledock.tui.widgets.forms import FormPanel

        app_instance = ProfileDockApp()
        async with app_instance.run_test() as pilot:
            await self._settle(pilot, app_instance)
            await pilot.press("o")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.press("o")
            await pilot.pause()
            form = app_instance.query_one("#form-pane", FormPanel)
            assert form.spec is not None and form.spec.id == "launch"
            assert form.query_one("#form-title") is not None
            assert form.query_one("#row-tabs") is not None

    @pytest.mark.asyncio
    async def test_focus_does_not_shift_layout(self):
        self._make_profiles()
        from profiledock.interactive import ProfileDockApp
        from profiledock.tui.widgets.forms import FormPanel

        app_instance = ProfileDockApp()
        async with app_instance.run_test() as pilot:
            await self._settle(pilot, app_instance)
            await pilot.press("r")
            await pilot.pause()
            form = app_instance.query_one("#form-pane", FormPanel)
            targets = [
                form.query_one("#picker-profile"),
                form.query_one("#field-new_name"),
                app_instance.query_one("#deck"),
            ]
            for widget in targets:
                before = widget.region
                widget.focus()
                await pilot.pause()
                after = widget.region
                assert after == before, f"{widget.id} shifted on focus: {before} -> {after}"

    @pytest.mark.asyncio
    async def test_form_has_no_badge_blocks(self):
        self._make_profiles()
        from profiledock.interactive import ProfileDockApp
        from profiledock.tui.widgets.forms import FormPanel

        app_instance = ProfileDockApp()
        async with app_instance.run_test() as pilot:
            await self._settle(pilot, app_instance)
            await pilot.press("r")
            await pilot.pause()
            form = app_instance.query_one("#form-pane", FormPanel)
            assert len(form.query("FieldRow .field-badge")) == 0
            label = form.query_one(".field-label")
            assert "Profile" in str(getattr(label, "content", ""))

    @pytest.mark.asyncio
    async def test_filter_narrows_deck(self):
        from profiledock.interactive import ProfileDockApp

        app_instance = ProfileDockApp()
        async with app_instance.run_test() as pilot:
            await pilot.press("/")
            await pilot.pause()
            filt = app_instance.query_one("#deck-filter")
            assert filt.styles.display == "block"
            # Label matches rank first: "engine" should surface set-engine
            # (label) ahead of descriptions that merely mention the word.
            filt.value = "engine"
            await pilot.pause()
            deck = app_instance.query_one("#deck")
            assert deck.match_count >= 2
            # Label matches rank first: set-engine (label) outranks
            # descriptions that merely contain the letters.
            first = str(deck.get_option_at_index(1).id)  # index 0 is the group header
            assert first == "set-engine"
            await pilot.press("escape")
            await pilot.pause()
            assert filt.styles.display == "none"
            # Closing the filter restores the full deck (18 commands).
            assert deck.match_count == 18

    @pytest.mark.asyncio
    async def test_rail_enter_opens_launch_form(self):
        self._make_profiles()
        from profiledock.interactive import ProfileDockApp
        from profiledock.tui.widgets.forms import FlagsList, FormPanel

        app_instance = ProfileDockApp()
        async with app_instance.run_test() as pilot:
            await self._settle(pilot, app_instance)
            rail = app_instance.query_one("#rail")
            rail.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            form = app_instance.query_one("#form-pane", FormPanel)
            assert form.spec is not None and form.spec.id == "launch"
            assert form.has_advanced
            assert not form.advanced_visible
            assert form.query_one("#row-browser").styles.display == "none"
            assert form.query_one("#row-tabs").styles.display == "block"
            await pilot.press("ctrl+o")
            await pilot.pause()
            assert form.advanced_visible
            assert form.query_one("#row-browser").styles.display == "block"
            flags = form.query_one("#flags-flags", FlagsList)
            assert flags.value == []
            assert form.query_one("#field-tabs").value == ""


def test_action_hotkeys_are_unique():
    hotkeys = [action.hotkey for action in ACTIONS]
    assert len(hotkeys) == len(set(hotkeys))


@pytest.mark.skipif(not TEXTUAL_INSTALLED, reason="textual extra not installed")
class TestInteractiveLifecycle:
    """Regressions for TUI lifecycle bugs fixed in 0.17.x."""

    @pytest.fixture(autouse=True)
    def _isolated_data_root(self, tmp_path_factory, monkeypatch):
        from profiledock import cli as pd_cli

        root = tmp_path_factory.mktemp("pd-root-lc")
        monkeypatch.setenv("PROFILEDOCK_DATA_ROOT", str(root))
        pd_cli._paths.set(None)
        pd_cli._paths_prepared.set(False)
        self._data_root = root
        return root

    def _make_profiles(self, count=1):
        from profiledock.data_root import resolve_data_root
        from profiledock.profile_manager import ProfileManager

        paths = resolve_data_root(self._data_root, prepare=True)
        manager = ProfileManager(paths)
        for index in range(count):
            manager.create(f"Profile{index}")

    async def _settle(self, pilot, app_instance, rounds=2):
        for _ in range(rounds):
            await pilot.pause()
            try:
                await app_instance.workers.wait_for_complete()
            except Exception:
                pass
        await pilot.pause()

    @pytest.mark.asyncio
    async def test_q_does_not_quit_while_form_is_open(self):
        self._make_profiles()
        from profiledock.interactive import ProfileDockApp
        from profiledock.tui.widgets.forms import FormPanel

        app_instance = ProfileDockApp()
        async with app_instance.run_test() as pilot:
            await self._settle(pilot, app_instance)
            await pilot.press("r")  # rename form
            await pilot.pause()
            await pilot.press("q")
            await pilot.pause()
            assert app_instance.is_running
            assert app_instance.query_one("#form-pane", FormPanel).styles.display == "block"
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("q")
            await pilot.pause()
            assert not app_instance.is_running

    @pytest.mark.asyncio
    async def test_q_does_not_quit_while_confirmation_modal_is_open(self):
        self._make_profiles(count=1)
        from profiledock.interactive import ProfileDockApp
        from profiledock.tui.widgets.forms import FormPanel
        from profiledock.tui.widgets.overlays import ConfirmModal

        app_instance = ProfileDockApp()
        async with app_instance.run_test() as pilot:
            await self._settle(pilot, app_instance)
            await pilot.press("x")  # delete
            await pilot.pause()
            app_instance.query_one("#form-pane", FormPanel).submit()
            await pilot.pause()
            assert isinstance(app_instance.screen, ConfirmModal)
            await pilot.press("q")
            await pilot.pause()
            assert isinstance(app_instance.screen, ConfirmModal)
            app_instance.exit()

    @pytest.mark.asyncio
    async def test_output_header_uses_cli_faithful_argv(self):
        """The output pane argv must come from build_argv, never raw str(values)."""
        from unittest.mock import patch as _patch

        from rich.text import Text

        from profiledock.interactive import ProfileDockApp
        from profiledock.tui.backend import ActionResult

        captured: dict[str, object] = {}

        def fake_run_action(paths, action_id, values):
            captured["argv_seen"] = None
            # emulate run_action argv assembly through build_argv
            from profiledock.tui.actions import ACTIONS_BY_ID, build_argv
            from profiledock.tui.backend import EXIT_SUCCESS

            spec = ACTIONS_BY_ID[action_id]
            argv = build_argv(spec, values)
            return ActionResult(argv=argv, exit_code=EXIT_SUCCESS, body=Text("ok"))

        self._make_profiles()
        app_instance = ProfileDockApp()
        async with app_instance.run_test() as pilot:
            await self._settle(pilot, app_instance)
            await pilot.press("b")  # backup form
            await pilot.pause()
            from profiledock.tui.widgets.forms import FormPanel

            with _patch.object(
                __import__("profiledock.tui.app", fromlist=["backend"]).backend,
                "run_action",
                fake_run_action,
            ):
                form = app_instance.query_one("#form-pane", FormPanel)
                form.submit()
                await self._settle(pilot, app_instance, rounds=3)
            body = app_instance.query_one("#output-body")
            text = str(getattr(body, "content", "") or getattr(body, "renderable", ""))
            assert "['" not in text, f"raw python list leaked into argv: {text[:120]}"
            assert "__all__" not in text, f"sentinel leaked into argv: {text[:120]}"

    @pytest.mark.asyncio
    async def test_launch_engine_inherit_does_not_override_stored_engine(self):
        """Choosing (inherit) must launch with the profile's stored engine."""
        self._make_profiles(count=1)
        from profiledock.data_root import resolve_data_root
        from profiledock.interactive import ProfileDockApp
        from profiledock.profile_manager import ProfileManager
        from profiledock.tui.widgets.forms import FormPanel

        paths = resolve_data_root(self._data_root, prepare=True)
        ProfileManager(paths).set_engine("Profile0", "playwright")

        launched: dict[str, object] = {}

        def fake_start_controller(data_dir, tabs, **kwargs):
            launched["data_dir"] = data_dir
            return {"controller_pid": 123, "pid": 123}

        app_instance = ProfileDockApp()
        async with app_instance.run_test() as pilot:
            await self._settle(pilot, app_instance)
            await pilot.press("o")  # launch form
            await pilot.pause()
            from unittest.mock import patch as _patch

            import profiledock.tui.backend as backend_mod

            form = app_instance.query_one("#form-pane", FormPanel)
            # engine defaults to (inherit); submit directly
            with _patch.object(backend_mod, "start_controller", fake_start_controller):
                form.submit()
                await self._settle(pilot, app_instance, rounds=3)
        assert launched, "controller launch was not invoked"
        # If engine had been overridden to direct, start_direct_chrome would
        # have been called instead and the dict stayed empty.


@pytest.mark.skipif(not TEXTUAL_INSTALLED, reason="textual extra not installed")
@pytest.mark.asyncio
class TestDoubleClickUX:
    """Single click previews, double click runs (deck only)."""

    @pytest.fixture(autouse=True)
    def _isolated_data_root(self, tmp_path_factory, monkeypatch):
        from profiledock import cli as pd_cli

        root = tmp_path_factory.mktemp("pd-root-dbl")
        monkeypatch.setenv("PROFILEDOCK_DATA_ROOT", str(root))
        pd_cli._paths.set(None)
        pd_cli._paths_prepared.set(False)
        return root

    async def test_deck_single_click_previews_only(self):
        from profiledock.interactive import ProfileDockApp

        app_instance = ProfileDockApp()
        async with app_instance.run_test() as pilot:
            for _ in range(3):
                await pilot.pause()
            deck = app_instance.query_one("#deck")
            await pilot.click("#deck", offset=(6, 3))
            await pilot.pause()
            assert app_instance._mode == "browse"
            assert deck.highlighted == 3

    async def test_deck_double_click_runs(self):
        from profiledock.interactive import ProfileDockApp
        from profiledock.tui.widgets.forms import FormPanel

        app_instance = ProfileDockApp()
        async with app_instance.run_test() as pilot:
            for _ in range(3):
                await pilot.pause()
            await pilot.double_click("#deck", offset=(6, 3))
            await pilot.pause()
            assert app_instance._mode == "form"
            form = app_instance.query_one("#form-pane", FormPanel)
            assert form.spec is not None and form.spec.id == "launch"

    async def test_choice_select_does_not_submit_form(self):
        """Choosing a radio option must not auto-submit single-field forms."""
        from profiledock.data_root import resolve_data_root
        from profiledock.interactive import ProfileDockApp
        from profiledock.profile_manager import ProfileManager
        from profiledock.tui.widgets.forms import FormPanel

        paths = resolve_data_root(app_instance_root(), prepare=True)
        ProfileManager(paths).create("Pick1")
        app_instance = ProfileDockApp()
        async with app_instance.run_test() as pilot:
            for _ in range(3):
                await pilot.pause()
            await pilot.press("c")  # create form
            await pilot.pause()
            form = app_instance.query_one("#form-pane", FormPanel)
            choice = form.query_one("#choice-engine")
            choice.set_value("playwright")
            await pilot.pause()
            # Form stays open; only preview updated.
            assert app_instance._mode == "form"
            assert form.spec is not None and form.spec.id == "create"


def app_instance_root():
    import os

    return os.environ["PROFILEDOCK_DATA_ROOT"]
