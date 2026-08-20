import time

import pytest

from profiledock.process_manager import _context_alive, _launch_context

pytestmark = pytest.mark.browser


def test_persistent_context_preserves_state_and_tab_count(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import Error

    data_dir = tmp_path / "browser-data"
    try:
        with playwright.sync_playwright() as pw:
            context, _ = _launch_context(pw, str(data_dir), True)
            while len(context.pages) > 3:
                context.pages[-1].close()
            while len(context.pages) < 3:
                context.new_page()
            assert len(context.pages) == 3
            context.add_cookies([{"name": "profiledock", "value": "persisted", "url": "https://example.com", "expires": time.time() + 3600}])
            context.close()

            context, _ = _launch_context(pw, str(data_dir), True)
            assert len(context.pages) == 1
            assert context.cookies("https://example.com")[0]["value"] == "persisted"
            context.close()
    except Error as exc:
        pytest.skip(f"Chromium is not installed: {exc}")


def test_closed_real_context_is_not_alive(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    try:
        with playwright.sync_playwright() as instance:
            context, _ = _launch_context(
                instance,
                str(tmp_path / "browser-data"),
                True,
            )
            assert _context_alive(context)
            context.close()
            assert not _context_alive(context)
    except playwright.Error as exc:
        pytest.skip(f"Chromium is not installed: {exc}")


def test_full_acceptance_lifecycle_dual_engine(tmp_path):
    from typer.testing import CliRunner
    from profiledock.cli import app
    from profiledock.data_root import DataPaths
    from profiledock.storage import load_metadata

    runner = CliRunner()
    data_root = tmp_path / "app_data"

    res_create_dir = runner.invoke(app, ["--data-root", str(data_root), "create", "DirectAcc", "--engine", "direct"])
    assert res_create_dir.exit_code == 0
    assert "Created profile 'DirectAcc'" in res_create_dir.output

    res_create_pw = runner.invoke(app, ["--data-root", str(data_root), "create", "PlaywrightAcc", "--engine", "playwright"])
    assert res_create_pw.exit_code == 0
    assert "Created profile 'PlaywrightAcc'" in res_create_pw.output

    paths = DataPaths.from_root(data_root)
    doc = load_metadata(paths.profiles_file)
    assert len(doc.profiles) == 2
    id_map = {p.name: p for p in doc.profiles}

    p_dir_data = Path(id_map["DirectAcc"].data_dir)
    p_pw_data = Path(id_map["PlaywrightAcc"].data_dir)

    (p_dir_data / "cookies.sqlite").write_text("direct-cookie-state", encoding="utf-8")
    (p_pw_data / "storage.json").write_text("playwright-storage-state", encoding="utf-8")

    backup_file = tmp_path / "dual_acc_backup.tar.gz"
    res_backup = runner.invoke(app, ["--data-root", str(data_root), "backup", "--all", "--output", str(backup_file), "--json"])
    assert res_backup.exit_code == 0
    assert backup_file.exists()

    res_del1 = runner.invoke(app, ["--data-root", str(data_root), "delete", "DirectAcc", "--yes"])
    assert res_del1.exit_code == 0
    res_del2 = runner.invoke(app, ["--data-root", str(data_root), "delete", "PlaywrightAcc", "--yes"])
    assert res_del2.exit_code == 0

    assert len(load_metadata(paths.profiles_file).profiles) == 0

    res_restore = runner.invoke(app, ["--data-root", str(data_root), "restore", str(backup_file), "--json"])
    assert res_restore.exit_code == 0

    restored_doc = load_metadata(paths.profiles_file)
    assert len(restored_doc.profiles) == 2
    restored_map = {p.name: p for p in restored_doc.profiles}

    assert restored_map["DirectAcc"].engine == "direct"
    assert restored_map["PlaywrightAcc"].engine == "playwright"

    assert (Path(restored_map["DirectAcc"].data_dir) / "cookies.sqlite").read_text(encoding="utf-8") == "direct-cookie-state"
    assert (Path(restored_map["PlaywrightAcc"].data_dir) / "storage.json").read_text(encoding="utf-8") == "playwright-storage-state"
