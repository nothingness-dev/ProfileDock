from dataclasses import asdict, dataclass
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import time
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import uuid4

from .data_root import DataPaths, DataRootError, _is_link, ensure_within_root, validate_path_component
from .models import METADATA_SCHEMA_VERSION, Profile, utc_now
from .process_manager import is_active_for_mutation
from .version import __version__

BACKUP_ARCHIVE_SCHEMA_VERSION = 1


class BackupError(Exception):
    pass


class ProfileNotStoppedError(BackupError):
    pass


class FileLockedError(BackupError):
    pass


class TargetExistsError(BackupError):
    pass


@dataclass
class BackupProfileResult:
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
class BackupReport:
    output_path: str
    format_version: int
    profiledock_version: str
    created_at: str
    profiles: List[BackupProfileResult]
    total_profiles: int
    total_files: int
    total_bytes: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "output_path": self.output_path,
            "format_version": self.format_version,
            "profiledock_version": self.profiledock_version,
            "created_at": self.created_at,
            "total_profiles": self.total_profiles,
            "total_files": self.total_files,
            "total_bytes": self.total_bytes,
            "profiles": [p.to_dict() for p in self.profiles],
        }


def _hash_file(path: Path) -> str:
    digest = sha256()
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
    except PermissionError as exc:
        raise FileLockedError(
            f"cannot read locked file '{path}': {exc}. "
            "A background browser process or application may still be holding the file open. "
            "Please ensure all browser processes and background apps are completely closed."
        ) from exc
    return digest.hexdigest()


def _is_runtime_or_log_file(rel_path_str: str) -> bool:
    name = Path(rel_path_str).name
    if name in ("running.json", "controller.error", "profiles.lock"):
        return True
    if name.endswith(".tmp"):
        return True
    return False


def _collect_profile_files(
    data_dir: Path,
) -> Tuple[Dict[str, Tuple[int, str]], int]:
    files_manifest: Dict[str, Tuple[int, str]] = {}
    total_size = 0

    if not data_dir.is_dir() or _is_link(data_dir):
        raise BackupError(f"profile data directory is missing or unsafe: {data_dir}")

    for root_dir, directory_names, filenames in os.walk(data_dir, followlinks=False):
        root_path = Path(root_dir)
        for directory_name in list(directory_names):
            directory = root_path / directory_name
            if _is_link(directory) or not directory.is_dir():
                raise BackupError(f"profile data contains an unsafe directory: {directory}")
        for fname in sorted(filenames):
            fpath = root_path / fname
            if _is_link(fpath) or not fpath.is_file():
                raise BackupError(f"profile data contains an unsafe file: {fpath}")
            rel_path = fpath.relative_to(data_dir).as_posix()
            if _is_runtime_or_log_file(rel_path):
                continue
            try:
                size = fpath.stat().st_size
                checksum = _hash_file(fpath)
                files_manifest[rel_path] = (size, checksum)
                total_size += size
            except PermissionError as exc:
                raise FileLockedError(
                    f"cannot read locked file '{fpath}': {exc}. "
                    "A background browser process or application may still be holding the file open. "
                    "Please ensure all browser processes and background apps are completely closed."
                ) from exc

    return files_manifest, total_size


def create_backup_archive(
    profiles: List[Profile],
    data_paths: DataPaths,
    output_file: Path,
    force: bool = False,
) -> BackupReport:
    requested_output = Path(output_file).expanduser()
    if _is_link(requested_output):
        raise BackupError(f"backup output cannot be a link or reparse point: {requested_output}")
    out_path = requested_output.resolve()
    if out_path.exists() and not force:
        raise TargetExistsError(f"output backup archive already exists: {out_path} (use --force to overwrite)")

    for p in profiles:
        try:
            validate_path_component(p.id, "profile id")
            profile_data = ensure_within_root(Path(p.data_dir), data_paths.root)
            expected_data = ensure_within_root(
                data_paths.profiles_dir / p.id / "browser-data", data_paths.root
            )
        except DataRootError as exc:
            raise BackupError(f"unsafe profile path for '{p.id}': {exc}") from exc
        if profile_data != expected_data:
            raise BackupError(f"profile data directory is outside its managed location: {p.id}")
        try:
            out_path.relative_to(profile_data)
        except ValueError:
            pass
        else:
            raise BackupError("backup output cannot be inside a profile browser-data directory")
        if is_active_for_mutation(p.data_dir, data_paths.runtime_dir / p.id):
            raise ProfileNotStoppedError(
                f"cannot backup profile '{p.name}' ({p.id}) because it is active. "
                "All profiles must be stopped before creating a backup."
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp_archive = out_path.with_name(f".backup_tmp_{uuid4().hex[:12]}.tar.gz")

    created_timestamp = utc_now()
    profile_results: List[BackupProfileResult] = []
    manifest_profiles: List[Dict[str, Any]] = []

    grand_total_files = 0
    grand_total_bytes = 0

    try:
        with tarfile.open(temp_archive, "w:gz") as tar:
            for p in profiles:
                p_data_dir = Path(p.data_dir)
                files_manifest, p_bytes = _collect_profile_files(p_data_dir)

                for rel_path, (size, checksum) in files_manifest.items():
                    fpath = p_data_dir / rel_path
                    if _is_link(fpath) or not fpath.is_file():
                        raise BackupError(f"profile data changed to an unsafe file during backup: {fpath}")
                    try:
                        fpath.resolve().relative_to(p_data_dir.resolve())
                    except ValueError as exc:
                        raise BackupError(f"profile data escaped during backup: {fpath}") from exc
                    arcname = f"profiles/{p.id}/browser-data/{rel_path}"
                    try:
                        tar.add(str(fpath), arcname=arcname)
                    except PermissionError as exc:
                        raise FileLockedError(
                            f"cannot read locked file '{fpath}': {exc}. "
                            "A background browser process or application may still be holding the file open. "
                            "Please ensure all browser processes and background apps are completely closed."
                        ) from exc

                profile_info = {
                    "id": p.id,
                    "name": p.name,
                    "created_at": p.created_at,
                    "last_launched_at": p.last_launched_at,
                    "engine": p.engine,
                    "launch_config": p.launch_config.to_dict() if p.launch_config else None,
                    "file_count": len(files_manifest),
                    "total_bytes": p_bytes,
                    "files": {
                        rel_path: {"size": size, "sha256": checksum}
                        for rel_path, (size, checksum) in files_manifest.items()
                    },
                }

                manifest_profiles.append(profile_info)

                grand_total_files += len(files_manifest)
                grand_total_bytes += p_bytes

                profile_results.append(
                    BackupProfileResult(
                        id=p.id,
                        name=p.name,
                        engine=p.engine,
                        status="backed_up",
                        file_count=len(files_manifest),
                        total_bytes=p_bytes,
                        message="successfully backed up",
                    )
                )

            manifest_document = {
                "format_version": BACKUP_ARCHIVE_SCHEMA_VERSION,
                "profiledock_version": __version__,
                "created_at": created_timestamp,
                "total_profiles": len(profiles),
                "total_files": grand_total_files,
                "total_bytes": grand_total_bytes,
                "profiles": manifest_profiles,
            }

            manifest_bytes = json.dumps(manifest_document, indent=2).encode("utf-8")
            tar_info = tarfile.TarInfo(name="backup_manifest.json")
            tar_info.size = len(manifest_bytes)
            tar_info.mtime = int(time.time())
            tar.addfile(tar_info, io.BytesIO(manifest_bytes))

        for p in profiles:
            if is_active_for_mutation(p.data_dir, data_paths.runtime_dir / p.id):
                raise ProfileNotStoppedError(
                    f"cannot finalize backup because profile '{p.name}' ({p.id}) became active"
                )

        with tarfile.open(temp_archive, "r:gz") as verify_tar:
            names = verify_tar.getnames()
            if len(names) != len(set(names)):
                raise BackupError("backup archive verification failed: duplicate member names")
            if "backup_manifest.json" not in names:
                raise BackupError("backup archive verification failed: missing manifest")
            manifest_file = verify_tar.extractfile("backup_manifest.json")
            if manifest_file is None:
                raise BackupError("backup archive verification failed: unreadable manifest")
            loaded_manifest = json.loads(manifest_file.read().decode("utf-8"))
            if loaded_manifest.get("format_version") != BACKUP_ARCHIVE_SCHEMA_VERSION:
                raise BackupError("backup archive verification failed: invalid format version")
            for profile_info in loaded_manifest.get("profiles", []):
                for rel_path, file_meta in profile_info.get("files", {}).items():
                    member_name = f"profiles/{profile_info['id']}/browser-data/{rel_path}"
                    member = verify_tar.getmember(member_name)
                    if not member.isfile():
                        raise BackupError(f"backup archive verification failed: unsafe member {member_name}")
                    extracted = verify_tar.extractfile(member)
                    if extracted is None:
                        raise BackupError(f"backup archive verification failed: unreadable {member_name}")
                    digest = sha256()
                    size = 0
                    with extracted:
                        while True:
                            chunk = extracted.read(1024 * 1024)
                            if not chunk:
                                break
                            size += len(chunk)
                            digest.update(chunk)
                    if size != file_meta["size"] or digest.hexdigest() != file_meta["sha256"]:
                        raise BackupError(f"backup archive verification failed: checksum mismatch for {member_name}")

        temp_archive.replace(out_path)

    except Exception:
        if temp_archive.exists():
            temp_archive.unlink(missing_ok=True)
        raise

    return BackupReport(
        output_path=str(out_path),
        format_version=BACKUP_ARCHIVE_SCHEMA_VERSION,
        profiledock_version=__version__,
        created_at=created_timestamp,
        profiles=profile_results,
        total_profiles=len(profiles),
        total_files=grand_total_files,
        total_bytes=grand_total_bytes,
    )
