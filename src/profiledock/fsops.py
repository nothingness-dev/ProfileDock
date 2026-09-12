import json
import os
import shutil
import time
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from .data_root import _is_link


def sha256_file(path: Path) -> str:

    digest = sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def replace_with_retry(source: Path, target: Path, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    poll_interval = 0.005
    while True:
        try:
            source.replace(target)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(poll_interval)
            poll_interval = min(poll_interval * 1.5, 0.05)


def rmtree_with_retry(directory: Path, timeout: float = 5.0) -> None:

    deadline = time.monotonic() + timeout
    poll_interval = 0.02
    while True:
        try:
            shutil.rmtree(directory, ignore_errors=False)
            return
        except FileNotFoundError:
            return
        except (PermissionError, OSError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(poll_interval)
            poll_interval = min(poll_interval * 1.5, 0.2)


def write_all(fd: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(fd, payload[offset:])
        if written < 1:
            raise OSError("write returned no data")
        offset += written


def write_private_json(path: Path, value: Any) -> None:
    target = path.expanduser().absolute()
    if _is_link(target) or (target.exists() and not target.is_file()):
        raise OSError(f"unsafe JSON output target: {target}")
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    payload = (json.dumps(value, indent=2) + "\n").encode("utf-8")
    fd: int | None = None
    try:
        fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        write_all(fd, payload)
        os.fsync(fd)
        os.close(fd)
        fd = None
        replace_with_retry(temporary, target)
    except OSError as exc:
        raise OSError(f"cannot write '{target}': {exc.strerror or exc}") from exc
    finally:
        if fd is not None:
            os.close(fd)
        temporary.unlink(missing_ok=True)
