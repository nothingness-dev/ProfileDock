import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from profiledock.process_manager import (
    ProfileRunningError,
    _alive,
    _launch_context,
    close_controller,
    is_running,
    start_controller,
    state_path,
)
from profiledock.profile_manager import ProfileManager

pytestmark = pytest.mark.browser


def _process_alive(pid: int) -> bool:
    return _alive(pid)


def _wait_until(predicate, timeout: float = 10) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


def _terminate_owned_tree(pid: int) -> None:
    if not _process_alive(pid):
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.kill(pid, 9)
        except (OSError, ProcessLookupError):
            pass


@pytest.fixture(scope="session")
def browser_available(tmp_path_factory):
    playwright = pytest.importorskip("playwright.sync_api")
    probe = tmp_path_factory.mktemp("browser-probe") / "browser-data"
    try:
        with playwright.sync_playwright() as instance:
            context = _launch_context(instance, str(probe), True)
            context.close()
    except playwright.Error as exc:
        pytest.skip(f"no supported browser found: {exc}")


@pytest.fixture
def controller_env(tmp_path, browser_available):
    manager = ProfileManager(tmp_path)
    profile = manager.create("Test")
    data_dir = Path(profile.data_dir)
    owned_pids: set[int] = set()
    yield manager, profile, data_dir, owned_pids
    if is_running(str(data_dir)):
        try:
            close_controller(str(data_dir), timeout=10)
        except Exception:
            pass
    for pid in owned_pids:
        if _process_alive(pid):
            _terminate_owned_tree(pid)
    state_path(str(data_dir)).unlink(missing_ok=True)
    shutil.rmtree(tmp_path, ignore_errors=True)


def _start(data_dir: Path, owned_pids: set[int], tabs: int = 1):
    state = start_controller(str(data_dir), tabs, headless=True)
    owned_pids.add(state["pid"])
    return state


def _close(data_dir: Path, owned_pids: set[int], pid: int) -> None:
    close_controller(str(data_dir), timeout=10)
    assert _wait_until(lambda: not _process_alive(pid))
    assert not state_path(str(data_dir)).exists()
    assert not is_running(str(data_dir))
    owned_pids.discard(pid)


def test_start_reports_ready_with_actual_page_count(controller_env):
    manager, profile, data_dir, owned_pids = controller_env
    state = _start(data_dir, owned_pids, tabs=3)
    assert state["port"] > 0
    assert state["pid"] > 0
    assert state["token"]
    assert state["page_count"] == 3
    assert is_running(str(data_dir))


def test_duplicate_launch_is_rejected(controller_env):
    manager, profile, data_dir, owned_pids = controller_env
    _start(data_dir, owned_pids)
    with pytest.raises(ProfileRunningError):
        start_controller(str(data_dir), 1, headless=True)


def test_stale_running_state_is_cleaned(controller_env):
    manager, profile, data_dir, owned_pids = controller_env
    path = state_path(str(data_dir))
    path.write_text(
        json.dumps({"pid": 999999, "port": 12345, "token": "x", "tabs": 1}),
        encoding="utf-8",
    )
    assert not is_running(str(data_dir))
    assert not path.exists()


def test_close_exits_controller_and_removes_state(controller_env):
    manager, profile, data_dir, owned_pids = controller_env
    state = _start(data_dir, owned_pids, tabs=2)
    _close(data_dir, owned_pids, state["pid"])


def test_relaunch_after_graceful_close(controller_env):
    manager, profile, data_dir, owned_pids = controller_env
    first = _start(data_dir, owned_pids, tabs=2)
    _close(data_dir, owned_pids, first["pid"])
    second = _start(data_dir, owned_pids, tabs=1)
    assert second["page_count"] == 1
    _close(data_dir, owned_pids, second["pid"])


def test_persistent_state_survives_controller_cycles(controller_env):
    playwright = pytest.importorskip("playwright.sync_api")
    manager, profile, data_dir, owned_pids = controller_env
    with playwright.sync_playwright() as instance:
        context = _launch_context(instance, str(data_dir), True)
        context.add_cookies(
            [
                {
                    "name": "profiledock-controller",
                    "value": "persisted",
                    "url": "https://example.com",
                    "expires": time.time() + 3600,
                }
            ]
        )
        context.close()

    first = _start(data_dir, owned_pids, tabs=2)
    _close(data_dir, owned_pids, first["pid"])
    second = _start(data_dir, owned_pids, tabs=1)
    _close(data_dir, owned_pids, second["pid"])

    with playwright.sync_playwright() as instance:
        context = _launch_context(instance, str(data_dir), True)
        cookies = context.cookies("https://example.com")
        context.close()
    assert any(
        cookie["name"] == "profiledock-controller"
        and cookie["value"] == "persisted"
        for cookie in cookies
    )
