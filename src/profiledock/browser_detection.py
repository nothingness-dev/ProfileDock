"""Single source of truth for supported system browser discovery.

Shared by the direct engine launcher and the interactive TUI so candidate
paths, command names, and aliases never drift apart.
"""

import os
import shutil
import sys
from pathlib import Path

DIRECT_BROWSER_ALIASES = frozenset(
    {"chrome", "chromium", "google-chrome", "google-chrome-stable", "chromium-browser"}
)

_PREFERRED_GROUP = {
    "chrome": "chrome",
    "google-chrome": "chrome",
    "google-chrome-stable": "chrome",
    "chromium": "chromium",
    "chromium-browser": "chromium",
}

_BROWSER_ROWS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # (display name, candidate executable paths across platforms)
    (
        "Google Chrome",
        (
            r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
            r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
            r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/opt/google/chrome/chrome",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ),
    ),
    (
        "Chromium",
        (
            r"%LocalAppData%\Chromium\Application\chrome.exe",
            r"%ProgramFiles%\Chromium\Application\chrome.exe",
            r"%ProgramFiles(x86)%\Chromium\Application\chrome.exe",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/snap/bin/chromium",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ),
    ),
)

_WHICH_COMMANDS: dict[str, dict[str, tuple[str, ...]]] = {
    "win32": {
        "chrome": ("chrome", "google-chrome", "google-chrome-stable"),
        "chromium": ("chromium", "chromium-browser"),
    },
    "darwin": {},
    "linux": {
        "chrome": ("google-chrome", "google-chrome-stable", "chrome"),
        "chromium": ("chromium", "chromium-browser"),
    },
}


def _platform_relevant(path: str) -> bool:
    if "%" in path:
        return sys.platform == "win32"
    if path.startswith("/Applications/"):
        return sys.platform == "darwin"
    if path.startswith("/"):
        return sys.platform != "win32"
    return True


def _candidate_paths(group: str) -> list[Path]:
    label = "Google Chrome" if group == "chrome" else "Chromium"
    rows = dict(_BROWSER_ROWS)
    candidates = [Path(os.path.expandvars(value)) for value in rows[label] if _platform_relevant(value)]
    commands = _WHICH_COMMANDS.get(sys.platform, _WHICH_COMMANDS["linux"]).get(group, ())
    candidates.extend(Path(value) for value in (shutil.which(name) for name in commands) if value)
    return candidates


def system_browser_executable(preferred: str | None = None) -> Path | None:
    """Return the first existing Chrome or Chromium binary, honoring an alias."""
    group = _PREFERRED_GROUP.get(preferred.lower()) if preferred else None
    if preferred and group is None:
        return None
    groups = [group] if group else ["chrome", "chromium"]
    for candidate_group in groups:
        for candidate in _candidate_paths(candidate_group):
            if candidate.is_file():
                return candidate
    return None


def browser_rows() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Static display rows for interactive pickers; callers filter by existence."""
    return _BROWSER_ROWS
