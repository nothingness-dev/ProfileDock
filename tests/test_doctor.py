import json
import sys
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from profiledock.cli import EXIT_SUCCESS, EXIT_USER_ERROR, app
from profiledock.data_root import DataPaths
from profiledock.doctor import (
    STATUS_FAILED,
    STATUS_OK,
    STATUS_WARNING,
    DiagnosticCheck,
    check_browser_availability,
    check_data_root_writable,
    check_direct_chrome,
    check_metadata_backup_state,
    check_metadata_schema,
    check_orphan_directories,
    check_playwright_package,
    check_profile_directories,
    check_python_version,
    check_stale_running_state,
    repair_environment,
)
from profiledock.models import MetadataDocument, Profile
from profiledock.storage import load_metadata, save_metadata

runner = CliRunner()


def paths(root):
    result = DataPaths.from_root(root)
    result.prepare()
    return result


def test_check_python_version():
    res = check_python_version()
    assert res.id == "python_version"
    assert res.status == STATUS_OK


def test_check_python_version_unsupported():
    with patch("sys.version_info", (3, 8, 0)):
        res = check_python_version()
        assert res.id == "python_version"
        assert res.status == STATUS_FAILED
        assert res.action is not None


def test_check_data_root_writable(tmp_path):
    res = check_data_root_writable(tmp_path)
    assert res.id == "writable_data_root"
    assert res.status == STATUS_OK


def test_check_data_root_unwritable(tmp_path):
    with patch.object(Path, "write_text", side_effect=PermissionError("read-only")):
        res = check_data_root_writable(tmp_path)
        assert res.id == "writable_data_root"
        assert res.status == STATUS_FAILED


def test_check_metadata_schema_missing(tmp_path):
    res = check_metadata_schema(tmp_path)
    assert res.id == "metadata_schema"
    assert res.status == STATUS_OK


def test_check_metadata_schema_valid(tmp_path):
    layout = paths(tmp_path)
    profiles_file = layout.profiles_file
    profiles_dir = layout.profiles_dir
    data_dir = profiles_dir / "p1" / "browser-data"
    doc = MetadataDocument(
        schema_version=1,
        profiles=[Profile("p1", "Name", "2026-01-01T00:00:00+00:00", str(data_dir))],
    )
    save_metadata(doc, profiles_file, profiles_dir)
    res = check_metadata_schema(tmp_path)
    assert res.status == STATUS_OK
    assert "Valid metadata document" in res.summary


def test_check_metadata_schema_legacy_bare_array(tmp_path):
    layout = paths(tmp_path)
    profiles_file = layout.profiles_file
    profiles_dir = layout.profiles_dir
    data_dir = profiles_dir / "p1" / "browser-data"
    profiles_file.write_text(
        json.dumps(
            [
                {
                    "id": "p1",
                    "name": "Name",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "data_dir": str(data_dir),
                }
            ]
        ),
        encoding="utf-8",
    )
    res = check_metadata_schema(tmp_path)
    assert res.status == STATUS_WARNING
    assert "legacy bare-array format" in res.summary


def test_check_metadata_schema_corrupted(tmp_path):
    profiles_file = paths(tmp_path).profiles_file
    profiles_file.write_text("invalid json", encoding="utf-8")
    res = check_metadata_schema(tmp_path)
    assert res.status == STATUS_FAILED


def test_check_metadata_backup_state_empty(tmp_path):
    res = check_metadata_backup_state(tmp_path)
    assert res.status == STATUS_OK


def test_check_metadata_backup_state_valid(tmp_path):
    bak = paths(tmp_path).backup_file
    bak.write_text(json.dumps({"schema_version": 1, "profiles": []}), encoding="utf-8")
    res = check_metadata_backup_state(tmp_path)
    assert res.status == STATUS_OK


def test_check_metadata_backup_state_corrupted(tmp_path):
    bak = paths(tmp_path).backup_file
    bak.write_text("invalid json", encoding="utf-8")
    res = check_metadata_backup_state(tmp_path)
    assert res.status == STATUS_WARNING


def test_check_metadata_backup_state_rejects_unsafe_profile(tmp_path):
    layout = paths(tmp_path)
    layout.backup_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profiles": [
                    {
                        "id": "unsafe",
                        "name": "Unsafe",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "data_dir": str(tmp_path / "outside" / "browser-data"),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    res = check_metadata_backup_state(tmp_path)
    assert res.status == STATUS_WARNING


def test_check_profile_directories(tmp_path):
    layout = paths(tmp_path)
    profiles_file = layout.profiles_file
    profiles_dir = layout.profiles_dir
    data_dir = profiles_dir / "p1" / "browser-data"
    data_dir.mkdir(parents=True)
    doc = MetadataDocument(
        schema_version=1,
        profiles=[Profile("p1", "Name", "2026-01-01T00:00:00+00:00", str(data_dir))],
    )
    save_metadata(doc, profiles_file, profiles_dir)

    exist_chk, path_chk = check_profile_directories(tmp_path)
    assert exist_chk.status == STATUS_OK
    assert path_chk.status == STATUS_OK


def test_check_profile_directories_missing(tmp_path):
    layout = paths(tmp_path)
    profiles_file = layout.profiles_file
    profiles_dir = layout.profiles_dir
    data_dir = profiles_dir / "p1" / "browser-data"
    doc = MetadataDocument(
        schema_version=1,
        profiles=[Profile("p1", "Name", "2026-01-01T00:00:00+00:00", str(data_dir))],
    )
    save_metadata(doc, profiles_file, profiles_dir)

    exist_chk, path_chk = check_profile_directories(tmp_path)
    assert exist_chk.status == STATUS_WARNING
    assert "Missing data directories" in exist_chk.summary


def test_check_direct_chrome():
    with patch("profiledock.doctor._system_browser_executable", return_value=Path("/usr/bin/google-chrome")):
        chk = check_direct_chrome()
        assert chk.id == "system_chrome_executable"
        assert chk.status == STATUS_OK

    with patch("profiledock.doctor._system_browser_executable", return_value=None):
        chk = check_direct_chrome()
        assert chk.id == "system_chrome_executable"
        assert chk.status == STATUS_WARNING
        assert chk.action is not None


def test_check_browser_availability():
    pw_ok = DiagnosticCheck("playwright_chromium", STATUS_OK, "ok")
    sys_warn = DiagnosticCheck("system_chrome", STATUS_WARNING, "warn")
    avail = check_browser_availability(pw_ok, sys_warn)
    assert avail.status == STATUS_OK

    pw_warn = DiagnosticCheck("playwright_chromium", STATUS_WARNING, "warn")
    sys_ok = DiagnosticCheck("system_chrome", STATUS_OK, "ok")
    avail = check_browser_availability(pw_warn, sys_ok)
    assert avail.status == STATUS_OK

    direct_ok = DiagnosticCheck("system_chrome_executable", STATUS_OK, "ok")
    avail_direct = check_browser_availability(pw_warn, sys_warn, direct_ok)
    assert avail_direct.status == STATUS_OK

    avail_failed = check_browser_availability(pw_warn, sys_warn)
    assert avail_failed.status == STATUS_FAILED


def test_check_stale_running_state_direct(tmp_path):
    layout = paths(tmp_path)
    p1_dir = layout.runtime_dir / "p1"
    p1_dir.mkdir(parents=True)
    running_json = p1_dir / "running.json"
    running_json.write_text(
        json.dumps({"pid": 999999, "engine": "direct", "tabs": 1, "channel": "chrome"}),
        encoding="utf-8",
    )

    with patch("profiledock.process_manager._alive", return_value=False):
        chk, stale_files = check_stale_running_state(tmp_path)
        assert chk.status == STATUS_WARNING
        assert "ambiguous" in chk.summary
        assert stale_files == []


def test_check_stale_running_state(tmp_path):
    layout = paths(tmp_path)
    p1_dir = layout.runtime_dir / "p1"
    p1_dir.mkdir(parents=True)
    running_json = p1_dir / "running.json"
    running_json.write_text(json.dumps({"pid": 999999, "port": 0}), encoding="utf-8")

    chk, stale_files = check_stale_running_state(tmp_path)
    assert chk.status == STATUS_WARNING
    assert "ambiguous" in chk.summary
    assert stale_files == []


def test_check_stale_running_state_unreadable_file_is_cleanable(tmp_path):
    layout = paths(tmp_path)
    p1_dir = layout.runtime_dir / "p1"
    p1_dir.mkdir(parents=True)
    running_json = p1_dir / "running.json"
    running_json.write_text("{broken json", encoding="utf-8")

    chk, stale_files = check_stale_running_state(tmp_path)
    assert chk.status == STATUS_WARNING
    assert "ambiguous" not in chk.summary
    assert stale_files == [running_json]

    repairs = repair_environment(tmp_path)
    assert any("unreadable" in r.summary or "stale" in r.summary for r in repairs)
    assert not running_json.exists()


def test_check_stale_running_state_future_version_file_stays_ambiguous(tmp_path):
    layout = paths(tmp_path)
    p1_dir = layout.runtime_dir / "p1"
    p1_dir.mkdir(parents=True)
    running_json = p1_dir / "running.json"
    running_json.write_text(
        json.dumps({"protocol_version": 999999, "engine": "direct", "profile_id": "p1"}),
        encoding="utf-8",
    )

    chk, stale_files = check_stale_running_state(tmp_path)
    assert chk.status == STATUS_WARNING
    assert "ambiguous" in chk.summary
    assert stale_files == []
    assert running_json.exists()


def test_check_orphan_directories(tmp_path):
    layout = paths(tmp_path)
    profiles_dir = layout.profiles_dir
    (profiles_dir / "orphan1").mkdir(parents=True)
    profiles_file = layout.profiles_file
    profiles_file.write_text(json.dumps({"schema_version": 1, "profiles": []}), encoding="utf-8")

    res = check_orphan_directories(tmp_path)
    assert res.status == STATUS_WARNING
    assert "orphan1" in res.summary


def test_check_orphan_directories_does_not_guess_when_metadata_is_corrupt(tmp_path):
    layout = paths(tmp_path)
    (layout.profiles_dir / "profile1").mkdir()
    layout.profiles_file.write_text("corrupted", encoding="utf-8")
    res = check_orphan_directories(tmp_path)
    assert res.status == STATUS_WARNING
    assert "Cannot determine orphan directories" in res.summary
    assert "profile1" not in res.summary


def test_repair_environment_stale_files(tmp_path):
    layout = paths(tmp_path)
    p1_dir = layout.runtime_dir / "p1"
    p1_dir.mkdir(parents=True)
    running_json = p1_dir / "running.json"
    running_json.write_text(json.dumps({"pid": 999999, "port": 0}), encoding="utf-8")

    repairs = repair_environment(tmp_path)
    assert repairs == []
    assert running_json.exists()


def test_repair_environment_stale_direct_files(tmp_path):
    layout = paths(tmp_path)
    p1_dir = layout.runtime_dir / "p1"
    p1_dir.mkdir(parents=True)
    running_json = p1_dir / "running.json"
    running_json.write_text(
        json.dumps({"pid": 999999, "engine": "direct", "tabs": 1, "channel": "chrome"}),
        encoding="utf-8",
    )

    with patch("profiledock.process_manager._alive", return_value=False):
        repairs = repair_environment(tmp_path)
        assert repairs == []
        assert running_json.exists()


def test_repair_environment_metadata_recovery(tmp_path):
    layout = paths(tmp_path)
    profiles_file = layout.profiles_file
    backup_file = layout.backup_file
    profiles_dir = layout.profiles_dir
    data_dir = profiles_dir / "p1" / "browser-data"

    profiles_file.write_text("corrupt json", encoding="utf-8")
    backup_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profiles": [
                    {
                        "id": "p1",
                        "name": "Name",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "data_dir": str(data_dir),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    repairs = repair_environment(tmp_path)
    assert len(repairs) >= 1
    assert "Recovered valid metadata" in repairs[0].summary
    assert "schema_version" in profiles_file.read_text(encoding="utf-8")


def test_repair_environment_recovers_when_versioned_primary_is_unsafe(tmp_path):
    layout = paths(tmp_path)
    layout.profiles_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profiles": [
                    {
                        "id": "unsafe",
                        "name": "Unsafe",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "data_dir": str(tmp_path / "outside" / "browser-data"),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    layout.backup_file.write_text(
        json.dumps({"schema_version": 1, "profiles": []}),
        encoding="utf-8",
    )
    repairs = repair_environment(tmp_path)
    assert any(repair.id == "repair_metadata_recovery" for repair in repairs)
    assert json.loads(layout.profiles_file.read_text(encoding="utf-8"))["profiles"] == []


def test_repair_refuses_metadata_recovery_for_active_profile(tmp_path):
    layout = paths(tmp_path)
    layout.profiles_file.write_text("corrupt", encoding="utf-8")
    data_dir = layout.profiles_dir / "p1" / "browser-data"
    data_dir.mkdir(parents=True)
    layout.backup_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profiles": [
                    {
                        "id": "p1",
                        "name": "Active",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "data_dir": str(data_dir),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with patch("profiledock.doctor.is_active_for_mutation", return_value=True):
        repairs = repair_environment(tmp_path)
    assert repairs == []
    assert layout.profiles_file.read_text(encoding="utf-8") == "corrupt"


def test_repair_recreation_rolls_back_when_later_profile_is_active(tmp_path):
    layout = paths(tmp_path)
    first_data = layout.profiles_dir / "p1" / "browser-data"
    second_data = layout.profiles_dir / "p2" / "browser-data"
    layout.profiles_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profiles": [
                    {
                        "id": "p1",
                        "name": "First",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "data_dir": str(first_data),
                    },
                    {
                        "id": "p2",
                        "name": "Second",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "data_dir": str(second_data),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    def active_state(data_dir, runtime_dir):
        return Path(data_dir) == second_data

    with patch("profiledock.doctor.is_active_for_mutation", side_effect=active_state):
        repairs = repair_environment(tmp_path, recreate_missing_directories=True)
    assert repairs == []
    assert not first_data.exists()
    assert not second_data.exists()


def test_doctor_cli_healthy():
    with patch("profiledock.cli.run_diagnostics") as mock_diag:
        mock_diag.return_value = [
            DiagnosticCheck("python_version", STATUS_OK, "Python version ok"),
            DiagnosticCheck("writable_data_root", STATUS_OK, "Data root writable"),
        ]
        result = runner.invoke(app, ["doctor"])
    assert result.exit_code == EXIT_SUCCESS
    assert "python_version" in result.output
    assert "OK" in result.output


def test_doctor_cli_warning_exits_zero():
    with patch("profiledock.cli.run_diagnostics") as mock_diag:
        mock_diag.return_value = [
            DiagnosticCheck(
                "orphan_profile_directories", STATUS_WARNING, "Found orphan dir", action="Review manually"
            ),
        ]
        result = runner.invoke(app, ["doctor"])
    assert result.exit_code == EXIT_SUCCESS
    assert "WARNING" in result.output
    assert "Suggested Actions:" in result.output


def test_doctor_cli_failed_exits_one():
    with patch("profiledock.cli.run_diagnostics") as mock_diag:
        mock_diag.return_value = [
            DiagnosticCheck("metadata_schema", STATUS_FAILED, "Metadata corrupted", action="Restore backup"),
        ]
        result = runner.invoke(app, ["doctor"])
    assert result.exit_code == EXIT_USER_ERROR
    assert "FAILED" in result.output
    assert "Suggested Actions:" in result.output


def test_doctor_cli_json():
    with patch("profiledock.cli.run_diagnostics") as mock_diag:
        mock_diag.return_value = [
            DiagnosticCheck("python_version", STATUS_OK, "Python version ok"),
            DiagnosticCheck(
                "orphan_profile_directories", STATUS_WARNING, "Found orphan dir", action="Review manually"
            ),
        ]
        result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == EXIT_SUCCESS
    data = json.loads(result.output)
    assert data["output_version"] == 1
    data = data["data"]
    assert "checks" in data
    assert "repairs" in data
    assert "healthy" in data
    assert data["healthy"] is True
    assert len(data["checks"]) == 2
    assert data["checks"][0]["id"] == "python_version"
    assert data["checks"][0]["status"] == "ok"
    assert data["checks"][1]["action"] == "Review manually"


def test_doctor_cli_repair():
    with (
        patch("profiledock.cli.repair_environment") as mock_repair,
        patch("profiledock.cli.run_diagnostics") as mock_diag,
    ):
        mock_repair.return_value = [
            DiagnosticCheck(
                "repair_stale_running_state", STATUS_OK, "Cleaned up 1 stale running.json file(s)."
            ),
        ]
        mock_diag.return_value = [
            DiagnosticCheck("stale_running_state", STATUS_OK, "No stale running-state files detected."),
        ]
        result = runner.invoke(app, ["doctor", "--repair"])
    assert result.exit_code == EXIT_SUCCESS
    assert "Repairs performed:" in result.output
    assert "Cleaned up 1 stale running.json file(s)." in result.output


def test_doctor_repair_json_requires_yes():
    with patch("profiledock.cli.repair_environment") as mock_repair:
        result = runner.invoke(app, ["doctor", "--repair", "--recreate-missing", "--json"])
    assert result.exit_code == EXIT_USER_ERROR
    assert mock_repair.called is False
    stderr = result.stderr if result.stderr else ""
    data = json.loads(stderr)
    assert data["command"] == "doctor"
    assert data["data"]["healthy"] is False
    assert data["data"]["repairs"] == []
    check = data["data"]["checks"][0]
    assert check["id"] == "confirmation_required"
    assert check["status"] == "failed"
    assert "--recreate-missing requires --yes" in check["summary"]


def test_doctor_reattach_json_requires_yes():
    with patch("profiledock.cli.repair_environment") as mock_repair:
        result = runner.invoke(app, ["doctor", "--repair", "--reattach-orphans", "--json"])
    assert result.exit_code == EXIT_USER_ERROR
    assert mock_repair.called is False
    data = json.loads(result.stderr)
    assert data["data"]["healthy"] is False
    assert "--reattach-orphans requires --yes" in data["data"]["checks"][0]["summary"]


def test_doctor_destructive_json_with_yes_runs_repairs():
    with (
        patch("profiledock.cli.repair_environment") as mock_repair,
        patch("profiledock.cli.run_diagnostics") as mock_diag,
    ):
        mock_repair.return_value = []
        mock_diag.return_value = [DiagnosticCheck("python_version", STATUS_OK, "ok")]
        result = runner.invoke(app, ["doctor", "--repair", "--recreate-missing", "--json", "--yes"])
    assert result.exit_code == EXIT_SUCCESS
    assert mock_repair.called is True


def test_doctor_destructive_declined_aborts():
    with patch("profiledock.cli.repair_environment") as mock_repair:
        result = runner.invoke(app, ["doctor", "--repair", "--recreate-missing"], input="n\n")
    assert result.exit_code == EXIT_USER_ERROR
    assert mock_repair.called is False
    assert "Recreate missing" in result.output


def test_repair_reattach_orphans(tmp_path):
    layout = paths(tmp_path)
    orphan_dir = layout.profiles_dir / "orphan123"
    orphan_data = orphan_dir / "browser-data"
    orphan_data.mkdir(parents=True)
    (orphan_data / "cookies.txt").write_text("data", encoding="utf-8")

    layout.profiles_file.write_text(
        json.dumps({"schema_version": 1, "profiles": []}),
        encoding="utf-8",
    )

    repairs = repair_environment(tmp_path, reattach_orphans=True)
    assert any(r.id == "repair_reattach_orphans" for r in repairs)

    doc = load_metadata(layout.profiles_file)
    assert len(doc.profiles) == 1
    assert doc.profiles[0].id == "orphan123"
    assert doc.profiles[0].name.startswith("Recovered-orphan123")


def test_repair_incomplete_operations_cleanup(tmp_path):
    layout = paths(tmp_path)
    stale_temp = layout.profiles_dir / ".temp_restore_abc123"
    stale_deletion = layout.profiles_dir / ".deleting-abc123-deadbeef"
    stale_temp.mkdir(parents=True)
    stale_deletion.mkdir(parents=True)
    (stale_temp / "partial.txt").write_text("data", encoding="utf-8")
    (stale_deletion / "browser-data").mkdir()

    repairs = repair_environment(tmp_path)
    assert any(r.id == "repair_incomplete_operations" for r in repairs)
    assert not stale_temp.exists()
    assert not stale_deletion.exists()


def test_repair_recreate_missing_directories(tmp_path):
    layout = paths(tmp_path)
    missing_data_dir = layout.profiles_dir / "p1" / "browser-data"
    profile = Profile("p1", "Name", "2026-01-01T00:00:00+00:00", str(missing_data_dir))
    layout.profiles_file.write_text(
        json.dumps({"schema_version": 1, "profiles": [profile.to_dict()]}),
        encoding="utf-8",
    )

    repairs = repair_environment(tmp_path, recreate_missing_directories=True)
    assert any(r.id == "repair_recreate_missing_directories" for r in repairs)
    assert missing_data_dir.exists()


def test_doctor_repair_refuses_future_schema(tmp_path):
    layout = paths(tmp_path)
    layout.profiles_file.write_text(
        json.dumps({"schema_version": 999, "profiles": []}),
        encoding="utf-8",
    )
    repairs = repair_environment(tmp_path)
    assert not any(r.id == "repair_metadata_recovery" for r in repairs)
    assert json.loads(layout.profiles_file.read_text(encoding="utf-8"))["schema_version"] == 999


def test_doctor_without_playwright_reports_warning(tmp_path):
    with patch.dict(sys.modules, {"playwright": None, "playwright.sync_api": None}):
        chk = check_playwright_package()
        assert chk.id == "playwright_package"
        assert chk.status in (STATUS_WARNING, STATUS_FAILED)
