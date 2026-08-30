"""Runtime state files: paths, atomic private writes, validation and error reports.

Every byte written here lands in a runtime directory that must stay private to
the current user (mode 0600 files, atomic replace-on-write).
"""

import json
import os
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..fsops import replace_with_retry as _replace_with_retry
from ..fsops import write_all as _write_all

_MAX_ERROR_BYTES = 4096
RUNNING_STATE_PROTOCOL_VERSION = 2
_DIRECT_STATE_FIELDS = frozenset(
    {
        "protocol_version",
        "engine",
        "profile_id",
        "pid",
        "launcher_pid",
        "process_create_time",
        "tabs",
        "channel",
        "started_at",
        "status",
        "closing",
    }
)
_PLAYWRIGHT_STATE_FIELDS = frozenset(
    {
        "protocol_version",
        "engine",
        "profile_id",
        "controller_pid",
        "controller_started_at",
        "launcher_pid",
        "port",
        "token",
        "tabs",
        "page_count",
        "channel",
        "status",
        "closing",
        "browser_channel",
        "start_urls",
        "window_width",
        "window_height",
        "legacy_controller",
        "browser_pid",
        "browser_create_time",
        "headless",
        "pid",
    }
)


def _runtime_dir(data_dir: str, runtime_dir: Path | None) -> Path:
    if runtime_dir is not None:
        selected = runtime_dir
    else:
        data_path = Path(data_dir)
        profile_dir = data_path.parent
        profiles_dir = profile_dir.parent
        if profiles_dir.name == "profiles":
            selected = profiles_dir.parent / "runtime" / profile_dir.name
        else:
            selected = profile_dir
    data_path = Path(data_dir)
    try:
        selected.resolve(strict=False).relative_to(data_path.resolve(strict=False))
    except ValueError:
        return selected
    raise ValueError("runtime directory cannot be inside browser-data")


def state_path(data_dir: str, runtime_dir: Path | None = None) -> Path:
    return _runtime_dir(data_dir, runtime_dir) / "running.json"


def error_path(data_dir: str, runtime_dir: Path | None = None) -> Path:
    return _runtime_dir(data_dir, runtime_dir) / "controller.error"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_private_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    fd = None
    try:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
        fd = os.open(str(temporary), flags, 0o600)
        _write_all(fd, payload)
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.chmod(temporary, 0o600)
        _replace_with_retry(temporary, path)
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _atomic_private_json(path: Path, value: dict[str, Any]) -> None:
    _atomic_private_bytes(path, json.dumps(value).encode("utf-8"))


StateDict = dict[str, Any]


def _valid_state(value: StateDict, profile_id: str | None = None) -> bool:
    if value.get("engine") != "playwright" or set(value) - _PLAYWRIGHT_STATE_FIELDS:
        return False
    if (
        type(value.get("protocol_version")) is not int
        or value["protocol_version"] != RUNNING_STATE_PROTOCOL_VERSION
    ):
        return False
    if not isinstance(value.get("profile_id"), str) or not value["profile_id"]:
        return False
    if profile_id is not None and value["profile_id"] != profile_id:
        return False
    if not isinstance(value.get("token"), str) or len(value["token"]) < 32:
        return False
    if type(value.get("controller_pid")) is not int or value["controller_pid"] < 0:
        return False
    if type(value.get("port")) is not int or not 0 <= value["port"] <= 65535:
        return False
    if type(value.get("tabs")) is not int or value["tabs"] < 1:
        return False
    if value.get("status") not in {"starting", "running", "closing"}:
        return False
    if "launcher_pid" in value and (type(value["launcher_pid"]) is not int or value["launcher_pid"] < 0):
        return False
    if "closing" in value and type(value["closing"]) is not bool:
        return False
    if not isinstance(value.get("controller_started_at"), str):
        return False
    try:
        started_at = datetime.fromisoformat(value["controller_started_at"])
    except (TypeError, ValueError):
        return False
    if "browser_pid" in value and (type(value["browser_pid"]) is not int or value["browser_pid"] < 0):
        return False
    if (
        "browser_create_time" in value
        and value["browser_create_time"] is not None
        and not isinstance(value["browser_create_time"], (int, float))
    ):
        return False
    if "headless" in value and type(value["headless"]) is not bool:
        return False
    return started_at.tzinfo is not None and started_at.utcoffset() is not None


def _valid_direct_state(value: StateDict, profile_id: str) -> bool:
    if (
        value.get("engine") != "direct"
        or value.get("profile_id") != profile_id
        or set(value) - _DIRECT_STATE_FIELDS
        or type(value.get("protocol_version")) is not int
        or value["protocol_version"] != RUNNING_STATE_PROTOCOL_VERSION
    ):
        return False
    if type(value.get("pid")) is not int or type(value.get("launcher_pid")) is not int:
        return False
    if value["pid"] < 0 or value["launcher_pid"] < 1:
        return False
    if type(value.get("tabs")) is not int or value["tabs"] < 1:
        return False
    if not isinstance(value.get("channel"), str) or not value["channel"]:
        return False
    if value.get("status") not in {"starting", "running", "closing"}:
        return False
    if "closing" in value and type(value["closing"]) is not bool:
        return False
    pid = value["pid"]
    process_create_time = value.get("process_create_time")
    # None is legal on platforms that cannot read process create times;
    # identity checks degrade to PID liveness for such states.
    if pid > 0 and process_create_time is not None and not isinstance(process_create_time, (int, float)):
        return False
    if not isinstance(value.get("started_at"), str):
        return False
    try:
        started_at = datetime.fromisoformat(value["started_at"])
    except (TypeError, ValueError):
        return False
    return started_at.tzinfo is not None and started_at.utcoffset() is not None


def _upgrade_legacy_state(path: Path, value: StateDict, profile_id: str) -> StateDict:
    version = value.get("protocol_version", 0)
    if type(version) is not int or version < 0 or version > RUNNING_STATE_PROTOCOL_VERSION:
        return value
    upgraded = dict(value)
    try:
        modified_at = path.stat().st_mtime
    except OSError:
        return value
    while version < RUNNING_STATE_PROTOCOL_VERSION:
        if version == 0:
            engine = "direct" if upgraded.get("engine") == "direct" else "playwright"
            if engine == "playwright":
                try:
                    pid = int(upgraded.get("controller_pid", upgraded.get("pid", 0)))
                    port = int(upgraded.get("port", 0))
                except (TypeError, ValueError):
                    return value
                token = upgraded.get("token")
                if pid < 1 or port < 1 or not isinstance(token, str) or len(token) < 32:
                    return value
                upgraded.update(
                    {
                        "profile_id": profile_id,
                        "controller_pid": pid,
                        "controller_started_at": upgraded.get("controller_started_at")
                        or datetime.fromtimestamp(modified_at, timezone.utc).isoformat(),
                        "status": upgraded.get("status", "running"),
                        "legacy_controller": True,
                    }
                )
            upgraded["protocol_version"] = 1
            version = 1
        elif version == 1:
            upgraded["engine"] = "direct" if upgraded.get("engine") == "direct" else "playwright"
            upgraded["protocol_version"] = 2
            version = 2
    if upgraded == value:
        return upgraded
    backup_path = path.with_name(f"{path.name}.v{value.get('protocol_version', 0)}.bak")
    try:
        if not backup_path.exists():
            _atomic_private_bytes(backup_path, json.dumps(value).encode("utf-8"))
        # Late-bound so patches of profiledock.process_manager._atomic_private_json keep applying.
        from profiledock.process_manager import _atomic_private_json as _atomic_private_json_impl

        _atomic_private_json_impl(path, upgraded)
    except OSError:
        return value
    return upgraded


def _write_error(
    path: Path,
    error_type: str,
    message: str,
    channel: str = "",
    redactions: Iterable[str] = (),
) -> None:
    for secret in redactions:
        if secret:
            message = message.replace(secret, "[redacted]")
    base: dict[str, Any] = {"error_type": error_type}
    if channel:
        base["channel"] = channel
    low = 0
    high = len(message)
    encoded = b""
    while low <= high:
        middle = (low + high) // 2
        payload = {**base, "message": message[:middle]}
        candidate = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        if len(candidate) <= _MAX_ERROR_BYTES:
            encoded = candidate
            low = middle + 1
        else:
            high = middle - 1
    try:
        _atomic_private_bytes(path, encoded)
    except OSError:
        pass


def _read_error(path: Path) -> StateDict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "error_type" in data:
            return data
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return None


def _read_state(path: Path) -> StateDict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return None


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def state_file_is_unreadable(state_file: Path) -> bool:
    """True when a running-state file exists but cannot be parsed as a JSON object.

    An unparseable file cannot verify or protect a live process, so it is a safe
    cleanup candidate; readable-but-invalid files are deliberately refused instead.
    A missing file is not unreadable.
    """
    if not state_file.is_file():
        return False
    return not isinstance(_read_state(state_file), dict)
