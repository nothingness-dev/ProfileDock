

from pathlib import Path

from .direct import _close_direct
from .errors import ProfileRunningError
from .identity import _terminate_matching_process
from .playwright import _close_playwright
from .state import (
    RUNNING_STATE_PROTOCOL_VERSION,
    StateDict,
    _read_error,
    _read_state,
    _unlink_quietly,
    _upgrade_legacy_state,
    _valid_direct_state,
    _valid_state,
    error_path,
    state_path,
)


def _reap_recorded_browser(state: StateDict, timeout: float) -> bool:

    raw_browser_pid = state.get("browser_pid")
    if type(raw_browser_pid) is not int or raw_browser_pid <= 0:
        return True
    return _terminate_matching_process(
        raw_browser_pid,
        state.get("browser_create_time"),
        min(max(timeout, 0.1), 5),
    )


def _launcher_starting(state: StateDict) -> bool:

    from profiledock.process_manager import _alive as alive_impl
    from profiledock.process_manager import (
        _get_process_create_time as create_time_impl,
    )

    launcher_pid = int(state.get("launcher_pid", -1))
    if launcher_pid <= 0:
        return False
    if not alive_impl(launcher_pid):
        return False
    recorded = state.get("launcher_create_time")
    if recorded is None:
        return True
    actual = create_time_impl(launcher_pid)
    if actual is None:
        return True
    return abs(actual - float(recorded)) < 2.0


def get_status(data_dir: str, clean_stale: bool = True, runtime_dir: Path | None = None) -> str:

    from profiledock.process_manager import _alive as _alive_impl
    from profiledock.process_manager import (
        _is_matching_process as _is_matching_process_impl,
    )

    path = state_path(data_dir, runtime_dir)
    err = error_path(data_dir, runtime_dir)
    if path.exists():
        state = _read_state(path)
        if not state or not isinstance(state, dict):
            return "error"
        state_version = state.get("protocol_version", 0)
        if type(state_version) is int and state_version > RUNNING_STATE_PROTOCOL_VERSION:
            return "error"
        state = _upgrade_legacy_state(path, state, Path(data_dir).parent.name)
        if state.get("engine") == "direct":
            if not _valid_direct_state(state, Path(data_dir).parent.name):
                return "error"
            pid = state["pid"]
            if pid > 0 and _is_matching_process_impl(pid, state.get("process_create_time")):
                if state.get("closing"):
                    return "closing"
                return "running"
            launcher_pid = state["launcher_pid"]
            if pid == 0 and launcher_pid > 0 and _launcher_starting(state):
                return "starting"
            if state.get("closing"):
                if clean_stale:
                    _unlink_quietly(path)
                    return "stopped"
                return "stale"
            if clean_stale:
                _unlink_quietly(path)
                return "crashed"
            return "stale"
        if not state or not _valid_state(state, Path(data_dir).parent.name):
            return "error"
        if state.get("closing"):
            pid = int(state.get("controller_pid", -1))
            if pid > 0 and _alive_impl(pid):
                return "closing"
            if clean_stale:
                _unlink_quietly(path)
                return "stopped"
            return "stale"
        pid = int(state.get("controller_pid", -1))
        if pid <= 0:
            if _launcher_starting(state):
                return "starting"
            if clean_stale:
                _unlink_quietly(path)
                return "stopped"
            return "stale"
        if not _alive_impl(pid):
            if state.get("closing"):
                if clean_stale:
                    if not _reap_recorded_browser(state, timeout=1.0):
                        return "error"
                    _unlink_quietly(path)
                    return "stopped"
                return "stale"
            if clean_stale:
                if not _reap_recorded_browser(state, timeout=1.0):
                    return "error"
                _unlink_quietly(path)
                return "crashed"
            return "stale"
        port = int(state.get("port", 0))
        if not port:
            return "starting"

        browser_pid = int(state.get("browser_pid", 0) or 0)
        if browser_pid > 0 and not _is_matching_process_impl(browser_pid, state.get("browser_create_time")):
            if clean_stale:
                _unlink_quietly(path)
                return "crashed"
            return "stale"
        return "running"
    if err.exists():
        err_data = _read_error(err)
        if err_data:
            return "error"
    return "stopped"


def is_running(data_dir: str, runtime_dir: Path | None = None) -> bool:
    return get_status(data_dir, clean_stale=True, runtime_dir=runtime_dir) in (
        "starting",
        "running",
        "closing",
        "error",
    )


def is_active_for_mutation(data_dir: str, runtime_dir: Path | None = None) -> bool:

    from profiledock.process_manager import _alive as _alive_impl
    from profiledock.process_manager import (
        _controller_available as _controller_available_impl,
    )
    from profiledock.process_manager import (
        _is_matching_process as _is_matching_process_impl,
    )

    path = state_path(data_dir, runtime_dir)
    state = _read_state(path)
    if not state:
        return path.exists()
    profile_id = Path(data_dir).parent.name
    state = _upgrade_legacy_state(path, state, profile_id)
    if state.get("engine") == "direct":
        if not _valid_direct_state(state, profile_id):
            return True
        pid = int(state.get("pid", -1))
        launcher_pid = int(state.get("launcher_pid", -1))
        return _is_matching_process_impl(pid, state.get("process_create_time")) or (
            pid == 0 and _alive_impl(launcher_pid)
        )
    upgraded = dict(state)
    if not _valid_state(upgraded, profile_id):
        return True
    controller_pid = int(upgraded.get("controller_pid", -1))
    launcher_pid = int(upgraded.get("launcher_pid", -1))

    from profiledock.process_manager import _is_matching_process as _matching_impl

    browser_pid = int(upgraded.get("browser_pid", 0) or 0)
    if (
        controller_pid > 0
        and _alive_impl(controller_pid)
        and browser_pid > 0
        and not _matching_impl(browser_pid, upgraded.get("browser_create_time"))
    ):
        return False

    if browser_pid > 0 and _matching_impl(browser_pid, upgraded.get("browser_create_time")):
        return True
    return (
        _alive_impl(controller_pid)
        or _controller_available_impl(upgraded)
        or (controller_pid <= 0 and _launcher_starting(upgraded))
    )


def close_controller(data_dir: str, timeout: float = 15, runtime_dir: Path | None = None) -> None:

    from profiledock.process_manager import _alive as _alive_impl
    from profiledock.process_manager import (
        _get_process_create_time as _get_process_create_time_impl,
    )
    from profiledock.process_manager import is_running as _is_running_impl

    path = state_path(data_dir, runtime_dir)
    initial_state = _read_state(path)
    if path.exists() and not initial_state:
        raise ProfileRunningError(
            "profile running state is invalid; refusing to remove ambiguous state. "
            "Run 'profiledock doctor --repair' to clean up unreadable state files."
        )
    if initial_state:
        initial_state = _upgrade_legacy_state(path, initial_state, Path(data_dir).parent.name)
    if initial_state and initial_state.get("engine") == "direct":
        if not _valid_direct_state(initial_state, Path(data_dir).parent.name):
            raise ProfileRunningError(
                "profile running state is invalid; refusing to signal an unverified process"
            )
        initial_pid = int(initial_state.get("pid", -1))
        expected_create_time = initial_state.get("process_create_time")
        if initial_pid > 0 and _alive_impl(initial_pid):
            actual_create_time = _get_process_create_time_impl(initial_pid)

            if (
                expected_create_time is not None
                and actual_create_time is not None
                and abs(actual_create_time - expected_create_time) >= 2.0
            ):
                _unlink_quietly(path)
                raise ProfileRunningError(
                    "profile process is not running (PID was reused by another process)", stopped=True
                )
    if initial_state and initial_state.get("engine") != "direct":
        raw_controller_pid = initial_state.get("controller_pid")
        raw_browser_pid = initial_state.get("browser_pid")
        controller_pid = raw_controller_pid if type(raw_controller_pid) is int else 0
        if controller_pid > 0 and not _alive_impl(controller_pid) and not initial_state.get("closing"):
            if type(raw_browser_pid) is int and raw_browser_pid > 0:
                _terminate_matching_process(
                    raw_browser_pid,
                    initial_state.get("browser_create_time"),
                    min(max(timeout, 0.1), 5),
                )
            _unlink_quietly(path)
            raise ProfileRunningError("profile is not running", stopped=True)
    if not _is_running_impl(data_dir, runtime_dir):
        raise ProfileRunningError("profile is not running", stopped=True)
    state = _read_state(path)
    if not state:
        raise ProfileRunningError("profile is not running", stopped=True)

    if state.get("engine") == "direct":
        _close_direct(path, state, timeout)
        return

    state = _upgrade_legacy_state(path, state, Path(data_dir).parent.name)
    if not _valid_state(state, Path(data_dir).parent.name):
        raise ProfileRunningError(
            "profile running state is invalid; refusing unauthenticated controller access"
        )
    _close_playwright(path, state, timeout)
