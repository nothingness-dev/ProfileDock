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


class ProfileManager:
    def __init__(self, root: Union[str, Path] = ".") -> None:
        self.root = Path(root).resolve()
        self.profiles_file = self.root / "profiles.json"
        self.profiles_dir = self.root / "profiles"

    def ensure_migrated(self) -> None:
        migrate_metadata(self.profiles_file, self.profiles_dir)

    def list_profiles(self) -> List[Profile]:
        doc = load_metadata(self.profiles_file)
        return doc.profiles

    def get(self, profile_id: str) -> Profile:
        for profile in self.list_profiles():
            if profile.id == profile_id:
                return profile
        raise ProfileNotFoundError(f"profile not found: {profile_id}")

    def create(self, name: str) -> Profile:
        name = name.strip()
        if not name:
            raise ValueError("profile name cannot be empty")
        profile_id = uuid.uuid4().hex[:8]
        data_dir = self.profiles_dir / profile_id / "browser-data"
        data_dir.mkdir(parents=True, exist_ok=False)
        profile = Profile(profile_id, name, utc_now(), str(data_dir))
        add_profile_atomic(profile, self.profiles_file, self.profiles_dir)
        return profile

    def delete(self, profile_id: str) -> Profile:
        profile = self.get(profile_id)
        shutil.rmtree(Path(profile.data_dir).parent, ignore_errors=False)
        remove_profile_atomic(profile_id, self.profiles_file, self.profiles_dir)
        return profile

    def rename(self, profile_id: str, new_name: str) -> Profile:
        new_name = new_name.strip()
        if not new_name:
            raise ValueError("profile name cannot be empty")
        rename_profile_atomic(profile_id, new_name, self.profiles_file, self.profiles_dir)
        return self.get(profile_id)

    def mark_launched(self, profile_id: str) -> None:
        self.get(profile_id)
        mark_launched_atomic(profile_id, utc_now(), self.profiles_file, self.profiles_dir)
