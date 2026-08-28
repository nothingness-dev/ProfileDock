"""Controller client communication.

The controller subprocess listens on a loopback socket; commands are
authenticated with the per-launch token stored in the running-state file and
responses are size-capped JSON lines.
"""

import json
import socket
from pathlib import Path
from typing import Any, Optional

from .errors import BrowserLaunchError, ProfileRunningError
from .playwright import start_controller
from .state import StateDict, _read_state, _upgrade_legacy_state, _valid_state, state_path

_MAX_COMMAND_BYTES = 65536
_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_IPC_COMMANDS = frozenset({"probe", "close", "tabs", "open_tab", "close_tab", "read_page", "eval", "cookies"})


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
    args: Optional[dict[str, Any]] = None,
    runtime_dir: Optional[Path] = None,
    timeout: float = 30.0,
    auto_start_headless: bool = True,
) -> dict[str, Any]:
    """Send a command to a Playwright controller, auto-starting headlessly if stopped."""
    # Late-bound so patches of profiledock.process_manager._controller_available
    # and ._MAX_RESPONSE_BYTES keep applying.
    from profiledock.process_manager import _MAX_RESPONSE_BYTES as _max_response_bytes
    from profiledock.process_manager import _controller_available as _controller_available_impl

    if cmd not in _IPC_COMMANDS:
        raise ValueError(f"unsupported controller command: {cmd}")
    if args is not None and not isinstance(args, dict):
        raise ValueError("controller command arguments must be an object")
    path = state_path(data_dir, runtime_dir)
    state = _read_state(path)
    profile_id = Path(data_dir).parent.name

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
        state = start_controller(data_dir, tabs=1, headless=True, runtime_dir=runtime_dir)

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
