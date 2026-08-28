"""Process management facade.

The implementation lives in the :mod:`profiledock.process` package:

- ``process.errors``     — shared exception types
- ``process.state``      — runtime state files (paths, atomic private writes,
  schema validation, legacy upgrades, error reports)
- ``process.identity``   — process identity, discovery and termination
  primitives (all platform-specific code lives here)
- ``process.ipc``        — controller client communication
- ``process.direct``     — direct Chrome engine lifecycle
- ``process.playwright`` — Playwright engine launcher lifecycle
- ``process.controller`` — controller subprocess entry point (IPC server side)
- ``process.manager``    — status reporting and close orchestration

This module keeps the historical import surface stable: every name that used
to be defined here is re-exported, and ``python -m profiledock.process_manager
--controller ...`` remains the controller subprocess entry point.

A handful of primitives (``_alive``, ``_is_matching_process``,
``_get_process_create_time``, ``_list_processes``, ``_stop_process``,
``_atomic_private_json``, ``_controller_available``,
``_system_browser_executable``, ``is_running``, ``_MAX_RESPONSE_BYTES``) are
resolved through this facade at call time by the submodules, so monkeypatching
``profiledock.process_manager.<name>`` keeps affecting behaviour exactly as it
did before the split.
"""

import argparse
import ctypes
import hmac
import json
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from subprocess import Popen
from typing import IO, TYPE_CHECKING, Any, Optional
from uuid import uuid4

from .browser_detection import system_browser_executable as _system_browser_impl
from .fsops import replace_with_retry as _replace_with_retry
from .fsops import write_all as _write_all
from .process.controller import (
    _context_alive,
    _controller,
    _encode_ipc_response,
    _execute_ipc_command,
    _launch_context,
    _wait_for_close,
    main,
)
from .process.direct import _close_direct, _system_browser_executable, start_direct_chrome
from .process.errors import BrowserLaunchError, ProfileRunningError
from .process.identity import (
    _CHROMIUM_PROCESS_NAMES,
    _alive,
    _close_stderr,
    _find_browser_pid,
    _get_process_create_time,
    _is_chromium_process_name,
    _is_matching_process,
    _kernel32,
    _list_processes,
    _parse_linux_process_stat,
    _signal_posix_process_group,
    _stderr_message,
    _stop_process,
    _terminate_matching_process,
)
from .process.ipc import (
    _IPC_COMMANDS,
    _MAX_COMMAND_BYTES,
    _MAX_RESPONSE_BYTES,
    _controller_available,
    send_controller_command,
)
from .process.manager import close_controller, get_status, is_active_for_mutation, is_running
from .process.playwright import _close_playwright, start_controller
from .process.state import (
    _DIRECT_STATE_FIELDS,
    _MAX_ERROR_BYTES,
    _PLAYWRIGHT_STATE_FIELDS,
    RUNNING_STATE_PROTOCOL_VERSION,
    StateDict,
    _atomic_private_bytes,
    _atomic_private_json,
    _read_error,
    _read_state,
    _runtime_dir,
    _unlink_quietly,
    _upgrade_legacy_state,
    _utc_now,
    _valid_direct_state,
    _valid_state,
    _write_error,
    error_path,
    state_file_is_unreadable,
    state_path,
)

if TYPE_CHECKING:
    pass

__all__ = [
    "IO",
    "RUNNING_STATE_PROTOCOL_VERSION",
    "TYPE_CHECKING",
    "_CHROMIUM_PROCESS_NAMES",
    "_DIRECT_STATE_FIELDS",
    "_IPC_COMMANDS",
    "_MAX_COMMAND_BYTES",
    "_MAX_ERROR_BYTES",
    "_MAX_RESPONSE_BYTES",
    "_PLAYWRIGHT_STATE_FIELDS",
    "Any",
    "BrowserLaunchError",
    "Iterable",
    "Optional",
    "Path",
    "Popen",
    "ProfileRunningError",
    "StateDict",
    "_alive",
    "_atomic_private_bytes",
    "_atomic_private_json",
    "_close_direct",
    "_close_playwright",
    "_close_stderr",
    "_context_alive",
    "_controller",
    "_controller_available",
    "_encode_ipc_response",
    "_execute_ipc_command",
    "_find_browser_pid",
    "_get_process_create_time",
    "_is_chromium_process_name",
    "_is_matching_process",
    "_kernel32",
    "_launch_context",
    "_list_processes",
    "_parse_linux_process_stat",
    "_read_error",
    "_read_state",
    "_replace_with_retry",
    "_runtime_dir",
    "_signal_posix_process_group",
    "_stderr_message",
    "_stop_process",
    "_system_browser_executable",
    "_system_browser_impl",
    "_terminate_matching_process",
    "_unlink_quietly",
    "_upgrade_legacy_state",
    "_utc_now",
    "_valid_direct_state",
    "_valid_state",
    "_wait_for_close",
    "_write_all",
    "_write_error",
    "argparse",
    "close_controller",
    "ctypes",
    "datetime",
    "error_path",
    "get_status",
    "hmac",
    "is_active_for_mutation",
    "is_running",
    "json",
    "lru_cache",
    "main",
    "os",
    "send_controller_command",
    "signal",
    "socket",
    "start_controller",
    "start_direct_chrome",
    "state_file_is_unreadable",
    "state_path",
    "subprocess",
    "sys",
    "time",
    "timezone",
    "uuid4",
]

if __name__ == "__main__":
    main()
