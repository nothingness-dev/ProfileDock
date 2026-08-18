import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from profiledock.cli import app, EXIT_SUCCESS, EXIT_USER_ERROR
from profiledock.data_root import DataPaths
from profiledock.migration import (
    ConflictError,
    MigrationError,
    SourceRunningError,
    migrate_project,
)
from profiledock.models import Profile, MetadataDocument
from profiledock.storage import save_metadata

runner = CliRunner()


def make_paths(root: Path) -> DataPaths:
    layout = DataPaths.from_root(root)
    layout.prepare()
    return layout


def test_migrate_success_legacy_format(tmp_path):
    src_root = tmp_path / "legacy_source"
    src_root.mkdir()
    src_profiles_dir = src_root / "profiles"
    src_p1_dir = src_profiles_dir / "p1"
    src_p1_data = src_p1_dir / "browser-data"
    src_p1_data.mkdir(parents=True)
    (src_p1_data / "cookies.txt").write_text("user_session_token", encoding="utf-8")

    src_meta = src_root / "profiles.json"
    src_meta.write_text(
        json.dumps(
            [
                {
                    "id": "p1",
                    "name": "Legacy Work",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "data_dir": str(src_p1_data),
                }
            ]
        ),
        encoding="utf-8",
    )

    dst_root = tmp_path / "destination"
    dst_paths = make_paths(dst_root)

    report = migrate_project(src_root, dst_paths)
    assert len(report.migrated) == 1
    assert report.migrated[0].id == "p1"
    assert report.migrated[0].name == "Legacy Work"

    dst_p1_data = dst_paths.profiles_dir / "p1" / "browser-data"
    assert dst_p1_data.exists()
    assert (dst_p1_data / "cookies.txt").read_text(encoding="utf-8") == "user_session_token"

    dst_meta = json.loads(dst_paths.profiles_file.read_text(encoding="utf-8"))
    assert len(dst_meta["profiles"]) == 1
    assert dst_meta["profiles"][0]["id"] == "p1"
    assert dst_meta["profiles"][0]["data_dir"] == str(dst_p1_data)


def test_migrate_repeated_is_idempotent(tmp_path):
    src_root = tmp_path / "source"
    src_root.mkdir()
    src_profiles_dir = src_root / "profiles"
    src_p1_data = src_profiles_dir / "p1" / "browser-data"
    src_p1_data.mkdir(parents=True)
    (src_p1_data / "state.txt").write_text("data", encoding="utf-8")

    src_meta = src_root / "profiles.json"
    src_meta.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profiles": [
                    {
                        "id": "p1",
                        "name": "Work",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "data_dir": str(src_p1_data),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    dst_root = tmp_path / "destination"
    dst_paths = make_paths(dst_root)

    rep1 = migrate_project(src_root, dst_paths)
    assert len(rep1.migrated) == 1

    rep2 = migrate_project(src_root, dst_paths)
    assert len(rep2.migrated) == 0
    assert len(rep2.skipped) == 1
    assert rep2.skipped[0].id == "p1"


def test_migrate_id_conflict_raises(tmp_path):
    src_root = tmp_path / "source"
    src_root.mkdir()
    src_p1_data = src_root / "profiles" / "p1" / "browser-data"
    src_p1_data.mkdir(parents=True)

    src_meta = src_root / "profiles.json"
    src_meta.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profiles": [
                    {
                        "id": "p1",
                        "name": "Different Name",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "data_dir": str(src_p1_data),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    dst_root = tmp_path / "destination"
    dst_paths = make_paths(dst_root)
    dst_p1_data = dst_paths.profiles_dir / "p1" / "browser-data"
    dst_p1_data.mkdir(parents=True)

    doc = MetadataDocument(
        schema_version=1,
        profiles=[Profile("p1", "Original Name", "2026-01-01T00:00:00+00:00", str(dst_p1_data))],
    )
    save_metadata(doc, dst_paths.profiles_file, dst_paths.profiles_dir)

    with pytest.raises(ConflictError):
        migrate_project(src_root, dst_paths)


def test_migrate_name_conflict_raises(tmp_path):
    src_root = tmp_path / "source"
    src_root.mkdir()
    src_p1_data = src_root / "profiles" / "p1" / "browser-data"
    src_p1_data.mkdir(parents=True)

    src_meta = src_root / "profiles.json"
    src_meta.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profiles": [
                    {
                        "id": "src1",
                        "name": "SharedName",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "data_dir": str(src_p1_data),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    dst_root = tmp_path / "destination"
    dst_paths = make_paths(dst_root)
    dst_p1_data = dst_paths.profiles_dir / "dst1" / "browser-data"
    dst_p1_data.mkdir(parents=True)

    doc = MetadataDocument(
        schema_version=1,
        profiles=[Profile("dst1", "SharedName", "2026-01-01T00:00:00+00:00", str(dst_p1_data))],
    )
    save_metadata(doc, dst_paths.profiles_file, dst_paths.profiles_dir)

    with pytest.raises(ConflictError):
        migrate_project(src_root, dst_paths)


def test_migrate_refuses_when_source_is_running(tmp_path):
    src_root = tmp_path / "source"
    src_root.mkdir()
    src_p1_data = src_root / "profiles" / "p1" / "browser-data"
    src_p1_data.mkdir(parents=True)

    src_meta = src_root / "profiles.json"
    src_meta.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profiles": [
                    {
                        "id": "p1",
                        "name": "Running",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "data_dir": str(src_p1_data),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    dst_root = tmp_path / "destination"
    dst_paths = make_paths(dst_root)

    with patch("profiledock.migration.is_running", return_value=True):
        with pytest.raises(SourceRunningError):
            migrate_project(src_root, dst_paths)


def test_migrate_corrupted_source_raises(tmp_path):
    src_root = tmp_path / "source"
    src_root.mkdir()
    (src_root / "profiles.json").write_text("corrupted json", encoding="utf-8")

    dst_root = tmp_path / "destination"
    dst_paths = make_paths(dst_root)

    with pytest.raises(MigrationError):
        migrate_project(src_root, dst_paths)


def test_migrate_rollback_on_failure(tmp_path):
    src_root = tmp_path / "source"
    src_root.mkdir()
    src_p1_data = src_root / "profiles" / "p1" / "browser-data"
    src_p1_data.mkdir(parents=True)
    (src_p1_data / "file.txt").write_text("payload", encoding="utf-8")

    src_meta = src_root / "profiles.json"
    src_meta.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profiles": [
                    {
                        "id": "p1",
                        "name": "Work",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "data_dir": str(src_p1_data),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    dst_root = tmp_path / "destination"
    dst_paths = make_paths(dst_root)

    with patch("profiledock.migration._atomic_write", side_effect=OSError("write failed")):
        with pytest.raises(OSError):
            migrate_project(src_root, dst_paths)

    assert not (dst_paths.profiles_dir / "p1").exists()


def test_migrate_remove_source(tmp_path):
    src_root = tmp_path / "source"
    src_root.mkdir()
    src_p1_data = src_root / "profiles" / "p1" / "browser-data"
    src_p1_data.mkdir(parents=True)
    (src_p1_data / "file.txt").write_text("payload", encoding="utf-8")

    src_meta = src_root / "profiles.json"
    src_meta.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profiles": [
                    {
                        "id": "p1",
                        "name": "Work",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "data_dir": str(src_p1_data),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    dst_root = tmp_path / "destination"
    dst_paths = make_paths(dst_root)

    report = migrate_project(src_root, dst_paths, remove_source=True)
    assert report.source_removed is True
    assert not src_meta.exists()
    assert not (src_root / "profiles").exists()


def test_migrate_cli_json_output(tmp_path):
    src_root = tmp_path / "source"
    src_root.mkdir()
    src_p1_data = src_root / "profiles" / "p1" / "browser-data"
    src_p1_data.mkdir(parents=True)

    src_meta = src_root / "profiles.json"
    src_meta.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profiles": [
                    {
                        "id": "p1",
                        "name": "Work",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "data_dir": str(src_p1_data),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    dst_root = tmp_path / "destination"
    result = runner.invoke(app, ["--data-root", str(dst_root), "migrate", "--from-project", str(src_root), "--json"])
    assert result.exit_code == EXIT_SUCCESS
    data = json.loads(result.output)
    assert "migrated" in data
    assert len(data["migrated"]) == 1
    assert data["migrated"][0]["id"] == "p1"
