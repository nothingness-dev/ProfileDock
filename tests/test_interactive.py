import importlib.util

import pytest
from typer.testing import CliRunner

from profiledock.cli import EXIT_USAGE_ERROR, app

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
        from profiledock.tui.actions import ACTIONS

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

        monkeypatch.setenv("PROFILEDOCK_THEME", "nord")
        app_instance = ProfileDockApp()
        async with app_instance.run_test():
            assert app_instance.theme == "nord"

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
    async def test_single_click_runs_command(self):
        self._make_profiles()
        from profiledock.interactive import ProfileDockApp

        app_instance = ProfileDockApp()
        async with app_instance.run_test() as pilot:
            await self._settle(pilot, app_instance)
            await pilot.click("#deck", offset=(6, 2))
            await self._settle(pilot, app_instance)
            assert app_instance._mode == "output"

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
            filt.value = "engine"
            await pilot.pause()
            deck = app_instance.query_one("#deck")
            assert deck.option_count == 2
            await pilot.press("escape")
            await pilot.pause()
            assert filt.styles.display == "none"
            assert deck.option_count > 2

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
