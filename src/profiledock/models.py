from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

METADATA_SCHEMA_VERSION = 1
_SUPPORTED_METADATA_SCHEMA_VERSIONS = frozenset({1})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Profile:
    id: str
    name: str
    created_at: str
    data_dir: str
    last_launched_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "Profile":
        required = ("id", "name", "created_at", "data_dir")
        if any(key not in value for key in required):
            raise ValueError("profile metadata is missing a required field")
        return cls(
            id=str(value["id"]),
            name=str(value["name"]),
            created_at=str(value["created_at"]),
            data_dir=str(value["data_dir"]),
            last_launched_at=value.get("last_launched_at"),
        )


@dataclass
class MetadataDocument:
    schema_version: int
    profiles: List[Profile] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "profiles": [p.to_dict() for p in self.profiles],
        }

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "MetadataDocument":
        if not isinstance(value, dict):
            raise ValueError("metadata must be a JSON object")
        schema_version = value.get("schema_version")
        if schema_version not in _SUPPORTED_METADATA_SCHEMA_VERSIONS:
            raise ValueError(f"unsupported metadata schema version: {schema_version}")
        if "profiles" not in value:
            raise ValueError("metadata is missing required field: profiles")
        profiles_list = value["profiles"]
        if not isinstance(profiles_list, list):
            raise ValueError("profiles must be a list")
        profiles = [Profile.from_dict(item) for item in profiles_list]
        return cls(schema_version=int(schema_version), profiles=profiles)

