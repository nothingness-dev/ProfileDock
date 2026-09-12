

import os
from pathlib import Path

from ..validation import validate_url
from .errors import BrowserLaunchError
from .state import _unlink_quietly, error_path, state_path


def validate_launch_request(
    data_dir: str,
    tabs: int,
    window_width: int | None,
    window_height: int | None,
    start_urls: list[str] | None,
    executable_path: Path | None = None,
    browser: str | None = None,
) -> None:

    if tabs < 1:
        raise ValueError("tab count must be at least 1")
    if executable_path is not None and browser is not None:
        raise ValueError("specify either executable_path or browser, not both")
    if (window_width is None) != (window_height is None):
        raise ValueError("both window_width and window_height must be specified together")
    if window_width is not None and (window_width < 100 or window_height is None or window_height < 100):
        raise ValueError("window width and height must be at least 100")
    if len(list(start_urls or [])) > tabs:
        raise ValueError("number of start URLs cannot exceed the requested tab count")
    for url in start_urls or []:
        validate_url(url)
    if not Path(data_dir).is_dir():
        raise BrowserLaunchError(
            "profile data directory is missing or invalid",
            "invalid_data_directory",
        )
    if not Path(data_dir).parent.name:
        raise BrowserLaunchError(
            "profile data directory must not sit directly under a filesystem root",
            "invalid_data_directory",
        )


def prepare_runtime_dir(data_dir: str, runtime_dir: Path | None) -> None:

    path = state_path(data_dir, runtime_dir)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        path.parent.chmod(0o700)
    _unlink_quietly(error_path(data_dir, runtime_dir))
