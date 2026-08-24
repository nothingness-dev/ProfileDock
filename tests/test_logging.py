import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from profiledock.cli import EXIT_SUCCESS, app
from profiledock.data_root import DataPaths
from profiledock.logger import (
    read_profile_logs,
    redact_sensitive_data,
    rotate_log_file,
    sanitize_url,
    write_log_entry,
)

runner = CliRunner()


def make_paths(root: Path) -> DataPaths:
    layout = DataPaths.from_root(root)
    layout.prepare()
    return layout


def test_sanitize_url():
    assert sanitize_url("https://example.com/login?token=secret#hash") == "https://example.com/login"
    assert (
        sanitize_url("https://accounts.google.com/signin/v2/identifier?auth=123")
        == "https://accounts.google.com/signin/..."
    )
    assert sanitize_url("about:blank") == "about:blank"
    assert sanitize_url("") == ""


def test_sanitize_url_strips_embedded_credentials():
    assert sanitize_url("https://admin:hunter2@example.com/dashboard") == "https://example.com/dashboard"
    assert sanitize_url("http://user@example.com/path/deep") == "http://example.com/path/..."
    assert "hunter2" not in sanitize_url("ftp://bob:letmein@files.example.com/drop")


def test_redact_sensitive_data():
    raw = 'user signed in with token=abc123secret and auth="bearer_token_12345"'
    redacted = redact_sensitive_data(raw, secrets=["abc123secret"])
    assert "abc123secret" not in redacted
    assert "[redacted]" in redacted

    bearer_raw = "Authorization: Bearer secret_access_token_xyz"
    assert "secret_access_token_xyz" not in redact_sensitive_data(bearer_raw)


def test_redact_sensitive_data_requires_word_boundary():
    assert redact_sensitive_data("monkey=abc donkey=xyz") == "monkey=abc donkey=xyz"
    assert redact_sensitive_data("api_key=supersecret") == "api_key=[redacted]"
    assert "shh" not in redact_sensitive_data("auth_token=shh password=shh")
    assert "[redacted]" in redact_sensitive_data("token=abc")


def test_write_log_entry_survives_unserializable_details(tmp_path):
    log_dir = tmp_path / "logs"

    class NotSerializable:
        pass

    write_log_entry(
        log_dir=log_dir,
        level="ERROR",
        event="crash_event",
        profile_id="p1",
        details={"context": {"nested": NotSerializable()}, "note": "kept"},
    )

    logs = read_profile_logs(log_dir, profile_id="p1")
    assert len(logs) == 1
    assert logs[0]["event"] == "crash_event"
    assert logs[0]["level"] == "ERROR"


def test_write_and_read_structured_logs(tmp_path):
    log_dir = tmp_path / "logs"
    write_log_entry(
        log_dir=log_dir,
        level="INFO",
        event="profile_launched",
        profile_id="p1",
        correlation_id="cid123",
        engine="direct",
        details={"url": ["https://example.com/secret_param"]},
    )

    logs = read_profile_logs(log_dir, profile_id="p1")
    assert len(logs) == 1
    assert logs[0]["event"] == "profile_launched"
    assert logs[0]["engine"] == "direct"
    assert logs[0]["correlation_id"] == "cid123"


def test_log_rotation_bounded_size(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True)
    log_file = log_dir / "profiledock.log"

    log_file.write_text("x" * 1000, encoding="utf-8")
    rotate_log_file(log_file, max_bytes=500, backup_count=2)

    assert not log_file.exists()
    assert (log_dir / "profiledock.log.1").exists()


def test_cli_logs_command(tmp_path):
    paths = make_paths(tmp_path)
    write_log_entry(
        log_dir=paths.logs_dir,
        level="INFO",
        event="test_event",
        profile_id="p1",
        correlation_id="cid999",
        engine="playwright",
        details={"info": "sample"},
    )

    runner.invoke(app, ["--data-root", str(tmp_path), "create", "Work"])
    res = runner.invoke(app, ["--data-root", str(tmp_path), "logs"])
    assert res.exit_code == EXIT_SUCCESS
    assert "test_event" in res.output

    res_json = runner.invoke(app, ["--data-root", str(tmp_path), "logs", "--json"])
    assert res_json.exit_code == EXIT_SUCCESS
    data = json.loads(res_json.output)
    assert data["output_version"] == 1
    data = data["data"]
    assert len(data) >= 1
    assert data[0]["correlation_id"] == "cid999"


def test_logging_failure_does_not_raise(tmp_path):
    log_dir = tmp_path / "unwritable_dir"
    with patch("pathlib.Path.mkdir", side_effect=PermissionError("no write")):
        write_log_entry(
            log_dir=log_dir,
            level="ERROR",
            event="safe_event",
        )
