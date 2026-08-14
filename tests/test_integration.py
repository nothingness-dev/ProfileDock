import time

import pytest

from profiledock.process_manager import _launch_context

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
