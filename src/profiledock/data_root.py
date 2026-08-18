import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional


class DataRootError(ValueError):
    pass


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
        if self.root.exists() and (not self.root.is_dir() or self.root.is_symlink()):
            raise DataRootError("data root must be a real directory")
        self.root.mkdir(parents=True, exist_ok=True)
        root = self.root.resolve()
        for path in (
            self.metadata_dir,
            self.profiles_dir,
            self.runtime_dir,
            self.logs_dir,
            self.backups_dir,
        ):
            if path.exists() and (not path.is_dir() or path.is_symlink()):
                raise DataRootError(f"managed data directory is unsafe: {path}")
            path.mkdir(parents=True, exist_ok=True)
            try:
                path.resolve().relative_to(root)
            except ValueError as exc:
                raise DataRootError(f"managed data directory escapes data root: {path}") from exc
        for path in (self.profiles_file, self.backup_file, self.profiles_file.with_suffix(".lock")):
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise DataRootError(f"managed data file is unsafe: {path}")


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
        selected = Path(env_value) if env_value and env_value.strip() else platform_data_root(platform, environ, home)
    if not str(selected).strip():
        raise DataRootError("data root cannot be empty")
    expanded = Path(os.path.expandvars(str(selected))).expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    root = expanded.resolve(strict=False)
    anchor = Path(root.anchor)
    user_home = (home or Path.home()).resolve(strict=False)
    if root == anchor or root == user_home:
        raise DataRootError("data root cannot be a filesystem root or home directory")
    if root.exists() and (not root.is_dir() or root.is_symlink()):
        raise DataRootError("data root must be a real directory")
    paths = DataPaths.from_root(root)
    if prepare:
        try:
            paths.prepare()
        except OSError as exc:
            raise DataRootError(f"cannot prepare data root: {exc}") from exc
    return paths
