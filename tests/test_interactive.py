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
    def _make_profiles(self, tmp_path, count=2):
        from profiledock.data_root import resolve_data_root
        from profiledock.profile_manager import ProfileManager

        paths = resolve_data_root(tmp_path, prepare=True)
        manager = ProfileManager(paths)
        for index in range(count):
            manager.create(f"Profile{index}")

    @pytest.mark.asyncio
    async def test_menu_lists_all_commands(self):
        from profiledock.interactive import MENU_ITEMS, InteractiveApp

        app_instance = InteractiveApp()
        async with app_instance.run_test():
            from textual.widgets import ListView

            items = app_instance.query_one("#menu", ListView)
            assert len(items.children) == len(MENU_ITEMS)

    @pytest.mark.asyncio
    async def test_selecting_list_shows_output(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PROFILEDOCK_DATA_ROOT", str(tmp_path))
        self._make_profiles(tmp_path)
        from textual.widgets import ListView

        from profiledock.interactive import InteractiveApp

        app_instance = InteractiveApp()
        async with app_instance.run_test() as pilot:
            menu = app_instance.query_one("#menu", ListView)
            menu.index = 0  # first entry is `list`
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            pane = app_instance.query_one("#output-pane")
            pane_text = str(pane.content)
            assert "Profile0" in pane_text or "Profile1" in pane_text
            assert pane.styles.display == "block"

    @pytest.mark.asyncio
    async def test_quit_binding_exits(self):
        from profiledock.interactive import InteractiveApp

        app_instance = InteractiveApp()
        async with app_instance.run_test() as pilot:
            await pilot.press("q")
        assert not app_instance.is_running
