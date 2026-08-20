from hashlib import sha256
import io
import json
import os
from pathlib import Path
import tarfile
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from profiledock.backup import create_backup_archive
from profiledock.cli import app, EXIT_SUCCESS, EXIT_USER_ERROR
from profiledock.data_root import DataPaths
from profiledock.models import Profile, MetadataDocument
from profiledock.restore import (
    DecompressionSecurityError,
    InvalidArchiveError,
    RestoreConflictError,
    restore_backup_archive,
)
from profiledock.storage import load_metadata, save_metadata

runner = CliRunner()


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

    p1 = Profile("p1", "DirectWork", "2026-01-01T00:00:00+00:00", str(p1_data), engine="direct")
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
                    "files": {"evil.txt": {"size": 4, "sha256": "dummy"}},
                }
            ],
        }
        manifest_bytes = json.dumps(manifest).encode("utf-8")
        tarinfo = tarfile.TarInfo("backup_manifest.json")
        tarinfo.size = len(manifest_bytes)
        tar.addfile(tarinfo, io.BytesIO(manifest_bytes))

        evil_info = tarfile.TarInfo("profiles/p1/browser-data/../../etc/passwd")
        evil_info.size = 4
        tar.addfile(evil_info, io.BytesIO(b"evil"))

    with pytest.raises(DecompressionSecurityError, match="parent traversal"):
        restore_backup_archive(malicious_archive, dst_paths)


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
        manifest_bytes = json.dumps(manifest).encode("utf-8")
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
        manifest_bytes = json.dumps(manifest).encode("utf-8")
        tarinfo = tarfile.TarInfo("backup_manifest.json")
        tarinfo.size = len(manifest_bytes)
        tar.addfile(tarinfo, io.BytesIO(manifest_bytes))

        link_info = tarfile.TarInfo("profiles/p1/browser-data/symlink")
        link_info.type = tarfile.SYMTYPE
        link_info.linkname = "/etc/passwd"
        tar.addfile(link_info)

    with pytest.raises(DecompressionSecurityError, match="unsafe link"):
        restore_backup_archive(malicious_archive, dst_paths)


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
                    "files": {"data.txt": {"size": 7, "sha256": "badchecksum"}},
                }
            ],
        }
        manifest_bytes = json.dumps(manifest).encode("utf-8")
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
    assert data["format_version"] == 1
    assert data["total_restored"] == 1
    assert data["restored"][0]["name"] == "Work"
    assert data["restored"][0]["engine"] == "direct"
