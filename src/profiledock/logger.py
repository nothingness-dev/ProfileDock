import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from uuid import uuid4

from .models import utc_now

DEFAULT_MAX_LOG_BYTES = 2 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 3
_REDACTED = "[redacted]"


def generate_correlation_id() -> str:
    return uuid4().hex[:12]


def sanitize_url(url: str) -> str:
    if not isinstance(url, str) or not url.strip():
        return ""
    clean = url.strip()
    if clean.startswith("about:"):
        return clean
    try:
        parsed = urlparse(clean)
        if not parsed.scheme or not parsed.netloc:
            return clean.split("?")[0].split("#")[0]
        sanitized = f"{parsed.scheme}://{parsed.netloc}"
        if parsed.path:
            path_parts = parsed.path.strip("/").split("/")
            if path_parts and path_parts[0]:
                sanitized += f"/{path_parts[0]}"
                if len(path_parts) > 1:
                    sanitized += "/..."
        return sanitized
    except Exception:
        return "[url]"


def redact_sensitive_data(message: str, secrets: Optional[List[str]] = None) -> str:
    if not isinstance(message, str):
        return str(message)
    redacted = message
    if secrets:
        for secret in secrets:
            if secret and len(secret) > 4:
                redacted = redacted.replace(secret, _REDACTED)

    redacted = re.sub(r"(token|secret|password|auth|cookie|key)=([^&\s]+)", r"\1=[redacted]", redacted, flags=re.IGNORECASE)
    redacted = re.sub(r'("token"|"secret"|"password"|"cookie"|"auth"|"key")\s*:\s*"[^"]+"', r'\1: "[redacted]"', redacted, flags=re.IGNORECASE)
    redacted = re.sub(r'Bearer\s+[A-Za-z0-9\-\._~\+\/]+=*', r'Bearer [redacted]', redacted, flags=re.IGNORECASE)
    return redacted



def rotate_log_file(log_file: Path, max_bytes: int = DEFAULT_MAX_LOG_BYTES, backup_count: int = DEFAULT_BACKUP_COUNT) -> None:
    try:
        if not log_file.exists() or log_file.stat().st_size < max_bytes:
            return

        for i in range(backup_count - 1, 0, -1):
            sfn = log_file.with_name(f"{log_file.name}.{i}")
            dfn = log_file.with_name(f"{log_file.name}.{i + 1}")
            if sfn.exists():
                if dfn.exists():
                    dfn.unlink(missing_ok=True)
                sfn.rename(dfn)

        dfn = log_file.with_name(f"{log_file.name}.1")
        if dfn.exists():
            dfn.unlink(missing_ok=True)
        log_file.rename(dfn)
    except Exception:
        pass


def write_log_entry(
    log_dir: Path,
    level: str,
    event: str,
    profile_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    engine: Optional[str] = None,
    command: Optional[str] = None,
    result: Optional[str] = None,
    browser_path: Optional[str] = None,
    pid: Optional[int] = None,
    error_category: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    secrets_to_redact: Optional[List[str]] = None,
    max_bytes: int = DEFAULT_MAX_LOG_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> None:
    try:
        log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            log_dir.chmod(0o700)

        entry: Dict[str, Any] = {
            "timestamp": utc_now(),
            "level": level.upper(),
            "event": event,
            "correlation_id": correlation_id or generate_correlation_id(),
        }

        if profile_id:
            entry["profile_id"] = profile_id
        if engine:
            entry["engine"] = engine
        if command:
            entry["command"] = command
        if result:
            entry["result"] = result
        if browser_path:
            entry["browser_path"] = str(browser_path)
        if pid:
            entry["pid"] = pid
        if error_category:
            entry["error_category"] = error_category

        if details:
            cleaned_details: Dict[str, Any] = {}
            for k, v in details.items():
                if isinstance(v, str):
                    cleaned_details[k] = redact_sensitive_data(v, secrets_to_redact)
                elif isinstance(v, list):
                    cleaned_details[k] = [
                        sanitize_url(item) if ("url" in k.lower() and isinstance(item, str)) else item
                        for item in v
                    ]
                else:
                    cleaned_details[k] = v
            entry["details"] = cleaned_details

        payload = json.dumps(entry) + "\n"

        target_files = [log_dir / "profiledock.log"]
        if profile_id:
            target_files.append(log_dir / f"profile_{profile_id}.log")

        for log_file in target_files:
            rotate_log_file(log_file, max_bytes=max_bytes, backup_count=backup_count)
            try:
                flags = os.O_CREAT | os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0)
                fd = os.open(str(log_file), flags, 0o600)
                with open(fd, "a", encoding="utf-8", closefd=True) as handle:
                    handle.write(payload)
            except OSError:
                pass
    except Exception:
        pass


def read_profile_logs(
    log_dir: Path,
    profile_id: Optional[str] = None,
    last_n: Optional[int] = None,
) -> List[Dict[str, Any]]:
    logs: List[Dict[str, Any]] = []
    if not log_dir.exists():
        return logs

    target_files: List[Path] = []
    if profile_id:
        p_file = log_dir / f"profile_{profile_id}.log"
        if p_file.exists():
            target_files.append(p_file)
        else:
            target_files.append(log_dir / "profiledock.log")
    else:
        target_files.append(log_dir / "profiledock.log")

    for f in target_files:
        if not f.exists():
            continue
        try:
            with f.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    clean = line.strip()
                    if not clean:
                        continue
                    try:
                        parsed = json.loads(clean)
                        if isinstance(parsed, dict):
                            if profile_id and parsed.get("profile_id") and parsed.get("profile_id") != profile_id:
                                continue
                            logs.append(parsed)
                    except Exception:
                        pass
        except Exception:
            pass

    logs.sort(key=lambda x: str(x.get("timestamp", "")))
    if last_n is not None and last_n > 0:
        return logs[-last_n:]
    return logs
