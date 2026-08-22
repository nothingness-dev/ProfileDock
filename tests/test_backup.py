import io
import json
import os
from pathlib import Path
import tarfile
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from profiledock.backup import (
    BACKUP_ARCHIVE_SCHEMA_VERSION,
    BackupError,
    FileLockedError,
    ProfileNotStoppedError,
    TargetExistsError,
    create_backup_archive,
)
from profiledock.cli import app, EXIT_SUCCESS, EXIT_USER_ERROR
from profiledock.data_root import DataPaths
from profiledock.models import Profile, MetadataDocument
from profiledock.storage import save_metadata

runner = CliRunner()


def make_paths(root: Path) -> DataPaths:
    layout = DataPaths.from_root(root)
    layout.prepare()
    return layout


def test_backup_single_profile_direct_engine(tmp_path):
    paths = make_paths(tmp_path)
    p_data = paths.profiles_dir / "p1" / "browser-data"
    p_data.mkdir(parents=True)
    (p_data / "cookies.sqlite").write_text("sqlite-cookie-data", encoding="utf-8")
    (p_data / "Preferences").write_text("{}", encoding="utf-8")

    profile = Profile(
        id="p1",
        name="DirectWork",
        created_at="2026-01-01T00:00:00+00:00",
        data_dir=str(p_data),
        engine="direct",
    )
    doc = MetadataDocument(schema_version=1, profiles=[profile])
    save_metadata(doc, paths.profiles_file, paths.profiles_dir)

    out_archive = tmp_path / "backups" / "work_backup.tar.gz"
    report = create_backup_archive([profile], paths, out_archive)

    assert report.total_profiles == 1
    assert report.total_files == 2
    assert report.profiles[0].engine == "direct"
    assert out_archive.exists()

    with tarfile.open(out_archive, "r:gz") as tar:
        manifest_file = tar.extractfile("backup_manifest.json")
        manifest = json.loads(manifest_file.read().decode("utf-8"))
        assert manifest["format_version"] == BACKUP_ARCHIVE_SCHEMA_VERSION
        assert manifest["profiles"][0]["id"] == "p1"
        assert manifest["profiles"][0]["engine"] == "direct"
        assert "cookies.sqlite" in manifest["profiles"][0]["files"]


def test_backup_all_profiles_mixed_engines(tmp_path):
    paths = make_paths(tmp_path)

    p1_data = paths.profiles_dir / "p1" / "browser-data"
    p1_data.mkdir(parents=True)
    (p1_data / "data1.txt").write_text("content1", encoding="utf-8")

    p2_data = paths.profiles_dir / "p2" / "browser-data"
    p2_data.mkdir(parents=True)
    (p2_data / "data2.txt").write_text("content2", encoding="utf-8")

    p1 = Profile("p1", "DirectP", "2026-01-01T00:00:00+00:00", str(p1_data), engine="direct")
    p2 = Profile("p2", "PlaywrightP", "2026-01-02T00:00:00+00:00", str(p2_data), engine="playwright")

    doc = MetadataDocument(schema_version=1, profiles=[p1, p2])
    save_metadata(doc, paths.profiles_file, paths.profiles_dir)

    out_archive = tmp_path / "all_profiles.tar.gz"
    report = create_backup_archive([p1, p2], paths, out_archive)

    assert report.total_profiles == 2
    assert report.total_files == 2

    with tarfile.open(out_archive, "r:gz") as tar:
        manifest = json.loads(tar.extractfile("backup_manifest.json").read().decode("utf-8"))
        assert len(manifest["profiles"]) == 2
        engines = {p["id"]: p["engine"] for p in manifest["profiles"]}
        assert engines == {"p1": "direct", "p2": "playwright"}


def test_backup_excludes_runtime_and_temporary_files(tmp_path):
    paths = make_paths(tmp_path)
    p_data = paths.profiles_dir / "p1" / "browser-data"
    p_data.mkdir(parents=True)
    (p_data / "state.txt").write_text("keep", encoding="utf-8")
    (p_data / "running.json").write_text("exclude", encoding="utf-8")
    (p_data / "controller.error").write_text("exclude", encoding="utf-8")
    (p_data / "file.tmp").write_text("exclude", encoding="utf-8")

    profile = Profile("p1", "Work", "2026-01-01T00:00:00+00:00", str(p_data), engine="direct")

    out_archive = tmp_path / "backup_clean.tar.gz"
    report = create_backup_archive([profile], paths, out_archive)
    assert report.total_files == 1

    with tarfile.open(out_archive, "r:gz") as tar:
        names = tar.getnames()
        assert "profiles/p1/browser-data/state.txt" in names
        assert not any("running.json" in n for n in names)
        assert not any("controller.error" in n for n in names)
        assert not any("file.tmp" in n for n in names)


def test_backup_refuses_when_profile_is_running(tmp_path):
    paths = make_paths(tmp_path)
    p_data = paths.profiles_dir / "p1" / "browser-data"
    p_data.mkdir(parents=True)
    profile = Profile("p1", "RunningWork", "2026-01-01T00:00:00+00:00", str(p_data), engine="direct")

    with patch("profiledock.backup.is_active_for_mutation", return_value=True):
        with pytest.raises(ProfileNotStoppedError, match="must be stopped before creating a backup"):
            create_backup_archive([profile], paths, tmp_path / "out.tar.gz")


def test_backup_target_exists_without_force(tmp_path):
    paths = make_paths(tmp_path)
    p_data = paths.profiles_dir / "p1" / "browser-data"
    p_data.mkdir(parents=True)
    profile = Profile("p1", "Work", "2026-01-01T00:00:00+00:00", str(p_data), engine="direct")

    out_file = tmp_path / "existing.tar.gz"
    out_file.write_text("dummy", encoding="utf-8")

    with pytest.raises(TargetExistsError, match="already exists"):
        create_backup_archive([profile], paths, out_file, force=False)

    report = create_backup_archive([profile], paths, out_file, force=True)
    assert report.total_profiles == 1


def test_backup_locked_file_raises_clear_error(tmp_path):
    paths = make_paths(tmp_path)
    p_data = paths.profiles_dir / "p1" / "browser-data"
    p_data.mkdir(parents=True)
    (p_data / "Cookies").write_text("locked", encoding="utf-8")
    profile = Profile("p1", "LockedWork", "2026-01-01T00:00:00+00:00", str(p_data), engine="direct")

    with patch.object(Path, "open", side_effect=PermissionError("File locked by process")):
        with pytest.raises(FileLockedError, match="cannot read locked file"):
            create_backup_archive([profile], paths, tmp_path / "out.tar.gz")


def test_cli_backup_single_and_json(tmp_path):
    paths = make_paths(tmp_path)
    runner.invoke(app, ["--data-root", str(tmp_path), "create", "Work", "--engine", "direct"])
    out_archive = tmp_path / "work_cli.tar.gz"

    result = runner.invoke(
        app,
        ["--data-root", str(tmp_path), "backup", "Work", "--output", str(out_archive), "--json"],
    )
    assert result.exit_code == EXIT_SUCCESS
    data = json.loads(result.output)
    assert data["output_version"] == 1
    data = data["data"]
    assert data["format_version"] == 1
    assert data["total_profiles"] == 1
    assert data["profiles"][0]["name"] == "Work"
    assert data["profiles"][0]["engine"] == "direct"
    assert out_archive.exists()


def test_cli_backup_all_command(tmp_path):
    paths = make_paths(tmp_path)
    runner.invoke(app, ["--data-root", str(tmp_path), "create", "Work1", "--engine", "direct"])
    runner.invoke(app, ["--data-root", str(tmp_path), "create", "Work2", "--engine", "playwright"])
    out_archive = tmp_path / "all_cli.tar.gz"

    result = runner.invoke(
        app,
        ["--data-root", str(tmp_path), "backup", "--all", "--output", str(out_archive)],
    )
    assert result.exit_code == EXIT_SUCCESS
    assert "Backup created successfully" in result.output
    assert "Work1" in result.output
    assert "Work2" in result.output


def test_backup_rejects_output_inside_browser_data(tmp_path):
    paths = make_paths(tmp_path)
    data_dir = paths.profiles_dir / "p1" / "browser-data"
    data_dir.mkdir(parents=True)
    profile = Profile("p1", "Work", "2026-01-01T00:00:00+00:00", str(data_dir))

    with pytest.raises(BackupError, match="cannot be inside"):
        create_backup_archive([profile], paths, data_dir / "backup.tar.gz")


def test_backup_rejects_linked_profile_content(tmp_path):
    paths = make_paths(tmp_path)
    data_dir = paths.profiles_dir / "p1" / "browser-data"
    data_dir.mkdir(parents=True)
    linked_file = data_dir / "linked"
    linked_file.write_text("content", encoding="utf-8")
    profile = Profile("p1", "Work", "2026-01-01T00:00:00+00:00", str(data_dir))

    with patch("profiledock.backup._is_link", side_effect=lambda path: path == linked_file):
        with pytest.raises(BackupError, match="unsafe file"):
            create_backup_archive([profile], paths, tmp_path / "backup.tar.gz")


def test_backup_rejects_profile_metadata_outside_data_root(tmp_path):
    paths = make_paths(tmp_path / "data")
    outside = tmp_path / "outside" / "browser-data"
    outside.mkdir(parents=True)
    profile = Profile(
        "p1",
        "Escaped",
        "2026-01-01T00:00:00+00:00",
        str(outside),
    )
    with pytest.raises(BackupError, match="unsafe profile path"):
        create_backup_archive([profile], paths, tmp_path / "backup.tar.gz")
