from unittest.mock import patch

from typer.testing import CliRunner

from profiledock.cli import app
from profiledock.process_manager import BrowserLaunchError, ProfileRunningError
from profiledock.profile_manager import ProfileNotFoundError

runner = CliRunner()


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

    def fake_start_controller(data_dir, tabs, headless=False):
        raise BrowserLaunchError(
            "Playwright Chromium: not installed\nGoogle Chrome: not found"
        )

    with patch("profiledock.cli.manager") as mock_manager, patch(
        "profiledock.cli.start_controller", side_effect=fake_start_controller
    ):
        mock_manager.return_value.get.return_value = type(
            "Profile",
            (),
            {"id": "abc123", "name": "Test", "data_dir": str(data_dir)},
        )()
        result = runner.invoke(app, ["launch", "abc123", "--tabs", "1"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "Error:" in result.output
    assert "Playwright Chromium" in result.output


def test_launch_profile_not_found_shown_concisely(tmp_path):
    with patch("profiledock.cli.manager") as mock_manager:
        mock_manager.return_value.get.side_effect = ProfileNotFoundError(
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

    def fake_start_controller(data_dir, tabs, headless=False):
        raise ProfileRunningError("profile is already running")

    with patch("profiledock.cli.manager") as mock_manager, patch(
        "profiledock.cli.start_controller", side_effect=fake_start_controller
    ):
        mock_manager.return_value.get.return_value = type(
            "Profile",
            (),
            {"id": "abc123", "name": "Test", "data_dir": str(data_dir)},
        )()
        result = runner.invoke(app, ["launch", "abc123", "--tabs", "1"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "Error: profile is already running" in result.output


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

    def fake_close_controller(data_dir, timeout=15):
        raise BrowserLaunchError("profile is not running")

    with patch("profiledock.cli.manager") as mock_manager, patch(
        "profiledock.cli.close_controller", side_effect=fake_close_controller
    ):
        mock_manager.return_value.get.return_value = type(
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
        mock_manager.return_value.get.side_effect = ProfileNotFoundError(
            "profile not found: missing"
        )
        result = runner.invoke(app, ["delete", "missing", "--yes"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "Error: profile not found: missing" in result.output
