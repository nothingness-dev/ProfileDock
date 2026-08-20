import shutil
import uuid
from pathlib import Path
from typing import List, Optional, Union

from .data_root import DataPaths, resolve_data_root
from .models import Profile, utc_now
from .storage import (
    add_profile_atomic,
    load_metadata,
    mark_launched_atomic,
    migrate_metadata,
    remove_profile_atomic,
    rename_profile_atomic,
    set_engine_atomic,
)


class ProfileNotFoundError(Exception):
    pass


class AmbiguousProfileError(Exception):
    pass


class ProfileManager:
    def __init__(self, root: Union[str, Path, DataPaths]) -> None:
        paths = root if isinstance(root, DataPaths) else resolve_data_root(Path(root))
        self.paths = paths
        self.root = paths.root
        self.profiles_file = paths.profiles_file
        self.profiles_dir = paths.profiles_dir
        self.runtime_dir = paths.runtime_dir
        self.backup_file = paths.backup_file

    def runtime_path(self, profile_id: str) -> Path:
        path = (self.runtime_dir / profile_id).resolve(strict=False)
        try:
            path.relative_to(self.runtime_dir.resolve())
        except ValueError as exc:
            raise ValueError("unsafe profile id") from exc
        return path

    def ensure_migrated(self) -> None:
        migrate_metadata(self.profiles_file, self.profiles_dir, backup_path=self.backup_file)

    def list_profiles(self) -> List[Profile]:
        self.ensure_migrated()
        doc = load_metadata(self.profiles_file)
        return doc.profiles

    def get(self, profile_id: str) -> Profile:
        for profile in self.list_profiles():
            if profile.id == profile_id:
                return profile
        raise ProfileNotFoundError(f"profile not found: {profile_id}")

    def resolve(self, identifier: str) -> Profile:
        profiles = self.list_profiles()
        for profile in profiles:
            if profile.id == identifier:
                return profile
        prefix_matches = [p for p in profiles if p.id.startswith(identifier)]
        if len(prefix_matches) == 1:
            return prefix_matches[0]
        if len(prefix_matches) > 1:
            matches = ", ".join(f"{p.id} ({p.name})" for p in prefix_matches)
            raise AmbiguousProfileError(
                f"ambiguous profile identifier '{identifier}' matches: {matches}"
            )
        name_matches = [p for p in profiles if p.name == identifier]
        if len(name_matches) == 1:
            return name_matches[0]
        if len(name_matches) > 1:
            matches = ", ".join(f"{p.id} ({p.name})" for p in name_matches)
            raise AmbiguousProfileError(
                f"ambiguous profile name '{identifier}' matches: {matches}"
            )
        raise ProfileNotFoundError(f"profile not found: {identifier}")

    def create(self, name: str, engine: Optional[str] = None) -> Profile:
        name = name.strip()
        if not name:
            raise ValueError("profile name cannot be empty")
        if engine is not None and engine not in {"direct", "playwright"}:
            raise ValueError(f"invalid engine '{engine}', must be 'direct' or 'playwright'")
        profile_id = uuid.uuid4().hex[:8]
        profile_dir = self.profiles_dir / profile_id
        profile_dir.mkdir(mode=0o700, exist_ok=False)
        profile_dir.chmod(0o700)
        data_dir = profile_dir / "browser-data"
        data_dir.mkdir(mode=0o700)
        data_dir.chmod(0o700)
        profile = Profile(profile_id, name, utc_now(), str(data_dir), engine=engine)
        try:
            add_profile_atomic(profile, self.profiles_file, self.profiles_dir, self.backup_file)
        except Exception:
            shutil.rmtree(data_dir.parent, ignore_errors=True)
            raise
        return profile

    def delete(self, identifier: str) -> Profile:
        profile = self.resolve(identifier)
        expected_root = (self.profiles_dir / profile.id).resolve()
        expected_data = expected_root / "browser-data"
        profile_root = Path(profile.data_dir).parent.resolve()
        if Path(profile.data_dir).resolve() != expected_data or profile_root != expected_root:
            raise ValueError("refusing to delete unsafe profile directory")
        quarantine = None
        if profile_root.exists():
            quarantine = self.profiles_dir / f".deleting-{profile.id}-{uuid.uuid4().hex}"
            profile_root.replace(quarantine)
        try:
            remove_profile_atomic(profile.id, self.profiles_file, self.profiles_dir, self.backup_file)
        except Exception:
            if quarantine is not None:
                quarantine.replace(profile_root)
            raise
        if quarantine is not None:
            shutil.rmtree(quarantine, ignore_errors=False)
        shutil.rmtree(self.runtime_path(profile.id), ignore_errors=True)
        return profile

    def rename(self, identifier: str, new_name: str) -> Profile:
        new_name = new_name.strip()
        if not new_name:
            raise ValueError("profile name cannot be empty")
        profile = self.resolve(identifier)
        rename_profile_atomic(profile.id, new_name, self.profiles_file, self.profiles_dir, self.backup_file)
        return self.get(profile.id)

    def set_engine(self, identifier: str, engine: Optional[str]) -> Profile:
        if engine is not None and engine not in {"direct", "playwright"}:
            raise ValueError(f"invalid engine '{engine}', must be 'direct' or 'playwright'")
        profile = self.resolve(identifier)
        set_engine_atomic(profile.id, engine, self.profiles_file, self.profiles_dir, self.backup_file)
        return self.get(profile.id)

    def mark_launched(self, identifier: str) -> None:
        profile = self.resolve(identifier)
        mark_launched_atomic(profile.id, utc_now(), self.profiles_file, self.profiles_dir, self.backup_file)
