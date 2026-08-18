from datetime import datetime
from pathlib import Path
import os
import re
from typing import List, Set

from .data_root import _is_link
from .models import Profile


class ValidationError(Exception):
    pass


def validate_timestamp(timestamp_str: str, field_name: str) -> None:
    try:
        value = datetime.fromisoformat(timestamp_str)
    except (ValueError, TypeError) as exc:
        raise ValidationError(f"{field_name} must be a valid ISO-8601 timestamp: {exc}") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"{field_name} must include a timezone offset")


def validate_required_fields(profile: Profile) -> None:
    if not profile.id or not profile.id.strip():
        raise ValidationError("profile id must not be empty")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", profile.id) is None:
        raise ValidationError("profile id contains unsafe characters")
    if not profile.name or not profile.name.strip():
        raise ValidationError("profile name must not be empty")
    if not profile.created_at or not profile.created_at.strip():
        raise ValidationError("profile created_at must not be empty")
    if not profile.data_dir or not profile.data_dir.strip():
        raise ValidationError("profile data_dir must not be empty")
    validate_timestamp(profile.created_at, "created_at")
    if profile.last_launched_at:
        validate_timestamp(profile.last_launched_at, "last_launched_at")


def validate_duplicate_ids(profiles: List[Profile]) -> None:
    seen_ids: Set[str] = set()
    for profile in profiles:
        if profile.id in seen_ids:
            raise ValidationError(f"duplicate profile id: {profile.id}")
        seen_ids.add(profile.id)


def validate_duplicate_directories(profiles: List[Profile]) -> None:
    seen_dirs: Set[str] = set()
    for profile in profiles:
        normalized = os.path.normcase(str(Path(profile.data_dir).resolve()))
        if normalized in seen_dirs:
            raise ValidationError(f"duplicate data directory: {profile.data_dir}")
        seen_dirs.add(normalized)


def validate_path_safety(data_dir: str, profile_root: Path) -> None:
    data_path = Path(data_dir)
    try:
        resolved = data_path.resolve()
    except (OSError, ValueError) as exc:
        raise ValidationError(f"cannot resolve data directory path: {data_dir}: {exc}") from exc
    try:
        resolved.relative_to(profile_root.resolve())
    except ValueError:
        raise ValidationError(
            f"data directory must be under profile root ({profile_root}): {data_dir}"
        )
    root_absolute = profile_root.absolute()
    path_absolute = data_path.absolute()
    try:
        relative = path_absolute.relative_to(root_absolute)
    except ValueError as exc:
        raise ValidationError(
            f"data directory must be under profile root ({profile_root}): {data_dir}"
        ) from exc
    current = root_absolute
    for part in relative.parts:
        current = current / part
        if _is_link(current):
            raise ValidationError(f"path contains symlink at {current}: {data_dir}")


def validate_metadata_document(profiles: List[Profile], profile_root: Path) -> None:
    for profile in profiles:
        validate_required_fields(profile)
    validate_duplicate_ids(profiles)
    validate_duplicate_directories(profiles)
    for profile in profiles:
        validate_path_safety(profile.data_dir, profile_root)
        expected = (profile_root / profile.id / "browser-data").resolve()
        if Path(profile.data_dir).resolve() != expected:
            raise ValidationError(
                f"profile data directory must match profiles/<id>/browser-data: {profile.data_dir}"
            )
