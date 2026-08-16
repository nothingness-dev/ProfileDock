from dataclasses import asdict, dataclass
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

from .models import METADATA_SCHEMA_VERSION, MetadataDocument, Profile
from .process_manager import _alive, _read_state, error_path, state_path
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
    current_str = f"{ver.major}.{ver.minor}.{ver.micro}"
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
            action="Check folder permissions for the project root directory.",
        )


def check_metadata_schema(root: Path) -> DiagnosticCheck:
    check_id = "metadata_schema"
    profiles_file = root / "profiles.json"
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
            profiles_dir = root / "profiles"
            validate_metadata_document(doc.profiles, profiles_dir)
            return DiagnosticCheck(
                id=check_id,
                status=STATUS_OK,
                summary=f"Valid metadata document (schema_version {doc.schema_version}, {len(doc.profiles)} profile(s)).",
            )
        if _is_bare_array(data):
            profiles = _load_profiles_from_bare_array(data)
            profiles_dir = root / "profiles"
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
    profiles_file = root / "profiles.json"
    backup_file = root / "profiles.json.bak"
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
            MetadataDocument.from_dict(data)
            return DiagnosticCheck(
                id=check_id,
                status=STATUS_OK,
                summary="Metadata backup is valid and intact.",
            )
        if _is_bare_array(data):
            _load_profiles_from_bare_array(data)
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
    profiles_file = root / "profiles.json"
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
    profiles_root = (root / "profiles").resolve()

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

    found = False
    if sys.platform == "win32":
        for env_var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env_var)
            if base and (Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe").exists():
                found = True
                break
    elif sys.platform == "darwin":
        found = Path("/Applications/Google Chrome.app").exists()
    else:
        found = shutil.which("google-chrome") is not None or shutil.which("google-chrome-stable") is not None or shutil.which("chromium-browser") is not None or shutil.which("chromium") is not None

    if found:
        return DiagnosticCheck(
            id=check_id,
            status=STATUS_OK,
            summary="System Google Chrome or Chromium detected on system.",
        )

    return DiagnosticCheck(
        id=check_id,
        status=STATUS_WARNING,
        summary="System Google Chrome not detected.",
        action="Install Google Chrome or install Playwright Chromium.",
    )


def check_browser_availability(
    pw_check: DiagnosticCheck, chrome_check: DiagnosticCheck
) -> DiagnosticCheck:
    check_id = "browser_availability"
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
        summary="No browser engine available (neither Playwright Chromium nor Google Chrome).",
        action="Install Playwright Chromium with 'playwright install chromium' or install Google Chrome.",
    )


def check_runtime_permissions(root: Path) -> DiagnosticCheck:
    check_id = "runtime_permissions"
    profiles_dir = root / "profiles"
    if not profiles_dir.exists():
        return DiagnosticCheck(
            id=check_id,
            status=STATUS_OK,
            summary="Runtime directory (profiles/) does not exist yet.",
        )
    try:
        test_file = profiles_dir / f".perm_test_{os.getpid()}"
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
            action="Ensure the user has read and write permissions to the profiles/ directory.",
        )


def check_stale_running_state(root: Path) -> Tuple[DiagnosticCheck, List[Path]]:
    check_id = "stale_running_state"
    profiles_dir = root / "profiles"
    if not profiles_dir.exists():
        return (
            DiagnosticCheck(
                id=check_id,
                status=STATUS_OK,
                summary="No running-state files found.",
            ),
            [],
        )

    stale_files: List[Path] = []
    for running_json in profiles_dir.glob("*/running.json"):
        state = _read_state(running_json)
        if not state or not isinstance(state, dict):
            stale_files.append(running_json)
            continue
        pid = int(state.get("pid", -1))
        if pid > 0 and not _alive(pid):
            stale_files.append(running_json)
        elif pid <= 0:
            pass

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
    profiles_dir = root / "profiles"
    profiles_file = root / "profiles.json"
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
        except Exception:
            pass

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

    # 1. Supported Python version
    checks.append(check_python_version())

    # 2. Writable data root
    checks.append(check_data_root_writable(root))

    # 3. Valid metadata schema
    checks.append(check_metadata_schema(root))

    # 4. Metadata backup state
    checks.append(check_metadata_backup_state(root))

    # 5 & 6. Profile directory existence & Profile paths under data root
    chk_exist, chk_paths = check_profile_directories(root)
    checks.append(chk_exist)
    checks.append(chk_paths)

    # 8. Playwright package availability
    pw_pkg_chk = check_playwright_package()
    checks.append(pw_pkg_chk)

    # 9. Playwright Chromium availability
    pw_chrom_chk = check_playwright_chromium()
    checks.append(pw_chrom_chk)

    # 10. System Chrome availability
    sys_chrome_chk = check_system_chrome()
    checks.append(sys_chrome_chk)

    # 7. Browser availability (aggregate)
    browser_chk = check_browser_availability(pw_chrom_chk, sys_chrome_chk)
    checks.append(browser_chk)

    # 11. Runtime directory permissions
    checks.append(check_runtime_permissions(root))

    # 12. Stale running-state files
    stale_chk, _ = check_stale_running_state(root)
    checks.append(stale_chk)

    # 13. Orphan profile directories
    checks.append(check_orphan_directories(root))

    # 14. Package/runtime version consistency
    checks.append(check_version_consistency())

    return checks


def repair_environment(root: Path) -> List[DiagnosticCheck]:
    repairs: List[DiagnosticCheck] = []

    # Safe repair 1: Cleanup stale running-state files
    profiles_dir = root / "profiles"
    if profiles_dir.exists():
        _, stale_files = check_stale_running_state(root)
        cleaned = 0
        for path in stale_files:
            try:
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

    # Safe repair 2: Recover from validated metadata backup if primary is corrupted or missing/legacy
    profiles_file = root / "profiles.json"
    backup_file = root / "profiles.json.bak"
    primary_bad = False
    if profiles_file.exists():
        try:
            data = _read_json_file(profiles_file)
            if _is_bare_array(data):
                # Legacy format -> migrate safely
                profiles = _load_profiles_from_bare_array(data)
                validate_metadata_document(profiles, profiles_dir)
                with metadata_lock(profiles_file):
                    _backup_metadata(profiles_file)
                    doc = MetadataDocument(
                        schema_version=METADATA_SCHEMA_VERSION, profiles=profiles
                    )
                    _atomic_write(
                        profiles_file, json.dumps(doc.to_dict(), indent=2) + "\n"
                    )
                repairs.append(
                    DiagnosticCheck(
                        id="repair_metadata_migration",
                        status=STATUS_OK,
                        summary="Migrated legacy bare-array metadata to schema_version 1.",
                    )
                )
            elif _is_versioned_document(data):
                MetadataDocument.from_dict(data)
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
                with metadata_lock(profiles_file):
                    _atomic_write(
                        profiles_file, json.dumps(doc.to_dict(), indent=2) + "\n"
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
                with metadata_lock(profiles_file):
                    doc = MetadataDocument(
                        schema_version=METADATA_SCHEMA_VERSION, profiles=profiles
                    )
                    _atomic_write(
                        profiles_file, json.dumps(doc.to_dict(), indent=2) + "\n"
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

    return repairs
