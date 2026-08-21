from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

METADATA_SCHEMA_VERSION = 1
_SUPPORTED_METADATA_SCHEMA_VERSIONS = frozenset({1})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class LaunchConfig:
    default_tabs: Optional[int] = None
    start_urls: List[str] = field(default_factory=list)
    engine: Optional[str] = None
    browser: Optional[str] = None
    window_width: Optional[int] = None
    window_height: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "default_tabs": self.default_tabs,
            "start_urls": list(self.start_urls),
            "engine": self.engine,
            "browser": self.browser,
            "window_width": self.window_width,
            "window_height": self.window_height,
        }

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "LaunchConfig":
        if not isinstance(value, dict):
            raise ValueError("launch config must be a JSON object")
        default_tabs = value.get("default_tabs")
        if default_tabs is not None and (type(default_tabs) is not int or default_tabs < 1):
            raise ValueError("default_tabs must be a positive integer or null")
        start_urls_raw = value.get("start_urls", [])
        if not isinstance(start_urls_raw, list):
            raise ValueError("start_urls must be a list of strings")
        start_urls = []
        for item in start_urls_raw:
            if not isinstance(item, str):
                raise ValueError("start_urls items must be strings")
            start_urls.append(item.strip())
        engine = value.get("engine")
        if engine is not None and not isinstance(engine, str):
            raise ValueError("engine must be a string or null")
        if engine is not None:
            engine = engine.strip().lower()
        browser = value.get("browser")
        if browser is not None and not isinstance(browser, str):
            raise ValueError("browser must be a string or null")
        if browser is not None:
            browser = browser.strip()
        window_width = value.get("window_width")
        if window_width is not None and (type(window_width) is not int or window_width < 100):
            raise ValueError("window_width must be an integer >= 100 or null")
        window_height = value.get("window_height")
        if window_height is not None and (type(window_height) is not int or window_height < 100):
            raise ValueError("window_height must be an integer >= 100 or null")
        return cls(
            default_tabs=default_tabs,
            start_urls=start_urls,
            engine=engine,
            browser=browser,
            window_width=window_width,
            window_height=window_height,
        )


@dataclass
class Profile:
    id: str
    name: str
    created_at: str
    data_dir: str
    last_launched_at: Optional[str] = None
    engine: Optional[str] = None
    launch_config: Optional[LaunchConfig] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        if self.launch_config is not None:
            data["launch_config"] = self.launch_config.to_dict()
        return data

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "Profile":
        if not isinstance(value, dict):
            raise ValueError("profile metadata must be a JSON object")
        required = ("id", "name", "created_at", "data_dir")
        if any(key not in value for key in required):
            raise ValueError("profile metadata is missing a required field")
        for key in required:
            if not isinstance(value[key], str):
                raise ValueError(f"profile field {key} must be a string")
        last_launched_at = value.get("last_launched_at")
        if last_launched_at is not None and not isinstance(last_launched_at, str):
            raise ValueError("profile field last_launched_at must be a string or null")
        engine = value.get("engine")
        if engine is not None and not isinstance(engine, str):
            raise ValueError("profile field engine must be a string or null")
        launch_config = None
        if "launch_config" in value and value["launch_config"] is not None:
            launch_config = LaunchConfig.from_dict(value["launch_config"])
        return cls(
            id=value["id"],
            name=value["name"],
            created_at=value["created_at"],
            data_dir=value["data_dir"],
            last_launched_at=last_launched_at,
            engine=engine,
            launch_config=launch_config,
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
        if type(schema_version) is not int or schema_version not in _SUPPORTED_METADATA_SCHEMA_VERSIONS:
            raise ValueError(f"unsupported metadata schema version: {schema_version}")
        if "profiles" not in value:
            raise ValueError("metadata is missing required field: profiles")
        profiles_list = value["profiles"]
        if not isinstance(profiles_list, list):
            raise ValueError("profiles must be a list")
        profiles = [Profile.from_dict(item) for item in profiles_list]
        return cls(schema_version=int(schema_version), profiles=profiles)
