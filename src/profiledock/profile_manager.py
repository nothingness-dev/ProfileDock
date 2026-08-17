import shutil
import uuid
from pathlib import Path
from typing import List, Optional, Union

from .models import Profile, utc_now
from .storage import (
    add_profile_atomic,
    load_metadata,
    mark_launched_atomic,
    migrate_metadata,
    remove_profile_atomic,
    rename_profile_atomic,
)


class ProfileNotFoundError(Exception):
    pass


class AmbiguousProfileError(Exception):
    pass


class ProfileManager:
    def __init__(self, root: Union[str, Path] = ".") -> None:
        self.root = Path(root).resolve()
        self.profiles_file = self.root / "profiles.json"
        self.profiles_dir = self.root / "profiles"

    def ensure_migrated(self) -> None:
        migrate_metadata(self.profiles_file, self.profiles_dir)

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

    def create(self, name: str) -> Profile:
        name = name.strip()
        if not name:
            raise ValueError("profile name cannot be empty")
        profile_id = uuid.uuid4().hex[:8]
        data_dir = self.profiles_dir / profile_id / "browser-data"
        data_dir.mkdir(parents=True, exist_ok=False)
        profile = Profile(profile_id, name, utc_now(), str(data_dir))
        try:
            add_profile_atomic(profile, self.profiles_file, self.profiles_dir)
        except Exception:
            shutil.rmtree(data_dir.parent, ignore_errors=True)
            raise
        return profile

    def delete(self, identifier: str) -> Profile:
        profile = self.resolve(identifier)
        remove_profile_atomic(profile.id, self.profiles_file, self.profiles_dir)
        shutil.rmtree(Path(profile.data_dir).parent, ignore_errors=False)
        return profile

    def rename(self, identifier: str, new_name: str) -> Profile:
        new_name = new_name.strip()
        if not new_name:
            raise ValueError("profile name cannot be empty")
        profile = self.resolve(identifier)
        rename_profile_atomic(profile.id, new_name, self.profiles_file, self.profiles_dir)
        return self.get(profile.id)

    def mark_launched(self, identifier: str) -> None:
        profile = self.resolve(identifier)
        mark_launched_atomic(profile.id, utc_now(), self.profiles_file, self.profiles_dir)
