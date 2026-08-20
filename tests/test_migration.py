import json
import os
from pathlib import Path
import shutil
import time
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
from profiledock.process_manager import close_controller, is_running, start_controller
from profiledock.storage import save_metadata

runner = CliRunner()


def make_paths(root: Path) -> DataPaths:
    layout = DataPaths.from_root(root)
    layout.prepare()
    return layout


def make_source(
    root: Path,
    profile_id: str = "p1",
    name: str = "Work",
    content: str = "payload",
    last_launched_at: str = "2026-01-02T00:00:00+00:00",
    versioned_layout: bool = False,
):
    data_dir = root / "profiles" / profile_id / "browser-data"
    data_dir.mkdir(parents=True)
    (data_dir / "state.txt").write_text(content, encoding="utf-8")
    profile = {
        "id": profile_id,
        "name": name,
        "created_at": "2026-01-01T00:00:00+00:00",
        "data_dir": str(data_dir),
        "last_launched_at": last_launched_at,
    }
    metadata = {"schema_version": 1, "profiles": [profile]}
    metadata_file = root / "metadata" / "profiles.json" if versioned_layout else root / "profiles.json"
    metadata_file.parent.mkdir(parents=True, exist_ok=True)
    metadata_file.write_text(json.dumps(metadata), encoding="utf-8")
    return metadata_file, data_dir


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
                    "last_launched_at": "2026-01-02T00:00:00+00:00",
                    "engine": "playwright",
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
    assert dst_meta["profiles"][0]["created_at"] == "2026-01-01T00:00:00+00:00"
    assert dst_meta["profiles"][0]["last_launched_at"] == "2026-01-02T00:00:00+00:00"
    assert dst_meta["profiles"][0]["engine"] == "playwright"
    assert src_meta.exists()
    assert src_p1_data.exists()


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
    src_p1_data = src_root / "profiles" / "src1" / "browser-data"
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

    with patch("profiledock.migration._source_profile_running", return_value=True):
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
        with pytest.raises(MigrationError, match="migration failed"):
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


def test_migrate_partial_destination_orphaned_dir(tmp_path):
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
    dst_paths = make_paths(dst_root)

    orphaned_dir = dst_paths.profiles_dir / "p1"
    orphaned_dir.mkdir(parents=True)

    with pytest.raises(ConflictError, match="destination directory"):
        migrate_project(src_root, dst_paths)


def test_migrate_multiple_profiles_partial_failure_rollback(tmp_path):
    src_root = tmp_path / "source"
    src_root.mkdir()

    src_p1_data = src_root / "profiles" / "p1" / "browser-data"
    src_p1_data.mkdir(parents=True)
    (src_p1_data / "file1.txt").write_text("data1", encoding="utf-8")

    src_p2_data = src_root / "profiles" / "p2" / "browser-data"
    src_p2_data.mkdir(parents=True)
    (src_p2_data / "file2.txt").write_text("data2", encoding="utf-8")

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
                    },
                    {
                        "id": "p2",
                        "name": "Personal",
                        "created_at": "2026-01-02T00:00:00+00:00",
                        "data_dir": str(src_p2_data),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    dst_root = tmp_path / "destination"
    dst_paths = make_paths(dst_root)

    original_copytree = shutil.copytree
    call_count = [0]

    def mock_copytree(src, dst, **kwargs):
        call_count[0] += 1
        if call_count[0] == 2:
            raise OSError("Copy failed")
        return original_copytree(src, dst, **kwargs)

    with patch("profiledock.migration.shutil.copytree", side_effect=mock_copytree):
        with pytest.raises(MigrationError, match="Copy failed"):
            migrate_project(src_root, dst_paths)

    assert not (dst_paths.profiles_dir / "p1").exists()
    assert not (dst_paths.profiles_dir / "p2").exists()


def test_migrate_source_with_runtime_dir(tmp_path):
    src_root = tmp_path / "source"
    src_root.mkdir()
    src_p1_data = src_root / "profiles" / "p1" / "browser-data"
    src_p1_data.mkdir(parents=True)

    src_runtime = src_root / "runtime"
    src_runtime.mkdir()
    (src_runtime / "p1").mkdir()

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
    assert len(report.migrated) == 1
    assert report.source_removed is True
    assert not src_meta.exists()
    assert not (src_root / "profiles").exists()
    assert not src_runtime.exists()


def test_migrate_rejects_missing_source_data(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    metadata, data_dir = make_source(source)
    shutil.rmtree(data_dir)
    destination = make_paths(tmp_path / "destination")
    with pytest.raises(MigrationError, match="missing or unsafe"):
        migrate_project(source, destination)
    assert metadata.exists()
    assert not destination.profiles_file.exists()


def test_migrate_detects_versioned_layout_and_relative_data_path(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    metadata, _ = make_source(source, versioned_layout=True)
    value = json.loads(metadata.read_text(encoding="utf-8"))
    value["profiles"][0]["data_dir"] = "profiles/p1/browser-data"
    metadata.write_text(json.dumps(value), encoding="utf-8")
    destination = make_paths(tmp_path / "destination")
    report = migrate_project(source, destination)
    assert len(report.migrated) == 1
    assert (destination.profiles_dir / "p1" / "browser-data" / "state.txt").is_file()


def test_migrate_rejects_source_path_outside_profile_directory(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    metadata, _ = make_source(source)
    value = json.loads(metadata.read_text(encoding="utf-8"))
    outside = tmp_path / "outside" / "browser-data"
    outside.mkdir(parents=True)
    value["profiles"][0]["data_dir"] = str(outside)
    metadata.write_text(json.dumps(value), encoding="utf-8")
    destination = make_paths(tmp_path / "destination")
    with pytest.raises(MigrationError, match="profiles/<id>/browser-data"):
        migrate_project(source, destination)


def test_migrate_rejects_invalid_source_timestamp(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    metadata, _ = make_source(source)
    value = json.loads(metadata.read_text(encoding="utf-8"))
    value["profiles"][0]["created_at"] = "not-a-timestamp"
    metadata.write_text(json.dumps(value), encoding="utf-8")
    destination = make_paths(tmp_path / "destination")
    with pytest.raises(MigrationError, match="source metadata corrupted"):
        migrate_project(source, destination)


def test_migrate_rejects_symlink_in_source_data(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _, data_dir = make_source(source)
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    try:
        (data_dir / "linked.txt").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")
    destination = make_paths(tmp_path / "destination")
    with pytest.raises(MigrationError, match="unsafe file"):
        migrate_project(source, destination)


def test_migrate_verifies_file_content_not_only_size(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    make_source(source, content="aaaa")
    destination = make_paths(tmp_path / "destination")
    original_copytree = shutil.copytree

    def corrupt_copy(source_path, target_path, **kwargs):
        result = original_copytree(source_path, target_path, **kwargs)
        (Path(target_path) / "state.txt").write_text("bbbb", encoding="utf-8")
        return result

    with patch("profiledock.migration.shutil.copytree", side_effect=corrupt_copy):
        with pytest.raises(MigrationError, match="verification failed"):
            migrate_project(source, destination)
    assert not (destination.profiles_dir / "p1").exists()


def test_repeated_migration_rejects_changed_destination_data(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    make_source(source)
    destination = make_paths(tmp_path / "destination")
    migrate_project(source, destination)
    destination_file = destination.profiles_dir / "p1" / "browser-data" / "state.txt"
    destination_file.write_text("changed", encoding="utf-8")
    with pytest.raises(ConflictError, match="differs from source"):
        migrate_project(source, destination)


def test_repeated_migration_does_not_rewrite_metadata(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    make_source(source)
    destination = make_paths(tmp_path / "destination")
    migrate_project(source, destination)
    before = destination.profiles_file.read_bytes()
    with patch("profiledock.migration._atomic_write") as atomic_write:
        report = migrate_project(source, destination)
    assert len(report.skipped) == 1
    assert destination.profiles_file.read_bytes() == before
    atomic_write.assert_not_called()


def test_migrate_rejects_stale_temporary_destination(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    make_source(source)
    destination = make_paths(tmp_path / "destination")
    stale = destination.profiles_dir / ".temp_migrating_p1_interrupted"
    stale.mkdir()
    with pytest.raises(ConflictError, match="incomplete destination migration"):
        migrate_project(source, destination)
    assert stale.exists()


def test_migrate_rejects_current_temporary_destination_layout(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    make_source(source)
    destination = make_paths(tmp_path / "destination")
    stale = destination.profiles_dir / ".m-interrupted"
    stale.mkdir()
    with pytest.raises(ConflictError, match="incomplete destination migration"):
        migrate_project(source, destination)
    assert stale.exists()


def test_rollback_does_not_delete_destination_created_by_another_actor(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    make_source(source)
    destination = make_paths(tmp_path / "destination")
    final_profile = destination.profiles_dir / "p1"
    original_replace = Path.replace

    def racing_replace(path, target):
        if Path(target) == final_profile:
            final_profile.mkdir()
            (final_profile / "external.txt").write_text("external", encoding="utf-8")
            raise FileExistsError("destination appeared")
        return original_replace(path, target)

    with patch.object(Path, "replace", racing_replace):
        with pytest.raises(MigrationError, match="destination appeared"):
            migrate_project(source, destination)
    assert (final_profile / "external.txt").read_text(encoding="utf-8") == "external"


def test_migrate_rolls_back_when_source_changes_during_copy(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    metadata, _ = make_source(source)
    destination = make_paths(tmp_path / "destination")
    original_copytree = shutil.copytree

    def mutate_source(source_path, target_path, **kwargs):
        result = original_copytree(source_path, target_path, **kwargs)
        metadata.write_text(metadata.read_text(encoding="utf-8") + " ", encoding="utf-8")
        return result

    with patch("profiledock.migration.shutil.copytree", side_effect=mutate_source):
        with pytest.raises(MigrationError, match="metadata changed"):
            migrate_project(source, destination)
    assert not (destination.profiles_dir / "p1").exists()
    assert not destination.profiles_file.exists()


def test_migrate_json_failure_is_machine_readable(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "profiles.json").write_text("broken", encoding="utf-8")
    (source / "profiles").mkdir()
    destination = tmp_path / "destination"
    result = runner.invoke(
        app,
        ["--data-root", str(destination), "migrate", "--from-project", str(source), "--json"],
    )
    assert result.exit_code == EXIT_USER_ERROR
    report = json.loads(result.output)
    assert report["migrated"] == []
    assert report["failed"][0]["status"] == "failed"
    assert "corrupted" in report["failed"][0]["message"]


def test_remove_source_refuses_untracked_profile_data(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    metadata, data_dir = make_source(source)
    (source / "profiles" / "orphan" / "browser-data").mkdir(parents=True)
    destination = make_paths(tmp_path / "destination")
    with pytest.raises(MigrationError, match="untracked profile"):
        migrate_project(source, destination, remove_source=True)
    assert metadata.exists()
    assert data_dir.exists()
    assert not destination.profiles_file.exists()


def test_remove_source_failure_is_not_reported_as_success(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    metadata, data_dir = make_source(source)
    destination = make_paths(tmp_path / "destination")
    with patch("profiledock.migration._remove_source", side_effect=OSError("denied")):
        with pytest.raises(MigrationError, match="failed to remove"):
            migrate_project(source, destination, remove_source=True)
    assert metadata.exists()
    assert data_dir.exists()
    assert (destination.profiles_dir / "p1" / "browser-data").exists()


def test_remove_source_requires_cli_confirmation(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    metadata, data_dir = make_source(source)
    destination = tmp_path / "destination"
    result = runner.invoke(
        app,
        [
            "--data-root",
            str(destination),
            "migrate",
            "--from-project",
            str(source),
            "--remove-source",
        ],
        input="n\n",
    )
    assert result.exit_code != EXIT_SUCCESS
    assert metadata.exists()
    assert data_dir.exists()


def test_json_remove_source_requires_yes_without_prompt_text(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    metadata, data_dir = make_source(source)
    destination = tmp_path / "destination"
    result = runner.invoke(
        app,
        [
            "--data-root",
            str(destination),
            "migrate",
            "--from-project",
            str(source),
            "--remove-source",
            "--json",
        ],
    )
    assert result.exit_code == EXIT_USER_ERROR
    report = json.loads(result.output)
    assert "requires --yes" in report["failed"][0]["message"]
    assert metadata.exists()
    assert data_dir.exists()


def test_migrate_human_report_lists_migrated_profiles(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    make_source(source)
    destination = tmp_path / "destination"
    result = runner.invoke(
        app,
        ["--data-root", str(destination), "migrate", "--from-project", str(source)],
    )
    assert result.exit_code == EXIT_SUCCESS
    assert "Migration completed" in result.output
    assert "Migrated (1)" in result.output
    assert "Work (p1)" in result.output


def test_migrate_detects_live_runtime_state_without_modifying_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _, _ = make_source(source, versioned_layout=True)
    state = source / "runtime" / "p1" / "running.json"
    state.parent.mkdir(parents=True)
    original = json.dumps({"controller_pid": os.getpid()})
    state.write_text(original, encoding="utf-8")
    destination = make_paths(tmp_path / "destination")
    with pytest.raises(SourceRunningError):
        migrate_project(source, destination)
    assert state.read_text(encoding="utf-8") == original


def test_migrate_rejects_overlapping_roots(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    make_source(source)
    destination = make_paths(source / "destination")
    with pytest.raises(MigrationError, match="cannot overlap"):
        migrate_project(source, destination)


def test_migrate_rejects_source_root_symlink(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    make_source(source)
    linked_source = tmp_path / "linked-source"
    try:
        linked_source.symlink_to(source, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")
    destination = make_paths(tmp_path / "destination")
    with pytest.raises(MigrationError, match="real directory"):
        migrate_project(linked_source, destination)


def test_idempotent_profile_still_detects_duplicate_destination_name(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    make_source(source)
    destination = make_paths(tmp_path / "destination")
    migrate_project(source, destination)
    second_data = destination.profiles_dir / "p2" / "browser-data"
    second_data.mkdir(parents=True)
    document = json.loads(destination.profiles_file.read_text(encoding="utf-8"))
    document["profiles"].append(
        {
            "id": "p2",
            "name": "Work",
            "created_at": "2026-01-03T00:00:00+00:00",
            "data_dir": str(second_data),
            "last_launched_at": None,
        }
    )
    destination.profiles_file.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ConflictError, match="also used"):
        migrate_project(source, destination)


@pytest.mark.browser
def test_migrated_profile_launches_with_persistent_browser_state(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    from profiledock.process_manager import _launch_context

    source = tmp_path / "source"
    source.mkdir()
    _, source_data = make_source(source)
    try:
        with playwright.sync_playwright() as instance:
            source_context, _ = _launch_context(instance, str(source_data), True)
            source_context.add_cookies(
                [
                    {
                        "name": "migrated-session",
                        "value": "preserved",
                        "url": "https://example.com",
                        "expires": time.time() + 3600,
                    }
                ]
            )
            source_context.close()
    except playwright.Error as exc:
        pytest.skip(f"no supported browser found: {exc}")

    destination = make_paths(tmp_path / "destination")
    report = migrate_project(source, destination)
    assert len(report.migrated) == 1
    destination_data = destination.profiles_dir / "p1" / "browser-data"
    state = start_controller(
        str(destination_data),
        2,
        headless=True,
        runtime_dir=destination.runtime_dir / "p1",
    )
    try:
        assert state["page_count"] == 2
    finally:
        if is_running(str(destination_data), destination.runtime_dir / "p1"):
            close_controller(
                str(destination_data),
                timeout=10,
                runtime_dir=destination.runtime_dir / "p1",
            )

    try:
        with playwright.sync_playwright() as instance:
            destination_context, _ = _launch_context(instance, str(destination_data), True)
            cookies = destination_context.cookies("https://example.com")
            destination_context.close()
    except playwright.Error as exc:
        pytest.skip(f"no supported browser found: {exc}")
    assert any(
        cookie["name"] == "migrated-session" and cookie["value"] == "preserved"
        for cookie in cookies
    )
