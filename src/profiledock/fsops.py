import os
import time
from pathlib import Path


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


def write_all(fd: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(fd, payload[offset:])
        if written < 1:
            raise OSError("write returned no data")
        offset += written
