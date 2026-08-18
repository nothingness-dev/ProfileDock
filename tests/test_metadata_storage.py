import json
import os
import shutil
import tempfile
import threading
import time
from unittest.mock import patch
from pathlib import Path
from typing import Generator

import pytest

from profiledock.models import MetadataDocument, METADATA_SCHEMA_VERSION, Profile
from profiledock.storage import (
    MetadataCorruptedError,
    add_profile_atomic,
    load_metadata,
    load_metadata_with_recovery,
    mark_launched_atomic,
    migrate_metadata,
    remove_profile_atomic,
    rename_profile_atomic,
    save_metadata,
    _atomic_write,
)
from profiledock.validation import ValidationError


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    dir_path = Path(tempfile.mkdtemp())
    yield dir_path
    shutil.rmtree(dir_path, ignore_errors=True)


@pytest.fixture
def profiles_dir(temp_dir: Path) -> Path:
    return temp_dir / "profiles"


@pytest.fixture
def metadata_path(temp_dir: Path) -> Path:
    return temp_dir / "profiles.json"


def _create_profile(
    profile_id: str,
    name: str,
    data_dir: str,
    created_at: str = "2024-01-01T00:00:00+00:00",
    last_launched_at: str = None,
) -> Profile:
    return Profile(
        id=profile_id,
        name=name,
        created_at=created_at,
        data_dir=data_dir,
        last_launched_at=last_launched_at,
    )


class TestBareArrayMigration:
    def test_migrate_bare_array(self, temp_dir: Path, metadata_path: Path, profiles_dir: Path) -> None:
        profiles_dir.mkdir(parents=True, exist_ok=True)
        profile = _create_profile("abc123", "Test", str(profiles_dir / "abc123" / "browser-data"))
        old_data = [profile.to_dict()]
        metadata_path.write_text(json.dumps(old_data), encoding="utf-8")
        doc = migrate_metadata(metadata_path, profiles_dir)
        assert doc.schema_version == METADATA_SCHEMA_VERSION
        assert len(doc.profiles) == 1
        assert doc.profiles[0].id == "abc123"
        assert metadata_path.with_suffix(".json.bak").exists()
        loaded = load_metadata(metadata_path)
        assert loaded.schema_version == METADATA_SCHEMA_VERSION

    def test_migrate_creates_backup(self, temp_dir: Path, metadata_path: Path, profiles_dir: Path) -> None:
        profiles_dir.mkdir(parents=True, exist_ok=True)
        old_data = [{"id": "abc123", "name": "Test", "created_at": "2024-01-01T00:00:00+00:00", "data_dir": str(profiles_dir / "abc123" / "browser-data")}]
        metadata_path.write_text(json.dumps(old_data), encoding="utf-8")
        migrate_metadata(metadata_path, profiles_dir)
        backup_path = metadata_path.with_suffix(".json.bak")
        assert backup_path.exists()
        backup_data = json.loads(backup_path.read_text(encoding="utf-8"))
        assert isinstance(backup_data, list)

    def test_migrate_validates_before_writing(self, temp_dir: Path, metadata_path: Path, profiles_dir: Path) -> None:
        profiles_dir.mkdir(parents=True, exist_ok=True)
        old_data = [
            {"id": "abc123", "name": "Test1", "created_at": "2024-01-01T00:00:00+00:00", "data_dir": str(profiles_dir / "abc123" / "browser-data")},
            {"id": "abc123", "name": "Test2", "created_at": "2024-01-01T00:00:00+00:00", "data_dir": str(profiles_dir / "abc123" / "browser-data2")},
        ]
        metadata_path.write_text(json.dumps(old_data), encoding="utf-8")
        with pytest.raises(ValidationError, match="duplicate profile id"):
            migrate_metadata(metadata_path, profiles_dir)
        assert not metadata_path.with_suffix(".json.bak").exists()


class TestCurrentSchema:
    def test_load_versioned_document(self, temp_dir: Path, metadata_path: Path, profiles_dir: Path) -> None:
        profiles_dir.mkdir(parents=True, exist_ok=True)
        profile = _create_profile("abc123", "Test", str(profiles_dir / "abc123" / "browser-data"))
        doc = MetadataDocument(schema_version=METADATA_SCHEMA_VERSION, profiles=[profile])
        metadata_path.write_text(json.dumps(doc.to_dict()), encoding="utf-8")
        loaded = load_metadata(metadata_path)
        assert loaded.schema_version == METADATA_SCHEMA_VERSION
        assert len(loaded.profiles) == 1

    def test_save_and_load_roundtrip(self, temp_dir: Path, metadata_path: Path, profiles_dir: Path) -> None:
        profiles_dir.mkdir(parents=True, exist_ok=True)
        profile = _create_profile("abc123", "Test", str(profiles_dir / "abc123" / "browser-data"))
        doc = MetadataDocument(schema_version=METADATA_SCHEMA_VERSION, profiles=[profile])
        save_metadata(doc, metadata_path, profiles_dir)
        loaded = load_metadata(metadata_path)
        assert loaded.to_dict() == doc.to_dict()


class TestUnsupportedFutureSchema:
    def test_reject_future_schema_version(self, temp_dir: Path, metadata_path: Path) -> None:
        data = {"schema_version": 999, "profiles": []}
        metadata_path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(MetadataCorruptedError, match="unsupported metadata schema version"):
            load_metadata(metadata_path)


class TestDuplicateIDs:
    def test_reject_duplicate_ids(self, temp_dir: Path, profiles_dir: Path, metadata_path: Path) -> None:
        profiles_dir.mkdir(parents=True, exist_ok=True)
        p1 = _create_profile("abc123", "Test1", str(profiles_dir / "abc123" / "browser-data"))
        p2 = _create_profile("abc123", "Test2", str(profiles_dir / "abc123" / "browser-data2"))
        doc = MetadataDocument(schema_version=METADATA_SCHEMA_VERSION, profiles=[p1, p2])
        with pytest.raises(ValidationError, match="duplicate profile id"):
            save_metadata(doc, metadata_path, profiles_dir)


class TestDuplicateDirectories:
    def test_reject_duplicate_directories(self, temp_dir: Path, profiles_dir: Path, metadata_path: Path) -> None:
        profiles_dir.mkdir(parents=True, exist_ok=True)
        data_dir = str(profiles_dir / "abc123" / "browser-data")
        p1 = _create_profile("abc123", "Test1", data_dir)
        p2 = _create_profile("def456", "Test2", data_dir)
        doc = MetadataDocument(schema_version=METADATA_SCHEMA_VERSION, profiles=[p1, p2])
        with pytest.raises(ValidationError, match="duplicate data directory"):
            save_metadata(doc, metadata_path, profiles_dir)


class TestCorruptedPrimaryWithValidBackup:
    def test_recover_from_backup(self, temp_dir: Path, metadata_path: Path, profiles_dir: Path) -> None:
        profiles_dir.mkdir(parents=True, exist_ok=True)
        profile = _create_profile("abc123", "Test", str(profiles_dir / "abc123" / "browser-data"))
        save_metadata(
            MetadataDocument(schema_version=METADATA_SCHEMA_VERSION, profiles=[]),
            metadata_path,
            profiles_dir,
        )
        doc = MetadataDocument(schema_version=METADATA_SCHEMA_VERSION, profiles=[profile])
        save_metadata(doc, metadata_path, profiles_dir)
        metadata_path.write_text("corrupted data {{{", encoding="utf-8")
        recovered = load_metadata_with_recovery(metadata_path)
        assert len(recovered.profiles) == 0
        assert recovered.schema_version == METADATA_SCHEMA_VERSION

    def test_fails_when_both_corrupted(self, temp_dir: Path, metadata_path: Path) -> None:
        metadata_path.write_text("corrupted data {{{", encoding="utf-8")
        with pytest.raises(MetadataCorruptedError):
            load_metadata_with_recovery(metadata_path)

    def test_recover_from_separate_backup(self, temp_dir: Path, metadata_path: Path, profiles_dir: Path) -> None:
        profiles_dir.mkdir(parents=True, exist_ok=True)
        backup_path = temp_dir / "backups" / "profiles.json.bak"
        save_metadata(
            MetadataDocument(schema_version=METADATA_SCHEMA_VERSION, profiles=[]),
            metadata_path,
            profiles_dir,
            backup_path,
        )
        profile = _create_profile("abc123", "Test", str(profiles_dir / "abc123" / "browser-data"))
        save_metadata(
            MetadataDocument(schema_version=METADATA_SCHEMA_VERSION, profiles=[profile]),
            metadata_path,
            profiles_dir,
            backup_path,
        )
        metadata_path.write_text("corrupted", encoding="utf-8")
        recovered = load_metadata_with_recovery(metadata_path, backup_path)
        assert recovered.profiles == []

    def test_recovery_rejects_unsafe_backup(self, temp_dir: Path, metadata_path: Path, profiles_dir: Path) -> None:
        profiles_dir.mkdir(parents=True, exist_ok=True)
        backup_path = temp_dir / "backups" / "profiles.json.bak"
        backup_path.parent.mkdir(parents=True)
        profile = _create_profile("unsafe", "Unsafe", str(temp_dir / "outside" / "browser-data"))
        backup_path.write_text(
            json.dumps(
                MetadataDocument(
                    schema_version=METADATA_SCHEMA_VERSION,
                    profiles=[profile],
                ).to_dict()
            ),
            encoding="utf-8",
        )
        metadata_path.write_text("corrupted", encoding="utf-8")
        with pytest.raises(MetadataCorruptedError):
            load_metadata_with_recovery(metadata_path, backup_path)


class TestConcurrentMutations:
    def test_concurrent_add_profiles(self, temp_dir: Path, metadata_path: Path, profiles_dir: Path) -> None:
        profiles_dir.mkdir(parents=True, exist_ok=True)
        doc = MetadataDocument(schema_version=METADATA_SCHEMA_VERSION, profiles=[])
        save_metadata(doc, metadata_path, profiles_dir)
        errors = []

        def add_profile(i: int) -> None:
            try:
                data_dir = str(profiles_dir / f"profile{i}" / "browser-data")
                profile = _create_profile(f"id{i}", f"Profile{i}", data_dir)
                add_profile_atomic(profile, metadata_path, profiles_dir)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=add_profile, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
        doc = load_metadata(metadata_path)
        assert len(doc.profiles) == 5

    def test_concurrent_remove_profiles(self, temp_dir: Path, metadata_path: Path, profiles_dir: Path) -> None:
        profiles_dir.mkdir(parents=True, exist_ok=True)
        profiles = []
        for i in range(5):
            data_dir = str(profiles_dir / f"profile{i}" / "browser-data")
            profiles.append(_create_profile(f"id{i}", f"Profile{i}", data_dir))
        doc = MetadataDocument(schema_version=METADATA_SCHEMA_VERSION, profiles=profiles)
        save_metadata(doc, metadata_path, profiles_dir)
        errors = []

        def remove_profile(i: int) -> None:
            try:
                remove_profile_atomic(f"id{i}", metadata_path, profiles_dir)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=remove_profile, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
        final_doc = load_metadata(metadata_path)
        assert len(final_doc.profiles) == 0


class TestInterruptedWrites:
    def test_interrupted_write_leaves_valid_document(self, temp_dir: Path, metadata_path: Path, profiles_dir: Path) -> None:
        profiles_dir.mkdir(parents=True, exist_ok=True)
        profile = _create_profile("abc123", "Test", str(profiles_dir / "abc123" / "browser-data"))
        doc = MetadataDocument(schema_version=METADATA_SCHEMA_VERSION, profiles=[profile])
        save_metadata(doc, metadata_path, profiles_dir)
        tmp_path = metadata_path.with_suffix(".tmp")
        tmp_path.write_text("partial write", encoding="utf-8")
        loaded = load_metadata(metadata_path)
        assert loaded.schema_version == METADATA_SCHEMA_VERSION
        assert len(loaded.profiles) == 1

    def test_backup_write_leaves_no_temporary_file(self, metadata_path: Path, profiles_dir: Path) -> None:
        profiles_dir.mkdir(parents=True, exist_ok=True)
        backup_path = metadata_path.parent / "backups" / "profiles.json.bak"
        save_metadata(
            MetadataDocument(schema_version=METADATA_SCHEMA_VERSION, profiles=[]),
            metadata_path,
            profiles_dir,
            backup_path,
        )
        save_metadata(
            MetadataDocument(schema_version=METADATA_SCHEMA_VERSION, profiles=[]),
            metadata_path,
            profiles_dir,
            backup_path,
        )
        assert backup_path.is_file()
        assert list(backup_path.parent.glob("*.tmp")) == []


class TestUnsafePaths:
    def test_reject_symlink_data_dir(self, temp_dir: Path, profiles_dir: Path, metadata_path: Path) -> None:
        profiles_dir.mkdir(parents=True, exist_ok=True)
        real_dir = profiles_dir / "real"
        real_dir.mkdir()
        symlink_dir = profiles_dir / "link"
        try:
            symlink_dir.symlink_to(real_dir, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"directory symlinks are unavailable: {exc}")
        profile = _create_profile("abc123", "Test", str(symlink_dir / "browser-data"))
        doc = MetadataDocument(schema_version=METADATA_SCHEMA_VERSION, profiles=[profile])
        with pytest.raises(ValidationError, match="symlink"):
            save_metadata(doc, metadata_path, profiles_dir)

    def test_reject_path_traversal(self, temp_dir: Path, profiles_dir: Path, metadata_path: Path) -> None:
        profiles_dir.mkdir(parents=True, exist_ok=True)
        profile = _create_profile("abc123", "Test", str(temp_dir / "outside" / "browser-data"))
        doc = MetadataDocument(schema_version=METADATA_SCHEMA_VERSION, profiles=[profile])
        with pytest.raises(ValidationError, match="must be under profile root"):
            save_metadata(doc, metadata_path, profiles_dir)


class TestMigrationWithTimestampValidation:
    def test_validate_timestamps_during_migration(self, temp_dir: Path, metadata_path: Path, profiles_dir: Path) -> None:
        profiles_dir.mkdir(parents=True, exist_ok=True)
        old_data = [
            {"id": "abc123", "name": "Test", "created_at": "not-a-timestamp", "data_dir": str(profiles_dir / "abc123" / "browser-data")}
        ]
        metadata_path.write_text(json.dumps(old_data), encoding="utf-8")
        with pytest.raises(ValidationError, match="ISO-8601 timestamp"):
            migrate_metadata(metadata_path, profiles_dir)


class TestEmptyMetadata:
    def test_load_empty_metadata(self, temp_dir: Path, metadata_path: Path) -> None:
        doc = load_metadata(metadata_path)
        assert doc.schema_version == METADATA_SCHEMA_VERSION
        assert len(doc.profiles) == 0


def test_rejects_path_like_profile_id(metadata_path: Path, profiles_dir: Path) -> None:
    profiles_dir.mkdir(parents=True, exist_ok=True)
    profile = _create_profile("../runtime", "Unsafe", str(profiles_dir / "runtime" / "browser-data"))
    doc = MetadataDocument(schema_version=METADATA_SCHEMA_VERSION, profiles=[profile])
    with pytest.raises(ValidationError, match="unsafe characters"):
        save_metadata(doc, metadata_path, profiles_dir)


def test_atomic_write_retries_transient_replace_failure(tmp_path: Path) -> None:
    target = tmp_path / "profiles.json"
    original_replace = Path.replace
    attempts = 0

    def replace_with_failures(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("temporarily locked")
        return original_replace(source, destination)

    with patch.object(Path, "replace", replace_with_failures):
        _atomic_write(target, "{}")
    assert target.read_text(encoding="utf-8") == "{}"
    assert attempts == 3
