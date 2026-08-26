import os
import re
import stat
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional


class DataRootError(ValueError):
    pass


def _is_link(path: Path) -> bool:
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def validate_path_component(value: str, label: str = "identifier") -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value) is None:
        raise DataRootError(f"unsafe {label}")
    return value


_resolved_root_cache: dict[str, Path] = {}


@lru_cache(maxsize=1)
def _win_long_path_fn() -> Optional[Callable[..., int]]:
    import ctypes
    from ctypes import wintypes

    try:
        fn = ctypes.windll.kernel32.GetLongPathNameW
        fn.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        fn.restype = wintypes.DWORD
        return fn
    except (AttributeError, OSError):
        return None


def _get_long_path(path: Path) -> Path:
    if sys.platform == "win32":
        get_long_path_name = _win_long_path_fn()
        if get_long_path_name is not None:
            import ctypes

            try:
                buffer = ctypes.create_unicode_buffer(32768)
                res = get_long_path_name(str(path), buffer, 32768)
                if res > 0:
                    return Path(buffer.value)
            except OSError:
                pass
    return path


def ensure_within_root(
    target: Path,
    root: Path,
    allow_root: bool = False,
    reject_links: bool = True,
) -> Path:
    root_absolute = Path(root).expanduser().absolute()
    target_absolute = Path(target).expanduser()
    if not target_absolute.is_absolute():
        target_absolute = root_absolute / target_absolute
    target_absolute = target_absolute.absolute()

    # The root resolves identically on every call with the same root; caching avoids
    # repeated GetLongPathName/realpath syscalls in hot metadata paths.
    root_key = str(root_absolute)
    cached = _resolved_root_cache.get(root_key)
    if cached is None:
        resolved_root = _get_long_path(root_absolute.resolve(strict=False))
        if len(_resolved_root_cache) >= 8:
            _resolved_root_cache.clear()
        _resolved_root_cache[root_key] = resolved_root
    else:
        resolved_root = cached
    resolved_target = _get_long_path(target_absolute.resolve(strict=False))

    try:
        relative = target_absolute.relative_to(root_absolute)
    except ValueError:
        try:
            relative = resolved_target.relative_to(resolved_root)
        except ValueError as exc:
            raise DataRootError(f"path escapes configured data root: {target}") from exc

    if any(part == ".." for part in relative.parts):
        raise DataRootError(f"path traversal is not allowed: {target}")
    if not relative.parts and not allow_root:
        raise DataRootError("refusing to target the configured data root")
    if reject_links:
        current = resolved_root
        for part in resolved_target.relative_to(resolved_root).parts:
            current = current / part
            if _is_link(current):
                raise DataRootError(f"path contains a link or reparse point: {current}")
    try:
        resolved_target.relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise DataRootError(f"resolved path escapes configured data root: {target}") from exc
    if resolved_target == resolved_root and not allow_root:
        raise DataRootError("refusing to target the configured data root")
    return resolved_target


def ensure_tree_safe(target: Path, root: Path) -> Path:
    resolved = ensure_within_root(target, root)
    if not resolved.exists():
        return resolved
    if not resolved.is_dir() or _is_link(resolved):
        raise DataRootError(f"unsafe directory target: {target}")
    root_absolute = Path(root).expanduser().absolute()
    resolved_root = _get_long_path(root_absolute.resolve(strict=False))
    for current, directories, files in os.walk(resolved, followlinks=False):
        current_path = Path(current)
        for name in directories + files:
            child = current_path / name
            if _is_link(child):
                raise DataRootError(f"directory tree contains a link or reparse point: {child}")
        try:
            _get_long_path(current_path.resolve(strict=False)).relative_to(resolved_root)
        except ValueError as exc:
            raise DataRootError(f"path escapes configured data root: {current_path}") from exc
    return resolved


@dataclass(frozen=True)
class DataPaths:
    root: Path
    metadata_dir: Path
    profiles_dir: Path
    runtime_dir: Path
    logs_dir: Path
    backups_dir: Path
    profiles_file: Path
    backup_file: Path

    @classmethod
    def from_root(cls, root: Path) -> "DataPaths":
        return cls(
            root=root,
            metadata_dir=root / "metadata",
            profiles_dir=root / "profiles",
            runtime_dir=root / "runtime",
            logs_dir=root / "logs",
            backups_dir=root / "backups",
            profiles_file=root / "metadata" / "profiles.json",
            backup_file=root / "backups" / "profiles.json.bak",
        )

    def prepare(self) -> None:
        if self.root.exists() and (not self.root.is_dir() or _is_link(self.root)):
            raise DataRootError("data root must be a real directory")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            self.root.chmod(0o700)
        root = self.root.resolve()
        for path in (
            self.metadata_dir,
            self.profiles_dir,
            self.runtime_dir,
            self.logs_dir,
            self.backups_dir,
        ):
            if path.exists() and (not path.is_dir() or _is_link(path)):
                raise DataRootError(f"managed data directory is unsafe: {path}")
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name != "nt":
                path.chmod(0o700)
            try:
                path.resolve().relative_to(root)
            except ValueError as exc:
                raise DataRootError(f"managed data directory escapes data root: {path}") from exc
        for path in (self.profiles_file, self.backup_file, self.profiles_file.with_suffix(".lock")):
            if _is_link(path) or (path.exists() and not path.is_file()):
                raise DataRootError(f"managed data file is unsafe: {path}")
            ensure_within_root(path, root)


def platform_data_root(
    platform: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> Path:
    platform = platform or sys.platform
    environ = environ if environ is not None else os.environ
    home = home or Path.home()
    if platform == "win32":
        base = environ.get("LOCALAPPDATA")
        if not base:
            raise DataRootError("LOCALAPPDATA is not set")
        return Path(base) / "ProfileDock"
    if platform == "darwin":
        return home / "Library" / "Application Support" / "ProfileDock"
    base = environ.get("XDG_DATA_HOME")
    return (Path(base) if base else home / ".local" / "share") / "profiledock"


def resolve_data_root(
    cli_value: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
    platform: Optional[str] = None,
    home: Optional[Path] = None,
    prepare: bool = True,
) -> DataPaths:
    environ = environ if environ is not None else os.environ
    selected = cli_value
    if selected is None:
        env_value = environ.get("PROFILEDOCK_DATA_ROOT")
        selected = (
            Path(env_value)
            if env_value and env_value.strip()
            else platform_data_root(platform, environ, home)
        )
    if not str(selected).strip():
        raise DataRootError("data root cannot be empty")
    expanded = Path(os.path.expandvars(str(selected))).expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    expanded_absolute = expanded.absolute()
    if _is_link(expanded_absolute):
        raise DataRootError("data root cannot be a link or reparse point")
    root = expanded_absolute.resolve(strict=False)
    anchor = Path(root.anchor)
    user_home = (home or Path.home()).resolve(strict=False)
    if root in (anchor, user_home):
        raise DataRootError("data root cannot be a filesystem root or home directory")
    if root.exists() and (not root.is_dir() or _is_link(root)):
        raise DataRootError("data root must be a real directory")
    paths = DataPaths.from_root(root)
    if prepare:
        try:
            paths.prepare()
        except OSError as exc:
            raise DataRootError(f"cannot prepare data root: {exc}") from exc
    return paths
