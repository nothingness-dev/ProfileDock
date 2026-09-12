import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
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
    check_playwright_chromium,
    check_playwright_package,
    check_profile_directories,
    check_python_version,
    check_stale_running_state,
    repair_environment,
    run_diagnostics,
)
from profiledock.models import METADATA_SCHEMA_VERSION, LaunchConfig, MetadataDocument, Profile
from profiledock.storage import load_metadata, metadata_lock, save_metadata

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


def test_check_metadata_schema_elevated_acl_hint(tmp_path, monkeypatch):
    layout = paths(tmp_path)
    profiles_file = layout.profiles_file
    profiles_file.write_text("{}", encoding="utf-8")

    real_read_text = Path.read_text

    def denying_read(self, *args, **kwargs):
        if self == profiles_file:
            raise PermissionError(13, "Permission denied")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denying_read)
    res = check_metadata_schema(tmp_path)
    assert res.status == STATUS_FAILED
    assert "cannot read it" in res.summary
    assert "icacls" in (res.action or "")
    assert "/reset /T /C" in (res.action or "")


def test_check_metadata_schema_valid(tmp_path):
    layout = paths(tmp_path)
    profiles_file = layout.profiles_file
    profiles_dir = layout.profiles_dir
    data_dir = profiles_dir / "p1" / "browser-data"
    doc = MetadataDocument(
        schema_version=METADATA_SCHEMA_VERSION,
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
        schema_version=METADATA_SCHEMA_VERSION,
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
        schema_version=METADATA_SCHEMA_VERSION,
        profiles=[Profile("p1", "Name", "2026-01-01T00:00:00+00:00", str(data_dir))],
    )
    save_metadata(doc, profiles_file, profiles_dir)

    exist_chk, path_chk = check_profile_directories(tmp_path)
    assert exist_chk.status == STATUS_WARNING
    assert "Missing data directories" in exist_chk.summary


def test_check_playwright_chromium_action_guidance():
    with patch.dict(sys.modules, {"playwright": None, "playwright.sync_api": None}):
        chk = check_playwright_chromium()
    assert chk.id == "playwright_chromium"
    assert chk.status == STATUS_WARNING
    assert chk.action == "Run 'playwright install chromium'."


def _store_profile_with_data_dir(layout, data_dir: str) -> None:
    doc = MetadataDocument(
        schema_version=METADATA_SCHEMA_VERSION,
        profiles=[Profile("p1", "Name", "2026-01-01T00:00:00+00:00", data_dir)],
    )
    save_metadata(doc, layout.profiles_file, layout.profiles_dir)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows treats path casing as equivalent")
def test_check_profile_directories_tolerates_windows_casing(tmp_path):
    layout = paths(tmp_path)
    canonical = layout.profiles_dir / "p1" / "browser-data"
    altered = str(canonical)[0].swapcase() + str(canonical)[1:]
    assert altered != str(canonical)
    _store_profile_with_data_dir(layout, altered)

    exist_chk, path_chk = check_profile_directories(tmp_path)

    assert exist_chk.status == STATUS_WARNING
    assert path_chk.status == STATUS_OK


@pytest.mark.skipif(sys.platform != "linux", reason="POSIX comparison stays case-sensitive")
def test_check_profile_directories_linux_flags_casing_mismatch(tmp_path):
    layout = paths(tmp_path)
    canonical = layout.profiles_dir / "p1" / "browser-data"
    altered = str(canonical).replace("/p1/", "/P1/")
    assert altered != str(canonical)
    _store_profile_with_data_dir(layout, altered)

    _, path_chk = check_profile_directories(tmp_path)

    assert path_chk.status == STATUS_FAILED


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


    assert not first_data.exists()
    assert not second_data.exists()
    assert any(r.status == STATUS_FAILED for r in repairs)


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


def test_doctor_playwright_hint_points_at_playwright_extra():
    with patch.dict(sys.modules, {"playwright": None, "playwright.sync_api": None}):
        chk = check_playwright_package()
        assert chk.action is not None
        assert ".[playwright]" in chk.action
        assert "requirements.txt" not in chk.action


def test_check_disk_space_reports_low_space(tmp_path):

    from profiledock.doctor import check_disk_space

    chk = check_disk_space(tmp_path)
    assert chk.id == "disk_space"


    assert chk.status in (STATUS_OK, STATUS_WARNING)


def test_check_disk_space_flags_critical_free_space(tmp_path):
    from profiledock.doctor import check_disk_space

    with patch("profiledock.doctor.shutil.disk_usage") as usage:
        usage.return_value = type(
            "Usage", (), {"total": 100 * 2**30, "used": 99 * 2**30, "free": 50 * 2**20}
        )()
        chk = check_disk_space(tmp_path)
    assert chk.status == STATUS_FAILED
    assert "MB" in chk.summary or "MiB" in chk.summary
    assert chk.action is not None

    with patch("profiledock.doctor.shutil.disk_usage") as usage:
        usage.return_value = type(
            "Usage", (), {"total": 100 * 2**30, "used": 98 * 2**30, "free": 500 * 2**20}
        )()
        warn = check_disk_space(tmp_path)
    assert warn.status == STATUS_WARNING

    with patch("profiledock.doctor.shutil.disk_usage") as usage:
        usage.return_value = type(
            "Usage", (), {"total": 100 * 2**30, "used": 10 * 2**30, "free": 90 * 2**30}
        )()
        ok = check_disk_space(tmp_path)
    assert ok.status == STATUS_OK


def test_check_disk_space_survives_disk_usage_failure(tmp_path):
    from profiledock.doctor import check_disk_space

    with patch("profiledock.doctor.shutil.disk_usage", side_effect=OSError("no stat")):
        chk = check_disk_space(tmp_path)
    assert chk.status == STATUS_OK
    assert chk.summary


def test_run_diagnostics_includes_new_checks(tmp_path):

    layout = paths(tmp_path)
    checks = run_diagnostics(layout.root)
    ids = [c.id for c in checks]
    assert "disk_space" in ids
    assert "metadata_lock_state" in ids


def test_check_metadata_lock_state_reports_stuck_lock(tmp_path):

    import threading

    from profiledock.doctor import check_metadata_lock_state

    layout = paths(tmp_path)


    release = threading.Event()
    acquired = threading.Event()

    def hold_lock():
        with metadata_lock(layout.profiles_file, timeout=5.0):
            acquired.set()
            release.wait(timeout=5.0)

    holder = threading.Thread(target=hold_lock, daemon=True)
    holder.start()
    assert acquired.wait(timeout=5.0)

    chk = check_metadata_lock_state(layout.profiles_file, probe_timeout=0.3)
    release.set()
    holder.join(timeout=5.0)

    assert chk.id == "metadata_lock_state"
    assert chk.status == STATUS_FAILED
    assert "lock" in chk.summary.lower()


def test_check_metadata_lock_state_ok_when_free(tmp_path):
    from profiledock.doctor import check_metadata_lock_state

    layout = paths(tmp_path)
    chk = check_metadata_lock_state(layout.profiles_file, probe_timeout=1.0)
    assert chk.status == STATUS_OK


def test_doctor_strict_flag_fails_on_warnings():

    with patch("profiledock.cli.run_diagnostics") as mock_diag:
        mock_diag.return_value = [
            DiagnosticCheck(
                "orphan_profile_directories", STATUS_WARNING, "Found orphan dir", action="Review"
            ),
        ]
        result = runner.invoke(app, ["doctor", "--strict"])
    assert result.exit_code == EXIT_USER_ERROR
    assert "WARNING" in result.output


    with patch("profiledock.cli.run_diagnostics") as mock_diag:
        mock_diag.return_value = [
            DiagnosticCheck("orphan_profile_directories", STATUS_WARNING, "warn"),
        ]
        result = runner.invoke(app, ["doctor", "--strict", "--json"])
    assert result.exit_code == EXIT_USER_ERROR
    data = json.loads(result.output)["data"]
    assert data["healthy"] is True
    assert data["strict_healthy"] is False


def test_doctor_strict_json_ok_when_healthy():
    with patch("profiledock.cli.run_diagnostics") as mock_diag:
        mock_diag.return_value = [DiagnosticCheck("python_version", STATUS_OK, "ok")]
        result = runner.invoke(app, ["doctor", "--strict", "--json"])
    assert result.exit_code == EXIT_SUCCESS
    data = json.loads(result.output)["data"]
    assert data["strict_healthy"] is True


def test_proxy_timezone_consistency_flags_unset_timezone(tmp_path):

    from profiledock.doctor import check_proxy_timezone_consistency

    layout = paths(tmp_path)
    data_dir = layout.profiles_dir / "p1" / "browser-data"
    data_dir.mkdir(parents=True)
    profile = Profile(
        "p1",
        "ProxyNoTz",
        "2026-01-01T00:00:00+00:00",
        str(data_dir),
        launch_config=LaunchConfig(proxy="socks5://127.0.0.1:9050", timezone=None),
    )
    layout.profiles_file.write_text(
        json.dumps({"schema_version": 1, "profiles": [profile.to_dict()]}),
        encoding="utf-8",
    )

    chk = check_proxy_timezone_consistency(tmp_path)
    assert chk.id == "proxy_timezone_consistency"
    assert chk.status == STATUS_WARNING
    assert "ProxyNoTz" in chk.summary
    assert chk.action is not None


def test_proxy_timezone_consistency_ok_when_timezone_matches_or_absent(tmp_path):
    from profiledock.doctor import check_proxy_timezone_consistency

    layout = paths(tmp_path)
    data_dir = layout.profiles_dir / "p1" / "browser-data"
    data_dir.mkdir(parents=True)

    with_proxy_tz = Profile(
        "p1",
        "ProxyWithTz",
        "2026-01-01T00:00:00+00:00",
        str(data_dir),
        launch_config=LaunchConfig(proxy="socks5://127.0.0.1:9050", timezone="America/Los_Angeles"),
    )

    without_proxy = Profile(
        "p2",
        "NoProxy",
        "2026-01-01T00:00:00+00:00",
        str(layout.profiles_dir / "p2" / "browser-data"),
    )
    layout.profiles_file.write_text(
        json.dumps({"schema_version": 1, "profiles": [with_proxy_tz.to_dict(), without_proxy.to_dict()]}),
        encoding="utf-8",
    )

    chk = check_proxy_timezone_consistency(tmp_path)
    assert chk.status == STATUS_OK


def test_proxy_timezone_consistency_flags_invalid_timezone(tmp_path):

    from profiledock.doctor import check_proxy_timezone_consistency

    layout = paths(tmp_path)
    data_dir = layout.profiles_dir / "p1" / "browser-data"
    data_dir.mkdir(parents=True)
    profile = Profile(
        "p1",
        "BadTz",
        "2026-01-01T00:00:00+00:00",
        str(data_dir),
        launch_config=LaunchConfig(proxy="socks5://127.0.0.1:9050", timezone="Mars/Olympus_Mons"),
    )
    layout.profiles_file.write_text(
        json.dumps({"schema_version": 1, "profiles": [profile.to_dict()]}),
        encoding="utf-8",
    )

    chk = check_proxy_timezone_consistency(tmp_path)
    assert chk.status == STATUS_WARNING
    assert "BadTz" in chk.summary


def test_recovery_preserves_corrupt_primary_for_inspection(tmp_path):

    layout = paths(tmp_path)
    corrupt_content = "corrupt json {"
    layout.profiles_file.write_text(corrupt_content, encoding="utf-8")
    layout.backup_file.write_text(
        json.dumps({"schema_version": 1, "profiles": []}),
        encoding="utf-8",
    )

    repairs = repair_environment(tmp_path)
    assert any(r.id == "repair_metadata_recovery" for r in repairs)


    preserved = list(layout.metadata_dir.glob("*profiles.json*")) + list(layout.root.glob("*profiles.json*"))
    preserved_contents = {p.name: p.read_text(encoding="utf-8") for p in preserved}
    assert any(content == corrupt_content for content in preserved_contents.values()), (
        f"corrupt primary was destroyed without preservation; found: {sorted(preserved_contents)}"
    )


def test_orphan_check_ignores_transient_operation_directories(tmp_path):

    layout = paths(tmp_path)
    layout.profiles_file.write_text(
        json.dumps({"schema_version": 1, "profiles": []}),
        encoding="utf-8",
    )
    (layout.profiles_dir / ".deleting-abc123-deadbeef").mkdir()
    (layout.profiles_dir / ".quarantine_abc123").mkdir()
    (layout.profiles_dir / ".temp_restore_abc123").mkdir()

    res = check_orphan_directories(tmp_path)
    assert res.status == STATUS_OK, res.summary
    assert "deleting" not in res.summary


def test_repair_temp_cleanup_retries_through_transient_file_locks(tmp_path, monkeypatch):

    import shutil as shutil_module

    from profiledock import doctor as doctor_module

    layout = paths(tmp_path)
    stale_temp = layout.profiles_dir / ".deleting-abc123-deadbeef"
    stale_temp.mkdir(parents=True)

    real_rmtree = shutil_module.rmtree
    attempts = {"count": 0}

    def flaky_rmtree(path, *args, **kwargs):
        if str(path) == str(stale_temp) and attempts["count"] < 1:
            attempts["count"] += 1
            raise PermissionError(5, "Access is denied (transient AV scan)")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(doctor_module.shutil, "rmtree", flaky_rmtree)

    repairs = repair_environment(tmp_path)
    assert any(r.id == "repair_incomplete_operations" for r in repairs), (
        "cleanup was skipped by a transient lock instead of retried"
    )
    assert not stale_temp.exists()


def test_repair_failure_is_reported_not_swallowed(tmp_path):

    layout = paths(tmp_path)
    missing_data_dir = layout.profiles_dir / "p1" / "browser-data"
    layout.profiles_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profiles": [
                    {
                        "id": "p1",
                        "name": "Name",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "data_dir": str(missing_data_dir),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with patch(
        "profiledock.doctor.is_active_for_mutation", return_value=True
    ):
        repairs = repair_environment(tmp_path, recreate_missing_directories=True)
    assert not missing_data_dir.exists()

    assert repairs, "repair failure was swallowed silently"
    assert any(r.status != STATUS_OK for r in repairs), (
        f"failure was reported as a successful repair: {[r.summary for r in repairs]}"
    )
    assert any("p1" in r.summary or "recreate" in r.summary.lower() for r in repairs)


def test_repair_warning_renders_as_warning_not_repaired(tmp_path):

    from profiledock.cli import app as _app  # noqa: F401 — imported for runner context

    with (
        patch("profiledock.cli.repair_environment") as mock_repair,
        patch("profiledock.cli.run_diagnostics") as mock_diag,
    ):
        mock_repair.return_value = [
            DiagnosticCheck(
                "repair_incomplete_operations",
                STATUS_WARNING,
                "Skipped temporary-directory cleanup because another metadata operation is active.",
            ),
            DiagnosticCheck(
                "repair_stale_running_state", STATUS_OK, "Cleaned up 1 stale running.json file(s)."
            ),
        ]
        mock_diag.return_value = [DiagnosticCheck("python_version", STATUS_OK, "ok")]
        result = runner.invoke(app, ["doctor", "--repair"])
    assert result.exit_code == EXIT_SUCCESS
    assert "[warning] Skipped temporary-directory cleanup" in result.output
    assert "[repaired] Skipped" not in result.output
    assert "[repaired] Cleaned up 1 stale" in result.output
