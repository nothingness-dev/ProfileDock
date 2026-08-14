from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional


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

