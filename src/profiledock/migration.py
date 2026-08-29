import json
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from .backup import _is_runtime_or_log_file
from .data_root import (
    DataPaths,
    DataRootError,
    _is_link,
    ensure_tree_safe,
    ensure_within_root,
    validate_path_component,
)
from .fsops import replace_with_retry as _replace_with_retry
from .fsops import sha256_file
from .models import METADATA_SCHEMA_VERSION, MetadataDocument, Profile, migrate_metadata_value
from .process_manager import _alive, is_active_for_mutation
from .storage import (
    _atomic_write,
    _backup_metadata,
    load_metadata,
    metadata_lock,
)
from .validation import ValidationError, validate_metadata_document


class MigrationError(Exception):
    pass


class SourceRunningError(MigrationError):
    pass


class ConflictError(MigrationError):
    pass


@dataclass(frozen=True)
class SourceLayout:
    root: Path
    metadata_file: Path
    profiles_dir: Path
    runtime_dir: Optional[Path]
    backup_file: Path


@dataclass
class MigrationProfileResult:
    id: str
    name: str
    status: str
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MigrationReport:
    source_root: str
    destination_root: str
    migrated: list[MigrationProfileResult]
    skipped: list[MigrationProfileResult]
    failed: list[MigrationProfileResult]
    source_removed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_root": self.source_root,
            "destination_root": self.destination_root,
            "migrated": [profile.to_dict() for profile in self.migrated],
            "skipped": [profile.to_dict() for profile in self.skipped],
            "failed": [profile.to_dict() for profile in self.failed],
            "source_removed": self.source_removed,
        }


def failure_report(source_root: Path, destination_root: Path, message: str) -> MigrationReport:
    return MigrationReport(
        source_root=str(source_root.resolve(strict=False)),
        destination_root=str(destination_root.resolve(strict=False)),
        migrated=[],
        skipped=[],
        failed=[MigrationProfileResult(id="", name="", status="failed", message=message)],
    )


def _detect_source_layout(source_root: Path) -> SourceLayout:
    selected_root = Path(source_root)
    if _is_link(selected_root):
        raise MigrationError("source project must be a real directory")
    root = selected_root.resolve(strict=True)
    if not root.is_dir() or _is_link(root):
        raise MigrationError("source project must be a real directory")
    versioned_metadata = root / "metadata" / "profiles.json"
    legacy_metadata = root / "profiles.json"
    runtime_dir: Optional[Path]
    if versioned_metadata.exists() and legacy_metadata.exists():
        raise MigrationError("source contains both legacy and application-data metadata")
    if versioned_metadata.exists():
        metadata_file = versioned_metadata
        runtime_dir = root / "runtime"
        backup_file = root / "backups" / "profiles.json.bak"
    elif legacy_metadata.exists():
        metadata_file = legacy_metadata
        runtime_dir = root / "runtime" if (root / "runtime").exists() else None
        backup_file = root / "profiles.json.bak"
    else:
        raise MigrationError(f"no ProfileDock metadata found in {source_root}")
    profiles_dir = root / "profiles"
    if _is_link(metadata_file) or not metadata_file.is_file():
        raise MigrationError("source profiles.json must be a real file")
    if not profiles_dir.is_dir() or _is_link(profiles_dir):
        raise MigrationError("source profiles directory is missing or unsafe")
    if (
        runtime_dir is not None
        and runtime_dir.exists()
        and (not runtime_dir.is_dir() or _is_link(runtime_dir))
    ):
        raise MigrationError("source runtime directory is unsafe")
    try:
        ensure_within_root(metadata_file, root)
        ensure_within_root(profiles_dir, root)
        ensure_within_root(backup_file, root)
        if runtime_dir is not None:
            ensure_within_root(runtime_dir, root)
    except DataRootError as exc:
        raise MigrationError(f"source layout escapes its root: {exc}") from exc
    return SourceLayout(root, metadata_file, profiles_dir, runtime_dir, backup_file)


def _load_source_profiles(layout: SourceLayout, metadata_bytes: bytes) -> list[Profile]:
    try:
        data = json.loads(metadata_bytes.decode("utf-8"))
        profiles = MetadataDocument.from_dict(migrate_metadata_value(data)).profiles
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise MigrationError(f"source metadata corrupted: {exc}") from exc
    normalized = []
    names: set[str] = set()
    for profile in profiles:
        try:
            validate_path_component(profile.id, "profile id")
        except DataRootError as exc:
            raise MigrationError(f"invalid source profile id: {profile.id}") from exc
        source_path = Path(profile.data_dir)
        if not source_path.is_absolute():
            source_path = layout.root / source_path
        source_path = source_path.resolve(strict=False)
        expected = (layout.profiles_dir / profile.id / "browser-data").resolve(strict=False)
        if source_path != expected:
            raise MigrationError(
                f"invalid source profile {profile.id}: data directory must match profiles/<id>/browser-data"
            )
        if profile.name in names:
            raise ConflictError(f"conflict: duplicate source profile name '{profile.name}'")
        names.add(profile.name)
        normalized.append(
            Profile(
                id=profile.id,
                name=profile.name,
                created_at=profile.created_at,
                data_dir=str(source_path),
                last_launched_at=profile.last_launched_at,
                engine=profile.engine,
                launch_config=profile.launch_config,
            )
        )
    try:
        validate_metadata_document(normalized, layout.profiles_dir)
    except ValidationError as exc:
        raise MigrationError(f"source metadata corrupted: {exc}") from exc
    for profile in normalized:
        data_dir = Path(profile.data_dir)
        if not data_dir.is_dir() or _is_link(data_dir):
            raise MigrationError(f"source profile data directory is missing or unsafe: {profile.id}")
    return normalized


def _paths_overlap(first: Path, second: Path) -> bool:
    try:
        first.relative_to(second)
        return True
    except ValueError:
        pass
    try:
        second.relative_to(first)
        return True
    except ValueError:
        return False


def _source_state_paths(layout: SourceLayout, profile_id: str) -> list[Path]:
    paths = [layout.profiles_dir / profile_id / "running.json"]
    if layout.runtime_dir is not None:
        paths.append(layout.runtime_dir / profile_id / "running.json")
    return paths


def _source_profile_running(layout: SourceLayout, profile_id: str) -> bool:
    validate_path_component(profile_id, "profile id")
    data_dir = layout.profiles_dir / profile_id / "browser-data"
    for path in _source_state_paths(layout, profile_id):
        if is_active_for_mutation(str(data_dir), path.parent):
            return True
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if not isinstance(state, dict):
            continue
        for field in ("controller_pid", "pid", "launcher_pid"):
            pid = state.get(field)
            if type(pid) is int and pid > 0 and _alive(pid):
                return True
    return False


def _directory_manifest(root: Path) -> tuple[set[str], dict[str, tuple[int, str]]]:
    directories: set[str] = set()
    files: dict[str, tuple[int, str]] = {}
    for current, directory_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        relative_current = current_path.relative_to(root)
        directories.add(relative_current.as_posix())
        for name in directory_names:
            directory = current_path / name
            if _is_link(directory) or not directory.is_dir():
                raise MigrationError(f"source profile contains unsafe directory: {directory}")
        for name in file_names:
            file_path = current_path / name
            if _is_link(file_path) or not file_path.is_file():
                raise MigrationError(f"source profile contains unsafe file: {file_path}")
            relative_file = file_path.relative_to(root).as_posix()
            # Runtime leftovers (running.json, controller.error, *.tmp) are
            # transient state, matching backup's exclusion set; migrating them
            # could make the destination profile appear falsely active.
            if _is_runtime_or_log_file(relative_file):
                continue
            files[relative_file] = (file_path.stat().st_size, sha256_file(file_path))
    return directories, files


def _identical_profile(source: Profile, destination: Profile) -> bool:
    return (
        source.id == destination.id
        and source.name == destination.name
        and source.created_at == destination.created_at
        and source.last_launched_at == destination.last_launched_at
        and source.engine == destination.engine
        and source.launch_config == destination.launch_config
    )


def _check_running_profiles(layout: SourceLayout, profiles: list[Profile]) -> None:
    for profile in profiles:
        if _source_profile_running(layout, profile.id):
            raise SourceRunningError(
                f"cannot migrate while profile '{profile.name}' ({profile.id}) is running"
            )


def _validate_removal_scope(layout: SourceLayout, profiles: list[Profile]) -> None:
    known_ids = {profile.id for profile in profiles}
    profile_entries = {entry.name for entry in layout.profiles_dir.iterdir()}
    unknown_profiles = sorted(profile_entries - known_ids)
    if unknown_profiles:
        raise MigrationError(
            "refusing to remove source with untracked profile entries: " + ", ".join(unknown_profiles)
        )
    if layout.runtime_dir is not None and layout.runtime_dir.exists():
        runtime_entries = {entry.name for entry in layout.runtime_dir.iterdir()}
        unknown_runtime = sorted(runtime_entries - known_ids)
        if unknown_runtime:
            raise MigrationError(
                "refusing to remove source with untracked runtime entries: " + ", ".join(unknown_runtime)
            )


def _remove_source(layout: SourceLayout, profiles: list[Profile]) -> None:
    _validate_removal_scope(layout, profiles)
    staged: list[tuple[Path, Path, bool]] = []
    for profile in profiles:
        profile_dir = layout.profiles_dir / profile.id
        if profile_dir.exists():
            ensure_tree_safe(profile_dir, layout.root)
            staged.append(
                (
                    profile_dir,
                    ensure_within_root(
                        layout.profiles_dir / f".removing-{profile.id}-{uuid4().hex}",
                        layout.root,
                    ),
                    True,
                )
            )
        if layout.runtime_dir is not None:
            runtime_profile = layout.runtime_dir / profile.id
            if runtime_profile.exists():
                ensure_tree_safe(runtime_profile, layout.root)
                staged.append(
                    (
                        runtime_profile,
                        ensure_within_root(
                            layout.runtime_dir / f".removing-{profile.id}-{uuid4().hex}",
                            layout.root,
                        ),
                        True,
                    )
                )
    ensure_within_root(layout.metadata_file, layout.root)
    staged.append(
        (
            layout.metadata_file,
            ensure_within_root(
                layout.metadata_file.with_name(f".{layout.metadata_file.name}.removing-{uuid4().hex}"),
                layout.root,
            ),
            False,
        )
    )
    if layout.backup_file.exists():
        ensure_within_root(layout.backup_file, layout.root)
        staged.append(
            (
                layout.backup_file,
                ensure_within_root(
                    layout.backup_file.with_name(f".{layout.backup_file.name}.removing-{uuid4().hex}"),
                    layout.root,
                ),
                False,
            )
        )
    moved: list[tuple[Path, Path, bool]] = []
    try:
        for original, quarantine, is_directory in staged:
            original.replace(quarantine)
            moved.append((original, quarantine, is_directory))
    except Exception:
        for original, quarantine, _ in reversed(moved):
            if quarantine.exists() and not original.exists():
                quarantine.replace(original)
        raise
    for _, quarantine, is_directory in moved:
        if is_directory:
            try:
                ensure_tree_safe(quarantine, layout.root)
                shutil.rmtree(quarantine, ignore_errors=False)
            except (DataRootError, OSError):
                pass
        else:
            try:
                ensure_within_root(quarantine, layout.root)
                quarantine.unlink(missing_ok=True)
            except (DataRootError, OSError):
                pass
    for directory in (layout.profiles_dir, layout.runtime_dir):
        if directory is not None and directory.exists():
            try:
                directory.rmdir()
            except OSError:
                pass


def migrate_project(
    source_root: Path,
    destination_paths: DataPaths,
    remove_source: bool = False,
) -> MigrationReport:
    try:
        layout = _detect_source_layout(source_root)
    except (OSError, RuntimeError) as exc:
        raise MigrationError(f"cannot resolve source project: {exc}") from exc
    destination_root = destination_paths.root.resolve()
    if _paths_overlap(layout.root, destination_root):
        raise MigrationError("source and destination projects cannot overlap")
    try:
        metadata_snapshot = layout.metadata_file.read_bytes()
        source_profiles = _load_source_profiles(layout, metadata_snapshot)
        _check_running_profiles(layout, source_profiles)
        if remove_source:
            _validate_removal_scope(layout, source_profiles)
        manifests = {profile.id: _directory_manifest(Path(profile.data_dir)) for profile in source_profiles}
    except MigrationError:
        raise
    except OSError as exc:
        raise MigrationError(f"cannot read source project: {exc}") from exc
    destination_metadata = destination_paths.profiles_file
    destination_profiles = destination_paths.profiles_dir
    destination_backup = destination_paths.backup_file
    migrated: list[MigrationProfileResult] = []
    skipped: list[MigrationProfileResult] = []

    with metadata_lock(destination_metadata):
        destination_document = load_metadata(destination_metadata)
        existing_profiles = list(destination_document.profiles)
        profiles_by_id = {profile.id: profile for profile in existing_profiles}
        profiles_by_name: dict[str, list[Profile]] = {}
        for existing_profile in existing_profiles:
            profiles_by_name.setdefault(existing_profile.name, []).append(existing_profile)
        to_migrate: list[Profile] = []

        for profile in source_profiles:
            existing_by_id = profiles_by_id.get(profile.id)
            if existing_by_id is not None:
                conflicting_names = [
                    existing
                    for existing in profiles_by_name.get(profile.name, [])
                    if existing.id != profile.id
                ]
                if conflicting_names:
                    raise ConflictError(
                        f"conflict: profile name '{profile.name}' is also used by ID '{conflicting_names[0].id}'"
                    )
                if not _identical_profile(profile, existing_by_id):
                    raise ConflictError(
                        f"conflict: profile ID '{profile.id}' already exists with different metadata"
                    )
                destination_data = destination_profiles / profile.id / "browser-data"
                if not destination_data.is_dir() or _is_link(destination_data):
                    raise ConflictError(
                        f"conflict: destination data for profile '{profile.id}' is missing or unsafe"
                    )
                if _directory_manifest(destination_data) != manifests[profile.id]:
                    raise ConflictError(
                        f"conflict: destination data for profile '{profile.id}' differs from source"
                    )
                skipped.append(
                    MigrationProfileResult(
                        id=profile.id,
                        name=profile.name,
                        status="skipped",
                        message="identical profile already exists in destination",
                    )
                )
                continue
            existing_by_name = profiles_by_name.get(profile.name, [])
            if existing_by_name:
                raise ConflictError(
                    f"conflict: profile name '{profile.name}' already exists with ID '{existing_by_name[0].id}'"
                )
            to_migrate.append(profile)

        temporary_directories: list[tuple[Path, Path]] = []
        finalized_directories: list[Path] = []
        try:
            stale_temporary = list(destination_profiles.glob(".m-*"))
            for profile in to_migrate:
                stale_temporary.extend(destination_profiles.glob(f".temp_migrating_{profile.id}_*"))
            if stale_temporary:
                raise ConflictError("conflict: incomplete destination migration exists")
            for profile in to_migrate:
                validate_path_component(profile.id, "profile id")
                final_profile = ensure_within_root(destination_profiles / profile.id, destination_root)
                destination_runtime = ensure_within_root(
                    destination_paths.runtime_dir / profile.id, destination_root
                )
                if is_active_for_mutation(str(final_profile / "browser-data"), destination_runtime):
                    raise ConflictError(
                        f"conflict: destination runtime state for profile '{profile.id}' is active"
                    )
                if final_profile.exists() or _is_link(final_profile):
                    raise ConflictError(
                        f"conflict: destination directory for profile '{profile.id}' already exists"
                    )
                temporary = ensure_within_root(
                    destination_profiles / f".m-{uuid4().hex[:12]}", destination_root
                )
                temporary.mkdir(mode=0o700)
                temporary_data = temporary / "browser-data"
                temporary_directories.append((temporary, final_profile))
                shutil.copytree(
                    Path(profile.data_dir),
                    temporary_data,
                    ignore=shutil.ignore_patterns(
                        "running.json", "controller.error", "profiles.lock", "*.tmp"
                    ),
                )
                if _directory_manifest(temporary_data) != manifests[profile.id]:
                    raise MigrationError(f"verification failed after copying data for {profile.id}")

            _check_running_profiles(layout, source_profiles)
            if layout.metadata_file.read_bytes() != metadata_snapshot:
                raise MigrationError("source metadata changed during migration")
            for profile in source_profiles:
                if _directory_manifest(Path(profile.data_dir)) != manifests[profile.id]:
                    raise MigrationError(f"source profile data changed during migration: {profile.id}")

            for temporary, final_profile in temporary_directories:
                _replace_with_retry(temporary, final_profile)
                finalized_directories.append(final_profile)

            new_profiles = list(existing_profiles)
            for profile in to_migrate:
                new_profile = Profile(
                    id=profile.id,
                    name=profile.name,
                    created_at=profile.created_at,
                    data_dir=str(destination_profiles / profile.id / "browser-data"),
                    last_launched_at=profile.last_launched_at,
                    engine=profile.engine,
                    launch_config=profile.launch_config,
                )
                new_profiles.append(new_profile)
                migrated.append(
                    MigrationProfileResult(
                        id=profile.id,
                        name=profile.name,
                        status="migrated",
                        message="successfully migrated",
                    )
                )
            new_document = MetadataDocument(
                schema_version=METADATA_SCHEMA_VERSION,
                profiles=new_profiles,
            )
            if to_migrate:
                validate_metadata_document(new_document.profiles, destination_profiles)
                _backup_metadata(destination_metadata, destination_backup, destination_root)
                _atomic_write(
                    destination_metadata,
                    json.dumps(new_document.to_dict(), indent=2) + "\n",
                    destination_root,
                )
        except Exception as exc:
            for temporary, _ in temporary_directories:
                if temporary.exists():
                    ensure_tree_safe(temporary, destination_root)
                    shutil.rmtree(temporary, ignore_errors=True)
            for final_profile in finalized_directories:
                if final_profile.exists():
                    ensure_tree_safe(final_profile, destination_root)
                    shutil.rmtree(final_profile, ignore_errors=True)
            if isinstance(exc, (MigrationError, ValidationError)):
                raise
            if isinstance(exc, OSError):
                raise MigrationError(f"migration failed: {exc}") from exc
            raise

    source_removed = False
    if remove_source:
        try:
            _check_running_profiles(layout, source_profiles)
            if layout.metadata_file.read_bytes() != metadata_snapshot:
                raise MigrationError("source metadata changed before removal")
            _remove_source(layout, source_profiles)
            source_removed = True
        except MigrationError:
            raise
        except OSError as exc:
            raise MigrationError(f"failed to remove source data: {exc}") from exc

    return MigrationReport(
        source_root=str(layout.root),
        destination_root=str(destination_root),
        migrated=migrated,
        skipped=skipped,
        failed=[],
        source_removed=source_removed,
    )
