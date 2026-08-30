"""Service layer between the ProfileDock TUI and the core managers.

The TUI never shells out through the Typer application; it calls these
functions on background workers so the interface stays responsive while
backups, launches, or diagnostics run. Every operation returns an
:class:`ActionResult` carrying the equivalent CLI argv, an exit code, and a
pre-formatted Rich body, which keeps the output pane byte-for-byte faithful
to what the same command would print in a plain terminal.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.table import Table
from rich.text import Text

from ..backup import create_backup_archive
from ..browser_detection import browser_rows
from ..cli_contract import EXIT_SUCCESS, EXIT_USER_ERROR, error_category
from ..cli_support import format_cpu_percent
from ..data_root import DataPaths
from ..doctor import STATUS_FAILED, STATUS_OK, STATUS_WARNING, DiagnosticCheck, run_diagnostics
from ..fsops import write_private_json
from ..logger import read_profile_logs
from ..models import Profile
from ..process_manager import (
    BrowserLaunchError,
    ProfileRunningError,
    close_controller,
    get_status,
    send_controller_command,
    start_controller,
    start_direct_chrome,
    state_path,
)
from ..profile_manager import AmbiguousProfileError, ProfileManager, ProfileNotFoundError
from ..restore import restore_backup_archive
from ..validation import ValidationError, validate_browser, validate_url

_HINTS = {
    "not_found": "run 'list' to see existing profiles",
    "ambiguous_profile": "use the full profile ID",
    "profile_active": "close the profile first",
    "browser_launch_failed": "run 'doctor' to check the browser installation",
    "security_violation": "inspect the archive path before retrying",
    "storage_error": "check disk space and data-root permissions",
}


class BackendError(Exception):
    """A failed operation with a CLI-equivalent category and hint."""

    def __init__(self, message: str, category: str | None = None) -> None:
        super().__init__(message)
        resolved = category or error_category(message)
        self.message = message
        self.category = resolved
        self.hint = _HINTS.get(resolved, "")


@dataclass
class ProfileRow:
    """A profile plus its cached runtime facts for list rendering."""

    profile: Profile
    status: str = "stopped"
    pid: int | None = None
    size_bytes: int | None = None

    @property
    def profile_id(self) -> str:
        return self.profile.id

    @property
    def name(self) -> str:
        return self.profile.name


@dataclass
class BrowserInfo:
    """An auto-detected browser binary for the launch/set-engine pickers."""

    name: str
    path: str
    version: str = ""
    is_default: bool = False

    def label(self) -> str:
        version = f" {self.version}" if self.version else ""
        suffix = "  (Default System Binary)" if self.is_default else ""
        return f"{self.name}{version}{suffix}"


@dataclass
class ActionResult:
    """Outcome of one backend operation, rendered verbatim in the TUI."""

    argv: list[str]
    exit_code: int
    body: Text
    category: str = ""
    hint: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == EXIT_SUCCESS


# ---------------------------------------------------------------------------
# formatting helpers


def compute_size(path_str: str) -> int | None:
    data_dir = Path(path_str)
    if not data_dir.is_dir():
        return None
    total = 0
    try:
        for root_dir, _, filenames in os.walk(data_dir):
            for fname in filenames:
                try:
                    total += (Path(root_dir) / fname).stat().st_size
                except OSError:
                    continue
    except OSError:
        return None
    return total


def format_size(num_bytes: int | None) -> str:
    if num_bytes is None:
        return "unknown"
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    if num_bytes < 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.1f} MB"
    return f"{num_bytes / (1024 * 1024 * 1024):.2f} GB"


def _status_text(status: str, pid: int | None = None) -> Text:
    color = {
        "running": "green",
        "starting": "yellow",
        "closing": "yellow",
        "stale": "red",
        "error": "red",
    }.get(status, "cyan")
    label = status.upper()
    if status == "running" and pid:
        label = f"RUNNING PID {pid}"
    return Text(label, style=color)


def _kv_table(title: str) -> Table:
    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0), title=title, title_justify="left")
    table.add_column("key", style="bold", no_wrap=True)
    table.add_column("value")
    return table


# ---------------------------------------------------------------------------
# queries


def _manager(paths: DataPaths) -> ProfileManager:
    return ProfileManager(paths)


def read_runtime_state(paths: DataPaths, profile: Profile) -> dict[str, Any] | None:
    try:
        state_file = state_path(profile.data_dir, paths.runtime_dir / profile.id)
    except ValueError:
        return None
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _row_for(paths: DataPaths, manager: ProfileManager, profile: Profile) -> ProfileRow:
    status = get_status(profile.data_dir, runtime_dir=paths.runtime_dir / profile.id)
    pid: int | None = None
    if status in ("running", "starting", "closing"):
        state = read_runtime_state(paths, profile)
        raw_pid = state.get("pid") or state.get("controller_pid") if state else None
        if isinstance(raw_pid, int) and raw_pid > 0:
            pid = raw_pid
    return ProfileRow(profile=profile, status=status, pid=pid)


def list_profile_rows(paths: DataPaths, with_sizes: bool = False) -> list[ProfileRow]:
    manager = _manager(paths)
    rows = [_row_for(paths, manager, profile) for profile in manager.list_profiles()]
    if with_sizes:
        for row in rows:
            row.size_bytes = compute_size(row.profile.data_dir)
    return rows


def effective_engine(profile: Profile) -> str:
    config = getattr(profile, "launch_config", None)
    if config is not None and config.engine:
        return str(config.engine)
    if profile.engine:
        return str(profile.engine)
    env_value = os.environ.get("PROFILEDOCK_DEFAULT_ENGINE", "").strip().lower()
    if env_value in ("direct", "playwright"):
        return env_value
    return "direct"


def profile_card(paths: DataPaths, row: ProfileRow) -> list[tuple[str, Text]]:
    """Telemetry card entries for the inspector's detail view."""
    profile = row.profile
    config = getattr(profile, "launch_config", None)
    engine = effective_engine(profile)
    card: list[tuple[str, Text]] = [
        ("ID", Text(profile.id, style="cyan")),
        ("Engine", Text(engine, style="bold")),
        ("Status", _status_text(row.status, row.pid)),
        ("Created", Text(profile.created_at or "-")),
        ("Last launch", Text(profile.last_launched_at or "never", style="italic")),
        ("Data dir", Text(profile.data_dir)),
        ("Disk usage", Text(format_size(row.size_bytes or compute_size(profile.data_dir)))),
    ]
    if config is not None:
        card.append(("Default tabs", Text(str(config.default_tabs) if config.default_tabs else "-")))
        card.append(("Browser", Text(config.browser or "auto-detect")))
        card.append(
            ("Window", Text(f"{config.window_width}x{config.window_height}" if config.window_width else "-"))
        )
        card.append(("Start URLs", Text(str(len(config.start_urls)) if config.start_urls else "0")))
    card.extend(_resource_entries(paths, profile, row))
    return card


def _resource_entries(paths: DataPaths, profile: Any, row: ProfileRow) -> list[tuple[str, Text]]:
    """Live CPU/RAM gauges plus disk breakdown for the inspector card.

    Best-effort: telemetry failures simply omit the entries.
    """
    from ..metrics import get_profile_metrics

    try:
        metrics = get_profile_metrics(
            profile,
            runtime_dir=paths.runtime_dir / profile.id,
            status=row.status,
            cpu_sample_interval=0.1,
        )
    except Exception:
        return []
    storage = metrics.storage
    live = metrics.live
    entries: list[tuple[str, Text]] = []
    if live is not None:
        entries.append(("CPU", Text(format_cpu_percent(live.total_cpu_percent), style="bold")))
        entries.append(("Memory (RSS)", Text(format_size(int(live.total_memory_rss_bytes)))))
        entries.append(("Processes", Text(str(live.process_count))))
    entries.append(("Disk total", Text(format_size(storage.total_bytes))))
    entries.append(
        (
            "Cache / Cookies / Logs",
            Text(
                f"{format_size(storage.cache_bytes)} / "
                f"{format_size(storage.cookies_storage_bytes)} / "
                f"{format_size(storage.logs_bytes)}"
            ),
        )
    )
    return entries


def doctor_checks(paths: DataPaths) -> list[DiagnosticCheck]:
    return run_diagnostics(paths.root)


def recent_logs(paths: DataPaths, profile_id: str | None, last_n: int) -> list[dict[str, Any]]:
    return read_profile_logs(paths.logs_dir, profile_id=profile_id, last_n=last_n)


# ---------------------------------------------------------------------------
# browser detection

_browser_version_cache: dict[str, str] = {}


def _windows_file_version(executable: str) -> str:
    """Read the VS_FIXEDFILEINFO product version from a PE image."""
    import ctypes

    version_api = ctypes.windll.version
    size = version_api.GetFileVersionInfoSizeW(executable, None)
    if not size:
        return ""
    data = ctypes.create_string_buffer(size)
    if not version_api.GetFileVersionInfoW(executable, 0, size, data):
        return ""
    pointer = ctypes.c_void_p()
    length = ctypes.c_uint()
    if not version_api.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length)):
        return ""
    if length.value < 16:
        return ""
    fields = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint * (length.value // 4))).contents
    signature = fields[0]
    major_pair, minor_pair = fields[2], fields[3]
    if signature != 0xFEEF04BD:
        return ""
    return f"{major_pair >> 16}.{major_pair & 0xFFFF}.{minor_pair >> 16}.{minor_pair & 0xFFFF}"


def _probe_version(executable: str) -> str:
    if sys.platform == "win32":
        if executable in _browser_version_cache:
            return _browser_version_cache[executable]
        version = _windows_file_version(executable)
        _browser_version_cache[executable] = version
        return version
    cached = _browser_version_cache.get(executable)
    if cached:
        return cached
    version = ""
    try:
        completed = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        raw = (completed.stdout or completed.stderr).strip().splitlines()
        if raw:
            version = raw[0].strip()
            for separator in (" ", "/"):
                if separator in version:
                    version = version.rsplit(separator, 1)[-1]
            version = version.strip()
    except (OSError, subprocess.SubprocessError):
        version = ""
    _browser_version_cache[executable] = version
    return version


def detect_browsers() -> list[BrowserInfo]:
    """Detect installed browsers for the interactive browser picker."""
    found: list[BrowserInfo] = []
    for name, paths in browser_rows():
        for candidate in paths:
            expanded = Path(os.path.expandvars(candidate))
            if expanded.is_file():
                found.append(
                    BrowserInfo(
                        name=name,
                        path=str(expanded),
                        version=_probe_version(str(expanded)),
                        is_default=not found,
                    )
                )
                break
    return found


# ---------------------------------------------------------------------------
# command execution


def _require_profile(manager: ProfileManager, identifier: str) -> Profile:
    try:
        return manager.resolve(identifier)
    except ProfileNotFoundError as exc:
        raise BackendError(str(exc), "not_found") from exc
    except AmbiguousProfileError as exc:
        raise BackendError(str(exc), "ambiguous_profile") from exc


def _parse_urls(raw: str) -> list[str]:
    urls = [item.strip() for item in raw.split(",") if item.strip()]
    for url in urls:
        try:
            validate_url(url)
        except ValidationError as exc:
            raise BackendError(str(exc), "invalid_input") from exc
    return urls


def _resolve_browser(raw: str, engine: str) -> str:
    candidate = Path(raw).expanduser()
    if candidate.is_file():
        return str(candidate.resolve())
    cleaned = raw.strip().lower()
    try:
        validate_browser(cleaned, engine, require_executable=False)
    except ValidationError as exc:
        raise BackendError(str(exc), "invalid_input") from exc
    return cleaned


def _launch(paths: DataPaths, values: dict[str, object]) -> Text:
    manager = _manager(paths)
    profile = _require_profile(manager, str(values.get("profile", "")))
    config = getattr(profile, "launch_config", None)
    raw_engine = str(values.get("engine") or "").strip().lower()
    engine = raw_engine if raw_engine in ("direct", "playwright") else effective_engine(profile)
    raw_tabs = str(values.get("tabs") or "").strip()
    if raw_tabs:
        if not raw_tabs.isdigit() or int(raw_tabs) < 1:
            raise BackendError("tab count must be a positive integer", "invalid_input")
        tabs = int(raw_tabs)
    elif config is not None and config.default_tabs:
        tabs = config.default_tabs
    else:
        tabs = 1
    raw_urls = str(values.get("urls") or "").strip()
    stored_urls = list(config.start_urls) if config is not None and config.start_urls else []
    urls = _parse_urls(raw_urls) if raw_urls else stored_urls
    if len(urls) > tabs:
        raise BackendError("number of start URLs cannot exceed the tab count", "invalid_input")
    raw_browser = str(values.get("browser") or "").strip()
    browser: str | None = (
        _resolve_browser(raw_browser, engine)
        if raw_browser
        else (config.browser if config is not None else None)
    )
    flags = values.get("flags")
    extra_flags = [str(flag) for flag in flags] if isinstance(flags, list) else []

    if not Path(profile.data_dir).is_dir():
        raise BackendError("profile data directory is missing", "invalid_data_directory")

    runtime_dir = paths.runtime_dir / profile.id
    if engine == "direct":
        options: dict[str, Any] = {"runtime_dir": runtime_dir}
        if browser is not None:
            browser_path = Path(browser).expanduser()
            if browser_path.is_file():
                options["executable_path"] = browser_path
            else:
                options["browser"] = browser
        if urls:
            options["start_urls"] = list(urls)
        if extra_flags:
            options["extra_args"] = extra_flags
        state = start_direct_chrome(profile.data_dir, tabs, **options)
        pid = state.get("pid")
    else:
        controller_options: dict[str, Any] = {"runtime_dir": runtime_dir}
        if browser is not None:
            controller_options["browser_channel"] = browser
        if urls:
            controller_options["start_urls"] = list(urls)
        state = start_controller(profile.data_dir, tabs, **controller_options)
        pid = state.get("controller_pid") or state.get("pid")

    launch_warning = ""
    try:
        manager.mark_launched(profile.id)
    except Exception:
        launch_warning = "\nWarning: browser launched but the launch timestamp was not saved."

    body = Text()
    body.append("Launched ", style="green")
    body.append(f"'{profile.name}'", style="bold")
    body.append(f" (engine: {engine}) with {tabs} tab(s).")
    if pid:
        body.append(f"\nPID: {pid}", style="dim")
    if launch_warning:
        body.append(launch_warning, style="yellow")
    if extra_flags and engine != "direct":
        body.append("\nNote: custom Chromium flags apply to the direct engine only.", style="yellow")
    return body


def run_action(paths: DataPaths, action_id: str, values: dict[str, object]) -> ActionResult:
    """Execute one action and return a CLI-faithful result."""
    argv = [action_id] + [str(v) for v in values.values() if str(v or "").strip()]
    try:
        body = _dispatch(paths, action_id, values)
        return ActionResult(argv=argv, exit_code=EXIT_SUCCESS, body=body)
    except BackendError as exc:
        body = Text()
        body.append(f"Error [{exc.category}]: ", style="bold red")
        body.append(exc.message)
        if exc.hint:
            body.append(f"\nNext steps: {exc.hint}", style="dim")
        return ActionResult(
            argv=argv,
            exit_code=EXIT_USER_ERROR,
            body=body,
            category=exc.category,
            hint=exc.hint,
        )
    except (ProfileRunningError, BrowserLaunchError) as exc:
        category = "profile_active" if isinstance(exc, ProfileRunningError) else "browser_launch_failed"
        body = Text()
        body.append(f"Error [{category}]: ", style="bold red")
        body.append(str(exc))
        return ActionResult(argv=argv, exit_code=EXIT_USER_ERROR, body=body, category=category)
    except (OSError, ValueError) as exc:
        category = error_category(str(exc))
        body = Text()
        body.append(f"Error [{category}]: ", style="bold red")
        body.append(str(exc))
        return ActionResult(argv=argv, exit_code=EXIT_USER_ERROR, body=body, category=category)


def _dispatch(paths: DataPaths, action_id: str, values: dict[str, object]) -> Text:
    manager = _manager(paths)
    if action_id == "list":
        return _render_list(list_profile_rows(paths, with_sizes=True))
    if action_id == "status":
        return _render_list(list_profile_rows(paths))
    if action_id == "doctor":
        return _render_doctor(doctor_checks(paths))
    if action_id == "logs":
        return _render_logs(paths, values)
    if action_id == "create":
        return _create(manager, values)
    if action_id == "show":
        return _show(paths, manager, values)
    if action_id == "launch":
        return _launch(paths, values)
    if action_id == "close":
        return _close(manager, values)
    if action_id == "rename":
        return _rename(manager, values)
    if action_id == "set-engine":
        return _set_engine(manager, values)
    if action_id == "delete":
        return _delete(manager, values)
    if action_id == "backup":
        return _backup(paths, manager, values)
    if action_id == "restore":
        return _restore(paths, values)
    if action_id == "tabs":
        return _tabs(paths, manager, values)
    if action_id == "open-tab":
        return _open_tab(paths, manager, values)
    if action_id == "read":
        return _read_page(paths, manager, values)
    if action_id == "cookies":
        return _cookies(paths, manager, values)
    raise BackendError(f"unknown action '{action_id}'", "invalid_input")


def _resolve_profile(manager: ProfileManager, values: dict[str, object]) -> Profile:
    identifier = str(values.get("profile") or "").strip()
    if not identifier:
        raise BackendError("a profile identifier is required", "invalid_input")
    return _require_profile(manager, identifier)


def _tabs(paths: DataPaths, manager: ProfileManager, values: dict[str, object]) -> Text:
    profile = _resolve_profile(manager, values)
    try:
        res = send_controller_command(
            profile.data_dir,
            cmd="tabs",
            runtime_dir=paths.runtime_dir / profile.id,
            auto_start_headless=False,
        )
    except Exception as exc:
        category = getattr(exc, "category", None) or error_category(str(exc))
        raise BackendError(str(exc), category) from exc

    tabs_data = res.get("tabs", [])
    if not tabs_data:
        return Text("No open tabs.", style="dim")

    table = Table(show_header=True, box=None, padding=(0, 2, 0, 0))
    table.add_column("INDEX", style="cyan")
    table.add_column("TITLE", style="bold")
    table.add_column("URL")
    for item in tabs_data:
        table.add_row(str(item.get("index", 0)), item.get("title", "") or "(untitled)", item.get("url", ""))
    return _table_text(table)


def _open_tab(paths: DataPaths, manager: ProfileManager, values: dict[str, object]) -> Text:
    profile = _resolve_profile(manager, values)
    url = str(values.get("url") or "about:blank").strip()
    if url != "about:blank":
        try:
            validate_url(url)
        except ValidationError as exc:
            raise BackendError(str(exc), "invalid_input") from exc
    try:
        res = send_controller_command(
            profile.data_dir,
            cmd="open_tab",
            args={"url": url},
            runtime_dir=paths.runtime_dir / profile.id,
            auto_start_headless=True,
        )
    except Exception as exc:
        category = getattr(exc, "category", None) or error_category(str(exc))
        raise BackendError(str(exc), category) from exc

    tab_info = res.get("tab", {})
    body = Text("Opened tab ", style="green")
    body.append(f"[{tab_info.get('index', 0)}]", style="bold cyan")
    body.append(f": {tab_info.get('url', url)}")
    return body


def _read_page(paths: DataPaths, manager: ProfileManager, values: dict[str, object]) -> Text:
    profile = _resolve_profile(manager, values)
    url_raw = str(values.get("url") or "").strip()
    url = url_raw if url_raw else None
    if url:
        try:
            validate_url(url)
        except ValidationError as exc:
            raise BackendError(str(exc), "invalid_input") from exc
    try:
        res = send_controller_command(
            profile.data_dir,
            cmd="read_page",
            args={"url": url, "tab": 0},
            runtime_dir=paths.runtime_dir / profile.id,
            auto_start_headless=True,
            timeout=40.0,
        )
    except Exception as exc:
        category = getattr(exc, "category", None) or error_category(str(exc))
        raise BackendError(str(exc), category) from exc

    title = res.get("title", "")
    content = res.get("content", "")
    page_url = res.get("url", "")
    body = Text()
    if title:
        body.append(f"# {title}\n", style="bold cyan")
        body.append(f"{page_url}\n\n", style="dim")
    body.append(content or "(empty content)")
    return body


def _cookies(paths: DataPaths, manager: ProfileManager, values: dict[str, object]) -> Text:
    profile = _resolve_profile(manager, values)
    output_raw = str(values.get("output") or "").strip()
    try:
        res = send_controller_command(
            profile.data_dir,
            cmd="cookies",
            args={},
            runtime_dir=paths.runtime_dir / profile.id,
            auto_start_headless=True,
        )
    except Exception as exc:
        category = getattr(exc, "category", None) or error_category(str(exc))
        raise BackendError(str(exc), category) from exc

    cookies_list = res.get("cookies", [])
    if output_raw:
        out_path = Path(output_raw).expanduser()
        try:
            write_private_json(out_path, cookies_list)
            body = Text("Exported ", style="green")
            body.append(f"{len(cookies_list)}", style="bold")
            body.append(f" cookie(s) to '{out_path}'.")
            return body
        except OSError as exc:
            raise BackendError(f"could not write cookies to '{out_path}': {exc}", "storage_error") from exc

    return Text(json.dumps(cookies_list, indent=2))


def _render_list(rows: list[ProfileRow]) -> Text:
    if not rows:
        return Text("No profiles found.", style="dim")
    table = Table(show_header=True, box=None, padding=(0, 2, 0, 0))
    table.add_column("ID", style="cyan")
    table.add_column("NAME", style="bold")
    table.add_column("ENGINE")
    table.add_column("STATUS")
    table.add_column("SIZE", justify="right")
    for row in rows:
        table.add_row(
            row.profile.id,
            row.profile.name,
            effective_engine(row.profile),
            _status_text(row.status, row.pid),
            format_size(row.size_bytes),
        )
    return _table_text(table)


def _table_text(table: Table) -> Text:
    from rich.console import Console

    console = Console(width=200, legacy_windows=False)
    with console.capture() as capture:
        console.print(table)
    return Text.from_ansi(capture.get().rstrip())


def _render_doctor(checks: list[DiagnosticCheck]) -> Text:
    body = Text()
    marks = {STATUS_OK: ("✓", "green"), STATUS_WARNING: ("!", "yellow"), STATUS_FAILED: ("✗", "red")}
    for check in checks:
        mark, color = marks.get(check.status, (" ", "dim"))
        body.append(f" {mark} ", style=color)
        body.append(f"{check.id:<24}", style="bold")
        body.append(f"{check.status.upper():<8}", style=color)
        body.append(check.summary)
        body.append("\n")
    actionable = [check for check in checks if check.action]
    if actionable:
        body.append("\nSuggested Actions:\n", style="bold")
        for check in actionable:
            body.append(f"  - {check.id}: {check.action}\n", style="dim")
    failed = any(check.status == STATUS_FAILED for check in checks)
    if failed:
        body.append("\nResult: UNHEALTHY", style="bold red")
    else:
        body.append("\nResult: healthy", style="bold green")
    return body


def _render_logs(paths: DataPaths, values: dict[str, object]) -> Text:
    raw_target = str(values.get("profile") or "").strip()
    profile_id: str | None = None
    if raw_target and raw_target != "__all__":
        manager = _manager(paths)
        profile_id = _require_profile(manager, raw_target).id
    raw_last = str(values.get("last") or "").strip()
    last_n = int(raw_last) if raw_last.isdigit() and int(raw_last) > 0 else 25
    entries = recent_logs(paths, profile_id, last_n)
    if not entries:
        return Text("No log entries found.", style="dim")
    body = Text()
    for entry in entries:
        body.append(f"[{entry.get('timestamp', '')}] ", style="dim")
        level = str(entry.get("level", "INFO"))
        level_style = "yellow" if level in ("WARNING", "ERROR") else "cyan"
        body.append(f"[{level}] ", style=level_style)
        body.append(f"[{entry.get('event', '')}] ", style="bold")
        body.append(f"(profile: {entry.get('profile_id', '-')}, engine: {entry.get('engine', '-')})\n")
        details = entry.get("details")
        if isinstance(details, dict):
            for key, value in details.items():
                body.append(f"    {key}: {value}\n", style="dim")
    return body


def _create(manager: ProfileManager, values: dict[str, object]) -> Text:
    name = str(values.get("name") or "").strip()
    engine = str(values.get("engine") or "").strip().lower() or None
    try:
        profile = manager.create(name, engine=engine)
    except ValueError as exc:
        raise BackendError(str(exc), "invalid_input") from exc
    except Exception as exc:
        raise BackendError(str(exc), "storage_error") from exc
    return Text(f"Created profile '{profile.name}' ({profile.id})", style="green")


def _show(paths: DataPaths, manager: ProfileManager, values: dict[str, object]) -> Text:
    row = _find_row(paths, manager, str(values.get("profile") or ""))
    table = _kv_table(f"Profile: {row.name}")
    for label, value in profile_card(paths, row):
        table.add_row(label + ":", value)
    return _table_text(table)


def _find_row(paths: DataPaths, manager: ProfileManager, identifier: str) -> ProfileRow:
    profile = _require_profile(manager, identifier)
    return _row_for(paths, manager, profile)


def _close(manager: ProfileManager, values: dict[str, object]) -> Text:
    profile = _require_profile(manager, str(values.get("profile") or ""))
    try:
        close_controller(profile.data_dir, runtime_dir=manager.runtime_path(profile.id))
    except ProfileRunningError as exc:
        if getattr(exc, "stopped", False):
            return Text(f"'{profile.name}' is not running.", style="yellow")
        raise
    return Text(f"Closed '{profile.name}'.", style="green")


def _rename(manager: ProfileManager, values: dict[str, object]) -> Text:
    profile = _require_profile(manager, str(values.get("profile") or ""))
    old_name = profile.name
    new_name = str(values.get("new_name") or "").strip()
    try:
        renamed = manager.rename(profile.id, new_name)
    except ValueError as exc:
        raise BackendError(str(exc), "invalid_input") from exc
    body = Text(f"Renamed profile to '{renamed.name}' ({renamed.id})", style="green")
    if old_name != renamed.name:
        body.append(f"\n  {old_name} -> {renamed.name}", style="dim")
    return body


def _set_engine(manager: ProfileManager, values: dict[str, object]) -> Text:
    profile = _require_profile(manager, str(values.get("profile") or ""))
    engine = str(values.get("engine") or "").strip().lower()
    if engine not in ("direct", "playwright"):
        raise BackendError("engine must be 'direct' or 'playwright'", "invalid_input")
    old_engine = profile.engine or "(unset)"
    try:
        updated = manager.set_engine(profile.id, engine)
    except ValueError as exc:
        raise BackendError(str(exc), "invalid_input") from exc
    body = Text(f"Set engine to '{engine}' for profile '{updated.name}' ({updated.id})", style="green")
    if old_engine != engine:
        body.append(f"\n  {old_engine} -> {engine}", style="dim")
    return body


def _delete(manager: ProfileManager, values: dict[str, object]) -> Text:
    profile = _require_profile(manager, str(values.get("profile") or ""))
    try:
        manager.delete(profile.id)
    except ProfileRunningError as exc:
        raise BackendError(str(exc), "profile_active") from exc
    except Exception as exc:
        raise BackendError(str(exc), "storage_error") from exc
    return Text(f"Deleted '{profile.name}'.", style="green")


def _selected_profiles(paths: DataPaths, manager: ProfileManager, target: str) -> list[Profile]:
    target = target.strip()
    if not target or target == "__all__":
        profiles = manager.list_profiles()
        if not profiles:
            raise BackendError("no profiles found to backup", "not_found")
        return profiles
    return [_require_profile(manager, target)]


def _backup(paths: DataPaths, manager: ProfileManager, values: dict[str, object]) -> Text:
    target = str(values.get("target") or "__all__")
    output_raw = str(values.get("output") or "").strip()
    if not output_raw:
        raise BackendError("an output archive path is required", "invalid_input")
    output = Path(output_raw).expanduser()
    profiles = _selected_profiles(paths, manager, target)
    try:
        report = create_backup_archive(
            profiles=profiles,
            data_paths=paths,
            output_file=output,
            force=bool(values.get("force")),
            exclude_cache=bool(values.get("exclude_cache", True)),
        )
    except Exception as exc:
        raise BackendError(str(exc), error_category(str(exc))) from exc
    body = Text("Backup created successfully: ", style="green")
    body.append(str(report.output_path), style="bold")
    body.append(
        f"\nProfiles: {report.total_profiles} | Files: {report.total_files}"
        f" | Size: {format_size(report.total_bytes)}",
        style="dim",
    )
    for item in report.profiles:
        body.append(f"\n  + {item.name} ({item.id})", style="green")
        body.append(f" - {item.file_count} files ({format_size(item.total_bytes)})", style="dim")
    return body


def _restore(paths: DataPaths, values: dict[str, object]) -> Text:
    archive_raw = str(values.get("archive") or "").strip()
    if not archive_raw:
        raise BackendError("an archive path is required", "invalid_input")
    archive = Path(archive_raw).expanduser()
    try:
        report = restore_backup_archive(
            archive_path=archive,
            data_paths=paths,
            overwrite=bool(values.get("force")),
        )
    except Exception as exc:
        category = getattr(exc, "category", None) or error_category(str(exc))
        raise BackendError(str(exc), category) from exc
    body = Text("Restore completed from archive: ", style="green")
    body.append(str(report.archive_path), style="bold")
    body.append(
        f"\nRestored: {report.total_restored} | Files: {report.total_files}"
        f" | Size: {format_size(report.total_bytes)}",
        style="dim",
    )
    for item in report.restored:
        body.append(f"\n  + {item.name} ({item.id})", style="green")
    for item in report.skipped:
        body.append(f"\n  = {item.name} ({item.id})", style="yellow")
        body.append(f" - {item.message}", style="dim")
    return body


def storage_summary(rows: list[ProfileRow]) -> tuple[int, str]:
    running = sum(1 for row in rows if row.status in ("running", "starting"))
    total_bytes = sum(row.size_bytes or 0 for row in rows)
    return running, format_size(total_bytes)
