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

from .data_root import DataPaths
from .models import METADATA_SCHEMA_VERSION, Profile, utc_now
from .process_manager import get_status
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

    if not data_dir.exists():
        return files_manifest, total_size

    for root_dir, _, filenames in os.walk(data_dir):
        for fname in sorted(filenames):
            fpath = Path(root_dir) / fname
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
    out_path = Path(output_file).resolve()
    if out_path.exists() and not force:
        raise TargetExistsError(f"output backup archive already exists: {out_path} (use --force to overwrite)")

    for p in profiles:
        status = get_status(p.data_dir, runtime_dir=data_paths.runtime_dir / p.id)
        if status != "stopped":
            raise ProfileNotStoppedError(
                f"cannot backup profile '{p.name}' ({p.id}) because its status is '{status}'. "
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

        with tarfile.open(temp_archive, "r:gz") as verify_tar:
            names = set(verify_tar.getnames())
            if "backup_manifest.json" not in names:
                raise BackupError("backup archive verification failed: missing manifest")
            manifest_file = verify_tar.extractfile("backup_manifest.json")
            if manifest_file is None:
                raise BackupError("backup archive verification failed: unreadable manifest")
            loaded_manifest = json.loads(manifest_file.read().decode("utf-8"))
            if loaded_manifest.get("format_version") != BACKUP_ARCHIVE_SCHEMA_VERSION:
                raise BackupError("backup archive verification failed: invalid format version")

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
