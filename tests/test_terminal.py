import io
import sys

import pytest

from profiledock import terminal


@pytest.fixture(autouse=True)
def _fresh_color_cache():
    terminal.reset_color_cache()
    yield
    terminal.reset_color_cache()


def test_is_stdout_tty_false_for_string_io(monkeypatch):
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    assert terminal.is_stdout_tty() is False


def test_no_color_env_disables_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    assert terminal.use_color() is False
    assert terminal.paint("x", "red") == "x"


def test_non_tty_disables_color(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    assert terminal.use_color() is False


def test_profiledock_color_always_overrides_non_tty(monkeypatch):
    monkeypatch.setenv("PROFILEDOCK_COLOR", "always")
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    assert terminal.use_color() is True
    styled = terminal.paint("bad", "red")
    assert styled.startswith("\033[31m") and styled.endswith("\033[0m")


def test_profiledock_color_never_beats_tty(monkeypatch):
    monkeypatch.setenv("PROFILEDOCK_COLOR", "never")

    class FakeTty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdout", FakeTty())
    assert terminal.use_color() is False


def test_marks_plain_when_ascii_stream():
    original_stdout = sys.stdout
    try:
        ascii_only = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        sys.stdout = ascii_only
        assert terminal.supports_unicode() is False
        assert terminal.ok_mark() in {"[ok]", "\033[32m[ok]\033[0m"}
        assert terminal.fail_mark().endswith(("x", "\033[0m"))
    finally:
        sys.stdout = original_stdout
        terminal.reset_color_cache()


def test_marks_unicode_when_utf8_stream():
    original_stdout = sys.stdout
    try:
        utf8 = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        sys.stdout = utf8
        assert terminal.supports_unicode() is True
        plain_ok = terminal.ok_mark()
        assert "✓" in plain_ok or "[ok]" in plain_ok
    finally:
        sys.stdout = original_stdout
        terminal.reset_color_cache()


def test_paint_empty_and_unknown_style_are_safe(monkeypatch):
    monkeypatch.setenv("PROFILEDOCK_COLOR", "always")
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    assert terminal.paint("", "red") == ""
    assert terminal.paint("plain", "not-a-style") == "plain"
