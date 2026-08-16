import fcntl
import json
import os
import shutil
import time as _time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, List, Union

from .models import MetadataDocument, METADATA_SCHEMA_VERSION, Profile
from .validation import ValidationError, validate_metadata_document


class StorageError(Exception):
    pass


class MetadataCorruptedError(StorageError):
    pass


class MetadataLockedError(StorageError):
    pass


@contextmanager
def metadata_lock(
    metadata_path: Union[str, Path], timeout: float = 5.0
) -> Generator[None, None, None]:
    lock_path = Path(metadata_path).with_suffix(".lock")
    lock_fd = None
    try:
        lock_fd = open(lock_path, "w")
        deadline = _time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if _time.monotonic() >= deadline:
                    raise MetadataLockedError(
                        f"could not acquire metadata lock within {timeout}s"
                    )
                _time.sleep(0.01)
        yield
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                lock_fd.close()
            except OSError:
                pass


def _read_json_file(path: Path) -> Any:
    try:
        raw = path.read_text(encoding="utf-8")
        return json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise MetadataCorruptedError(f"could not read {path}: {exc}") from exc


def _is_bare_array(data: Any) -> bool:
    return isinstance(data, list)


def _is_versioned_document(data: Any) -> bool:
    return isinstance(data, dict) and "schema_version" in data and "profiles" in data


def _atomic_write(path: Path, content: str) -> None:
    dir_path = path.parent
    dir_path.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    fd = None
    try:
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        os.write(fd, content.encode("utf-8"))
        os.fsync(fd)
        os.close(fd)
        fd = None
        tmp_path.replace(path)
    except OSError as exc:
        if fd is not None:
            os.close(fd)
        raise StorageError(f"could not write {path}: {exc}") from exc
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _backup_metadata(path: Path) -> None:
    backup_path = path.with_suffix(".json.bak")
    if path.exists():
        try:
            shutil.copy2(str(path), str(backup_path))
        except OSError as exc:
            raise StorageError(f"could not backup {path}: {exc}") from exc


def _load_profiles_from_bare_array(data: List[Any]) -> List[Profile]:
    profiles = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValidationError(f"bare array item {i} must be a JSON object")
        profiles.append(Profile.from_dict(item))
    return profiles


def load_metadata(path: Union[str, Path] = "profiles.json") -> MetadataDocument:
    path = Path(path)
    if not path.exists():
        return MetadataDocument(schema_version=METADATA_SCHEMA_VERSION, profiles=[])
    data = _read_json_file(path)
    if _is_versioned_document(data):
        try:
            return MetadataDocument.from_dict(data)
        except (ValidationError, ValueError) as exc:
            raise MetadataCorruptedError(f"metadata is corrupted: {exc}") from exc
    if _is_bare_array(data):
        profiles = _load_profiles_from_bare_array(data)
        return MetadataDocument(schema_version=METADATA_SCHEMA_VERSION, profiles=profiles)
    raise MetadataCorruptedError(f"unrecognized metadata format in {path}")


def migrate_metadata(
    path: Union[str, Path],
    profile_root: Union[str, Path],
    backup: bool = True,
) -> MetadataDocument:
    path = Path(path)
    profile_root = Path(profile_root)
    if not path.exists():
        return MetadataDocument(schema_version=METADATA_SCHEMA_VERSION, profiles=[])
    data = _read_json_file(path)
    if _is_versioned_document(data):
        try:
            return MetadataDocument.from_dict(data)
        except (ValidationError, ValueError) as exc:
            raise MetadataCorruptedError(f"metadata is corrupted: {exc}") from exc
    if not _is_bare_array(data):
        raise MetadataCorruptedError(f"unrecognized metadata format in {path}")
    profiles = _load_profiles_from_bare_array(data)
    validate_metadata_document(profiles, profile_root)
    if backup:
        _backup_metadata(path)
    doc = MetadataDocument(schema_version=METADATA_SCHEMA_VERSION, profiles=profiles)
    _atomic_write(path, json.dumps(doc.to_dict(), indent=2) + "\n")
    return doc


def load_metadata_with_recovery(path: Union[str, Path]) -> MetadataDocument:
    path = Path(path)
    try:
        return load_metadata(path)
    except MetadataCorruptedError:
        backup_path = path.with_suffix(".json.bak")
        if backup_path.exists():
            try:
                data = _read_json_file(backup_path)
                if _is_versioned_document(data):
                    return MetadataDocument.from_dict(data)
                if _is_bare_array(data):
                    profiles = _load_profiles_from_bare_array(data)
                    return MetadataDocument(
                        schema_version=METADATA_SCHEMA_VERSION, profiles=profiles
                    )
            except (MetadataCorruptedError, ValidationError, ValueError):
                pass
        raise


def save_metadata(
    doc: MetadataDocument,
    path: Union[str, Path] = "profiles.json",
    profile_root: Union[str, Path] = "profiles",
) -> None:
    path = Path(path)
    profile_root = Path(profile_root)
    with metadata_lock(path):
        validate_metadata_document(doc.profiles, profile_root)
        _backup_metadata(path)
        content = json.dumps(doc.to_dict(), indent=2) + "\n"
        _atomic_write(path, content)


def load_profiles(path: Union[str, Path] = "profiles.json") -> List[Profile]:
    doc = load_metadata(path)
    return doc.profiles


def save_profiles(
    profiles: List[Profile],
    path: Union[str, Path] = "profiles.json",
    profile_root: Union[str, Path] = "profiles",
) -> None:
    doc = MetadataDocument(schema_version=METADATA_SCHEMA_VERSION, profiles=profiles)
    save_metadata(doc, path, profile_root)


def atomic_update_metadata(
    path: Union[str, Path],
    profile_root: Union[str, Path],
    updater: Any,
) -> MetadataDocument:
    path = Path(path)
    profile_root = Path(profile_root)
    with metadata_lock(path):
        doc = load_metadata(path)
        original_dict = json.dumps(doc.to_dict(), sort_keys=True)
        new_doc = updater(doc)
        new_dict = json.dumps(new_doc.to_dict(), sort_keys=True)
        if original_dict == new_dict:
            return new_doc
        validate_metadata_document(new_doc.profiles, profile_root)
        _backup_metadata(path)
        _atomic_write(path, json.dumps(new_doc.to_dict(), indent=2) + "\n")
        return new_doc


def add_profile_atomic(
    profile: Profile,
    path: Union[str, Path] = "profiles.json",
    profile_root: Union[str, Path] = "profiles",
) -> MetadataDocument:
    def _add(doc: MetadataDocument) -> MetadataDocument:
        new_profiles = list(doc.profiles)
        new_profiles.append(profile)
        return MetadataDocument(
            schema_version=doc.schema_version, profiles=new_profiles
        )

    return atomic_update_metadata(path, profile_root, _add)


def remove_profile_atomic(
    profile_id: str,
    path: Union[str, Path] = "profiles.json",
    profile_root: Union[str, Path] = "profiles",
) -> MetadataDocument:
    def _remove(doc: MetadataDocument) -> MetadataDocument:
        new_profiles = [p for p in doc.profiles if p.id != profile_id]
        return MetadataDocument(
            schema_version=doc.schema_version, profiles=new_profiles
        )

    return atomic_update_metadata(path, profile_root, _remove)


def rename_profile_atomic(
    profile_id: str,
    new_name: str,
    path: Union[str, Path] = "profiles.json",
    profile_root: Union[str, Path] = "profiles",
) -> MetadataDocument:
    def _rename(doc: MetadataDocument) -> MetadataDocument:
        new_profiles = []
        for p in doc.profiles:
            if p.id == profile_id:
                new_profiles.append(
                    Profile(
                        id=p.id,
                        name=new_name,
                        created_at=p.created_at,
                        data_dir=p.data_dir,
                        last_launched_at=p.last_launched_at,
                    )
                )
            else:
                new_profiles.append(p)
        return MetadataDocument(
            schema_version=doc.schema_version, profiles=new_profiles
        )

    return atomic_update_metadata(path, profile_root, _rename)


def mark_launched_atomic(
    profile_id: str,
    launched_at: str,
    path: Union[str, Path] = "profiles.json",
    profile_root: Union[str, Path] = "profiles",
) -> MetadataDocument:
    def _mark(doc: MetadataDocument) -> MetadataDocument:
        new_profiles = []
        for p in doc.profiles:
            if p.id == profile_id:
                new_profiles.append(
                    Profile(
                        id=p.id,
                        name=p.name,
                        created_at=p.created_at,
                        data_dir=p.data_dir,
                        last_launched_at=launched_at,
                    )
                )
            else:
                new_profiles.append(p)
        return MetadataDocument(
            schema_version=doc.schema_version, profiles=new_profiles
        )

    return atomic_update_metadata(path, profile_root, _mark)
