import json
import os
from unittest.mock import patch

from typer.testing import CliRunner

from profiledock.cli import app, EXIT_SUCCESS, EXIT_USER_ERROR, resolve_engine
from profiledock.models import Profile
from profiledock.process_manager import BrowserLaunchError, ProfileRunningError
from profiledock.profile_manager import AmbiguousProfileError, ProfileNotFoundError
from profiledock.storage import StorageError
from profiledock.version import __version__


runner = CliRunner()


def test_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


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

    def fake_start_controller(data_dir, tabs, headless=False, runtime_dir=None):
        raise BrowserLaunchError(
            "Playwright Chromium: not installed\nGoogle Chrome: not found"
        )

    with patch("profiledock.cli.manager") as mock_manager, patch(
        "profiledock.cli.start_controller", side_effect=fake_start_controller
    ):
        mock_manager.return_value.resolve.return_value = type(
            "Profile",
            (),
            {"id": "abc123", "name": "Test", "data_dir": str(data_dir), "engine": "playwright"},
        )()
        result = runner.invoke(app, ["launch", "abc123", "--tabs", "1"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "Error:" in result.output
    assert "Playwright Chromium" in result.output


def test_launch_profile_not_found_shown_concisely(tmp_path):
    with patch("profiledock.cli.manager") as mock_manager:
        mock_manager.return_value.resolve.side_effect = ProfileNotFoundError(
            "profile not found: missing"
        )
        result = runner.invoke(app, ["launch", "missing", "--tabs", "1"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "Error: profile not found: missing" in result.output


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

    def fake_start_controller(data_dir, tabs, headless=False, runtime_dir=None):
        raise ProfileRunningError("profile is already running")

    with patch("profiledock.cli.manager") as mock_manager, patch(
        "profiledock.cli.start_controller", side_effect=fake_start_controller
    ):
        mock_manager.return_value.resolve.return_value = type(
            "Profile",
            (),
            {"id": "abc123", "name": "Test", "data_dir": str(data_dir), "engine": "playwright"},
        )()
        result = runner.invoke(app, ["launch", "abc123", "--tabs", "1"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "Error: profile is already running" in result.output


def test_launch_succeeds_when_timestamp_update_fails(tmp_path):
    data_dir = tmp_path / "profiles" / "abc123" / "browser-data"
    data_dir.mkdir(parents=True)
    profile = type(
        "Profile",
        (),
        {"id": "abc123", "name": "Test", "data_dir": str(data_dir), "engine": "playwright"},
    )()
    with patch("profiledock.cli.manager") as mock_manager, patch(
        "profiledock.cli.start_controller"
    ):
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

    with patch("profiledock.cli.manager") as mock_manager, patch(
        "profiledock.cli.close_controller", side_effect=fake_close_controller
    ):
        mock_manager.return_value.resolve.return_value = type(
            "Profile",
            (),
            {"id": "abc123", "name": "Test", "data_dir": str(data_dir)},
        )()
        result = runner.invoke(app, ["close", "abc123"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "Error: profile is not running" in result.output


def test_delete_profile_not_found_shown_concisely(tmp_path):
    with patch("profiledock.cli.manager") as mock_manager:
        mock_manager.return_value.resolve.side_effect = ProfileNotFoundError(
            "profile not found: missing"
        )
        result = runner.invoke(app, ["delete", "missing", "--yes"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "Error: profile not found: missing" in result.output


def test_list_table_format():
    with patch("profiledock.cli.manager") as mock_manager, patch(
        "profiledock.cli.get_status", return_value="stopped"
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

    with patch("profiledock.cli.manager") as mock_manager, patch(
        "profiledock.cli.get_status", return_value="running"
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
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["id"] == "abc123"
    assert data[0]["name"] == "Work"
    assert data[0]["status"] == "running"
    assert "token" not in result.output


def test_show_command():
    with patch("profiledock.cli.manager") as mock_manager, patch(
        "profiledock.cli.get_status", return_value="stopped"
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
    assert "Never" in result.output


def test_show_json_command():
    import json

    with patch("profiledock.cli.manager") as mock_manager, patch(
        "profiledock.cli.get_status", return_value="running"
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
    assert "Error: profile name cannot be empty" in result.output


def test_status_all_profiles():
    with patch("profiledock.cli.manager") as mock_manager, patch(
        "profiledock.cli.get_status", return_value="running"
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
    with patch("profiledock.cli.manager") as mock_manager, patch(
        "profiledock.cli.get_status", return_value="stopped"
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

    with patch("profiledock.cli.manager") as mock_manager, patch(
        "profiledock.cli.get_status", return_value="running"
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
    assert isinstance(data, list)
    assert data[0]["id"] == "abc123"
    assert data[0]["name"] == "Work"
    assert data[0]["status"] == "running"


def test_status_single_profile_json():
    import json

    with patch("profiledock.cli.manager") as mock_manager, patch(
        "profiledock.cli.get_status", return_value="error"
    ):
        mock_manager.return_value.resolve.return_value = type(
            "Profile",
            (),
            {"id": "abc123", "name": "Work", "data_dir": "/path/to/work"},
        )()
        result = runner.invoke(app, ["status", "abc123", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["id"] == "abc123"
    assert data[0]["name"] == "Work"
    assert data[0]["status"] == "error"


def test_exit_code_success():
    with patch("profiledock.cli.manager") as mock_manager:
        mock_manager.return_value.list_profiles.return_value = []
        result = runner.invoke(app, ["list"])
    assert result.exit_code == EXIT_SUCCESS


def test_exit_code_user_error():
    with patch("profiledock.cli.manager") as mock_manager:
        mock_manager.return_value.resolve.side_effect = ProfileNotFoundError(
            "profile not found: missing"
        )
        result = runner.invoke(app, ["show", "missing"])
    assert result.exit_code == EXIT_USER_ERROR


def test_exit_code_empty_name():
    result = runner.invoke(app, ["rename", "abc123", "   "])
    assert result.exit_code == EXIT_USER_ERROR


def test_json_list_schema():
    import json

    with patch("profiledock.cli.manager") as mock_manager, patch(
        "profiledock.cli.get_status", return_value="stopped"
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

    with patch("profiledock.cli.manager") as mock_manager, patch(
        "profiledock.cli.get_status", return_value="running"
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

    with patch("profiledock.cli.manager") as mock_manager, patch(
        "profiledock.cli.get_status", return_value="running"
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
    with patch("profiledock.cli.manager") as mock_manager, patch(
        "profiledock.cli.get_status", return_value="stopped"
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
        mock_manager.return_value.resolve.side_effect = ProfileNotFoundError(
            "profile not found: missing"
        )
        result = runner.invoke(app, ["show", "missing"])

    assert result.exit_code == EXIT_USER_ERROR
    assert "Error:" in result.stderr


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
    with patch("profiledock.cli.manager") as mock_manager, patch(
        "profiledock.cli.start_direct_chrome"
    ) as mock_direct:
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
    with patch("profiledock.cli.manager") as mock_manager, patch(
        "profiledock.cli.start_controller"
    ) as mock_ctrl:
        mock_manager.return_value.resolve.return_value = profile
        mock_manager.return_value.runtime_path.return_value = tmp_path / "runtime" / "abc123"
        result = runner.invoke(app, ["launch", "abc123", "--tabs", "1", "--engine", "playwright"])
    assert result.exit_code == 0
    assert mock_ctrl.called
    assert "Launched 'OverrideTest' (engine: playwright) with 1 tab(s)." in result.output


def test_create_with_engine_direct_integration(tmp_path):
    result = runner.invoke(app, ["--data-root", str(tmp_path), "create", "DirectProfile", "--engine", "direct"])
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


def test_resolve_engine_rejects_invalid_environment_value():
    profile = Profile("id1", "Name", "2026-01-01T00:00:00+00:00", "/path")
    with patch.dict(
        os.environ, {"PROFILEDOCK_DEFAULT_ENGINE": "invalid"}
    ), patch("profiledock.cli.manager") as mock_manager, patch(
        "profiledock.cli.get_status", return_value="stopped"
    ):
        mock_manager.return_value.list_profiles.return_value = [profile]
        result = runner.invoke(app, ["status"])
    assert result.exit_code == EXIT_USER_ERROR
    assert "PROFILEDOCK_DEFAULT_ENGINE must be 'direct' or 'playwright'" in result.stderr
