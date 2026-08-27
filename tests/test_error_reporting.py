import json
import os
import socket
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from profiledock.process_manager import (
    _MAX_ERROR_BYTES,
    BrowserLaunchError,
    _alive,
    _controller,
    _launch_context,
    _read_error,
    _write_error,
    error_path,
    start_controller,
)


def test_error_path_is_sibling_to_state():
    data_dir = "/some/path/profiles/abc123/browser-data"
    assert error_path(data_dir) == Path("/some/path/runtime/abc123/controller.error")


def test_write_and_read_error_round_trip(tmp_path):
    err = tmp_path / "controller.error"
    _write_error(err, "browser_unavailable", "No browser found", channel="chromium")
    result = _read_error(err)
    assert result is not None
    assert result["error_type"] == "browser_unavailable"
    assert result["message"] == "No browser found"
    assert result["channel"] == "chromium"


def test_read_error_returns_none_on_missing_file(tmp_path):
    assert _read_error(tmp_path / "nonexistent.error") is None


def test_read_error_returns_none_on_corrupt_json(tmp_path):
    err = tmp_path / "controller.error"
    err.write_text("not json {{{", encoding="utf-8")
    assert _read_error(err) is None


def test_read_error_returns_none_on_wrong_structure(tmp_path):
    err = tmp_path / "controller.error"
    err.write_text(json.dumps({"foo": "bar"}), encoding="utf-8")
    assert _read_error(err) is None


def test_write_error_truncates_long_message(tmp_path):
    err = tmp_path / "controller.error"
    long_msg = "x" * (_MAX_ERROR_BYTES + 1000)
    _write_error(err, "test", long_msg)
    result = _read_error(err)
    assert result is not None
    assert len(err.read_bytes()) <= _MAX_ERROR_BYTES
    assert result["message"]
    assert len(result["message"]) < len(long_msg)


def test_write_error_limits_multibyte_message_by_encoded_bytes(tmp_path):
    err = tmp_path / "controller.error"
    _write_error(err, "test", "🔥" * _MAX_ERROR_BYTES)
    assert len(err.read_bytes()) <= _MAX_ERROR_BYTES
    assert _read_error(err) is not None


def test_write_error_redacts_controller_token(tmp_path):
    err = tmp_path / "controller.error"
    token = "controller-secret-token"
    _write_error(err, "test", f"failed with {token}", redactions=(token,))
    assert token not in err.read_text(encoding="utf-8")
    assert "[redacted]" in _read_error(err)["message"]


def test_write_error_includes_channel_when_provided(tmp_path):
    err = tmp_path / "controller.error"
    _write_error(err, "test", "msg", channel="chrome")
    result = _read_error(err)
    assert result["channel"] == "chrome"


def test_write_error_omits_channel_when_empty(tmp_path):
    err = tmp_path / "controller.error"
    _write_error(err, "test", "msg")
    result = _read_error(err)
    assert "channel" not in result


def test_write_error_survives_readonly_fs(tmp_path):
    err = tmp_path / "controller.error"
    with patch("profiledock.process_manager.os.open", side_effect=OSError("read only")):
        _write_error(err, "test", "new message")
    assert not err.exists()


def test_write_error_empty_message(tmp_path):
    err = tmp_path / "controller.error"
    _write_error(err, "test", "")
    result = _read_error(err)
    assert result is not None
    assert result["message"] == ""


def test_write_error_special_characters(tmp_path):
    err = tmp_path / "controller.error"
    msg = 'Line1\nLine2\tTabbed\\Backslash"Quoted'
    _write_error(err, "test", msg)
    result = _read_error(err)
    assert result["message"] == msg


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions only")
def test_error_file_is_owner_only(tmp_path):
    err = tmp_path / "controller.error"
    _write_error(err, "test", "private")
    assert stat.S_IMODE(err.stat().st_mode) == 0o600


def test_invalid_data_directory_has_stable_category(tmp_path):
    with pytest.raises(BrowserLaunchError) as raised:
        start_controller(str(tmp_path / "missing"), 1, headless=True)
    assert raised.value.category == "invalid_data_directory"


def test_startup_timeout_terminates_owned_process(tmp_path):
    data_dir = tmp_path / "browser-data"
    data_dir.mkdir()
    script = tmp_path / "sleep.py"
    script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    started = []
    original_popen = subprocess.Popen

    def sleeping_process(command, **kwargs):
        if command[0] == "taskkill":
            return original_popen(command, **kwargs)
        process = original_popen([sys.executable, str(script)], **kwargs)
        started.append(process)
        return process

    with patch("profiledock.process_manager.subprocess.Popen", side_effect=sleeping_process):
        with pytest.raises(BrowserLaunchError) as raised:
            start_controller(
                str(data_dir),
                1,
                headless=True,
                startup_timeout=0.2,
            )

    assert raised.value.category == "controller_timeout"
    assert started
    assert not _alive(started[0].pid)
    assert not (tmp_path / "running.json").exists()
    error = _read_error(tmp_path / "controller.error")
    assert error["error_type"] == "controller_timeout"


def test_playwright_import_failure_writes_category(tmp_path):
    data_dir = tmp_path / "browser-data"
    data_dir.mkdir()
    state = tmp_path / "running.json"
    original_import = __import__

    def blocked_import(name, *args, **kwargs):
        if name == "playwright.sync_api":
            raise ImportError("playwright missing")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=blocked_import):
        result = _controller(state, str(data_dir), 1, "secret", True)

    error = _read_error(error_path(str(data_dir)))
    assert result == 2
    assert error["error_type"] == "playwright_unavailable"


def test_socket_initialization_failure_writes_category(tmp_path):
    pytest.importorskip("playwright.sync_api")
    data_dir = tmp_path / "browser-data"
    data_dir.mkdir()
    state = tmp_path / "running.json"
    with patch.object(socket, "socket", side_effect=OSError("socket unavailable")):
        result = _controller(state, str(data_dir), 1, "secret", True)

    error = _read_error(error_path(str(data_dir)))
    assert result == 2
    assert error["error_type"] == "controller_error"
    assert "socket unavailable" in error["message"]


def test_browser_attempt_errors_identify_the_missing_channel():
    playwright = pytest.importorskip("playwright.sync_api")

    class FailingChromium:
        def launch_persistent_context(self, data_dir, **kwargs):
            channel = kwargs.get("channel", "chromium")
            raise playwright.Error(f"{channel} unavailable")

    instance = type("Playwright", (), {"chromium": FailingChromium()})()
    with pytest.raises(playwright.Error) as raised:
        _launch_context(instance, "unused", True)
    assert "Playwright Chromium" in str(raised.value)
    assert "playwright install chromium" in str(raised.value)
