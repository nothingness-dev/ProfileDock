"""Controller client communication.

The controller subprocess listens on a loopback socket; commands are
authenticated with the per-launch token stored in the running-state file and
responses are size-capped JSON lines.
"""

import json
import socket
from pathlib import Path
from typing import Any

from .errors import BrowserLaunchError, ProfileRunningError
from .playwright import start_controller
from .state import (
    StateDict,
    _read_state,
    _upgrade_legacy_state,
    _valid_direct_state,
    _valid_state,
    state_path,
)

_MAX_COMMAND_BYTES = 65536
_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_IPC_COMMANDS = frozenset(
    {
        "probe",
        "close",
        "tabs",
        "open_tab",
        "close_tab",
        "read_page",
        "eval",
        "cookies",
        "set_cookies",
        "screenshot",
        "pdf",
    }
)


def _controller_available(state: StateDict) -> bool:
    try:
        port = int(state.get("port", 0))
        token = state.get("token", "")
        if port < 1 or not isinstance(token, str) or not token:
            return False
        with socket.create_connection(("127.0.0.1", port), timeout=0.5) as connection:
            if state.get("legacy_controller"):
                return True
            connection.settimeout(0.5)
            connection.sendall(("probe:" + token + "\n").encode("utf-8"))
            return connection.recv(16) == b"ok\n"
    except (OSError, TypeError, ValueError):
        return False


def send_controller_command(
    data_dir: str,
    cmd: str,
    args: dict[str, Any] | None = None,
    runtime_dir: Path | None = None,
    timeout: float = 30.0,
    auto_start_headless: bool = True,
    proxy: str | None = None,
    user_agent: str | None = None,
    locale: str | None = None,
    timezone: str | None = None,
) -> dict[str, Any]:
    """Send a command to a Playwright controller, auto-starting headlessly if stopped.

    Identity presets (proxy/user_agent/locale/timezone) apply to the
    auto-started session exactly as a manual launch would; a profile
    configured with a proxy must never egress via the real IP because an
    automation command happened to arrive while it was stopped.
    """

    from profiledock.process_manager import _MAX_RESPONSE_BYTES as _max_response_bytes
    from profiledock.process_manager import _controller_available as _controller_available_impl
    from profiledock.process_manager import _is_matching_process as _is_matching_process_impl

    if cmd not in _IPC_COMMANDS:
        raise ValueError(f"unsupported controller command: {cmd}")
    if args is not None and not isinstance(args, dict):
        raise ValueError("controller command arguments must be an object")
    path = state_path(data_dir, runtime_dir)
    state = _read_state(path)
    profile_id = Path(data_dir).parent.name

    if state and state.get("engine") == "direct":
        if not _valid_direct_state(state, profile_id):
            raise ProfileRunningError(f"invalid direct runtime state for '{profile_id}'")
        if cmd == "probe":
            if _is_matching_process_impl(
                int(state["pid"]), state.get("process_create_time"), require_verification=True
            ):
                return {"status": "ok"}
            raise ProfileRunningError(f"profile '{profile_id}' is not running")
        if cmd == "close":
            from .direct import _close_direct

            _close_direct(path, state, timeout)
            return {"status": "ok"}
        cdp_port = state.get("cdp_port")
        if type(cdp_port) is not int or not 1 <= cdp_port <= 65535:
            raise ProfileRunningError(
                f"profile '{profile_id}' runs on the direct engine without a DevTools endpoint; "
                "relaunch it to enable automation"
            )
        if not _is_matching_process_impl(
            int(state["pid"]), state.get("process_create_time"), require_verification=True
        ):
            raise ProfileRunningError(f"profile '{profile_id}' is not running")
        from .direct_bridge import run_direct_cdp_command

        result = run_direct_cdp_command(data_dir, cmd, args, timeout=timeout, cdp_port=cdp_port)
        if result.get("status") == "error":
            raise BrowserLaunchError(str(result.get("message", "unknown direct browser error")))
        return result

    if state:
        state = _upgrade_legacy_state(path, state, profile_id)

    if (
        not state
        or not _valid_state(state, profile_id)
        or not state.get("port")
        or not _controller_available_impl(state)
    ):
        if not auto_start_headless:
            raise ProfileRunningError(f"profile '{profile_id}' is not running with Playwright controller")
        state = start_controller(
            data_dir,
            tabs=1,
            headless=True,
            runtime_dir=runtime_dir,
            proxy=proxy,
            user_agent=user_agent,
            locale=locale,
            timezone=timezone,
        )

    port = int(state.get("port", 0))
    token = str(state.get("token", ""))
    payload = {"cmd": cmd, "token": token, "args": args or {}}
    encoded_payload = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
    if len(encoded_payload) > _MAX_COMMAND_BYTES:
        raise ValueError("controller command exceeds the maximum request size")

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as connection:
            connection.settimeout(timeout)
            connection.sendall(encoded_payload)
            response_raw = b""
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                response_raw += chunk
                if len(response_raw) > _max_response_bytes:
                    raise BrowserLaunchError("profile controller response exceeds the maximum size")
                if b"\n" in chunk:
                    break
            if not response_raw:
                raise BrowserLaunchError("empty response from profile controller")
            response_line = response_raw.split(b"\n", 1)[0]
            decoded = json.loads(response_line.decode("utf-8"))
            if not isinstance(decoded, dict):
                raise BrowserLaunchError("invalid response from profile controller")
            res_obj: dict[str, Any] = decoded
            if res_obj.get("status") == "error":
                raise BrowserLaunchError(res_obj.get("message", "unknown controller error"))
            return res_obj
    except (OSError, json.JSONDecodeError) as exc:
        raise BrowserLaunchError(f"failed to communicate with controller: {exc}") from exc
