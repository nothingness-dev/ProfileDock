from __future__ import annotations

from pathlib import Path

import pytest
from rich.text import Text

from profiledock.models import Profile

pytest.importorskip("textual")


def _fresh_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    tmp = tmp_path / "data"
    monkeypatch.setenv("PROFILEDOCK_DATA_ROOT", str(tmp))
    from profiledock import cli_support

    monkeypatch.setattr(cli_support, "_paths", cli_support.ContextVar("pd_test_paths", default=None))
    monkeypatch.setattr(cli_support, "_paths_prepared", cli_support.ContextVar("pd_test_prepared", default=False))
    return tmp


def _make_profile_with_data(paths, name: str = "Work") -> Profile:
    from profiledock.profile_manager import ProfileManager

    manager = ProfileManager(paths)
    profile = manager.create(name)
    data_dir = Path(profile.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "marker.txt").write_text("hello")
    return profile


def test_backup_output_shows_elapsed_seconds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import asyncio

    from textual.widgets import Input

    from profiledock.data_root import resolve_data_root
    from profiledock.tui.app import ProfileDockApp
    from profiledock.tui.widgets.forms import FormPanel
    from profiledock.tui.widgets.inspector import OutputPane

    async def scenario() -> None:
        tmp = _fresh_env(monkeypatch, tmp_path)
        paths = resolve_data_root(prepare=True)
        _make_profile_with_data(paths)
        output = tmp / "out.tar.gz"

        app = ProfileDockApp()
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            for _ in range(40):
                await pilot.pause(0.25)
                if app._rows:
                    break
            assert app._rows, "profile rows never loaded"
            await pilot.press("b")
            await pilot.pause()
            form = app.query_one("#form-pane", FormPanel)
            form.query_one("#field-output", Input).value = str(output)
            await pilot.pause()
            form.query_one("#form-submit").press()
            for _ in range(60):
                await pilot.pause(0.5)
                if not app._busy:
                    break
            assert app._busy is False
            assert output.exists()

            output_pane = app.query_one("#output-pane", OutputPane)
            output_pane.set_busy("running profiledock backup …")
            text0 = str(output_pane._body.content)
            assert "elapsed 0s" in text0, f"busy line lacks elapsed counter: {text0!r}"
            for _ in range(20):
                await pilot.pause(0.25)
                if "elapsed 1s" in str(output_pane._body.content):
                    break
            assert "elapsed 1s" in str(output_pane._body.content), "elapsed did not advance"
            output_pane.set_result(["backup"], 0, Text("done"))
            text2 = str(output_pane._body.content)
            assert "elapsed" not in text2, f"result did not replace busy line: {text2!r}"

    asyncio.run(scenario())


def test_restore_modal_has_clickable_confirm_button(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import asyncio

    from textual.widgets import Button, Input

    from profiledock.data_root import resolve_data_root
    from profiledock.tui.app import ProfileDockApp
    from profiledock.tui.widgets.forms import FormPanel
    from profiledock.tui.widgets.overlays import ConfirmModal

    async def scenario() -> None:
        tmp = _fresh_env(monkeypatch, tmp_path)
        paths = resolve_data_root(prepare=True)
        profile = _make_profile_with_data(paths)
        archive = tmp / "out.tar.gz"
        from profiledock.backup import create_backup_archive

        create_backup_archive([profile], paths, archive)

        app = ProfileDockApp()
        executed: list[str] = []

        def execute(spec, values) -> None:
            executed.append(spec.id)

        monkeypatch.setattr(app, "_execute", execute)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            for _ in range(40):
                await pilot.pause(0.25)
                if app._rows:
                    break
            assert app._rows, "profile rows never loaded"
            await pilot.press("u")
            await pilot.pause()
            form = app.query_one("#form-pane", FormPanel)
            form.query_one("#field-archive", Input).value = str(archive)
            await pilot.pause()
            form.query_one("#form-submit").press()
            await pilot.pause(0.1)
            modal = app.screen if isinstance(app.screen, ConfirmModal) else app.screen.query_one(ConfirmModal)
            confirm = modal.query_one("#confirm-ok", Button)
            cancel = modal.query_one("#confirm-cancel", Button)
            assert cancel is not None
            confirm.press()
            await pilot.pause(0.2)
            for _ in range(60):
                await pilot.pause(0.5)
                if executed:
                    break
            assert executed == ["restore"]

    asyncio.run(scenario())


def test_restore_modal_cancel_button_returns_to_form(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import asyncio

    from textual.widgets import Button, Input

    from profiledock.data_root import resolve_data_root
    from profiledock.tui.app import ProfileDockApp
    from profiledock.tui.widgets.forms import FormPanel
    from profiledock.tui.widgets.overlays import ConfirmModal

    async def scenario() -> None:
        tmp = _fresh_env(monkeypatch, tmp_path)
        paths = resolve_data_root(prepare=True)
        profile = _make_profile_with_data(paths)
        archive = tmp / "out.tar.gz"
        from profiledock.backup import create_backup_archive

        create_backup_archive([profile], paths, archive)

        app = ProfileDockApp()
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            for _ in range(40):
                await pilot.pause(0.25)
                if app._rows:
                    break
            assert app._rows, "profile rows never loaded"
            await pilot.press("u")
            await pilot.pause()
            form = app.query_one("#form-pane", FormPanel)
            form.query_one("#field-archive", Input).value = str(archive)
            await pilot.pause()
            form.query_one("#form-submit").press()
            await pilot.pause(0.1)
            modal = app.screen if isinstance(app.screen, ConfirmModal) else app.screen.query_one(ConfirmModal)
            modal.query_one("#confirm-cancel", Button).press()
            await pilot.pause(0.2)
            assert app._pending is None
            assert app._busy is False

    asyncio.run(scenario())
