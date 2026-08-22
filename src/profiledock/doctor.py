from dataclasses import asdict, dataclass
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

from .models import METADATA_SCHEMA_VERSION, MetadataDocument, Profile, utc_now
from .data_root import DataPaths, _is_link, ensure_tree_safe, ensure_within_root, validate_path_component
from .process_manager import _system_browser_executable, get_status, is_active_for_mutation
from .storage import (
    MetadataCorruptedError,
    MetadataLockedError,
    StorageError,
    _atomic_write,
    _backup_metadata,
    _is_bare_array,
    _is_versioned_document,
    _load_profiles_from_bare_array,
    _read_json_file,
    load_metadata,
    metadata_lock,
)
from .validation import ValidationError, validate_metadata_document
from .version import __version__

STATUS_OK = "ok"
STATUS_WARNING = "warning"
STATUS_FAILED = "failed"


@dataclass
class DiagnosticCheck:
    id: str
    status: str
    summary: str
    action: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "id": self.id,
            "status": self.status,
            "summary": self.summary,
        }
        if self.action is not None:
            data["action"] = self.action
        return data


def check_python_version() -> DiagnosticCheck:
    check_id = "python_version"
    ver = sys.version_info
    current_str = f"{ver[0]}.{ver[1]}.{ver[2]}"
    if ver >= (3, 9):
        return DiagnosticCheck(
            id=check_id,
            status=STATUS_OK,
            summary=f"Python version is {current_str} (>= 3.9 required).",
        )
    return DiagnosticCheck(
        id=check_id,
        status=STATUS_FAILED,
        summary=f"Python version {current_str} is unsupported (< 3.9).",
        action="Upgrade Python to 3.9 or newer.",
    )


def check_data_root_writable(root: Path) -> DiagnosticCheck:
    check_id = "writable_data_root"
    test_file = root / f".doctor_write_test_{os.getpid()}"
    try:
        root.mkdir(parents=True, exist_ok=True)
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
        return DiagnosticCheck(
            id=check_id,
            status=STATUS_OK,
            summary=f"Data root directory is writable: {root}",
        )
    except (OSError, PermissionError) as exc:
        return DiagnosticCheck(
            id=check_id,
            status=STATUS_FAILED,
            summary=f"Data root directory is not writable ({root}): {exc}",
            action="Check folder permissions for the ProfileDock data root.",
        )


def check_metadata_schema(root: Path) -> DiagnosticCheck:
    check_id = "metadata_schema"
    paths = DataPaths.from_root(root)
    profiles_file = paths.profiles_file
    if not profiles_file.exists():
        return DiagnosticCheck(
            id=check_id,
            status=STATUS_OK,
            summary="profiles.json does not exist yet (will be created on first profile creation).",
        )
    try:
        data = _read_json_file(profiles_file)
        if _is_versioned_document(data):
            doc = MetadataDocument.from_dict(data)
            profiles_dir = paths.profiles_dir
            validate_metadata_document(doc.profiles, profiles_dir)
            return DiagnosticCheck(
                id=check_id,
                status=STATUS_OK,
                summary=f"Valid metadata document (schema_version {doc.schema_version}, {len(doc.profiles)} profile(s)).",
            )
        if _is_bare_array(data):
            profiles = _load_profiles_from_bare_array(data)
            profiles_dir = paths.profiles_dir
            validate_metadata_document(profiles, profiles_dir)
            return DiagnosticCheck(
                id=check_id,
                status=STATUS_WARNING,
                summary="Metadata is in legacy bare-array format.",
                action="Run 'profiledock doctor --repair' to migrate to versioned format.",
            )
        return DiagnosticCheck(
            id=check_id,
            status=STATUS_FAILED,
            summary="Metadata format is unrecognized.",
            action="Restore metadata from profiles.json.bak or recreate profiles.json.",
        )
    except (MetadataCorruptedError, ValidationError, ValueError, StorageError) as exc:
        return DiagnosticCheck(
            id=check_id,
            status=STATUS_FAILED,
            summary=f"Metadata is invalid or corrupted: {exc}",
            action="Restore metadata from profiles.json.bak or run 'profiledock doctor --repair'.",
        )


def check_metadata_backup_state(root: Path) -> DiagnosticCheck:
    check_id = "metadata_backup_state"
    paths = DataPaths.from_root(root)
    profiles_file = paths.profiles_file
    backup_file = paths.backup_file
    if not profiles_file.exists() and not backup_file.exists():
        return DiagnosticCheck(
            id=check_id,
            status=STATUS_OK,
            summary="No metadata or backup files exist yet.",
        )
    if not backup_file.exists():
        return DiagnosticCheck(
            id=check_id,
            status=STATUS_OK,
            summary="No backup file found (a backup is created on metadata modifications).",
        )
    try:
        data = _read_json_file(backup_file)
        if _is_versioned_document(data):
            doc = MetadataDocument.from_dict(data)
            validate_metadata_document(doc.profiles, paths.profiles_dir)
            return DiagnosticCheck(
                id=check_id,
                status=STATUS_OK,
                summary="Metadata backup is valid and intact.",
            )
        if _is_bare_array(data):
            profiles = _load_profiles_from_bare_array(data)
            validate_metadata_document(profiles, paths.profiles_dir)
            return DiagnosticCheck(
                id=check_id,
                status=STATUS_OK,
                summary="Metadata backup is valid legacy array format.",
            )
        return DiagnosticCheck(
            id=check_id,
            status=STATUS_WARNING,
            summary="Metadata backup file contains unrecognized format.",
            action="Backup may not be usable for automated recovery.",
        )
    except Exception as exc:
        return DiagnosticCheck(
            id=check_id,
            status=STATUS_WARNING,
            summary=f"Metadata backup file is corrupted or unreadable: {exc}",
            action="Remove or recreate profiles.json.bak after verifying profiles.json.",
        )


def check_profile_directories(root: Path) -> Tuple[DiagnosticCheck, DiagnosticCheck]:
    existence_id = "profile_directories_exist"
    paths_id = "profile_paths_under_data_root"
    paths = DataPaths.from_root(root)
    profiles_file = paths.profiles_file
    if not profiles_file.exists():
        return (
            DiagnosticCheck(
                id=existence_id,
                status=STATUS_OK,
                summary="No profiles configured yet.",
            ),
            DiagnosticCheck(
                id=paths_id,
                status=STATUS_OK,
                summary="No profiles configured yet.",
            ),
        )
    try:
        doc = load_metadata(profiles_file)
    except Exception as exc:
        return (
            DiagnosticCheck(
                id=existence_id,
                status=STATUS_FAILED,
                summary=f"Cannot check profile directories (metadata unreadable): {exc}",
            ),
            DiagnosticCheck(
                id=paths_id,
                status=STATUS_FAILED,
                summary=f"Cannot check profile paths (metadata unreadable): {exc}",
            ),
        )

    missing_dirs: List[str] = []
    invalid_paths: List[str] = []
    profiles_root = paths.profiles_dir.resolve()

    for p in doc.profiles:
        data_path = Path(p.data_dir)
        if not data_path.is_dir():
            missing_dirs.append(f"{p.id} ({p.data_dir})")
        try:
            resolved = data_path.resolve()
            resolved.relative_to(profiles_root)
        except (ValueError, OSError):
            invalid_paths.append(f"{p.id} ({p.data_dir})")

    if not missing_dirs:
        chk_exist = DiagnosticCheck(
            id=existence_id,
            status=STATUS_OK,
            summary=f"All {len(doc.profiles)} profile data directories exist.",
        )
    else:
        chk_exist = DiagnosticCheck(
            id=existence_id,
            status=STATUS_WARNING,
            summary=f"Missing data directories for profiles: {', '.join(missing_dirs)}",
            action="Recreate missing directories or delete the corresponding profiles.",
        )

    if not invalid_paths:
        chk_paths = DiagnosticCheck(
            id=paths_id,
            status=STATUS_OK,
            summary=f"All {len(doc.profiles)} profile data paths are safely inside profile root.",
        )
    else:
        chk_paths = DiagnosticCheck(
            id=paths_id,
            status=STATUS_FAILED,
            summary=f"Profile paths outside profile root: {', '.join(invalid_paths)}",
            action="Correct data_dir paths in profiles.json to reside under the profile root.",
        )

    return chk_exist, chk_paths


def check_playwright_package() -> DiagnosticCheck:
    check_id = "playwright_package"
    try:
        import playwright
        return DiagnosticCheck(
            id=check_id,
            status=STATUS_OK,
            summary=f"Playwright package is installed (version {getattr(playwright, '__version__', 'unknown')}).",
        )
    except ImportError:
        return DiagnosticCheck(
            id=check_id,
            status=STATUS_FAILED,
            summary="Playwright package is not installed.",
            action="Install playwright with 'pip install -r requirements.txt'.",
        )


def check_playwright_chromium() -> DiagnosticCheck:
    check_id = "playwright_chromium"
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            exec_path = p.chromium.executable_path
            if exec_path and Path(exec_path).exists():
                return DiagnosticCheck(
                    id=check_id,
                    status=STATUS_OK,
                    summary=f"Playwright Chromium is installed at {exec_path}.",
                )
            return DiagnosticCheck(
                id=check_id,
                status=STATUS_WARNING,
                summary="Playwright Chromium executable is not found.",
                action="Run 'playwright install chromium' to install Playwright browser.",
            )
    except Exception as exc:
        return DiagnosticCheck(
            id=check_id,
            status=STATUS_WARNING,
            summary=f"Playwright Chromium is not available: {exc}",
            action="Run 'playwright install chromium' or ensure Google Chrome is installed.",
        )


def check_direct_chrome() -> DiagnosticCheck:
    check_id = "system_chrome_executable"
    executable = _system_browser_executable()
    if executable is not None:
        return DiagnosticCheck(
            id=check_id,
            status=STATUS_OK,
            summary=f"Direct browser executable found at {executable}.",
        )
    return DiagnosticCheck(
        id=check_id,
        status=STATUS_WARNING,
        summary="No Chrome, Chromium, or Brave executable found for direct mode.",
        action="Install Google Chrome, Chromium, or Brave for direct engine support.",
    )


def check_system_chrome() -> DiagnosticCheck:
    check_id = "system_chrome"
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel="chrome", headless=True)
                browser.close()
                return DiagnosticCheck(
                    id=check_id,
                    status=STATUS_OK,
                    summary="System Google Chrome is installed and functional.",
                )
            except Exception:
                pass
    except Exception:
        pass

    executable = _system_browser_executable()
    if executable is not None:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=str(executable), headless=True)
                browser.close()
            return DiagnosticCheck(
                id=check_id,
                status=STATUS_OK,
                summary=f"System browser is installed and functional at {executable}.",
            )
        except Exception:
            pass

    return DiagnosticCheck(
        id=check_id,
        status=STATUS_WARNING,
        summary="System Google Chrome not detected.",
        action="Install Google Chrome or install Playwright Chromium.",
    )


def check_browser_availability(
    pw_check: DiagnosticCheck,
    chrome_check: DiagnosticCheck,
    direct_check: Optional[DiagnosticCheck] = None,
) -> DiagnosticCheck:
    check_id = "browser_availability"
    if direct_check is not None and direct_check.status == STATUS_OK:
        return DiagnosticCheck(
            id=check_id,
            status=STATUS_OK,
            summary="Direct browser engine detected (Chrome, Chromium, or Brave).",
        )
    if pw_check.status == STATUS_OK:
        return DiagnosticCheck(
            id=check_id,
            status=STATUS_OK,
            summary="Browser engine available (Playwright Chromium).",
        )
    if chrome_check.status == STATUS_OK:
        return DiagnosticCheck(
            id=check_id,
            status=STATUS_OK,
            summary="Browser engine available (System Google Chrome fallback).",
        )
    return DiagnosticCheck(
        id=check_id,
        status=STATUS_FAILED,
        summary="No browser engine available.",
        action="Install Playwright Chromium, Google Chrome, Chromium, or Brave.",
    )

def check_runtime_permissions(root: Path) -> DiagnosticCheck:
    check_id = "runtime_permissions"
    runtime_dir = DataPaths.from_root(root).runtime_dir
    if not runtime_dir.exists():
        return DiagnosticCheck(
            id=check_id,
            status=STATUS_OK,
            summary="Runtime directory does not exist yet.",
        )
    try:
        test_file = runtime_dir / f".perm_test_{os.getpid()}"
        test_file.write_text("test", encoding="utf-8")
        test_file.unlink()
        return DiagnosticCheck(
            id=check_id,
            status=STATUS_OK,
            summary="Runtime directory has full read/write permissions.",
        )
    except (OSError, PermissionError) as exc:
        return DiagnosticCheck(
            id=check_id,
            status=STATUS_FAILED,
            summary=f"Runtime directory has permission issues: {exc}",
            action="Ensure the user has read and write permissions to the runtime/ directory.",
        )


def check_stale_running_state(root: Path) -> Tuple[DiagnosticCheck, List[Path]]:
    check_id = "stale_running_state"
    paths = DataPaths.from_root(root)
    runtime_dir = paths.runtime_dir
    if not runtime_dir.exists():
        return (
            DiagnosticCheck(
                id=check_id,
                status=STATUS_OK,
                summary="No running-state files found.",
            ),
            [],
        )

    stale_files: List[Path] = []
    ambiguous_files: List[Path] = []
    for running_json in runtime_dir.glob("*/running.json"):
        data_dir = paths.profiles_dir / running_json.parent.name / "browser-data"
        status = get_status(
            str(data_dir), clean_stale=False, runtime_dir=running_json.parent
        )
        if status == "stale":
            if is_active_for_mutation(str(data_dir), runtime_dir=running_json.parent):
                ambiguous_files.append(running_json)
            else:
                stale_files.append(running_json)

    if ambiguous_files:
        return (
            DiagnosticCheck(
                id=check_id,
                status=STATUS_WARNING,
                summary=f"Found {len(ambiguous_files)} ambiguous running.json file(s) that cannot be verified safely.",
                action="Review the runtime state manually and confirm no browser process is active before removing it.",
            ),
            stale_files,
        )

    if not stale_files:
        return (
            DiagnosticCheck(
                id=check_id,
                status=STATUS_OK,
                summary="No stale running-state files detected.",
            ),
            [],
        )

    return (
        DiagnosticCheck(
            id=check_id,
            status=STATUS_WARNING,
            summary=f"Found {len(stale_files)} stale running.json file(s).",
            action="Run 'profiledock doctor --repair' to clean stale running-state files.",
        ),
        stale_files,
    )


def check_orphan_directories(root: Path) -> DiagnosticCheck:
    check_id = "orphan_profile_directories"
    paths = DataPaths.from_root(root)
    profiles_dir = paths.profiles_dir
    profiles_file = paths.profiles_file
    if not profiles_dir.exists():
        return DiagnosticCheck(
            id=check_id,
            status=STATUS_OK,
            summary="No orphan profile directories found.",
        )

    known_ids: Set[str] = set()
    if profiles_file.exists():
        try:
            doc = load_metadata(profiles_file)
            known_ids = {p.id for p in doc.profiles}
        except Exception as exc:
            return DiagnosticCheck(
                id=check_id,
                status=STATUS_WARNING,
                summary=f"Cannot determine orphan directories because metadata is unreadable: {exc}",
                action="Repair or restore profiles.json before reviewing orphan directories.",
            )

    orphans: List[str] = []
    for item in profiles_dir.iterdir():
        if item.is_dir() and item.name not in known_ids:
            orphans.append(item.name)

    if not orphans:
        return DiagnosticCheck(
            id=check_id,
            status=STATUS_OK,
            summary="No orphan profile directories detected.",
        )

    return DiagnosticCheck(
        id=check_id,
        status=STATUS_WARNING,
        summary=f"Found {len(orphans)} orphan profile directory(ies) not in metadata: {', '.join(orphans)}",
        action="Orphan directories were not deleted automatically (requires user review/manual removal).",
    )


def check_version_consistency() -> DiagnosticCheck:
    check_id = "version_consistency"
    runtime_ver = __version__
    try:
        pkg_ver = importlib.metadata.version("profiledock")
        if pkg_ver == runtime_ver:
            return DiagnosticCheck(
                id=check_id,
                status=STATUS_OK,
                summary=f"Runtime version ({runtime_ver}) matches installed package version ({pkg_ver}).",
            )
        return DiagnosticCheck(
            id=check_id,
            status=STATUS_WARNING,
            summary=f"Runtime version ({runtime_ver}) differs from installed package version ({pkg_ver}).",
            action="Reinstall package with 'pip install -e .' or rerun 'python scripts/setup_project.py'.",
        )
    except importlib.metadata.PackageNotFoundError:
        return DiagnosticCheck(
            id=check_id,
            status=STATUS_OK,
            summary=f"Runtime version is {runtime_ver} (package metadata not found, running from source).",
        )


def run_diagnostics(root: Path) -> List[DiagnosticCheck]:
    checks: List[DiagnosticCheck] = []

    checks.append(check_python_version())
    checks.append(check_data_root_writable(root))
    checks.append(check_metadata_schema(root))
    checks.append(check_metadata_backup_state(root))
    chk_exist, chk_paths = check_profile_directories(root)
    checks.append(chk_exist)
    checks.append(chk_paths)
    pw_pkg_chk = check_playwright_package()
    checks.append(pw_pkg_chk)
    pw_chrom_chk = check_playwright_chromium()
    checks.append(pw_chrom_chk)
    sys_chrome_chk = check_system_chrome()
    checks.append(sys_chrome_chk)
    direct_chk = check_direct_chrome()
    checks.append(direct_chk)
    browser_chk = check_browser_availability(pw_chrom_chk, sys_chrome_chk, direct_chk)
    checks.append(browser_chk)

    checks.append(check_runtime_permissions(root))
    stale_chk, _ = check_stale_running_state(root)
    checks.append(stale_chk)
    checks.append(check_orphan_directories(root))
    checks.append(check_version_consistency())

    return checks


def repair_environment(
    root: Path,
    reattach_orphans: bool = False,
    recreate_missing_directories: bool = False,
) -> List[DiagnosticCheck]:
    repairs: List[DiagnosticCheck] = []

    paths = DataPaths.from_root(root)
    profiles_dir = paths.profiles_dir
    runtime_dir = paths.runtime_dir

    def profiles_are_stopped(profiles: List[Profile]) -> bool:
        return all(
            not is_active_for_mutation(
                profile.data_dir, paths.runtime_dir / profile.id
            )
            for profile in profiles
        )

    if runtime_dir.exists():
        _, stale_files = check_stale_running_state(root)
        cleaned = 0
        for path in stale_files:
            try:
                ensure_within_root(path, paths.root)
                path.unlink(missing_ok=True)
                cleaned += 1
            except OSError:
                pass
        if cleaned > 0:
            repairs.append(
                DiagnosticCheck(
                    id="repair_stale_running_state",
                    status=STATUS_OK,
                    summary=f"Cleaned up {cleaned} stale running.json file(s).",
                )
            )

    if profiles_dir.exists():
        try:
            with metadata_lock(paths.profiles_file):
                stale_temp_dirs: List[Path] = []
                for pattern in (".m-*", ".temp_restore_*", ".temp_migrating_*", ".quarantine_*"):
                    stale_temp_dirs.extend(profiles_dir.glob(pattern))
                cleaned_temps = 0
                for temp_path in stale_temp_dirs:
                    if temp_path.is_dir() and not _is_link(temp_path):
                        try:
                            ensure_tree_safe(temp_path, paths.root)
                            shutil.rmtree(temp_path, ignore_errors=False)
                            cleaned_temps += 1
                        except OSError:
                            pass
                if cleaned_temps > 0:
                    repairs.append(
                        DiagnosticCheck(
                            id="repair_incomplete_operations",
                            status=STATUS_OK,
                            summary=f"Cleaned up {cleaned_temps} incomplete migration/restore temporary directory(ies).",
                        )
                    )
        except MetadataLockedError:
            repairs.append(
                DiagnosticCheck(
                    id="repair_incomplete_operations",
                    status=STATUS_WARNING,
                    summary="Skipped temporary-directory cleanup because another metadata operation is active.",
                )
            )

    profiles_file = paths.profiles_file
    backup_file = paths.backup_file
    primary_bad = False
    if profiles_file.exists():
        try:
            data = _read_json_file(profiles_file)
            if _is_bare_array(data):
                profiles = _load_profiles_from_bare_array(data)
                validate_metadata_document(profiles, profiles_dir)
                if not profiles_are_stopped(profiles):
                    raise StorageError("cannot repair metadata while a profile is active")
                with metadata_lock(profiles_file):
                    _backup_metadata(profiles_file, backup_file, paths.root)
                    doc = MetadataDocument(
                        schema_version=METADATA_SCHEMA_VERSION, profiles=profiles
                    )
                    _atomic_write(
                        profiles_file,
                        json.dumps(doc.to_dict(), indent=2) + "\n",
                        paths.root,
                    )
                repairs.append(
                    DiagnosticCheck(
                        id="repair_metadata_migration",
                        status=STATUS_OK,
                        summary="Migrated legacy bare-array metadata to schema_version 1.",
                    )
                )
            elif _is_versioned_document(data):
                doc = MetadataDocument.from_dict(data)
                validate_metadata_document(doc.profiles, profiles_dir)
            else:
                primary_bad = True
        except Exception:
            primary_bad = True
    elif backup_file.exists():
        primary_bad = True

    if primary_bad and backup_file.exists():
        try:
            data = _read_json_file(backup_file)
            if _is_versioned_document(data):
                doc = MetadataDocument.from_dict(data)
                validate_metadata_document(doc.profiles, profiles_dir)
                if not profiles_are_stopped(doc.profiles):
                    raise StorageError("cannot repair metadata while a profile is active")
                with metadata_lock(profiles_file):
                    _atomic_write(
                        profiles_file,
                        json.dumps(doc.to_dict(), indent=2) + "\n",
                        paths.root,
                    )
                repairs.append(
                    DiagnosticCheck(
                        id="repair_metadata_recovery",
                        status=STATUS_OK,
                        summary="Recovered valid metadata from profiles.json.bak backup.",
                    )
                )
            elif _is_bare_array(data):
                profiles = _load_profiles_from_bare_array(data)
                validate_metadata_document(profiles, profiles_dir)
                if not profiles_are_stopped(profiles):
                    raise StorageError("cannot repair metadata while a profile is active")
                with metadata_lock(profiles_file):
                    doc = MetadataDocument(
                        schema_version=METADATA_SCHEMA_VERSION, profiles=profiles
                    )
                    _atomic_write(
                        profiles_file,
                        json.dumps(doc.to_dict(), indent=2) + "\n",
                        paths.root,
                    )
                repairs.append(
                    DiagnosticCheck(
                        id="repair_metadata_recovery",
                        status=STATUS_OK,
                        summary="Recovered and migrated valid metadata from profiles.json.bak backup.",
                    )
                )
        except Exception:
            pass

    if profiles_file.exists():
        try:
            doc = load_metadata(profiles_file)
            validate_metadata_document(doc.profiles, profiles_dir)
            if recreate_missing_directories:
                recreated_count = 0
                recreated_paths: List[Path] = []
                try:
                    for p in doc.profiles:
                        p_data_path = Path(p.data_dir)
                        if not p_data_path.exists():
                            if is_active_for_mutation(p.data_dir, paths.runtime_dir / p.id):
                                raise StorageError("cannot recreate data for an active profile")
                            ensure_within_root(p_data_path, paths.root)
                            rollback_path = p_data_path.parent if not p_data_path.parent.exists() else p_data_path
                            p_data_path.mkdir(parents=True, mode=0o700, exist_ok=False)
                            recreated_paths.append(rollback_path)
                            recreated_count += 1
                except Exception:
                    for recreated in reversed(recreated_paths):
                        if recreated.exists():
                            ensure_tree_safe(recreated, paths.root)
                            shutil.rmtree(recreated, ignore_errors=True)
                    raise
                if recreated_count > 0:
                    repairs.append(
                        DiagnosticCheck(
                            id="repair_recreate_missing_directories",
                            status=STATUS_OK,
                            summary=f"Recreated {recreated_count} missing profile browser-data directory(ies).",
                        )
                    )

            if reattach_orphans and profiles_dir.exists():
                known_ids = {p.id for p in doc.profiles}
                known_names = {p.name for p in doc.profiles}
                reattached_profiles: List[Profile] = []
                for entry in sorted(profiles_dir.iterdir()):
                    if entry.is_dir() and not _is_link(entry) and not entry.name.startswith(".") and entry.name not in known_ids:
                        validate_path_component(entry.name, "profile id")
                        ensure_within_root(entry, paths.root)
                        data_dir_path = entry / "browser-data"
                        if data_dir_path.is_dir():
                            ensure_tree_safe(data_dir_path, paths.root)
                            if is_active_for_mutation(
                                str(data_dir_path), paths.runtime_dir / entry.name
                            ):
                                raise StorageError("cannot reattach an active profile")
                            base_name = f"Recovered-{entry.name}"
                            candidate_name = base_name
                            counter = 1
                            while candidate_name in known_names:
                                candidate_name = f"{base_name}-{counter}"
                                counter += 1
                            known_names.add(candidate_name)

                            reattached_p = Profile(
                                id=entry.name,
                                name=candidate_name,
                                created_at=utc_now(),
                                data_dir=str(data_dir_path.resolve()),
                                engine=None,
                            )
                            reattached_profiles.append(reattached_p)

                if reattached_profiles:
                    new_profiles = list(doc.profiles) + reattached_profiles
                    new_doc = MetadataDocument(
                        schema_version=METADATA_SCHEMA_VERSION,
                        profiles=new_profiles,
                    )
                    validate_metadata_document(new_doc.profiles, profiles_dir)
                    with metadata_lock(profiles_file):
                        _backup_metadata(profiles_file, backup_file, paths.root)
                        _atomic_write(
                            profiles_file,
                            json.dumps(new_doc.to_dict(), indent=2) + "\n",
                            paths.root,
                        )
                    repairs.append(
                        DiagnosticCheck(
                            id="repair_reattach_orphans",
                            status=STATUS_OK,
                            summary=f"Reattached {len(reattached_profiles)} orphan profile directory(ies) to metadata.",
                        )
                    )
        except Exception:
            pass

    return repairs
