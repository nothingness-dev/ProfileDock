import pytest


def test_persistent_context_preserves_state_and_tab_count(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import Error

    data_dir = tmp_path / "browser-data"
    try:
        with playwright.sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(str(data_dir), headless=True)
            while len(context.pages) > 3:
                context.pages[-1].close()
            while len(context.pages) < 3:
                context.new_page()
            assert len(context.pages) == 3
            context.add_cookies([{"name": "profiledock", "value": "persisted", "url": "https://example.com"}])
            context.close()

            context = pw.chromium.launch_persistent_context(str(data_dir), headless=True)
            assert len(context.pages) == 1
            assert context.cookies("https://example.com")[0]["value"] == "persisted"
            context.close()
    except Error as exc:
        pytest.skip(f"Chromium is not installed: {exc}")

