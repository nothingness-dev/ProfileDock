import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

from profiledock.cli import EXIT_SUCCESS, EXIT_USER_ERROR, app, resolve_engine
from profiledock.models import Profile
from profiledock.process_manager import BrowserLaunchError, ProfileRunningError
from profiledock.profile_manager import ProfileNotFoundError
from profiledock.storage import StorageError
from profiledock.version import __version__

runner = CliRunner()


def test_help_does_not_create_data_root(tmp_path):
    data_root = tmp_path / "unused-data"
    result = runner.invoke(app, ["--data-root", str(data_root), "config", "--help"])
    assert result.exit_code == 0
    assert not data_root.exists()


def test_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_invalid_log_level_fails_with_usage_error():
    from profiledock.cli_contract import EXIT_USAGE_ERROR

    result = runner.invoke(app, ["--log-level", "BOGUS", "list"])
    assert result.exit_code == EXIT_USAGE_ERROR
    assert "DEBUG, INFO, WARNING, ERROR" in result.output

    ok = runner.invoke(app, ["--data-root", "unused", "--log-level", "warning", "--help"])
    assert ok.exit_code == 0


def test_logs_last_must_be_positive(tmp_path):
    result = runner.invoke(app, ["--data-root", str(tmp_path), "logs", "--last", "-5"])
    assert result.exit_code == EXIT_USER_ERROR
    assert "--last must be a positive integer" in result.output

    zero = runner.invoke(app, ["--data-root", str(tmp_path), "logs", "--last", "0"])
    assert zero.exit_code == EXIT_USER_ERROR


def test_close_on_stopped_profile_hint_is_not_circular(tmp_path):
    runner.invoke(app, ["--data-root", str(tmp_path), "create", "StoppedProfile"])
    result = runner.invoke(app, ["--data-root", str(tmp_path), "close", "StoppedProfile"])
    assert result.exit_code == EXIT_USER_ERROR
    assert "Error [profile_active]: profile is not running" in result.output
    assert "already stopped" in result.output
    assert "close the profile first" not in result.output


def test_restore_missing_archive_hint_points_at_archive_path(tmp_path):
    missing = tmp_path / "missing.tar.gz"
    result = runner.invoke(app, ["--data-root", str(tmp_path), "restore", str(missing)])
    assert result.exit_code == EXIT_USER_ERROR
    assert "Error [not_found]" in result.output
    assert "check the archive path" in result.output
    assert "profiledock list" not in result.output


def test_data_root_error_categories_follow_message(tmp_path):
    from profiledock.cli import fail_exception
    from profiledock.data_root import DataRootError

    with pytest.raises(typer.Exit) as exc_info:
        try:
            raise DataRootError("LOCALAPPDATA is not set")
        except DataRootError as error:
            fail_exception(error)
    assert exc_info.value.exit_code == 1

    import contextlib
    import io as io_module

    stderr = io_module.StringIO()
    with pytest.raises(typer.Exit):
        with contextlib.redirect_stderr(stderr):
            try:
                raise DataRootError("path traversal is not allowed: /etc")
            except DataRootError as error:
                fail_exception(error)
    assert "[security_violation]" in stderr.getvalue()

    stderr = io_module.StringIO()
    with pytest.raises(typer.Exit):
        with contextlib.redirect_stderr(stderr):
            try:
                raise DataRootError("data root must be a real directory")
            except DataRootError as error:
                fail_exception(error)
    assert "[storage_error]" in stderr.getvalue()


def test_version_short_flag():
    result = runner.invoke(app, ["-V"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_launch_browser_error_shown_concisely(tmp_path):
    profile = tmp_path / "profiles.json"
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    pid_dir = profiles_dir / "abc123"
    pid_dir.mkdir()
    data_dir = pid_dir / "browser-data"
    data_dir.mkdir()
    import json

    profile.write_text(
        json.dumps(
            [
                {
                    "id": "abc123",
                    "name": "Test",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "data_dir": str(data_dir),
                }
            ]
        ),
        encoding="utf-8",
    )

    def fake_start_controller(data_dir, tabs, headless=False, runtime_dir=None, startup_timeout=30.0):
        raise BrowserLaunchError("Playwright Chromium: not installed\nGoogle Chrome: not found")

    with (
        patch("profiledock.cli.manager") as mock_manager,
        patch("profiledock.cli.start_controller", side_effect=fake_start_controller),
    ):
        mock_manager.return_value.resolve.return_value = type(
            "Profile",
            (),
            {"id": "abc123", "name": "Test", "data_dir": str(data_dir), "engine": "playwright"},
        )()
        result = runner.invoke(app, ["launch", "abc123", "--tabs", "1"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "Error [browser_launch_failed]:" in result.output
    assert "Playwright Chromium" in result.output


def test_launch_profile_not_found_shown_concisely(tmp_path):
    with patch("profiledock.cli.manager") as mock_manager:
        mock_manager.return_value.resolve.side_effect = ProfileNotFoundError("profile not found: missing")
        result = runner.invoke(app, ["launch", "missing", "--tabs", "1"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "Error [not_found]: profile not found: missing" in result.output


def test_launch_running_error_shown_concisely(tmp_path):
    profile = tmp_path / "profiles.json"
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    pid_dir = profiles_dir / "abc123"
    pid_dir.mkdir()
    data_dir = pid_dir / "browser-data"
    data_dir.mkdir()
    import json

    profile.write_text(
        json.dumps(
            [
                {
                    "id": "abc123",
                    "name": "Test",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "data_dir": str(data_dir),
                }
            ]
        ),
        encoding="utf-8",
    )

    def fake_start_controller(data_dir, tabs, headless=False, runtime_dir=None, startup_timeout=30.0):
        raise ProfileRunningError("profile is already running")

    with (
        patch("profiledock.cli.manager") as mock_manager,
        patch("profiledock.cli.start_controller", side_effect=fake_start_controller),
    ):
        mock_manager.return_value.resolve.return_value = type(
            "Profile",
            (),
            {"id": "abc123", "name": "Test", "data_dir": str(data_dir), "engine": "playwright"},
        )()
        result = runner.invoke(app, ["launch", "abc123", "--tabs", "1"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "Error [profile_active]: profile is already running" in result.output


def test_launch_succeeds_when_timestamp_update_fails(tmp_path):
    data_dir = tmp_path / "profiles" / "abc123" / "browser-data"
    data_dir.mkdir(parents=True)
    profile = type(
        "Profile",
        (),
        {"id": "abc123", "name": "Test", "data_dir": str(data_dir), "engine": "playwright"},
    )()
    with patch("profiledock.cli.manager") as mock_manager, patch("profiledock.cli.start_controller"):
        mock_manager.return_value.resolve.return_value = profile
        mock_manager.return_value.runtime_path.return_value = tmp_path / "runtime" / "abc123"
        mock_manager.return_value.mark_launched.side_effect = StorageError("metadata locked")
        result = runner.invoke(app, ["launch", "abc123", "--tabs", "1"])
    assert result.exit_code == 0
    assert "Launched 'Test'" in result.stdout
    assert "browser launched but launch timestamp was not saved" in result.stderr


def test_close_browser_error_shown_concisely(tmp_path):
    profile = tmp_path / "profiles.json"
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    pid_dir = profiles_dir / "abc123"
    pid_dir.mkdir()
    data_dir = pid_dir / "browser-data"
    data_dir.mkdir()
    import json

    profile.write_text(
        json.dumps(
            [
                {
                    "id": "abc123",
                    "name": "Test",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "data_dir": str(data_dir),
                }
            ]
        ),
        encoding="utf-8",
    )

    def fake_close_controller(data_dir, timeout=15, runtime_dir=None):
        raise BrowserLaunchError("profile is not running")

    with (
        patch("profiledock.cli.manager") as mock_manager,
        patch("profiledock.cli.close_controller", side_effect=fake_close_controller),
    ):
        mock_manager.return_value.resolve.return_value = type(
            "Profile",
            (),
            {"id": "abc123", "name": "Test", "data_dir": str(data_dir)},
        )()
        result = runner.invoke(app, ["close", "abc123"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "Error [browser_launch_failed]: profile is not running" in result.output


def test_delete_profile_not_found_shown_concisely(tmp_path):
    with patch("profiledock.cli.manager") as mock_manager:
        mock_manager.return_value.resolve.side_effect = ProfileNotFoundError("profile not found: missing")
        result = runner.invoke(app, ["delete", "missing", "--yes"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "Error [not_found]: profile not found: missing" in result.output


def test_list_table_format():
    with (
        patch("profiledock.cli.manager") as mock_manager,
        patch("profiledock.cli.get_status", return_value="stopped"),
    ):
        mock_manager.return_value.list_profiles.return_value = [
            type(
                "Profile",
                (),
                {"id": "abc123", "name": "Work", "data_dir": "/path/to/work"},
            )()
        ]
        result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "ID" in result.output
    assert "NAME" in result.output
    assert "STATUS" in result.output
    assert "abc123" in result.output
    assert "Work" in result.output
    assert "stopped" in result.output


def test_list_json_format():
    import json

    with (
        patch("profiledock.cli.manager") as mock_manager,
        patch("profiledock.cli.get_status", return_value="running"),
    ):
        mock_manager.return_value.list_profiles.return_value = [
            type(
                "Profile",
                (),
                {
                    "id": "abc123",
                    "name": "Work",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "data_dir": "/path/to/work",
                    "last_launched_at": "2026-01-01T12:00:00+00:00",
                },
            )()
        ]
        result = runner.invoke(app, ["list", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["output_version"] == 1
    data = data["data"]
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["id"] == "abc123"
    assert data[0]["name"] == "Work"
    assert data[0]["status"] == "running"
    assert "token" not in result.output


def test_show_command():
    with (
        patch("profiledock.cli.manager") as mock_manager,
        patch("profiledock.cli.get_status", return_value="stopped"),
    ):
        mock_manager.return_value.resolve.return_value = type(
            "Profile",
            (),
            {
                "id": "abc123",
                "name": "Work",
                "created_at": "2026-01-01T00:00:00+00:00",
                "data_dir": "/path/to/work",
                "last_launched_at": None,
            },
        )()
        result = runner.invoke(app, ["show", "abc123"])
    assert result.exit_code == 0
    assert "ID:" in result.output
    assert "abc123" in result.output
    assert "Name:" in result.output
    assert "Work" in result.output
    assert "Status:" in result.output
    assert "stopped" in result.output
    assert "Disk usage:" in result.output
    assert "Never" in result.output


def test_show_json_command():
    import json

    with (
        patch("profiledock.cli.manager") as mock_manager,
        patch("profiledock.cli.get_status", return_value="running"),
    ):
        mock_manager.return_value.resolve.return_value = type(
            "Profile",
            (),
            {
                "id": "abc123",
                "name": "Work",
                "created_at": "2026-01-01T00:00:00+00:00",
                "data_dir": "/path/to/work",
                "last_launched_at": "2026-01-01T12:00:00+00:00",
            },
        )()
        result = runner.invoke(app, ["show", "abc123", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["output_version"] == 1
    data = data["data"]
    assert data["id"] == "abc123"
    assert data["name"] == "Work"
    assert data["status"] == "running"
    assert data["created_at"] == "2026-01-01T00:00:00+00:00"
    assert "token" not in result.output


def test_rename_command():
    with patch("profiledock.cli.manager") as mock_manager:
        mock_manager.return_value.rename.return_value = type(
            "Profile",
            (),
            {"id": "abc123", "name": "NewName"},
        )()
        result = runner.invoke(app, ["rename", "abc123", "NewName"])
    assert result.exit_code == 0
    assert "Renamed profile to 'NewName' (abc123)" in result.output


def test_rename_empty_name_fails():
    result = runner.invoke(app, ["rename", "abc123", "   "])
    assert result.exit_code == 1
    assert "Error [invalid_input]: profile name cannot be empty" in result.output


def test_status_all_profiles():
    with (
        patch("profiledock.cli.manager") as mock_manager,
        patch("profiledock.cli.get_status", return_value="running"),
    ):
        mock_manager.return_value.list_profiles.return_value = [
            type(
                "Profile",
                (),
                {"id": "abc123", "name": "Work", "data_dir": "/path/to/work"},
            )()
        ]
        result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "ID" in result.output
    assert "NAME" in result.output
    assert "STATUS" in result.output
    assert "abc123" in result.output
    assert "Work" in result.output
    assert "running" in result.output


def test_status_single_profile():
    with (
        patch("profiledock.cli.manager") as mock_manager,
        patch("profiledock.cli.get_status", return_value="stopped"),
    ):
        mock_manager.return_value.resolve.return_value = type(
            "Profile",
            (),
            {"id": "abc123", "name": "Work", "data_dir": "/path/to/work"},
        )()
        result = runner.invoke(app, ["status", "abc123"])
    assert result.exit_code == 0
    assert "abc123\tWork\tdirect\tstopped" in result.output


def test_status_all_profiles_json():
    import json

    with (
        patch("profiledock.cli.manager") as mock_manager,
        patch("profiledock.cli.get_status", return_value="running"),
    ):
        mock_manager.return_value.list_profiles.return_value = [
            type(
                "Profile",
                (),
                {"id": "abc123", "name": "Work", "data_dir": "/path/to/work"},
            )()
        ]
        result = runner.invoke(app, ["status", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["output_version"] == 1
    data = data["data"]
    assert isinstance(data, list)
    assert data[0]["id"] == "abc123"
    assert data[0]["name"] == "Work"
    assert data[0]["status"] == "running"


def test_status_single_profile_json():
    import json

    with (
        patch("profiledock.cli.manager") as mock_manager,
        patch("profiledock.cli.get_status", return_value="error"),
    ):
        mock_manager.return_value.resolve.return_value = type(
            "Profile",
            (),
            {"id": "abc123", "name": "Work", "data_dir": "/path/to/work"},
        )()
        result = runner.invoke(app, ["status", "abc123", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["output_version"] == 1
    data = data["data"]
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["id"] == "abc123"
    assert data[0]["name"] == "Work"
    assert data[0]["status"] == "error"


def test_status_watch_flag(tmp_path):
    with (
        patch("profiledock.cli.manager") as mock_manager,
        patch("profiledock.cli.get_status", return_value="running"),
        patch("time.sleep", side_effect=KeyboardInterrupt),
    ):
        mock_manager.return_value.list_profiles.return_value = [
            type(
                "Profile",
                (),
                {"id": "abc123", "name": "Work", "data_dir": "/path/to/work"},
            )()
        ]
        result = runner.invoke(app, ["status", "--watch", "--interval", "0.5"])
    assert result.exit_code == 0
    assert "Work" in result.output
    assert "running" in result.output


def test_status_watch_invalid_interval():
    result = runner.invoke(app, ["status", "--watch", "--interval", "0"])
    assert result.exit_code == EXIT_USER_ERROR
    assert "interval must be greater than 0" in result.stderr


def test_status_watch_rejects_json_combination(tmp_path):
    result = runner.invoke(app, ["--data-root", str(tmp_path), "status", "--watch", "--json"])
    assert result.exit_code == EXIT_USER_ERROR
    assert "--watch cannot be combined with --json" in result.stderr


def test_status_json_without_watch_emits_single_envelope(tmp_path):
    with (
        patch("profiledock.cli.manager") as mock_manager,
        patch("profiledock.cli.get_status", return_value="stopped"),
    ):
        mock_manager.return_value.list_profiles.return_value = []
        result = runner.invoke(app, ["--data-root", str(tmp_path), "status", "--json"])
    assert result.exit_code == EXIT_SUCCESS
    payload = json.loads(result.stdout)
    assert payload["output_version"] == 1
    assert payload["data"] == []


def test_exit_code_success():
    with patch("profiledock.cli.manager") as mock_manager:
        mock_manager.return_value.list_profiles.return_value = []
        result = runner.invoke(app, ["list"])
    assert result.exit_code == EXIT_SUCCESS


def test_exit_code_user_error():
    with patch("profiledock.cli.manager") as mock_manager:
        mock_manager.return_value.resolve.side_effect = ProfileNotFoundError("profile not found: missing")
        result = runner.invoke(app, ["show", "missing"])
    assert result.exit_code == EXIT_USER_ERROR


def test_exit_code_empty_name():
    result = runner.invoke(app, ["rename", "abc123", "   "])
    assert result.exit_code == EXIT_USER_ERROR


def test_json_list_schema():
    import json

    with (
        patch("profiledock.cli.manager") as mock_manager,
        patch("profiledock.cli.get_status", return_value="stopped"),
    ):
        mock_manager.return_value.list_profiles.return_value = [
            type(
                "Profile",
                (),
                {
                    "id": "abc123",
                    "name": "Work",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "data_dir": "/path/to/work",
                    "last_launched_at": None,
                },
            )()
        ]
        result = runner.invoke(app, ["list", "--json"])

    assert result.exit_code == EXIT_SUCCESS
    data = json.loads(result.output)
    assert data["output_version"] == 1
    data = data["data"]
    assert isinstance(data, list)
    assert len(data) == 1

    profile = data[0]
    assert "id" in profile
    assert "name" in profile
    assert "status" in profile
    assert "created_at" in profile
    assert "data_dir" in profile
    assert "last_launched_at" in profile

    assert isinstance(profile["id"], str)
    assert isinstance(profile["name"], str)
    assert isinstance(profile["status"], str)
    assert isinstance(profile["created_at"], str)
    assert isinstance(profile["data_dir"], str)

    valid_statuses = {"stopped", "starting", "running", "closing", "stale", "error"}
    assert profile["status"] in valid_statuses

    assert "token" not in profile
    assert "password" not in profile
    assert "secret" not in profile


def test_json_show_schema():
    import json

    with (
        patch("profiledock.cli.manager") as mock_manager,
        patch("profiledock.cli.get_status", return_value="running"),
    ):
        mock_manager.return_value.resolve.return_value = type(
            "Profile",
            (),
            {
                "id": "abc123",
                "name": "Work",
                "created_at": "2026-01-01T00:00:00+00:00",
                "data_dir": "/path/to/work",
                "last_launched_at": "2026-01-01T12:00:00+00:00",
            },
        )()
        result = runner.invoke(app, ["show", "abc123", "--json"])

    assert result.exit_code == EXIT_SUCCESS
    data = json.loads(result.output)
    assert data["output_version"] == 1
    data = data["data"]
    assert isinstance(data, dict)

    required_fields = ["id", "name", "status", "created_at", "data_dir", "last_launched_at", "engine"]
    for field in required_fields:
        assert field in data, f"Missing required field: {field}"

    assert isinstance(data["id"], str)
    assert isinstance(data["name"], str)
    assert isinstance(data["status"], str)
    assert isinstance(data["created_at"], str)
    assert isinstance(data["data_dir"], str)
    assert data["last_launched_at"] is None or isinstance(data["last_launched_at"], str)
    assert data["engine"] is None or isinstance(data["engine"], str)

    assert "token" not in data
    assert "password" not in data
    assert "secret" not in data


def test_json_status_schema():
    import json

    with (
        patch("profiledock.cli.manager") as mock_manager,
        patch("profiledock.cli.get_status", return_value="running"),
    ):
        mock_manager.return_value.list_profiles.return_value = [
            type(
                "Profile",
                (),
                {"id": "abc123", "name": "Work", "data_dir": "/path/to/work"},
            )()
        ]
        result = runner.invoke(app, ["status", "--json"])

    assert result.exit_code == EXIT_SUCCESS
    data = json.loads(result.output)
    assert data["output_version"] == 1
    data = data["data"]

    assert isinstance(data, list)
    assert len(data) == 1

    profile = data[0]
    assert "id" in profile
    assert "name" in profile
    assert "status" in profile

    assert isinstance(profile["id"], str)
    assert isinstance(profile["name"], str)
    assert isinstance(profile["status"], str)

    valid_statuses = {"stopped", "starting", "running", "closing", "stale", "error"}
    assert profile["status"] in valid_statuses

    assert "token" not in profile


def test_human_output_goes_to_stdout():
    with (
        patch("profiledock.cli.manager") as mock_manager,
        patch("profiledock.cli.get_status", return_value="stopped"),
    ):
        mock_manager.return_value.list_profiles.return_value = [
            type(
                "Profile",
                (),
                {"id": "abc123", "name": "Work", "data_dir": "/path/to/work"},
            )()
        ]
        result = runner.invoke(app, ["list"])

    assert result.exit_code == EXIT_SUCCESS
    assert "abc123" in result.output
    assert "Work" in result.output


def test_errors_go_to_stderr():
    with patch("profiledock.cli.manager") as mock_manager:
        mock_manager.return_value.resolve.side_effect = ProfileNotFoundError("profile not found: missing")
        result = runner.invoke(app, ["show", "missing"])

    assert result.exit_code == EXIT_USER_ERROR
    assert "Error [not_found]:" in result.stderr


def test_create_with_engine_flag():
    with patch("profiledock.cli.manager") as mock_manager:
        mock_manager.return_value.create.return_value = type(
            "Profile",
            (),
            {"id": "abc123", "name": "Work", "engine": "playwright"},
        )()
        result = runner.invoke(app, ["create", "Work", "--engine", "playwright"])
    assert result.exit_code == 0
    assert "Created profile 'Work' (abc123)" in result.output


def test_create_with_invalid_engine_fails():
    result = runner.invoke(app, ["create", "Work", "--engine", "unknown"])
    assert result.exit_code == EXIT_USER_ERROR
    assert "engine must be 'direct' or 'playwright'" in result.stderr


def test_set_engine_command():
    with patch("profiledock.cli.manager") as mock_manager:
        mock_manager.return_value.set_engine.return_value = type(
            "Profile",
            (),
            {"id": "abc123", "name": "Work", "engine": "direct"},
        )()
        result = runner.invoke(app, ["set-engine", "abc123", "direct"])
    assert result.exit_code == 0
    assert "Set engine to 'direct' for profile 'Work' (abc123)" in result.output


def test_set_engine_invalid_fails():
    result = runner.invoke(app, ["set-engine", "abc123", "invalid"])
    assert result.exit_code == EXIT_USER_ERROR
    assert "engine must be 'direct' or 'playwright'" in result.stderr


def test_launch_with_direct_engine(tmp_path):
    data_dir = tmp_path / "profiles" / "abc123" / "browser-data"
    data_dir.mkdir(parents=True)
    profile = type(
        "Profile",
        (),
        {"id": "abc123", "name": "DirectTest", "data_dir": str(data_dir), "engine": "direct"},
    )()
    with (
        patch("profiledock.cli.manager") as mock_manager,
        patch("profiledock.cli.start_direct_chrome") as mock_direct,
    ):
        mock_manager.return_value.resolve.return_value = profile
        mock_manager.return_value.runtime_path.return_value = tmp_path / "runtime" / "abc123"
        result = runner.invoke(app, ["launch", "abc123", "--tabs", "2"])
    assert result.exit_code == 0
    assert mock_direct.called
    assert "Launched 'DirectTest' (engine: direct) with 2 tab(s)." in result.output


def test_launch_with_playwright_override(tmp_path):
    data_dir = tmp_path / "profiles" / "abc123" / "browser-data"
    data_dir.mkdir(parents=True)
    profile = type(
        "Profile",
        (),
        {"id": "abc123", "name": "OverrideTest", "data_dir": str(data_dir), "engine": "direct"},
    )()
    with (
        patch("profiledock.cli.manager") as mock_manager,
        patch("profiledock.cli.start_controller") as mock_ctrl,
    ):
        mock_manager.return_value.resolve.return_value = profile
        mock_manager.return_value.runtime_path.return_value = tmp_path / "runtime" / "abc123"
        result = runner.invoke(app, ["launch", "abc123", "--tabs", "1", "--engine", "playwright"])
    assert result.exit_code == 0
    assert mock_ctrl.called
    assert "Launched 'OverrideTest' (engine: playwright, visible) with 1 tab(s)." in result.output


def test_create_with_engine_direct_integration(tmp_path):
    result = runner.invoke(
        app, ["--data-root", str(tmp_path), "create", "DirectProfile", "--engine", "direct"]
    )
    assert result.exit_code == 0
    assert "Created profile 'DirectProfile'" in result.output
    metadata_file = tmp_path / "metadata" / "profiles.json"
    doc = json.loads(metadata_file.read_text(encoding="utf-8"))
    assert doc["profiles"][0]["name"] == "DirectProfile"
    assert doc["profiles"][0]["engine"] == "direct"


def test_set_engine_updates_metadata_document(tmp_path):
    runner.invoke(app, ["--data-root", str(tmp_path), "create", "EngineTest"])
    metadata_file = tmp_path / "metadata" / "profiles.json"
    doc = json.loads(metadata_file.read_text(encoding="utf-8"))
    profile_id = doc["profiles"][0]["id"]
    assert doc["profiles"][0]["engine"] is None

    result = runner.invoke(app, ["--data-root", str(tmp_path), "set-engine", profile_id, "playwright"])
    assert result.exit_code == 0
    assert f"Set engine to 'playwright' for profile 'EngineTest' ({profile_id})" in result.output

    updated_doc = json.loads(metadata_file.read_text(encoding="utf-8"))
    assert updated_doc["profiles"][0]["engine"] == "playwright"


def test_resolve_engine_precedence():
    p_direct = Profile("id1", "Name", "2026-01-01T00:00:00+00:00", "/path", engine="direct")
    p_pw = Profile("id2", "Name", "2026-01-01T00:00:00+00:00", "/path", engine="playwright")
    p_none = Profile("id3", "Name", "2026-01-01T00:00:00+00:00", "/path", engine=None)

    with patch.dict(os.environ, {"PROFILEDOCK_DEFAULT_ENGINE": "playwright"}):
        assert resolve_engine("direct", p_pw) == "direct"

    with patch.dict(os.environ, {"PROFILEDOCK_DEFAULT_ENGINE": "direct"}):
        assert resolve_engine("playwright", p_direct) == "playwright"

    with patch.dict(os.environ, {"PROFILEDOCK_DEFAULT_ENGINE": "playwright"}):
        assert resolve_engine(None, p_direct) == "direct"

    with patch.dict(os.environ, {"PROFILEDOCK_DEFAULT_ENGINE": "direct"}):
        assert resolve_engine(None, p_pw) == "playwright"

    with patch.dict(os.environ, {"PROFILEDOCK_DEFAULT_ENGINE": "playwright"}):
        assert resolve_engine(None, p_none) == "playwright"

    with patch.dict(os.environ, {}, clear=True):
        assert resolve_engine(None, p_none) == "direct"


def test_config_cli_commands_and_presets(tmp_path):
    runner.invoke(app, ["--data-root", str(tmp_path), "create", "ConfigProfile"])

    res_set_tabs = runner.invoke(
        app, ["--data-root", str(tmp_path), "config", "set", "ConfigProfile", "default-tabs", "4"]
    )
    assert res_set_tabs.exit_code == 0
    assert "Set default-tabs to 4" in res_set_tabs.output

    res_set_engine = runner.invoke(
        app, ["--data-root", str(tmp_path), "config", "set", "ConfigProfile", "engine", "playwright"]
    )
    assert res_set_engine.exit_code == 0
    assert "Set engine to 'playwright'" in res_set_engine.output

    res_set_win = runner.invoke(
        app, ["--data-root", str(tmp_path), "config", "set", "ConfigProfile", "window-size", "1440x900"]
    )
    assert res_set_win.exit_code == 0
    assert "Set window-size to 1440x900" in res_set_win.output

    res_add_url = runner.invoke(
        app,
        ["--data-root", str(tmp_path), "config", "add-url", "ConfigProfile", "https://news.ycombinator.com"],
    )
    assert res_add_url.exit_code == 0

    res_show = runner.invoke(app, ["--data-root", str(tmp_path), "config", "show", "ConfigProfile"])
    assert res_show.exit_code == 0
    assert "1440x900" in res_show.output
    assert "https://news.ycombinator.com" in res_show.output

    res_show_json = runner.invoke(
        app, ["--data-root", str(tmp_path), "config", "show", "ConfigProfile", "--json"]
    )
    assert res_show_json.exit_code == 0
    cfg_data = json.loads(res_show_json.output)
    assert cfg_data["output_version"] == 1
    cfg_data = cfg_data["data"]
    assert cfg_data["default_tabs"] == 4
    assert cfg_data["engine"] == "playwright"
    assert cfg_data["window_width"] == 1440
    assert cfg_data["start_urls"] == ["https://news.ycombinator.com"]

    res_rem_url = runner.invoke(
        app,
        [
            "--data-root",
            str(tmp_path),
            "config",
            "remove-url",
            "ConfigProfile",
            "https://news.ycombinator.com",
        ],
    )
    assert res_rem_url.exit_code == 0

    res_reset = runner.invoke(app, ["--data-root", str(tmp_path), "config", "reset", "ConfigProfile"])
    assert res_reset.exit_code == 0

    res_show_after = runner.invoke(
        app, ["--data-root", str(tmp_path), "config", "show", "ConfigProfile", "--json"]
    )
    cfg_after = json.loads(res_show_after.output)["data"]
    assert cfg_after["default_tabs"] is None
    assert cfg_after["start_urls"] == []


def test_resolve_engine_rejects_invalid_environment_value():
    profile = Profile("id1", "Name", "2026-01-01T00:00:00+00:00", "/path")
    with (
        patch.dict(os.environ, {"PROFILEDOCK_DEFAULT_ENGINE": "invalid"}),
        patch("profiledock.cli.manager") as mock_manager,
        patch("profiledock.cli.get_status", return_value="stopped"),
    ):
        mock_manager.return_value.list_profiles.return_value = [profile]
        result = runner.invoke(app, ["status"])
    assert result.exit_code == EXIT_USER_ERROR
    assert "PROFILEDOCK_DEFAULT_ENGINE must be 'direct' or 'playwright'" in result.stderr


def test_shot_command_success_and_json(tmp_path, monkeypatch):
    from profiledock import cli as pd_cli

    runner.invoke(app, ["--data-root", str(tmp_path), "create", "ShotProfile"])
    captured: dict[str, object] = {}

    def fake_send(data_dir, cmd, args, runtime_dir=None, timeout=30.0, auto_start_headless=True):
        captured["cmd"] = cmd
        captured["args"] = args
        out_path = args["output"]
        Path(out_path).write_bytes(b"\x89PNG")
        return {"status": "ok", "output": out_path, "url": "https://example.com", "title": "Ex", "bytes": 4}

    monkeypatch.setattr(pd_cli, "send_controller_command", fake_send)
    out_file = tmp_path / "page.png"
    res = runner.invoke(
        app,
        ["--data-root", str(tmp_path), "shot", "ShotProfile", "--output", str(out_file)],
    )
    assert res.exit_code == EXIT_SUCCESS, res.output
    assert "Screenshot saved" in res.output
    assert captured["cmd"] == "screenshot"
    assert captured["args"]["full_page"] is False
    assert Path(captured["args"]["output"]) == out_file.resolve()

    json_res = runner.invoke(
        app,
        ["--data-root", str(tmp_path), "shot", "ShotProfile", "--output", str(out_file), "--json"],
    )
    assert json_res.exit_code == EXIT_SUCCESS
    payload = json.loads(json_res.output)
    assert payload["command"] == "shot"
    assert payload["data"]["bytes"] == 4
    assert payload["data"]["full_page"] is False


def test_shot_command_full_page_passthrough(tmp_path, monkeypatch):
    from profiledock import cli as pd_cli

    runner.invoke(app, ["--data-root", str(tmp_path), "create", "FullPage"])
    captured: dict[str, object] = {}

    def fake_send(data_dir, cmd, args, **kwargs):
        captured["args"] = args
        Path(args["output"]).write_bytes(b"\x89PNG")
        return {"status": "ok", "output": args["output"], "url": "", "title": "", "bytes": 4}

    monkeypatch.setattr(pd_cli, "send_controller_command", fake_send)
    out_file = tmp_path / "full.png"
    res = runner.invoke(
        app,
        ["--data-root", str(tmp_path), "shot", "FullPage", "--output", str(out_file), "--full-page"],
    )
    assert res.exit_code == EXIT_SUCCESS
    assert captured["args"]["full_page"] is True


def test_shot_command_rejects_non_png_output(tmp_path):
    runner.invoke(app, ["--data-root", str(tmp_path), "create", "PngOnly"])
    res = runner.invoke(
        app,
        ["--data-root", str(tmp_path), "shot", "PngOnly", "--output", str(tmp_path / "page.jpg")],
    )
    assert res.exit_code == EXIT_USER_ERROR
    assert ".png" in res.output


def test_shot_command_rejects_missing_output_directory(tmp_path):
    runner.invoke(app, ["--data-root", str(tmp_path), "create", "DirCheck"])
    res = runner.invoke(
        app,
        ["--data-root", str(tmp_path), "shot", "DirCheck", "--output", str(tmp_path / "nope" / "p.png")],
    )
    assert res.exit_code == EXIT_USER_ERROR
    assert "directory does not exist" in res.output


def test_shot_command_rejects_invalid_url(tmp_path):
    runner.invoke(app, ["--data-root", str(tmp_path), "create", "UrlCheck"])
    res = runner.invoke(
        app,
        ["--data-root", str(tmp_path), "shot", "UrlCheck", "javascript:alert(1)"],
    )
    assert res.exit_code == EXIT_USER_ERROR


def test_shot_command_rejects_negative_tab(tmp_path):
    runner.invoke(app, ["--data-root", str(tmp_path), "create", "TabCheck"])
    res = runner.invoke(
        app,
        ["--data-root", str(tmp_path), "shot", "TabCheck", "--tab", "-1"],
    )
    assert res.exit_code == EXIT_USER_ERROR
    assert "tab index" in res.output


def test_pdf_command_success_and_json(tmp_path, monkeypatch):
    from profiledock import cli as pd_cli

    runner.invoke(app, ["--data-root", str(tmp_path), "create", "PdfProfile"])
    captured: dict[str, object] = {}

    def fake_send(data_dir, cmd, args, **kwargs):
        captured["cmd"] = cmd
        Path(args["output"]).write_bytes(b"%PDF-1.4")
        return {"status": "ok", "output": args["output"], "url": "https://example.com", "title": "T", "bytes": 8}

    monkeypatch.setattr(pd_cli, "send_controller_command", fake_send)
    out_file = tmp_path / "page.pdf"
    res = runner.invoke(
        app,
        ["--data-root", str(tmp_path), "pdf", "PdfProfile", "--output", str(out_file)],
    )
    assert res.exit_code == EXIT_SUCCESS, res.output
    assert "PDF saved" in res.output
    assert captured["cmd"] == "pdf"

    json_res = runner.invoke(
        app,
        ["--data-root", str(tmp_path), "pdf", "PdfProfile", "--output", str(out_file), "--json"],
    )
    assert json_res.exit_code == EXIT_SUCCESS
    payload = json.loads(json_res.output)
    assert payload["command"] == "pdf"
    assert payload["data"]["bytes"] == 8


def test_pdf_command_rejects_non_pdf_output(tmp_path):
    runner.invoke(app, ["--data-root", str(tmp_path), "create", "PdfExt"])
    res = runner.invoke(
        app,
        ["--data-root", str(tmp_path), "pdf", "PdfExt", "--output", str(tmp_path / "page.png")],
    )
    assert res.exit_code == EXIT_USER_ERROR
    assert ".pdf" in res.output


def test_shot_unknown_profile_fails_cleanly_with_log(tmp_path):
    """resolve() failures must produce a categorized error AND a log entry."""
    from profiledock.cli import EXIT_USER_ERROR

    runner.invoke(app, ["--data-root", str(tmp_path), "create", "Known"])
    res = runner.invoke(
        app,
        ["--data-root", str(tmp_path), "shot", "NoSuchProfile", "--output", str(tmp_path / "x.png")],
    )
    assert res.exit_code == EXIT_USER_ERROR
    assert "Error [not_found]" in res.output + (res.stderr or "")

    from profiledock.data_root import resolve_data_root
    from profiledock.logger import read_profile_logs

    paths = resolve_data_root(Path(tmp_path), prepare=True)
    entries = read_profile_logs(paths.logs_dir, profile_id=None, last_n=10)
    assert any(e.get("event") == "screenshot_failed" for e in entries)


def test_shot_default_filename_uses_profile_name(tmp_path, monkeypatch):
    from profiledock import cli as pd_cli

    created = {}

    def fake_send(data_dir, cmd, args, **kwargs):
        created["output"] = args["output"]
        Path(args["output"]).write_bytes(b"\x89PNG")
        return {"status": "ok", "output": args["output"], "url": "", "title": "", "bytes": 4}

    runner.invoke(app, ["--data-root", str(tmp_path), "create", "My Fancy Profile"])
    monkeypatch.setattr(pd_cli, "send_controller_command", fake_send)
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(app, ["--data-root", str(tmp_path), "shot", "My Fancy Profile"])
    assert res.exit_code == EXIT_SUCCESS, res.output
    name = Path(created["output"]).name
    assert name.startswith("My Fancy Profile-"), name
    assert name.endswith(".png")
