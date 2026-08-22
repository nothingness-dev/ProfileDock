import io
import json
import tarfile

import pytest

from profiledock.profile_manager import ProfileManager


@pytest.fixture(autouse=True)
def isolated_data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("PROFILEDOCK_DATA_ROOT", str(tmp_path / "app-data"))


@pytest.fixture
def manager(tmp_path):
    return ProfileManager(tmp_path)


@pytest.fixture
def malicious_metadata_document(tmp_path):
    return {
        "schema_version": 1,
        "profiles": [
            {
                "id": "safe-id",
                "name": "Escaped",
                "created_at": "2026-01-01T00:00:00+00:00",
                "data_dir": str(tmp_path / "outside" / "browser-data"),
            }
        ],
    }


@pytest.fixture
def malicious_archive(tmp_path):
    archive = tmp_path / "malicious-backslash.tar.gz"
    manifest = {
        "format_version": 1,
        "profiledock_version": "test",
        "created_at": "2026-01-01T00:00:00+00:00",
        "total_profiles": 1,
        "total_files": 1,
        "total_bytes": 1,
        "profiles": [
            {
                "id": "safe-id",
                "name": "Escaped",
                "created_at": "2026-01-01T00:00:00+00:00",
                "last_launched_at": None,
                "engine": None,
                "launch_config": None,
                "file_count": 1,
                "total_bytes": 1,
                "files": {"..\\escape.txt": {"size": 1, "sha256": "0" * 64}},
            }
        ],
    }
    with tarfile.open(archive, "w:gz") as tar:
        payload = json.dumps(manifest).encode("utf-8")
        member = tarfile.TarInfo("backup_manifest.json")
        member.size = len(payload)
        tar.addfile(member, io.BytesIO(payload))
    return archive
