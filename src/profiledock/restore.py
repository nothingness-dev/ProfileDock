from dataclasses import asdict, dataclass
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import shutil
import tarfile
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import uuid4

from .data_root import DataPaths
from .models import LaunchConfig, METADATA_SCHEMA_VERSION, MetadataDocument, Profile
from .storage import (
    _atomic_write,
    _backup_metadata,
    load_metadata,
    metadata_lock,
)
from .validation import ValidationError, validate_metadata_document, validate_required_fields
from .version import __version__

MAX_MEMBER_SIZE_BYTES = 5 * 1024 * 1024 * 1024
MAX_TOTAL_EXTRACT_BYTES = 20 * 1024 * 1024 * 1024


class RestoreError(Exception):
    pass


class InvalidArchiveError(RestoreError):
    pass


class DecompressionSecurityError(RestoreError):
    pass


class RestoreConflictError(RestoreError):
    pass


@dataclass
class RestoreProfileResult:
    id: str
    name: str
    engine: Optional[str]
    status: str
    file_count: int = 0
    total_bytes: int = 0
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RestoreReport:
    archive_path: str
    format_version: int
    profiledock_version: str
    restored: List[RestoreProfileResult]
    skipped: List[RestoreProfileResult]
    total_restored: int
    total_files: int
    total_bytes: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "archive_path": self.archive_path,
            "format_version": self.format_version,
            "profiledock_version": self.profiledock_version,
            "total_restored": self.total_restored,
            "total_files": self.total_files,
            "total_bytes": self.total_bytes,
            "restored": [p.to_dict() for p in self.restored],
            "skipped": [p.to_dict() for p in self.skipped],
        }


def _verify_checksum(path: Path, expected_sha256: str) -> bool:
    digest = sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest() == expected_sha256


def _validate_safe_member_path(member_name: str) -> Path:
    if member_name.startswith("/") or member_name.startswith("\\"):
        raise DecompressionSecurityError(f"archive member contains absolute path: {member_name}")
    parts = Path(member_name).parts
    if ".." in parts:
        raise DecompressionSecurityError(f"archive member contains parent traversal: {member_name}")
    return Path(member_name)


def restore_backup_archive(
    archive_path: Path,
    data_paths: DataPaths,
    overwrite: bool = False,
) -> RestoreReport:
    archive = Path(archive_path).resolve()
    if not archive.exists() or not archive.is_file():
        raise InvalidArchiveError(f"backup archive file does not exist: {archive}")

    try:
        tar = tarfile.open(archive, "r:gz")
    except Exception as exc:
        raise InvalidArchiveError(f"could not open backup archive: {exc}") from exc

    with tar:
        try:
            manifest_member = tar.getmember("backup_manifest.json")
        except KeyError:
            raise InvalidArchiveError("archive missing required 'backup_manifest.json'")

        manifest_bytes = tar.extractfile(manifest_member).read()
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except Exception as exc:
            raise InvalidArchiveError(f"corrupted manifest in archive: {exc}") from exc

        format_version = manifest.get("format_version")
        if format_version != 1:
            raise InvalidArchiveError(f"unsupported backup archive format version: {format_version}")

        profiles_data = manifest.get("profiles", [])
        if not isinstance(profiles_data, list):
            raise InvalidArchiveError("manifest profiles must be a list")

        archive_profile_ids: Set[str] = set()
        archive_profile_names: Set[str] = set()

        for prof in profiles_data:
            pid = prof.get("id")
            pname = prof.get("name")
            pcreated = prof.get("created_at")
            if not pid or not pname or not pcreated:
                raise InvalidArchiveError("manifest profile entry missing required fields")
            if pid in archive_profile_ids:
                raise InvalidArchiveError(f"duplicate profile id in manifest: {pid}")
            archive_profile_ids.add(pid)
            if pname in archive_profile_names:
                raise InvalidArchiveError(f"duplicate profile name in manifest: {pname}")
            archive_profile_names.add(pname)

        total_extracted_bytes = 0
        for member in tar.getmembers():
            if member.islnk() or member.issym():
                raise DecompressionSecurityError(f"archive member is an unsafe link: {member.name}")
            if member.size > MAX_MEMBER_SIZE_BYTES:
                raise DecompressionSecurityError(f"archive member exceeds maximum allowed size: {member.name}")
            total_extracted_bytes += member.size
            if total_extracted_bytes > MAX_TOTAL_EXTRACT_BYTES:
                raise DecompressionSecurityError("total archive uncompressed size exceeds maximum allowed threshold")

            _validate_safe_member_path(member.name)

        dst_metadata = data_paths.profiles_file
        dst_profiles_dir = data_paths.profiles_dir
        dst_backup = data_paths.backup_file

        with metadata_lock(dst_metadata):
            current_doc = load_metadata(dst_metadata)
            current_id_map = {p.id: p for p in current_doc.profiles}
            current_name_map = {p.name: p for p in current_doc.profiles}

            to_restore: List[Dict[str, Any]] = []
            skipped: List[RestoreProfileResult] = []

            for prof in profiles_data:
                pid = prof["id"]
                pname = prof["name"]
                pengine = prof.get("engine")

                if pid in current_id_map:
                    existing = current_id_map[pid]
                    if not overwrite:
                        if existing.name == pname and existing.engine == pengine:
                            skipped.append(
                                RestoreProfileResult(
                                    id=pid,
                                    name=pname,
                                    engine=pengine,
                                    status="skipped",
                                    message="identical profile already exists in destination (use --force to overwrite)",
                                )
                            )
                            continue
                        raise RestoreConflictError(
                            f"conflict: profile ID '{pid}' already exists with different attributes in destination"
                        )

                if pname in current_name_map and current_name_map[pname].id != pid:
                    existing_named = current_name_map[pname]
                    raise RestoreConflictError(
                        f"conflict: profile name '{pname}' is already used by profile ID '{existing_named.id}' in destination"
                    )

                to_restore.append(prof)

            temp_restore_root = dst_profiles_dir / f".temp_restore_{uuid4().hex[:12]}"
            temp_restore_root.mkdir(parents=True, mode=0o700)

            restored_results: List[RestoreProfileResult] = []
            finalized_dirs: List[Tuple[Path, Path]] = []

            try:
                for prof in to_restore:
                    pid = prof["id"]
                    pname = prof["name"]
                    pcreated = prof["created_at"]
                    plaunched = prof.get("last_launched_at")
                    pengine = prof.get("engine")
                    files_dict = prof.get("files", {})

                    temp_prof_dir = temp_restore_root / pid
                    temp_browser_data = temp_prof_dir / "browser-data"
                    temp_browser_data.mkdir(parents=True, mode=0o700)

                    for rel_file_path, file_meta in files_dict.items():
                        expected_sha = file_meta.get("sha256")
                        member_path = f"profiles/{pid}/browser-data/{rel_file_path}"
                        try:
                            member = tar.getmember(member_path)
                        except KeyError:
                            raise InvalidArchiveError(f"archive missing member for file '{member_path}'")

                        target_file_path = temp_browser_data / rel_file_path
                        target_file_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

                        with tar.extractfile(member) as src_f, target_file_path.open("wb") as dst_f:
                            shutil.copyfileobj(src_f, dst_f)

                        if expected_sha and not _verify_checksum(target_file_path, expected_sha):
                            raise InvalidArchiveError(
                                f"checksum verification failed for restored file '{member_path}'"
                            )

                    target_final_prof_dir = dst_profiles_dir / pid
                    finalized_dirs.append((temp_prof_dir, target_final_prof_dir))

                quarantined_existing: List[Tuple[Path, Path]] = []
                for _, final_dir in finalized_dirs:
                    if final_dir.exists():
                        q_dir = dst_profiles_dir / f".quarantine_{final_dir.name}_{uuid4().hex[:8]}"
                        final_dir.replace(q_dir)
                        quarantined_existing.append((q_dir, final_dir))

                try:
                    for temp_dir, final_dir in finalized_dirs:
                        temp_dir.replace(final_dir)

                    new_profiles_map = {p.id: p for p in current_doc.profiles}
                    for prof in to_restore:
                        pid = prof["id"]
                        pname = prof["name"]
                        pcreated = prof["created_at"]
                        plaunched = prof.get("last_launched_at")
                        pengine = prof.get("engine")
                        files_dict = prof.get("files", {})

                        prof_bytes = sum(f["size"] for f in files_dict.values())
                        data_dir_str = str((dst_profiles_dir / pid / "browser-data").resolve())

                        launch_cfg = None
                        if "launch_config" in prof and prof["launch_config"] is not None:
                            launch_cfg = LaunchConfig.from_dict(prof["launch_config"])

                        new_p = Profile(
                            id=pid,
                            name=pname,
                            created_at=pcreated,
                            data_dir=data_dir_str,
                            last_launched_at=plaunched,
                            engine=pengine,
                            launch_config=launch_cfg,
                        )

                        new_profiles_map[pid] = new_p

                        restored_results.append(
                            RestoreProfileResult(
                                id=pid,
                                name=pname,
                                engine=pengine,
                                status="restored",
                                file_count=len(files_dict),
                                total_bytes=prof_bytes,
                                message="successfully restored",
                            )
                        )

                    new_doc = MetadataDocument(
                        schema_version=METADATA_SCHEMA_VERSION,
                        profiles=list(new_profiles_map.values()),
                    )
                    validate_metadata_document(new_doc.profiles, dst_profiles_dir)
                    _backup_metadata(dst_metadata, dst_backup)
                    _atomic_write(dst_metadata, json.dumps(new_doc.to_dict(), indent=2) + "\n")

                    for q_dir, _ in quarantined_existing:
                        shutil.rmtree(q_dir, ignore_errors=True)

                except Exception:
                    for temp_dir, final_dir in finalized_dirs:
                        if final_dir.exists():
                            shutil.rmtree(final_dir, ignore_errors=True)
                    for q_dir, final_dir in quarantined_existing:
                        if q_dir.exists():
                            q_dir.replace(final_dir)
                    raise

            finally:
                if temp_restore_root.exists():
                    shutil.rmtree(temp_restore_root, ignore_errors=True)

    return RestoreReport(
        archive_path=str(archive),
        format_version=format_version,
        profiledock_version=manifest.get("profiledock_version", "unknown"),
        restored=restored_results,
        skipped=skipped,
        total_restored=len(restored_results),
        total_files=sum(p.file_count for p in restored_results),
        total_bytes=sum(p.total_bytes for p in restored_results),
    )
