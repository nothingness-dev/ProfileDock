import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from profiledock.browser_detection import (
    DIRECT_BROWSER_ALIASES,
    browser_rows,
    system_browser_executable,
)


def test_direct_aliases_match_validated_contract():
    from profiledock.validation import _ALLOWED_DIRECT_BROWSERS

    assert {
        "chrome",
        "chromium",
        "google-chrome",
        "google-chrome-stable",
        "chromium-browser",
    } == DIRECT_BROWSER_ALIASES
    assert DIRECT_BROWSER_ALIASES is _ALLOWED_DIRECT_BROWSERS


def test_browser_rows_cover_only_supported_families():
    labels = [name for name, _ in browser_rows()]
    assert labels == ["Google Chrome", "Chromium"]


def test_preferred_alias_selects_requested_family(tmp_path):
    chrome = tmp_path / "google-chrome"
    chromium = tmp_path / "chromium"
    chrome.write_text("chrome", encoding="utf-8")
    chromium.write_text("chromium", encoding="utf-8")

    def find_which(name):
        return {"google-chrome": str(chrome), "chromium": str(chromium)}.get(name)

    with (
        patch("profiledock.browser_detection.sys.platform", "linux"),
        patch("profiledock.browser_detection.shutil.which", side_effect=find_which),
    ):
        assert system_browser_executable("chromium") == chromium
        assert system_browser_executable("google-chrome-stable") == chrome
        assert system_browser_executable("unsupported") is None


def test_windows_probe_reads_pe_version(tmp_path):
    from profiledock.tui.backend import _windows_file_version

    system_root = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
    pe_file = system_root / "System32" / "cmd.exe"
    if sys.platform != "win32" or not pe_file.is_file():
        pytest.skip("requires a Windows PE image")

    probed = _windows_file_version(str(pe_file))
    missing = _windows_file_version(str(tmp_path / "not-a-pe.exe"))
    assert probed == "" or all(part.isdigit() for part in probed.split("."))
    assert missing == ""
