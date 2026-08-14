import json
import stat
from pathlib import Path

from profiledock.process_manager import (
    _MAX_ERROR_BYTES,
    _read_error,
    _write_error,
    error_path,
)


def test_error_path_is_sibling_to_state():
    data_dir = "/some/path/profiles/abc123/browser-data"
    assert error_path(data_dir) == Path("/some/path/profiles/abc123/controller.error")


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
    assert len(result["message"]) == _MAX_ERROR_BYTES


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
    err.write_text("existing", encoding="utf-8")
    err.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        _write_error(err, "test", "new message")
    finally:
        err.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)


def test_write_error_empty_message(tmp_path):
    err = tmp_path / "controller.error"
    _write_error(err, "test", "")
    result = _read_error(err)
    assert result is not None
    assert result["message"] == ""


def test_write_error_special_characters(tmp_path):
    err = tmp_path / "controller.error"
    msg = "Line1\nLine2\tTabbed\\Backslash\"Quoted"
    _write_error(err, "test", msg)
    result = _read_error(err)
    assert result["message"] == msg
