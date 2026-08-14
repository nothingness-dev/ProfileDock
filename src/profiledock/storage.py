import json
from pathlib import Path
from typing import List, Union

from .models import Profile


class StorageError(Exception):
    pass


def load_profiles(path: Union[str, Path] = "profiles.json") -> List[Profile]:
    path = Path(path)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("root must be a list")
        return [Profile.from_dict(item) for item in raw]
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise StorageError(f"could not read {path}: {exc}") from exc


def save_profiles(profiles: List[Profile], path: Union[str, Path] = "profiles.json") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps([profile.to_dict() for profile in profiles], indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError as exc:
        raise StorageError(f"could not write {path}: {exc}") from exc
