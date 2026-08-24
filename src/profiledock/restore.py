import json
import os
import re
import shutil
import tarfile
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from .data_root import (
    DataPaths,
    DataRootError,
    _is_link,
    ensure_tree_safe,
    ensure_within_root,
    validate_path_component,
)
from .models import METADATA_SCHEMA_VERSION, LaunchConfig, MetadataDocument, Profile, migrate_launch_config
from .process_manager import is_active_for_mutation
from .storage import (
    _atomic_write,
    _backup_metadata,
    _replace_with_retry,
    load_metadata,
    metadata_lock,
)
from .validation import ValidationError, validate_metadata_document, validate_required_fields

MAX_MEMBER_SIZE_BYTES = 5 * 1024 * 1024 * 1024
MAX_TOTAL_EXTRACT_BYTES = 20 * 1024 * 1024 * 1024
MAX_MANIFEST_SIZE_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 100000


class RestoreError(Exception):
    pass


class InvalidArchiveError(RestoreError):
    pass


class DecompressionSecurityError(RestoreError):
    pass


class RestoreConflictError(RestoreError):
    pass


def _restore_quarantines(quarantined: list[tuple[Path, Path]]) -> list[str]:
    """Move quarantined original directories back to their canonical paths.

    Contents were validated by ensure_tree_safe before quarantine, so this
    prioritizes restoring user data over re-validation. Returns human-readable
    descriptions of any directories that could not be restored.
    """
    failures: list[str] = []
    for q_dir, final_dir in reversed(quarantined):
        if q_dir.exists() and not final_dir.exists():
            try:
                _replace_with_retry(q_dir, final_dir)
            except OSError as exc:
                failures.append(f"could not move '{q_dir.name}' back to '{final_dir.name}': {exc}")
    return failures


@dataclass
class RestoreProfileResult:
    id: str
    name: str
    engine: Optional[str]
    status: str
    file_count: int = 0
    total_bytes: int = 0
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RestoreReport:
    archive_path: str
    format_version: int
    profiledock_version: str
    restored: list[RestoreProfileResult]
    skipped: list[RestoreProfileResult]
    total_restored: int
    total_files: int
    total_bytes: int

    def to_dict(self) -> dict[str, Any]:
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
    if (
        not isinstance(member_name, str)
        or not member_name
        or "\x00" in member_name
        or "\\" in member_name
        or any(":" in part for part in member_name.split("/"))
    ):
        raise DecompressionSecurityError(f"archive member contains an unsafe path: {member_name}")
    if member_name.startswith("/") or member_name.startswith("\\"):
        raise DecompressionSecurityError(f"archive member contains absolute path: {member_name}")
    candidate = Path(member_name)
    if candidate.is_absolute() or candidate.drive:
        raise DecompressionSecurityError(f"archive member contains absolute path: {member_name}")
    parts = candidate.parts
    if ".." in parts:
        raise DecompressionSecurityError(f"archive member contains parent traversal: {member_name}")
    return candidate


def _validated_archive_profile(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidArchiveError("manifest profile entries must be JSON objects")
    expected_fields = {
        "id",
        "name",
        "created_at",
        "last_launched_at",
        "engine",
        "launch_config",
        "file_count",
        "total_bytes",
        "files",
    }
    if set(value) != expected_fields:
        raise InvalidArchiveError("manifest profile fields do not match backup format version 1")
    launch_config = value.get("launch_config")
    if launch_config is not None:
        try:
            launch_config = migrate_launch_config(launch_config)
        except ValueError as exc:
            raise InvalidArchiveError(f"invalid launch config in manifest: {exc}") from exc
    profile_value = {
        "id": value.get("id"),
        "name": value.get("name"),
        "created_at": value.get("created_at"),
        "data_dir": "archive-placeholder",
        "last_launched_at": value.get("last_launched_at"),
        "engine": value.get("engine"),
        "launch_config": launch_config,
    }
    try:
        profile = Profile.from_dict(profile_value)
        validate_required_fields(profile)
    except (TypeError, ValueError, ValidationError) as exc:
        raise InvalidArchiveError(f"invalid profile metadata in manifest: {exc}") from exc
    files = value.get("files")
    if not isinstance(files, dict):
        raise InvalidArchiveError(f"manifest files for profile '{profile.id}' must be an object")
    if type(value["file_count"]) is not int or value["file_count"] != len(files):
        raise InvalidArchiveError(f"manifest file count for profile '{profile.id}' is invalid")
    if type(value["total_bytes"]) is not int or value["total_bytes"] < 0:
        raise InvalidArchiveError(f"manifest byte total for profile '{profile.id}' is invalid")
    calculated_bytes = 0
    for relative_path, metadata in files.items():
        if not isinstance(relative_path, str) or not relative_path or relative_path in (".", ".."):
            raise InvalidArchiveError(f"invalid file path in manifest for profile '{profile.id}'")
        _validate_safe_member_path(relative_path)
        if not isinstance(metadata, dict):
            raise InvalidArchiveError(f"invalid file metadata for '{relative_path}'")
        size = metadata.get("size")
        checksum = metadata.get("sha256")
        if type(size) is not int or size < 0 or size > MAX_MEMBER_SIZE_BYTES:
            raise InvalidArchiveError(f"invalid file size for '{relative_path}'")
        calculated_bytes += size
        if not isinstance(checksum, str) or re.fullmatch(r"[0-9a-f]{64}", checksum) is None:
            raise InvalidArchiveError(f"invalid SHA-256 checksum for '{relative_path}'")
    if calculated_bytes != value["total_bytes"]:
        raise InvalidArchiveError(f"manifest byte total for profile '{profile.id}' does not match files")
    normalized = dict(value)
    normalized["id"] = profile.id
    normalized["name"] = profile.name
    normalized["created_at"] = profile.created_at
    normalized["last_launched_at"] = profile.last_launched_at
    normalized["engine"] = profile.engine
    normalized["launch_config"] = (
        profile.launch_config.to_dict() if profile.launch_config is not None else None
    )
    return normalized


def _existing_profile_matches_archive(profile: Profile, archive_profile: dict[str, Any]) -> bool:
    launch_config = profile.launch_config.to_dict() if profile.launch_config is not None else None
    if (
        profile.name != archive_profile["name"]
        or profile.created_at != archive_profile["created_at"]
        or profile.last_launched_at != archive_profile.get("last_launched_at")
        or profile.engine != archive_profile.get("engine")
        or launch_config != archive_profile.get("launch_config")
    ):
        return False
    data_dir = Path(profile.data_dir)
    if not data_dir.is_dir() or _is_link(data_dir):
        return False
    expected_files = archive_profile["files"]
    actual_paths: set[str] = set()
    for root, directories, files in os.walk(data_dir, followlinks=False):
        root_path = Path(root)
        if any(_is_link(root_path / directory) for directory in directories):
            return False
        for filename in files:
            file_path = root_path / filename
            if _is_link(file_path) or not file_path.is_file():
                return False
            relative_path = file_path.relative_to(data_dir).as_posix()
            actual_paths.add(relative_path)
            metadata = expected_files.get(relative_path)
            if metadata is None or file_path.stat().st_size != metadata["size"]:
                return False
            if not _verify_checksum(file_path, metadata["sha256"]):
                return False
    return actual_paths == set(expected_files)


def restore_backup_archive(
    archive_path: Path,
    data_paths: DataPaths,
    overwrite: bool = False,
) -> RestoreReport:
    archive = Path(archive_path).resolve()
    if not archive.exists() or not archive.is_file():
        raise InvalidArchiveError(f"backup archive file does not exist: {archive}")

    try:
        tar = tarfile.open(archive, "r:gz")  # noqa: SIM115 - closed by the `with tar` below
    except Exception as exc:
        raise InvalidArchiveError(f"could not open backup archive: {exc}") from exc

    with tar:
        try:
            manifest_member = tar.getmember("backup_manifest.json")
        except KeyError as exc:
            raise InvalidArchiveError("archive missing required 'backup_manifest.json'") from exc

        if not manifest_member.isfile() or manifest_member.size > MAX_MANIFEST_SIZE_BYTES:
            raise InvalidArchiveError("backup manifest is not a safe regular file")

        manifest_file = tar.extractfile(manifest_member)
        if manifest_file is None:
            raise InvalidArchiveError("backup manifest is unreadable")
        manifest_bytes = manifest_file.read(MAX_MANIFEST_SIZE_BYTES + 1)
        if len(manifest_bytes) > MAX_MANIFEST_SIZE_BYTES:
            raise InvalidArchiveError("backup manifest exceeds the maximum allowed size")
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except Exception as exc:
            raise InvalidArchiveError(f"corrupted manifest in archive: {exc}") from exc

        if not isinstance(manifest, dict):
            raise InvalidArchiveError("backup manifest must be a JSON object")
        expected_manifest_fields = {
            "format_version",
            "profiledock_version",
            "created_at",
            "total_profiles",
            "total_files",
            "total_bytes",
            "profiles",
        }
        if set(manifest) != expected_manifest_fields:
            raise InvalidArchiveError("manifest fields do not match backup format version 1")
        format_version = manifest.get("format_version")
        if type(format_version) is not int or format_version != 1:
            raise InvalidArchiveError(f"unsupported backup archive format version: {format_version}")
        if not isinstance(manifest["profiledock_version"], str) or not isinstance(
            manifest["created_at"], str
        ):
            raise InvalidArchiveError("manifest version and creation time must be strings")
        for field in ("total_profiles", "total_files", "total_bytes"):
            if type(manifest[field]) is not int or manifest[field] < 0:
                raise InvalidArchiveError(f"manifest {field} must be a non-negative integer")
        profiles_data = manifest.get("profiles", [])
        if not isinstance(profiles_data, list):
            raise InvalidArchiveError("manifest profiles must be a list")
        profiles_data = [_validated_archive_profile(profile) for profile in profiles_data]
        if manifest["total_profiles"] != len(profiles_data):
            raise InvalidArchiveError("manifest profile total does not match profile entries")
        if manifest["total_files"] != sum(profile["file_count"] for profile in profiles_data):
            raise InvalidArchiveError("manifest file total does not match profile entries")
        if manifest["total_bytes"] != sum(profile["total_bytes"] for profile in profiles_data):
            raise InvalidArchiveError("manifest byte total does not match profile entries")

        archive_profile_ids: set[str] = set()
        archive_profile_names: set[str] = set()

        for prof in profiles_data:
            pid = prof["id"]
            pname = prof["name"]
            if pid in archive_profile_ids:
                raise InvalidArchiveError(f"duplicate profile id in manifest: {pid}")
            archive_profile_ids.add(pid)
            if pname in archive_profile_names:
                raise InvalidArchiveError(f"duplicate profile name in manifest: {pname}")
            archive_profile_names.add(pname)

        members = tar.getmembers()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise DecompressionSecurityError("archive contains more members than the allowed maximum")
        member_names = [member.name for member in members]
        if len(member_names) != len(set(member_names)):
            raise InvalidArchiveError("archive contains duplicate member names")
        total_extracted_bytes = 0
        regular_members: set[str] = set()
        for member in members:
            if member.islnk() or member.issym():
                raise DecompressionSecurityError(f"archive member is an unsafe link: {member.name}")
            if not member.isfile() and not member.isdir():
                raise DecompressionSecurityError(f"archive member has an unsafe type: {member.name}")
            if member.size > MAX_MEMBER_SIZE_BYTES:
                raise DecompressionSecurityError(
                    f"archive member exceeds maximum allowed size: {member.name}"
                )
            total_extracted_bytes += member.size
            if total_extracted_bytes > MAX_TOTAL_EXTRACT_BYTES:
                raise DecompressionSecurityError(
                    "total archive uncompressed size exceeds maximum allowed threshold"
                )

            _validate_safe_member_path(member.name)
            if member.isfile():
                regular_members.add(member.name)

        expected_members = {"backup_manifest.json"}
        for profile in profiles_data:
            for relative_path, metadata in profile["files"].items():
                member_name = f"profiles/{profile['id']}/browser-data/{relative_path}"
                expected_members.add(member_name)
                try:
                    member = tar.getmember(member_name)
                except KeyError as exc:
                    raise InvalidArchiveError(f"archive missing member for file '{member_name}'") from exc
                if not member.isfile() or member.size != metadata["size"]:
                    raise InvalidArchiveError(f"archive member size does not match manifest: {member_name}")
        unexpected_members = regular_members - expected_members
        if unexpected_members:
            raise InvalidArchiveError(f"archive contains unlisted file: {sorted(unexpected_members)[0]}")

        dst_metadata = data_paths.profiles_file
        dst_profiles_dir = data_paths.profiles_dir
        dst_backup = data_paths.backup_file

        with metadata_lock(dst_metadata):
            current_doc = load_metadata(dst_metadata)
            current_id_map = {p.id: p for p in current_doc.profiles}
            current_name_map = {p.name: p for p in current_doc.profiles}

            to_restore: list[dict[str, Any]] = []
            skipped: list[RestoreProfileResult] = []

            for prof in profiles_data:
                pid = prof["id"]
                pname = prof["name"]
                pengine = prof.get("engine")
                try:
                    validate_path_component(pid, "profile id")
                    expected_profile_dir = ensure_within_root(dst_profiles_dir / pid, data_paths.root)
                    expected_runtime_dir = ensure_within_root(data_paths.runtime_dir / pid, data_paths.root)
                except DataRootError as exc:
                    raise InvalidArchiveError(f"unsafe profile destination for '{pid}': {exc}") from exc
                if pid not in current_id_map and is_active_for_mutation(
                    str(expected_profile_dir / "browser-data"), expected_runtime_dir
                ):
                    raise RestoreConflictError(f"cannot restore active profile state for '{pname}' ({pid})")

                if pid in current_id_map:
                    existing = current_id_map[pid]
                    if not overwrite:
                        if _existing_profile_matches_archive(existing, prof):
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
                    if is_active_for_mutation(
                        existing.data_dir,
                        runtime_dir=data_paths.runtime_dir / existing.id,
                    ):
                        raise RestoreConflictError(
                            f"cannot overwrite running profile '{existing.name}' ({existing.id})"
                        )

                if pname in current_name_map and current_name_map[pname].id != pid:
                    existing_named = current_name_map[pname]
                    raise RestoreConflictError(
                        f"conflict: profile name '{pname}' is already used by profile ID '{existing_named.id}' in destination"
                    )

                to_restore.append(prof)

            temp_restore_root = ensure_within_root(
                dst_profiles_dir / f".temp_restore_{uuid4().hex[:12]}", data_paths.root
            )
            temp_restore_root.mkdir(parents=True, mode=0o700)

            restored_results: list[RestoreProfileResult] = []
            finalized_dirs: list[tuple[Path, Path]] = []

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
                        except KeyError as exc:
                            raise InvalidArchiveError(
                                f"archive missing member for file '{member_path}'"
                            ) from exc

                        target_file_path = temp_browser_data / rel_file_path
                        target_file_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

                        source_file = tar.extractfile(member)
                        if source_file is None:
                            raise InvalidArchiveError(f"archive member is unreadable: {member_path}")
                        with source_file, target_file_path.open("xb") as dst_f:
                            shutil.copyfileobj(source_file, dst_f)

                        if not _verify_checksum(target_file_path, expected_sha):
                            raise InvalidArchiveError(
                                f"checksum verification failed for restored file '{member_path}'"
                            )

                    target_final_prof_dir = dst_profiles_dir / pid
                    ensure_within_root(target_final_prof_dir, data_paths.root)
                    finalized_dirs.append((temp_prof_dir, target_final_prof_dir))

                quarantined_existing: list[tuple[Path, Path]] = []
                try:
                    for _, final_dir in finalized_dirs:
                        if final_dir.exists():
                            ensure_tree_safe(final_dir, data_paths.root)
                            q_dir = ensure_within_root(
                                dst_profiles_dir / f".quarantine_{final_dir.name}_{uuid4().hex[:8]}",
                                data_paths.root,
                            )
                            _replace_with_retry(final_dir, q_dir)
                            quarantined_existing.append((q_dir, final_dir))
                except Exception as exc:
                    rollback_failures = _restore_quarantines(quarantined_existing)
                    if rollback_failures:
                        raise RestoreError(
                            f"restore aborted while quarantining existing profiles ({exc}); "
                            "some original data remains under hidden quarantine directories: "
                            + "; ".join(rollback_failures)
                        ) from exc
                    raise

                try:
                    for temp_dir, final_dir in finalized_dirs:
                        _replace_with_retry(temp_dir, final_dir)

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
                    _backup_metadata(dst_metadata, dst_backup, data_paths.root)
                    _atomic_write(
                        dst_metadata,
                        json.dumps(new_doc.to_dict(), indent=2) + "\n",
                        data_paths.root,
                    )

                    for q_dir, _ in quarantined_existing:
                        try:
                            ensure_tree_safe(q_dir, data_paths.root)
                            shutil.rmtree(q_dir, ignore_errors=False)
                        except (DataRootError, OSError):
                            pass

                except Exception as exc:
                    cleanup_failures: list[str] = []
                    for _temp_dir, final_dir in finalized_dirs:
                        if final_dir.exists():
                            try:
                                ensure_tree_safe(final_dir, data_paths.root)
                                shutil.rmtree(final_dir, ignore_errors=False)
                            except (DataRootError, OSError) as rmtree_exc:
                                cleanup_failures.append(
                                    f"could not remove partially restored '{final_dir.name}': {rmtree_exc}"
                                )
                    rollback_failures = _restore_quarantines(quarantined_existing)
                    if cleanup_failures or rollback_failures:
                        raise RestoreError(
                            f"restore failed ({exc}); rollback issues: "
                            + "; ".join(cleanup_failures + rollback_failures)
                        ) from exc
                    raise

            finally:
                if temp_restore_root.exists():
                    try:
                        ensure_tree_safe(temp_restore_root, data_paths.root)
                        shutil.rmtree(temp_restore_root, ignore_errors=False)
                    except (DataRootError, OSError):
                        pass

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
