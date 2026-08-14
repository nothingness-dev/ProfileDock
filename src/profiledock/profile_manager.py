import shutil
import uuid
from pathlib import Path
from typing import List, Optional, Union

from .models import Profile, utc_now
from .storage import load_profiles, save_profiles


class ProfileNotFoundError(Exception):
    pass


class ProfileManager:
    def __init__(self, root: Union[str, Path] = ".") -> None:
        self.root = Path(root).resolve()
        self.profiles_file = self.root / "profiles.json"
        self.profiles_dir = self.root / "profiles"

    def list_profiles(self) -> List[Profile]:
        return load_profiles(self.profiles_file)

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
        profiles = self.list_profiles()
        profiles.append(profile)
        save_profiles(profiles, self.profiles_file)
        return profile

    def delete(self, profile_id: str) -> Profile:
        profile = self.get(profile_id)
        shutil.rmtree(Path(profile.data_dir).parent, ignore_errors=False)
        save_profiles([p for p in self.list_profiles() if p.id != profile_id], self.profiles_file)
        return profile

    def mark_launched(self, profile_id: str) -> None:
        profiles = self.list_profiles()
        profile = next((p for p in profiles if p.id == profile_id), None)
        if profile is None:
            raise ProfileNotFoundError(f"profile not found: {profile_id}")
        profile.last_launched_at = utc_now()
        save_profiles(profiles, self.profiles_file)

