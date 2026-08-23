import io
import json
from pathlib import Path
import tarfile
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from profiledock.backup import create_backup_archive
from profiledock.cli import app, EXIT_SUCCESS
from profiledock.data_root import DataPaths
from profiledock.models import LaunchConfig, Profile, MetadataDocument
from profiledock.restore import (
    DecompressionSecurityError,
    InvalidArchiveError,
    RestoreConflictError,
    restore_backup_archive,
)
from profiledock.storage import load_metadata, save_metadata

runner = CliRunner(mix_stderr=False)


def complete_manifest(value):
    profiles = value.get("profiles", [])
    for profile in profiles:
        profile.setdefault("last_launched_at", None)
        profile.setdefault("engine", None)
        profile.setdefault("launch_config", None)
        profile.setdefault("file_count", len(profile.get("files", {})))
        profile.setdefault("total_bytes", sum(item.get("size", 0) for item in profile.get("files", {}).values()))
    value.setdefault("profiledock_version", "test")
    value.setdefault("created_at", "2026-01-01T00:00:00+00:00")
    value.setdefault("total_profiles", len(profiles))
    value.setdefault("total_files", sum(profile["file_count"] for profile in profiles))
    value.setdefault("total_bytes", sum(profile["total_bytes"] for profile in profiles))
    return value


def make_paths(root: Path) -> DataPaths:
    layout = DataPaths.from_root(root)
    layout.prepare()
    return layout


def test_restore_single_and_multiple_profiles(tmp_path):
    src_paths = make_paths(tmp_path / "src")
    p1_data = src_paths.profiles_dir / "p1" / "browser-data"
    p1_data.mkdir(parents=True)
    (p1_data / "Cookies").write_text("cookie_payload", encoding="utf-8")

    p2_data = src_paths.profiles_dir / "p2" / "browser-data"
    p2_data.mkdir(parents=True)
    (p2_data / "History").write_text("history_payload", encoding="utf-8")

    p1 = Profile(
        "p1",
        "DirectWork",
        "2026-01-01T00:00:00+00:00",
        str(p1_data),
        engine="direct",
        launch_config=LaunchConfig(
            default_tabs=2,
            start_urls=["https://example.com"],
            engine="direct",
            browser="chrome",
            window_width=1280,
            window_height=720,
        ),
    )
    p2 = Profile("p2", "PlaywrightWork", "2026-01-02T00:00:00+00:00", str(p2_data), engine="playwright")

    archive_file = tmp_path / "backup.tar.gz"
    create_backup_archive([p1, p2], src_paths, archive_file)

    dst_paths = make_paths(tmp_path / "dst")
    report = restore_backup_archive(archive_file, dst_paths)

    assert report.total_restored == 2
    assert (dst_paths.profiles_dir / "p1" / "browser-data" / "Cookies").read_text(encoding="utf-8") == "cookie_payload"
    assert (dst_paths.profiles_dir / "p2" / "browser-data" / "History").read_text(encoding="utf-8") == "history_payload"

    loaded_doc = load_metadata(dst_paths.profiles_file)
    assert len(loaded_doc.profiles) == 2
    assert loaded_doc.profiles[0].engine == "direct"
    assert loaded_doc.profiles[1].engine == "playwright"
    assert loaded_doc.profiles[0].launch_config is not None
    assert loaded_doc.profiles[0].launch_config.default_tabs == 2
    assert loaded_doc.profiles[0].launch_config.start_urls == ["https://example.com"]


def test_restore_security_traversal_rejected(tmp_path):
    dst_paths = make_paths(tmp_path / "dst")
    malicious_archive = tmp_path / "traversal.tar.gz"

    with tarfile.open(malicious_archive, "w:gz") as tar:
        manifest = {
            "format_version": 1,
            "profiles": [
                {
                    "id": "p1",
                    "name": "Malicious",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "files": {"evil.txt": {"size": 4, "sha256": "0" * 64}},
                }
            ],
        }
        manifest_bytes = json.dumps(complete_manifest(manifest)).encode("utf-8")
        tarinfo = tarfile.TarInfo("backup_manifest.json")
        tarinfo.size = len(manifest_bytes)
        tar.addfile(tarinfo, io.BytesIO(manifest_bytes))

        evil_info = tarfile.TarInfo("profiles/p1/browser-data/../../etc/passwd")
        evil_info.size = 4
        tar.addfile(evil_info, io.BytesIO(b"evil"))

    with pytest.raises(DecompressionSecurityError, match="parent traversal"):
        restore_backup_archive(malicious_archive, dst_paths)


def test_restore_rejects_cross_platform_backslash_traversal(tmp_path, malicious_archive):
    with pytest.raises(DecompressionSecurityError, match="unsafe path"):
        restore_backup_archive(malicious_archive, make_paths(tmp_path / "destination"))


def test_restore_security_absolute_path_rejected(tmp_path):
    dst_paths = make_paths(tmp_path / "dst")
    malicious_archive = tmp_path / "abs.tar.gz"

    with tarfile.open(malicious_archive, "w:gz") as tar:
        manifest = {
            "format_version": 1,
            "profiles": [
                {
                    "id": "p1",
                    "name": "Malicious",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "files": {},
                }
            ],
        }
        manifest_bytes = json.dumps(complete_manifest(manifest)).encode("utf-8")
        tarinfo = tarfile.TarInfo("backup_manifest.json")
        tarinfo.size = len(manifest_bytes)
        tar.addfile(tarinfo, io.BytesIO(manifest_bytes))

        evil_info = tarfile.TarInfo("/etc/shadow")
        evil_info.size = 4
        tar.addfile(evil_info, io.BytesIO(b"evil"))

    with pytest.raises(DecompressionSecurityError, match="absolute path"):
        restore_backup_archive(malicious_archive, dst_paths)


def test_restore_security_link_rejected(tmp_path):
    dst_paths = make_paths(tmp_path / "dst")
    malicious_archive = tmp_path / "symlink.tar.gz"

    with tarfile.open(malicious_archive, "w:gz") as tar:
        manifest = {
            "format_version": 1,
            "profiles": [
                {
                    "id": "p1",
                    "name": "Malicious",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "files": {},
                }
            ],
        }
        manifest_bytes = json.dumps(complete_manifest(manifest)).encode("utf-8")
        tarinfo = tarfile.TarInfo("backup_manifest.json")
        tarinfo.size = len(manifest_bytes)
        tar.addfile(tarinfo, io.BytesIO(manifest_bytes))

        link_info = tarfile.TarInfo("profiles/p1/browser-data/symlink")
        link_info.type = tarfile.SYMTYPE
        link_info.linkname = "/etc/passwd"
        tar.addfile(link_info)

    with pytest.raises(DecompressionSecurityError, match="unsafe link"):
        restore_backup_archive(malicious_archive, dst_paths)


def test_restore_rejects_excessive_archive_member_count(tmp_path):
    archive_file = tmp_path / "too-many-members.tar.gz"
    with tarfile.open(archive_file, "w:gz") as tar:
        manifest = json.dumps(complete_manifest({"format_version": 1, "profiles": []})).encode("utf-8")
        manifest_member = tarfile.TarInfo("backup_manifest.json")
        manifest_member.size = len(manifest)
        tar.addfile(manifest_member, io.BytesIO(manifest))
        tar.addfile(tarfile.TarInfo("extra-directory"))
    with patch("profiledock.restore.MAX_ARCHIVE_MEMBERS", 1):
        with pytest.raises(DecompressionSecurityError, match="more members"):
            restore_backup_archive(archive_file, make_paths(tmp_path / "destination"))


def test_restore_security_checksum_mismatch_fails(tmp_path):
    src_paths = make_paths(tmp_path / "src")
    p_data = src_paths.profiles_dir / "p1" / "browser-data"
    p_data.mkdir(parents=True)
    (p_data / "data.txt").write_text("content", encoding="utf-8")
    p1 = Profile("p1", "DirectWork", "2026-01-01T00:00:00+00:00", str(p_data), engine="direct")

    archive_file = tmp_path / "backup_tampered.tar.gz"

    with tarfile.open(archive_file, "w:gz") as tar:
        manifest = {
            "format_version": 1,
            "profiles": [
                {
                    "id": "p1",
                    "name": "DirectWork",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "files": {"data.txt": {"size": 7, "sha256": "0" * 64}},
                }
            ],
        }
        manifest_bytes = json.dumps(complete_manifest(manifest)).encode("utf-8")
        tarinfo = tarfile.TarInfo("backup_manifest.json")
        tarinfo.size = len(manifest_bytes)
        tar.addfile(tarinfo, io.BytesIO(manifest_bytes))

        file_bytes = b"content"
        file_info = tarfile.TarInfo("profiles/p1/browser-data/data.txt")
        file_info.size = len(file_bytes)
        tar.addfile(file_info, io.BytesIO(file_bytes))

    dst_paths = make_paths(tmp_path / "dst")
    with pytest.raises(InvalidArchiveError, match="checksum verification failed"):
        restore_backup_archive(archive_file, dst_paths)


def test_restore_conflict_handling_and_force(tmp_path):
    src_paths = make_paths(tmp_path / "src")
    p1_data = src_paths.profiles_dir / "p1" / "browser-data"
    p1_data.mkdir(parents=True)
    (p1_data / "file.txt").write_text("archive_data", encoding="utf-8")
    p1 = Profile("p1", "SharedName", "2026-01-01T00:00:00+00:00", str(p1_data), engine="direct")

    archive_file = tmp_path / "backup.tar.gz"
    create_backup_archive([p1], src_paths, archive_file)

    dst_paths = make_paths(tmp_path / "dst")
    dst_p1_data = dst_paths.profiles_dir / "p1" / "browser-data"
    dst_p1_data.mkdir(parents=True)
    (dst_p1_data / "file.txt").write_text("existing_data", encoding="utf-8")

    dst_p = Profile("p1", "DifferentName", "2026-01-01T00:00:00+00:00", str(dst_p1_data), engine="playwright")
    save_metadata(MetadataDocument(schema_version=1, profiles=[dst_p]), dst_paths.profiles_file, dst_paths.profiles_dir)

    with pytest.raises(RestoreConflictError, match="conflict: profile ID 'p1' already exists"):
        restore_backup_archive(archive_file, dst_paths, overwrite=False)

    report = restore_backup_archive(archive_file, dst_paths, overwrite=True)
    assert report.total_restored == 1
    assert (dst_p1_data / "file.txt").read_text(encoding="utf-8") == "archive_data"
    loaded = load_metadata(dst_paths.profiles_file)
    assert loaded.profiles[0].name == "SharedName"
    assert loaded.profiles[0].engine == "direct"


def test_restore_rejects_unsafe_manifest_profile_id(tmp_path):
    archive_file = tmp_path / "unsafe-id.tar.gz"
    manifest = {
        "format_version": 1,
        "profiles": [
            {
                "id": "../../escape",
                "name": "Unsafe",
                "created_at": "2026-01-01T00:00:00+00:00",
                "files": {},
            }
        ],
    }
    with tarfile.open(archive_file, "w:gz") as tar:
        manifest_bytes = json.dumps(complete_manifest(manifest)).encode("utf-8")
        member = tarfile.TarInfo("backup_manifest.json")
        member.size = len(manifest_bytes)
        tar.addfile(member, io.BytesIO(manifest_bytes))

    destination = make_paths(tmp_path / "destination")
    with pytest.raises(InvalidArchiveError, match="unsafe characters"):
        restore_backup_archive(archive_file, destination)
    assert not (tmp_path / "escape").exists()


def test_restore_rejects_unsafe_manifest_file_path(tmp_path):
    archive_file = tmp_path / "unsafe-file.tar.gz"
    manifest = {
        "format_version": 1,
        "profiles": [
            {
                "id": "safe-id",
                "name": "Unsafe File",
                "created_at": "2026-01-01T00:00:00+00:00",
                "files": {"../../escape.txt": {"size": 1, "sha256": "0" * 64}},
            }
        ],
    }
    with tarfile.open(archive_file, "w:gz") as tar:
        manifest_bytes = json.dumps(complete_manifest(manifest)).encode("utf-8")
        member = tarfile.TarInfo("backup_manifest.json")
        member.size = len(manifest_bytes)
        tar.addfile(member, io.BytesIO(manifest_bytes))

    with pytest.raises(DecompressionSecurityError, match="parent traversal"):
        restore_backup_archive(archive_file, make_paths(tmp_path / "destination"))


def test_restore_is_idempotent_only_for_identical_content(tmp_path):
    source = make_paths(tmp_path / "source")
    data_dir = source.profiles_dir / "p1" / "browser-data"
    data_dir.mkdir(parents=True)
    (data_dir / "state.txt").write_text("original", encoding="utf-8")
    profile = Profile("p1", "Work", "2026-01-01T00:00:00+00:00", str(data_dir))
    archive_file = tmp_path / "idempotent.tar.gz"
    create_backup_archive([profile], source, archive_file)

    destination = make_paths(tmp_path / "destination")
    restore_backup_archive(archive_file, destination)
    repeated = restore_backup_archive(archive_file, destination)
    assert repeated.total_restored == 0
    assert len(repeated.skipped) == 1

    restored_file = destination.profiles_dir / "p1" / "browser-data" / "state.txt"
    restored_file.write_text("changed", encoding="utf-8")
    with pytest.raises(RestoreConflictError, match="different attributes"):
        restore_backup_archive(archive_file, destination)


def test_force_restore_refuses_running_profile(tmp_path):
    source = make_paths(tmp_path / "source")
    data_dir = source.profiles_dir / "p1" / "browser-data"
    data_dir.mkdir(parents=True)
    profile = Profile("p1", "Work", "2026-01-01T00:00:00+00:00", str(data_dir))
    archive_file = tmp_path / "running.tar.gz"
    create_backup_archive([profile], source, archive_file)

    destination = make_paths(tmp_path / "destination")
    destination_data = destination.profiles_dir / "p1" / "browser-data"
    destination_data.mkdir(parents=True)
    save_metadata(
        MetadataDocument(
            schema_version=1,
            profiles=[Profile("p1", "Work", profile.created_at, str(destination_data))],
        ),
        destination.profiles_file,
        destination.profiles_dir,
    )
    with patch("profiledock.restore.is_active_for_mutation", return_value=True):
        with pytest.raises(RestoreConflictError, match="cannot overwrite running profile"):
            restore_backup_archive(archive_file, destination, overwrite=True)


def test_restore_refuses_active_orphan_runtime_state(tmp_path):
    source = make_paths(tmp_path / "source")
    data_dir = source.profiles_dir / "p1" / "browser-data"
    data_dir.mkdir(parents=True)
    profile = Profile("p1", "Work", "2026-01-01T00:00:00+00:00", str(data_dir))
    archive_file = tmp_path / "active-orphan.tar.gz"
    create_backup_archive([profile], source, archive_file)
    destination = make_paths(tmp_path / "destination")
    with patch("profiledock.restore.is_active_for_mutation", return_value=True):
        with pytest.raises(RestoreConflictError, match="active profile state"):
            restore_backup_archive(archive_file, destination)
    assert not (destination.profiles_dir / "p1").exists()


def test_cli_restore_command_and_json(tmp_path):
    src_paths = make_paths(tmp_path / "src")
    p1_data = src_paths.profiles_dir / "p1" / "browser-data"
    p1_data.mkdir(parents=True)
    (p1_data / "test.txt").write_text("hello", encoding="utf-8")
    p1 = Profile("p1", "Work", "2026-01-01T00:00:00+00:00", str(p1_data), engine="direct")

    archive_file = tmp_path / "backup_cli.tar.gz"
    create_backup_archive([p1], src_paths, archive_file)

    dst_dir = tmp_path / "dst_cli"
    result = runner.invoke(app, ["--data-root", str(dst_dir), "restore", str(archive_file), "--json"])
    assert result.exit_code == EXIT_SUCCESS
    data = json.loads(result.output)
    assert data["output_version"] == 1
    data = data["data"]
    assert data["format_version"] == 1
    assert data["total_restored"] == 1
    assert data["restored"][0]["name"] == "Work"
    assert data["restored"][0]["engine"] == "direct"
