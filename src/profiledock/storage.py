import json
import os
import sys
import time as _time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import IO, Any
from uuid import uuid4

from .data_root import DataRootError, ensure_within_root
from .fsops import replace_with_retry as _replace_with_retry
from .fsops import write_all as _write_all
from .models import METADATA_SCHEMA_VERSION, LaunchConfig, MetadataDocument, Profile, migrate_metadata_value
from .validation import ValidationError, validate_metadata_document


class StorageError(Exception):
    pass


class MetadataCorruptedError(StorageError):
    pass


class MetadataUnreadableError(StorageError):
    """The metadata file could not be read (transient I/O failure, not corruption)."""


class MetadataLockedError(StorageError):
    pass


def _metadata_root(path: Path) -> Path:
    absolute = path.expanduser().absolute()
    return absolute.parent.parent if absolute.parent.name == "metadata" else absolute.parent


def _validate_metadata_paths(
    path: Path,
    profile_root: Path,
    backup_path: str | Path | None = None,
) -> Path:
    root = profile_root.expanduser().absolute().parent
    try:
        ensure_within_root(profile_root, root)
        ensure_within_root(path, root)
        ensure_within_root(path.with_suffix(".lock"), root)
        if backup_path is not None:
            ensure_within_root(Path(backup_path), root)
    except DataRootError as exc:
        raise StorageError(f"unsafe metadata storage path: {exc}") from exc
    return root.resolve(strict=False)


def _lock_file(fd: IO[bytes]) -> None:
    if sys.platform == "win32":
        import msvcrt

        fd.seek(0)
        msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(fd: IO[bytes]) -> None:
    if sys.platform == "win32":
        import msvcrt

        try:
            fd.seek(0)
            msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl

        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


@contextmanager
def metadata_lock(metadata_path: str | Path, timeout: float = 5.0) -> Generator[None, None, None]:
    metadata_path = Path(metadata_path)
    root = _metadata_root(metadata_path)
    try:
        ensure_within_root(metadata_path, root)
        lock_path = ensure_within_root(metadata_path.with_suffix(".lock"), root)
    except DataRootError as exc:
        raise MetadataLockedError(f"unsafe metadata lock path: {exc}") from exc
    lock_fd = None
    raw_fd = None
    try:
        raw_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        lock_fd = os.fdopen(raw_fd, "a+b")
        raw_fd = None
        os.chmod(lock_path, 0o600)
        deadline = _time.monotonic() + timeout
        poll_interval = 0.005
        while True:
            try:
                _lock_file(lock_fd)
                break
            except OSError:
                if _time.monotonic() >= deadline:
                    raise MetadataLockedError(f"could not acquire metadata lock within {timeout}s") from None
                _time.sleep(poll_interval)
                poll_interval = min(poll_interval * 1.5, 0.05)
        yield
    finally:
        if raw_fd is not None:
            try:
                os.close(raw_fd)
            except OSError:
                pass
        if lock_fd is not None:
            try:
                _unlock_file(lock_fd)
                lock_fd.close()
            except OSError:
                pass


def _read_json_file(path: Path) -> Any:
    try:
        raw = path.read_text(encoding="utf-8")
        return json.loads(raw)
    except OSError as exc:
        raise MetadataUnreadableError(f"could not read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise MetadataCorruptedError(f"could not parse {path}: {exc}") from exc


def _is_bare_array(data: object) -> bool:
    return isinstance(data, list)


def _is_versioned_document(data: object) -> bool:
    return isinstance(data, dict) and "schema_version" in data and "profiles" in data


def _profile_root_for_metadata(path: Path) -> Path:
    if path.parent.name == "metadata":
        return path.parent.parent / "profiles"
    return path.parent / "profiles"


def _atomic_write(path: Path, content: str, root: Path | None = None) -> None:
    if root is None:
        root = _metadata_root(path)
    try:
        path = ensure_within_root(path, root)
    except DataRootError as exc:
        raise StorageError(f"unsafe metadata write path: {exc}") from exc
    dir_path = path.parent
    dir_path.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    fd = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        fd = os.open(str(tmp_path), flags, 0o600)
        _write_all(fd, content.encode("utf-8"))
        os.fsync(fd)
        os.close(fd)
        fd = None
        _replace_with_retry(tmp_path, path)
        if sys.platform != "win32":
            try:
                directory_fd = os.open(str(dir_path), os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
    except OSError as exc:
        if fd is not None:
            os.close(fd)
            fd = None
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


def _backup_metadata(
    path: Path,
    backup_path: str | Path | None = None,
    root: Path | None = None,
) -> None:
    backup_path = Path(backup_path) if backup_path is not None else path.with_suffix(".json.bak")
    root = root or _metadata_root(path)
    try:
        ensure_within_root(path, root)
        backup_path = ensure_within_root(backup_path, root)
    except DataRootError as exc:
        raise StorageError(f"unsafe metadata backup path: {exc}") from exc
    if path.exists():
        try:
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(backup_path, path.read_text(encoding="utf-8"), root)
        except (OSError, StorageError) as exc:
            raise StorageError(f"could not backup {path}: {exc}") from exc


def _load_profiles_from_bare_array(data: list[dict[str, Any]]) -> list[Profile]:
    return MetadataDocument.from_dict(migrate_metadata_value(data)).profiles


def load_metadata(path: str | Path = "profiles.json") -> MetadataDocument:
    path = Path(path)
    if not path.exists():
        return MetadataDocument(schema_version=METADATA_SCHEMA_VERSION, profiles=[])
    data = _read_json_file(path)
    try:
        doc = MetadataDocument.from_dict(migrate_metadata_value(data))
        validate_metadata_document(doc.profiles, _profile_root_for_metadata(path))
        return doc
    except (ValidationError, ValueError) as exc:
        raise MetadataCorruptedError(f"metadata is corrupted: {exc}") from exc


def _migrate_metadata_unlocked(
    path: str | Path,
    profile_root: str | Path,
    backup: bool = True,
    backup_path: str | Path | None = None,
) -> MetadataDocument:
    path = Path(path)
    profile_root = Path(profile_root)
    root = _validate_metadata_paths(path, profile_root, backup_path)
    if not path.exists():
        return MetadataDocument(schema_version=METADATA_SCHEMA_VERSION, profiles=[])
    data = _read_json_file(path)
    try:
        migrated = migrate_metadata_value(data)
        doc = MetadataDocument.from_dict(migrated)
        validate_metadata_document(doc.profiles, profile_root)
    except ValueError as exc:
        raise MetadataCorruptedError(f"metadata is corrupted: {exc}") from exc
    if migrated != data:
        if backup:
            _backup_metadata(path, backup_path, root)
        _atomic_write(path, json.dumps(migrated, indent=2) + "\n", root)
    return doc


def migrate_metadata(
    path: str | Path,
    profile_root: str | Path,
    backup: bool = True,
    backup_path: str | Path | None = None,
) -> MetadataDocument:
    path = Path(path)
    with metadata_lock(path):
        return _migrate_metadata_unlocked(path, profile_root, backup, backup_path)


def load_metadata_with_recovery(
    path: str | Path,
    backup_path: str | Path | None = None,
) -> MetadataDocument:
    path = Path(path)
    try:
        return load_metadata(path)
    except MetadataCorruptedError:
        recovery_path = Path(backup_path) if backup_path is not None else path.with_suffix(".json.bak")
        if recovery_path.exists():
            try:
                data = _read_json_file(recovery_path)
                doc = MetadataDocument.from_dict(migrate_metadata_value(data))
                validate_metadata_document(doc.profiles, _profile_root_for_metadata(path))
                return doc
            except (MetadataCorruptedError, ValidationError, ValueError):
                pass
        raise


def save_metadata(
    doc: MetadataDocument,
    path: str | Path = "profiles.json",
    profile_root: str | Path = "profiles",
    backup_path: str | Path | None = None,
) -> None:
    if doc.schema_version != METADATA_SCHEMA_VERSION:
        raise StorageError(f"refusing to write unsupported metadata schema version: {doc.schema_version}")
    path = Path(path)
    profile_root = Path(profile_root)
    root = _validate_metadata_paths(path, profile_root, backup_path)
    with metadata_lock(path):
        validate_metadata_document(doc.profiles, profile_root)
        _backup_metadata(path, backup_path, root)
        content = json.dumps(doc.to_dict(), indent=2) + "\n"
        _atomic_write(path, content, root)


def load_profiles(path: str | Path = "profiles.json") -> list[Profile]:
    doc = load_metadata(path)
    return doc.profiles


def save_profiles(
    profiles: list[Profile],
    path: str | Path = "profiles.json",
    profile_root: str | Path = "profiles",
    backup_path: str | Path | None = None,
) -> None:
    doc = MetadataDocument(schema_version=METADATA_SCHEMA_VERSION, profiles=profiles)
    save_metadata(doc, path, profile_root, backup_path)


def atomic_update_metadata(
    path: str | Path,
    profile_root: str | Path,
    updater: Callable[[MetadataDocument], MetadataDocument],
    backup_path: str | Path | None = None,
) -> MetadataDocument:
    path = Path(path)
    profile_root = Path(profile_root)
    root = _validate_metadata_paths(path, profile_root, backup_path)
    with metadata_lock(path):
        doc = load_metadata(path)
        original_dict = json.dumps(doc.to_dict(), sort_keys=True)
        new_doc = updater(doc)
        new_dict = json.dumps(new_doc.to_dict(), sort_keys=True)
        if original_dict == new_dict:
            return new_doc
        validate_metadata_document(new_doc.profiles, profile_root)
        _backup_metadata(path, backup_path, root)
        _atomic_write(path, json.dumps(new_doc.to_dict(), indent=2) + "\n", root)
        return new_doc


def add_profile_atomic(
    profile: Profile,
    path: str | Path = "profiles.json",
    profile_root: str | Path = "profiles",
    backup_path: str | Path | None = None,
) -> MetadataDocument:
    def _add(doc: MetadataDocument) -> MetadataDocument:
        new_profiles = list(doc.profiles)
        new_profiles.append(profile)
        return MetadataDocument(schema_version=doc.schema_version, profiles=new_profiles)

    return atomic_update_metadata(path, profile_root, _add, backup_path)


def remove_profile_atomic(
    profile_id: str,
    path: str | Path = "profiles.json",
    profile_root: str | Path = "profiles",
    backup_path: str | Path | None = None,
) -> MetadataDocument:
    def _remove(doc: MetadataDocument) -> MetadataDocument:
        new_profiles = [p for p in doc.profiles if p.id != profile_id]
        return MetadataDocument(schema_version=doc.schema_version, profiles=new_profiles)

    return atomic_update_metadata(path, profile_root, _remove, backup_path)


def _mutate_profile_atomic(
    profile_id: str,
    mutate: Callable[[Profile], Profile],
    path: str | Path,
    profile_root: str | Path,
    backup_path: str | Path | None = None,
) -> MetadataDocument:
    def _apply(doc: MetadataDocument) -> MetadataDocument:
        new_profiles = [mutate(p) if p.id == profile_id else p for p in doc.profiles]
        return MetadataDocument(schema_version=doc.schema_version, profiles=new_profiles)

    return atomic_update_metadata(path, profile_root, _apply, backup_path)


def rename_profile_atomic(
    profile_id: str,
    new_name: str,
    path: str | Path = "profiles.json",
    profile_root: str | Path = "profiles",
    backup_path: str | Path | None = None,
) -> MetadataDocument:
    return _mutate_profile_atomic(
        profile_id, lambda p: replace(p, name=new_name), path, profile_root, backup_path
    )


def mark_launched_atomic(
    profile_id: str,
    launched_at: str,
    path: str | Path = "profiles.json",
    profile_root: str | Path = "profiles",
    backup_path: str | Path | None = None,
) -> MetadataDocument:
    return _mutate_profile_atomic(
        profile_id, lambda p: replace(p, last_launched_at=launched_at), path, profile_root, backup_path
    )


def set_engine_atomic(
    profile_id: str,
    engine: str | None,
    path: str | Path = "profiles.json",
    profile_root: str | Path = "profiles",
    backup_path: str | Path | None = None,
) -> MetadataDocument:
    return _mutate_profile_atomic(
        profile_id, lambda p: replace(p, engine=engine), path, profile_root, backup_path
    )


def set_launch_config_atomic(
    profile_id: str,
    launch_config: LaunchConfig | None,
    path: str | Path = "profiles.json",
    profile_root: str | Path = "profiles",
    backup_path: str | Path | None = None,
) -> MetadataDocument:
    return _mutate_profile_atomic(
        profile_id, lambda p: replace(p, launch_config=launch_config), path, profile_root, backup_path
    )
