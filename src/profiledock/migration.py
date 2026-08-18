from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import shutil
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import uuid4

from .data_root import DataPaths, resolve_data_root
from .models import METADATA_SCHEMA_VERSION, MetadataDocument, Profile
from .process_manager import get_status, is_running
from .storage import (
    MetadataCorruptedError,
    StorageError,
    _atomic_write,
    _backup_metadata,
    _is_bare_array,
    _is_versioned_document,
    _load_profiles_from_bare_array,
    _read_json_file,
    load_metadata,
    metadata_lock,
    save_metadata,
)
from .validation import ValidationError, validate_metadata_document, validate_required_fields


class MigrationError(Exception):
    pass


class SourceRunningError(MigrationError):
    pass


class ConflictError(MigrationError):
    pass


@dataclass
class MigrationProfileResult:
    id: str
    name: str
    status: str
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MigrationReport:
    source_root: str
    destination_root: str
    migrated: List[MigrationProfileResult]
    skipped: List[MigrationProfileResult]
    failed: List[MigrationProfileResult]
    source_removed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_root": self.source_root,
            "destination_root": self.destination_root,
            "migrated": [p.to_dict() for p in self.migrated],
            "skipped": [p.to_dict() for p in self.skipped],
            "failed": [p.to_dict() for p in self.failed],
            "source_removed": self.source_removed,
        }


def _detect_source_layout(source_root: Path) -> Tuple[Path, Path, Optional[Path]]:
    root = source_root.resolve()
    new_meta = root / "metadata" / "profiles.json"
    if new_meta.exists():
        profiles_dir = root / "profiles"
        runtime_dir = root / "runtime"
        return new_meta, profiles_dir, runtime_dir if runtime_dir.exists() else None

    legacy_meta = root / "profiles.json"
    if legacy_meta.exists():
        profiles_dir = root / "profiles"
        return legacy_meta, profiles_dir, profiles_dir

    raise MigrationError(f"no ProfileDock metadata found in {source_root}")


def _load_source_profiles(meta_file: Path, profiles_dir: Path) -> List[Profile]:
    try:
        data = _read_json_file(meta_file)
    except Exception as exc:
        raise MigrationError(f"source metadata corrupted: {exc}") from exc

    if _is_versioned_document(data):
        try:
            doc = MetadataDocument.from_dict(data)
            profiles = doc.profiles
        except Exception as exc:
            raise MigrationError(f"source metadata corrupted: {exc}") from exc
    elif _is_bare_array(data):
        try:
            profiles = _load_profiles_from_bare_array(data)
        except Exception as exc:
            raise MigrationError(f"source metadata corrupted: {exc}") from exc
    else:
        raise MigrationError("unrecognized source metadata format")

    for p in profiles:
        try:
            validate_required_fields(p)
        except ValidationError as exc:
            raise MigrationError(f"invalid source profile {p.id}: {exc}") from exc

    return profiles


def _verify_directory_contents(source: Path, target: Path) -> bool:
    if not target.exists() or not target.is_dir():
        return False
    for root_dir, _, files in os.walk(source):
        rel_path = Path(root_dir).relative_to(source)
        target_dir = target / rel_path
        if not target_dir.exists() or not target_dir.is_dir():
            return False
        for f in files:
            src_file = Path(root_dir) / f
            tgt_file = target_dir / f
            if not tgt_file.exists() or not tgt_file.is_file():
                return False
            if src_file.stat().st_size != tgt_file.stat().st_size:
                return False
    return True


def migrate_project(
    source_root: Path,
    destination_paths: DataPaths,
    remove_source: bool = False,
) -> MigrationReport:
    src_root = source_root.resolve()
    dst_root = destination_paths.root.resolve()
    if src_root == dst_root:
        raise MigrationError("source and destination projects cannot be the same")

    meta_file, profiles_dir, runtime_dir = _detect_source_layout(src_root)
    source_profiles = _load_source_profiles(meta_file, profiles_dir)

    for p in source_profiles:
        p_data_dir = Path(p.data_dir)
        if not p_data_dir.is_absolute():
            p_data_dir = (src_root / p_data_dir).resolve()
        p_runtime = None
        if runtime_dir is not None:
            p_runtime = runtime_dir / p.id if (runtime_dir / p.id).exists() else profiles_dir / p.id
        if is_running(str(p_data_dir), runtime_dir=p_runtime):
            raise SourceRunningError(
                f"cannot migrate while profile '{p.name}' ({p.id}) is running"
            )

    dst_meta_file = destination_paths.profiles_file
    dst_profiles_dir = destination_paths.profiles_dir
    dst_backup_file = destination_paths.backup_file

    with metadata_lock(dst_meta_file):
        dst_doc = load_metadata(dst_meta_file)
        dst_profiles = list(dst_doc.profiles)
        dst_id_map = {p.id: p for p in dst_profiles}
        dst_name_map = {p.name: p for p in dst_profiles}

        to_migrate: List[Profile] = []
        skipped: List[MigrationProfileResult] = []

        for p in source_profiles:
            if p.id in dst_id_map:
                existing = dst_id_map[p.id]
                if existing.name == p.name and existing.created_at == p.created_at:
                    skipped.append(
                        MigrationProfileResult(
                            id=p.id,
                            name=p.name,
                            status="skipped",
                            message="profile already exists identically in destination",
                        )
                    )
                    continue
                raise ConflictError(
                    f"conflict: profile ID '{p.id}' already exists in destination with different attributes"
                )

            if p.name in dst_name_map:
                existing = dst_name_map[p.name]
                if existing.id == p.id and existing.created_at == p.created_at:
                    skipped.append(
                        MigrationProfileResult(
                            id=p.id,
                            name=p.name,
                            status="skipped",
                            message="profile already exists identically in destination",
                        )
                    )
                    continue
                raise ConflictError(
                    f"conflict: profile name '{p.name}' already exists in destination with ID '{existing.id}'"
                )

            to_migrate.append(p)

        migrated: List[MigrationProfileResult] = []
        temp_dirs: List[Tuple[Path, Path]] = []

        try:
            for p in to_migrate:
                src_data_dir = Path(p.data_dir)
                if not src_data_dir.is_absolute():
                    src_data_dir = (src_root / src_data_dir).resolve()

                dst_prof_dir = dst_profiles_dir / p.id
                dst_browser_data = dst_prof_dir / "browser-data"

                if dst_prof_dir.exists():
                    raise ConflictError(
                        f"conflict: destination directory for profile '{p.id}' already exists"
                    )

                temp_dir = dst_profiles_dir / f".temp_migrating_{p.id}_{uuid4().hex}"
                temp_dirs.append((temp_dir, dst_prof_dir))

                temp_browser_data = temp_dir / "browser-data"
                temp_browser_data.mkdir(parents=True, mode=0o700)

                if src_data_dir.exists() and src_data_dir.is_dir():
                    shutil.copytree(src_data_dir, temp_browser_data, dirs_exist_ok=True)
                    if not _verify_directory_contents(src_data_dir, temp_browser_data):
                        raise MigrationError(f"verification failed after copying data for {p.id}")

            for temp_dir, dst_prof_dir in temp_dirs:
                temp_dir.replace(dst_prof_dir)

            new_profiles = list(dst_profiles)
            for p in to_migrate:
                target_data_dir = str(dst_profiles_dir / p.id / "browser-data")
                new_p = Profile(
                    id=p.id,
                    name=p.name,
                    created_at=p.created_at,
                    data_dir=target_data_dir,
                    last_launched_at=p.last_launched_at,
                )
                new_profiles.append(new_p)
                migrated.append(
                    MigrationProfileResult(
                        id=p.id,
                        name=p.name,
                        status="migrated",
                        message="successfully migrated",
                    )
                )

            new_doc = MetadataDocument(
                schema_version=METADATA_SCHEMA_VERSION, profiles=new_profiles
            )
            validate_metadata_document(new_doc.profiles, dst_profiles_dir)
            _backup_metadata(dst_meta_file, dst_backup_file)
            _atomic_write(dst_meta_file, json.dumps(new_doc.to_dict(), indent=2) + "\n")

        except Exception:
            for temp_dir, _ in temp_dirs:
                if temp_dir.exists():
                    shutil.rmtree(temp_dir, ignore_errors=True)
            for _, dst_prof_dir in temp_dirs:
                if dst_prof_dir.exists():
                    shutil.rmtree(dst_prof_dir, ignore_errors=True)
            raise

    source_removed_flag = False
    if remove_source:
        try:
            if profiles_dir.exists():
                shutil.rmtree(profiles_dir, ignore_errors=True)
            if meta_file.exists():
                meta_file.unlink(missing_ok=True)
            bak_file = meta_file.with_suffix(".json.bak")
            if bak_file.exists():
                bak_file.unlink(missing_ok=True)
            source_removed_flag = True
        except OSError as exc:
            raise MigrationError(f"failed to remove source data: {exc}") from exc

    return MigrationReport(
        source_root=str(src_root),
        destination_root=str(dst_root),
        migrated=migrated,
        skipped=skipped,
        failed=[],
        source_removed=source_removed_flag,
    )
