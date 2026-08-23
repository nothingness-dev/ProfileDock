"""Terminal-aware output helpers.

Human output adapts to the environment: color and Unicode symbols appear only
when stdout is an interactive terminal that can render them, and never corrupt
piped or redirected output. Machine JSON paths are untouched.
"""

import os
import sys
from typing import Optional

_RESET = "\033[0m"
_STYLES = {
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
}

_color_enabled: Optional[bool] = None


def is_stdout_tty() -> bool:
    try:
        return bool(sys.stdout.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def _windows_supports_ansi() -> bool:
    return any(
        os.environ.get(variable) for variable in ("WT_SESSION", "TERM_PROGRAM", "ANSICON", "ConEmuANSI")
    )


def _decide_color() -> bool:
    override = os.environ.get("PROFILEDOCK_COLOR", "").strip().lower()
    if override == "always":
        return True
    if override == "never":
        return False
    if os.environ.get("NO_COLOR", "").strip():
        return False
    if not is_stdout_tty():
        return False
    if os.environ.get("TERM", "").strip().lower() == "dumb":
        return False
    if sys.platform == "win32":
        return _windows_supports_ansi()
    return True


def use_color() -> bool:
    global _color_enabled
    if _color_enabled is None:
        _color_enabled = _decide_color()
    return _color_enabled


def reset_color_cache() -> None:
    global _color_enabled
    _color_enabled = None


def paint(text: str, *styles: str) -> str:
    if not text:
        return text
    if not use_color():
        return text
    codes = [_STYLES[style] for style in styles if style in _STYLES]
    if not codes:
        return text
    return "".join(codes) + text + _RESET


def supports_unicode() -> bool:
    encoding = getattr(sys.stdout, "encoding", None) or ""
    try:
        "✓".encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def ok_mark() -> str:
    mark = "✓" if supports_unicode() else "[ok]"
    return paint(mark, "green")


def fail_mark() -> str:
    mark = "✗" if supports_unicode() else "x"
    return paint(mark, "red")


def warn_mark() -> str:
    return paint("!", "yellow")
