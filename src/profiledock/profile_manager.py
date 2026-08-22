import shutil
import uuid
from pathlib import Path
from typing import Any, List, Optional, Union

from .data_root import DataPaths, ensure_tree_safe, ensure_within_root, resolve_data_root, validate_path_component
from .models import LaunchConfig, Profile, utc_now
from .process_manager import ProfileRunningError, is_active_for_mutation
from .storage import (
    add_profile_atomic,
    load_metadata,
    mark_launched_atomic,
    migrate_metadata,
    remove_profile_atomic,
    rename_profile_atomic,
    set_engine_atomic,
    set_launch_config_atomic,
)
from .validation import validate_launch_config


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
        try:
            validate_path_component(profile_id, "profile id")
            return ensure_within_root(self.runtime_dir / profile_id, self.root)
        except ValueError as exc:
            raise ValueError("unsafe profile id") from exc

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
        if not isinstance(identifier, str) or not identifier:
            raise ProfileNotFoundError("profile not found: empty identifier")
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
        profile_id = ""
        profile_dir = self.profiles_dir
        for _ in range(16):
            candidate_id = uuid.uuid4().hex[:8]
            candidate_dir = self.profiles_dir / candidate_id
            try:
                candidate_dir.mkdir(mode=0o700, exist_ok=False)
            except FileExistsError:
                continue
            profile_id = candidate_id
            profile_dir = candidate_dir
            break
        if not profile_id:
            raise StorageError("could not allocate a unique profile ID")
        try:
            profile_dir.chmod(0o700)
            data_dir = profile_dir / "browser-data"
            data_dir.mkdir(mode=0o700)
            data_dir.chmod(0o700)
            profile = Profile(profile_id, name, utc_now(), str(data_dir), engine=engine)
            add_profile_atomic(profile, self.profiles_file, self.profiles_dir, self.backup_file)
        except Exception:
            shutil.rmtree(profile_dir, ignore_errors=True)
            raise
        return profile

    def delete(self, identifier: str) -> Profile:
        profile = self.resolve(identifier)
        validate_path_component(profile.id, "profile id")
        expected_root = ensure_within_root(self.profiles_dir / profile.id, self.root)
        expected_data = expected_root / "browser-data"
        profile_data = ensure_within_root(Path(profile.data_dir), self.root)
        profile_root = profile_data.parent
        if profile_data != expected_data or profile_root != expected_root:
            raise ValueError("refusing to delete unsafe profile directory")
        runtime_path = self.runtime_path(profile.id)
        if is_active_for_mutation(profile.data_dir, runtime_path):
            raise ProfileRunningError("profile is already running; close it before deletion")
        quarantine = None
        if profile_root.exists():
            ensure_tree_safe(profile_root, self.root)
            quarantine = ensure_within_root(
                self.profiles_dir / f".deleting-{profile.id}-{uuid.uuid4().hex}",
                self.root,
            )
            profile_root.replace(quarantine)
        try:
            remove_profile_atomic(profile.id, self.profiles_file, self.profiles_dir, self.backup_file)
        except Exception:
            if quarantine is not None:
                quarantine.replace(profile_root)
            raise
        if quarantine is not None:
            ensure_tree_safe(quarantine, self.root)
            shutil.rmtree(quarantine, ignore_errors=False)
        if runtime_path.exists():
            ensure_tree_safe(runtime_path, self.root)
            shutil.rmtree(runtime_path, ignore_errors=False)
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

    def set_launch_config(self, identifier: str, config: Optional[LaunchConfig]) -> Profile:
        profile = self.resolve(identifier)
        if config is not None:
            validate_launch_config(config, profile.engine, require_browser_executable=True)
        set_launch_config_atomic(profile.id, config, self.profiles_file, self.profiles_dir, self.backup_file)
        return self.get(profile.id)

    def get_launch_config(self, identifier: str) -> LaunchConfig:
        profile = self.resolve(identifier)
        return profile.launch_config or LaunchConfig()

    def update_launch_config(self, identifier: str, **kwargs: Any) -> Profile:
        profile = self.resolve(identifier)
        current = profile.launch_config or LaunchConfig()
        cfg_dict = current.to_dict()
        for k, v in kwargs.items():
            if k not in cfg_dict:
                raise ValueError(f"unknown launch configuration field: {k}")
            cfg_dict[k] = v
        new_cfg = LaunchConfig.from_dict(cfg_dict)
        return self.set_launch_config(identifier, new_cfg)

    def add_start_url(self, identifier: str, url: str) -> Profile:
        profile = self.resolve(identifier)
        current = profile.launch_config or LaunchConfig()
        clean_url = url.strip()
        urls = list(current.start_urls)
        if clean_url not in urls:
            urls.append(clean_url)
        return self.update_launch_config(identifier, start_urls=urls)

    def remove_start_url(self, identifier: str, url: str) -> Profile:
        profile = self.resolve(identifier)
        current = profile.launch_config or LaunchConfig()
        clean_url = url.strip()
        urls = [u for u in current.start_urls if u != clean_url]
        return self.update_launch_config(identifier, start_urls=urls)

    def reset_launch_config(self, identifier: str) -> Profile:
        return self.set_launch_config(identifier, None)

    def mark_launched(self, identifier: str) -> None:
        profile = self.resolve(identifier)
        mark_launched_atomic(profile.id, utc_now(), self.profiles_file, self.profiles_dir, self.backup_file)
