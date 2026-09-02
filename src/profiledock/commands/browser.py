"""Browser lifecycle and inspection commands: launch, close, status, set-engine."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import typer

from ..cli_support import (
    _non_interactive,
    emit_json,
    fail,
    fail_exception,
    format_cpu_percent,
    format_size_bytes,
    redact_proxy,
    resolve_engine,
    selected_paths,
)
from ..launch_service import LaunchPlan, build_launch_plan, controller_launch_options, direct_launch_options
from ..logger import generate_correlation_id, write_log_entry
from ..process_manager import (
    BrowserLaunchError,
    ProfileRunningError,
)
from ..profile_manager import AmbiguousProfileError, ProfileManager, ProfileNotFoundError
from ..storage import StorageError
from ..terminal import is_stdout_tty
from ..validation import ValidationError

if TYPE_CHECKING:
    from ..models import Profile


def _get_manager() -> ProfileManager:
    from ..cli import manager

    return manager()


@dataclass
class _LaunchOptions:
    profile: "Profile"
    plan: "LaunchPlan"
    engine: str
    tabs: int
    urls: list[str]
    browser: str | None
    width: int | None
    height: int | None


def _resolve_launch_options(
    profile_id: str,
    tabs: int | None,
    engine: str | None,
    browser: str | None,
    url: list[str] | None,
    proxy: str | None = None,
    user_agent: str | None = None,
    locale: str | None = None,
    timezone: str | None = None,
) -> _LaunchOptions:
    from ..launch_service import resolve_launch_tabs

    profile = _get_manager().resolve(profile_id)
    target_tabs = tabs
    if target_tabs is None:
        target_tabs = resolve_launch_tabs(profile, None)
    if target_tabs is None:
        if _non_interactive.get():
            fail("tab count is required in non-interactive mode; use --tabs")
        target_tabs = typer.prompt("How many tabs do you want to open?", type=int)
    try:
        plan = build_launch_plan(
            profile,
            engine=engine,
            tabs=target_tabs,
            urls=list(url) if url else None,
            browser=browser,
            proxy=proxy,
            user_agent=user_agent,
            locale=locale,
            timezone=timezone,
        )
    except ValueError as exc:
        fail(str(exc))
    return _LaunchOptions(
        profile=profile,
        plan=plan,
        engine=plan.engine,
        tabs=plan.tabs,
        urls=list(plan.urls),
        browser=plan.browser,
        width=plan.window_width,
        height=plan.window_height,
    )


def set_engine_command(
    profile_id: str = typer.Argument(..., help="Profile ID, unique ID prefix, or exact name."),
    engine: str = typer.Argument(..., help="Engine to use: 'direct' or 'playwright'"),
) -> None:
    """Set the profile-level default engine.

    A stored launch-config engine (config set) still overrides this value.
    """
    clean_engine = engine.strip().lower()
    if clean_engine not in ("direct", "playwright"):
        fail("engine must be 'direct' or 'playwright'")
    try:
        old_engine = _get_manager().resolve(profile_id).engine
        profile = _get_manager().set_engine(profile_id, clean_engine)
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError, ValidationError, ValueError) as exc:
        fail_exception(exc)
    typer.echo(f"Set engine to '{clean_engine}' for profile '{profile.name}' ({profile.id})")
    if old_engine != clean_engine:
        typer.echo(f"  {old_engine or '(unset)'} -> {clean_engine}")


def status_command(
    profile_id: str | None = typer.Argument(None, help="Profile ID, prefix, or name."),
    watch: bool = typer.Option(False, "--watch", "-w", help="Continuously poll and display live status."),
    interval: float = typer.Option(
        1.0, "--interval", "-i", help="Poll interval in seconds when using --watch."
    ),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format."),
    metrics: bool = typer.Option(
        False,
        "--metrics",
        "-m",
        help="Include resource metrics: process-tree CPU %, resident memory, and disk footprint.",
    ),
) -> None:
    """Report runtime status of one profile or all profiles.

    Status values: stopped, starting, running, closing, crashed, stale, error.
    With --metrics, appends resource columns (CPU %, RSS, disk); with --watch,
    refreshes continuously until Ctrl+C.
    """
    from ..cli import _render_table, get_status, runtime_path

    if watch and interval <= 0:
        fail("interval must be greater than 0")
    if watch and json_output:
        fail(
            "--watch cannot be combined with --json; poll 'profiledock status --json' from the caller instead"
        )

    def _metrics_dict(prof: Any, status_value: str) -> dict[str, Any]:
        from ..metrics import get_profile_metrics

        return get_profile_metrics(
            prof,
            runtime_dir=runtime_path(prof),
            status=status_value,
            cpu_sample_interval=0.2,
        ).to_dict()

    def _render_once() -> None:
        if profile_id is not None:
            profile = _get_manager().resolve(profile_id)
            profiles = [profile]
            single = True
        else:
            profiles = _get_manager().list_profiles()
            single = False

        if json_output:
            items = []
            for prof in profiles:
                st = get_status(prof.data_dir, runtime_dir=runtime_path(prof))
                item: dict[str, Any] = {
                    "id": prof.id,
                    "name": prof.name,
                    "engine": resolve_engine(None, prof),
                    "status": st,
                }
                if metrics:
                    item["metrics"] = _metrics_dict(prof, st)
                items.append(item)
            emit_json("status", items)
            return

        if not profiles:
            typer.echo("No profiles found.")
            return

        if single:
            prof = profiles[0]
            st = get_status(prof.data_dir, runtime_dir=runtime_path(prof))
            eng = resolve_engine(None, prof)
            line = f"{prof.id}\t{prof.name}\t{eng}\t{st}"
            if metrics:
                m = _metrics_dict(prof, st)
                live = m["live"]
                cpu = format_cpu_percent(live["total_cpu_percent"]) if live else "-"
                rss = format_size_bytes(int(live["total_memory_rss_bytes"])) if live else "-"
                disk = format_size_bytes(m["storage"]["total_bytes"])
                line += f"\t{cpu}\t{rss}\t{disk}"
            typer.echo(line)
        else:
            table = [["ID", "NAME", "ENGINE", "STATUS"]]
            if metrics:
                table = [["ID", "NAME", "ENGINE", "STATUS", "CPU%", "RSS", "PROCS", "TABS", "DISK"]]
            for prof in profiles:
                st = get_status(prof.data_dir, runtime_dir=runtime_path(prof))
                eng = resolve_engine(None, prof)
                if metrics:
                    m = _metrics_dict(prof, st)
                    live = m["live"]
                    table.append(
                        [
                            prof.id,
                            prof.name,
                            eng,
                            st,
                            format_cpu_percent(live["total_cpu_percent"]) if live else "-",
                            format_size_bytes(int(live["total_memory_rss_bytes"])) if live else "-",
                            str(live["process_count"]) if live else "-",
                            str(live["tab_count"]) if live and live["tab_count"] is not None else "-",
                            format_size_bytes(m["storage"]["total_bytes"]),
                        ]
                    )
                else:
                    table.append([prof.id, prof.name, eng, st])
            typer.echo(_render_table(table))

    try:
        if not watch:
            _render_once()
        else:
            import time

            interactive = is_stdout_tty()
            first_frame = True
            while True:
                if not json_output:
                    if interactive:
                        typer.echo("\033[2J\033[H", nl=False)
                    elif not first_frame:
                        typer.echo()
                first_frame = False
                try:
                    _render_once()
                except StorageError as exc:
                    # A transient lock or I/O blip should not kill a monitor;
                    # report it and keep polling.
                    typer.echo(f"status refresh skipped: {exc}", err=True)
                time.sleep(interval)
    except KeyboardInterrupt:
        pass
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError) as exc:
        fail_exception(exc)


def launch_command(
    profile_id: str = typer.Argument(..., help="Profile ID, unique ID prefix, or exact name."),
    tabs: int | None = typer.Option(
        None, "--tabs", "-t", help="Number of tabs to open (at least 1). Prompts if no preset exists."
    ),
    engine: str | None = typer.Option(
        None,
        "--engine",
        "-e",
        help="One-launch engine override: 'direct' or 'playwright'",
    ),
    browser: str | None = typer.Option(
        None,
        "--browser",
        "-b",
        help="Browser name (chrome, chromium) or executable path for this launch.",
    ),
    url: list[str] | None = typer.Option(
        None,
        "--url",
        "-u",
        help="Start URL for one tab; repeat for multiple pages. Overrides stored start URLs.",
    ),
    headless: bool = typer.Option(
        False,
        "--headless",
        help="Run the browser in the background without a visible window. "
        "By default the launch opens a visible Chromium window.",
    ),
    wait_timeout: float = typer.Option(
        30.0,
        "--wait-timeout",
        help="Seconds to wait for the browser and controller to become fully ready.",
    ),
    proxy: str | None = typer.Option(
        None,
        "--proxy",
        help="Proxy for this launch, e.g. http://host:8080 or socks5://user:pass@host:1080. "
        "Overrides the stored preset. Credentials are never written to logs.",
    ),
    user_agent: str | None = typer.Option(
        None, "--user-agent", help="Custom user agent for this launch; overrides the stored preset."
    ),
    locale: str | None = typer.Option(
        None, "--locale", help="Browser locale for this launch, e.g. en-GB; overrides the stored preset."
    ),
    timezone: str | None = typer.Option(
        None,
        "--timezone",
        help="IANA timezone for this launch, e.g. Europe/Berlin; overrides the stored preset.",
    ),
) -> None:
    """Launch a profile's browser with its persistent data.

    Playwright launches open a visible Chromium window by default; pass
    --headless for a background launch. Login is always manual. Relaunching the
    same profile reuses its saved cookies, sessions, and history. A duplicate
    launch is refused while the profile is starting or already running.
    """
    from ..cli import runtime_path, start_controller, start_direct_chrome

    if wait_timeout <= 0:
        fail("wait timeout must be greater than 0")
    corr_id = generate_correlation_id()
    paths = selected_paths()
    try:
        opts = _resolve_launch_options(
            profile_id, tabs, engine, browser, url, proxy, user_agent, locale, timezone
        )
        plan = opts.plan
        profile = opts.profile
        active_engine = opts.engine
        target_tabs = opts.tabs
        target_browser = opts.browser
        width = opts.width
        height = opts.height
        if headless and active_engine != "playwright":
            fail("--headless requires the Playwright engine")
        if plan.proxy and active_engine == "direct" and "@" in plan.proxy:
            fail(
                "direct engine does not support proxy credentials; use the playwright engine",
                hint="re-run with --engine playwright, or store a credentialess proxy",
            )

        write_log_entry(
            log_dir=paths.logs_dir,
            level="INFO",
            event="browser_launch_requested",
            profile_id=profile.id,
            correlation_id=corr_id,
            engine=active_engine,
            command="launch",
            browser_path=target_browser,
            details={
                "tabs": target_tabs,
                "url_count": len(plan.urls),
                "window_width": width,
                "window_height": height,
                "proxy": redact_proxy(plan.proxy),
                "locale": plan.locale,
                "timezone": plan.timezone,
            },
        )

        if active_engine == "direct":
            direct_options: dict[str, Any] = {
                "runtime_dir": runtime_path(profile),
                **direct_launch_options(plan),
            }
            state = start_direct_chrome(profile.data_dir, target_tabs, **direct_options)
            write_log_entry(
                log_dir=paths.logs_dir,
                level="INFO",
                event="browser_process_spawned",
                profile_id=profile.id,
                correlation_id=corr_id,
                engine="direct",
                pid=state.get("pid"),
                result="success",
            )
        else:
            controller_options: dict[str, Any] = {
                "runtime_dir": runtime_path(profile),
                **controller_launch_options(plan),
            }
            state = start_controller(
                profile.data_dir,
                target_tabs,
                headless=headless,
                startup_timeout=wait_timeout,
                **controller_options,
            )
            write_log_entry(
                log_dir=paths.logs_dir,
                level="INFO",
                event="controller_spawned",
                profile_id=profile.id,
                correlation_id=corr_id,
                engine="playwright",
                pid=state.get("controller_pid") or state.get("pid"),
                result="success",
                details={"headless": headless},
            )
    except (
        ProfileNotFoundError,
        AmbiguousProfileError,
        StorageError,
        ProfileRunningError,
        BrowserLaunchError,
        ValueError,
        ValidationError,
    ) as exc:
        write_log_entry(
            log_dir=paths.logs_dir,
            level="ERROR",
            event="browser_launch_failed",
            correlation_id=corr_id,
            result="failed",
            error_category=getattr(exc, "category", type(exc).__name__),
            details={"error": str(exc)},
        )
        fail_exception(exc)

    try:
        _get_manager().mark_launched(profile.id)
    except (ProfileNotFoundError, AmbiguousProfileError, StorageError, ValueError) as exc:
        typer.echo(f"Warning: browser launched but launch timestamp was not saved: {exc}", err=True)
    if active_engine == "playwright":
        mode = "headless" if headless else "visible"
        typer.echo(f"Launched '{profile.name}' (engine: {active_engine}, {mode}) with {target_tabs} tab(s).")
        return
    typer.echo(f"Launched '{profile.name}' (engine: {active_engine}) with {target_tabs} tab(s).")


def close_command(
    profile_id: str | None = typer.Argument(None, help="Profile ID, unique ID prefix, or exact name."),
    all_profiles: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Close every profile. Conflicts with a profile identifier.",
    ),
    timeout: float = typer.Option(
        15.0,
        "--timeout",
        help="Seconds to wait for the browser and controller to terminate and flush data.",
    ),
) -> None:
    """Close a running profile's browser cleanly.

    Requests graceful shutdown, waits for the browser and controller processes
    to terminate and persistent data to flush, then removes runtime state.
    Recovers crashed runtime state and terminates only identity-verified
    orphan browser processes. Use --all to close every profile at once.
    """
    from ..cli import close_controller, runtime_path

    if timeout <= 0:
        fail("timeout must be greater than 0")
    if all_profiles and profile_id is not None:
        fail("cannot specify both a profile identifier and --all")
    if not all_profiles and profile_id is None:
        fail("must specify a profile identifier or use --all to close all profiles")

    if all_profiles:
        manager = _get_manager()
        paths = selected_paths()
        corr_id = generate_correlation_id()
        try:
            profiles = manager.list_profiles()
        except (StorageError, ProfileNotFoundError, AmbiguousProfileError) as exc:
            fail_exception(exc)
        if not profiles:
            typer.echo("No profiles found.")
            return
        already_stopped = 0
        for profile in profiles:
            try:
                close_controller(profile.data_dir, timeout=timeout, runtime_dir=runtime_path(profile))
            except ProfileRunningError as exc:
                if not exc.stopped:
                    fail_exception(exc)
                already_stopped += 1
                write_log_entry(
                    log_dir=paths.logs_dir,
                    level="INFO",
                    event="browser_close_skipped",
                    profile_id=profile.id,
                    correlation_id=corr_id,
                    result="already_stopped",
                    details={"mode": "close_all"},
                )
                continue
            except (StorageError, BrowserLaunchError) as exc:
                write_log_entry(
                    log_dir=paths.logs_dir,
                    level="ERROR",
                    event="browser_close_failed",
                    profile_id=profile.id,
                    correlation_id=corr_id,
                    result="failed",
                    error_category=getattr(exc, "category", type(exc).__name__),
                    details={"error": str(exc), "mode": "close_all"},
                )
                fail_exception(exc)
            write_log_entry(
                log_dir=paths.logs_dir,
                level="INFO",
                event="browser_closed",
                profile_id=profile.id,
                correlation_id=corr_id,
                result="success",
                details={"mode": "close_all"},
            )
            typer.echo(f"Closed '{profile.name}'.")
        if already_stopped:
            typer.echo(
                f"{already_stopped} profile(s) already stopped."
            )
        return

    corr_id = generate_correlation_id()
    paths = selected_paths()
    if profile_id is None:
        # Guarded by fail() above when neither a profile nor --all was given.
        fail("must specify a profile identifier or use --all to close all profiles")
    try:
        profile = _get_manager().resolve(profile_id)
        close_controller(profile.data_dir, timeout=timeout, runtime_dir=runtime_path(profile))
    except ProfileRunningError as exc:
        if exc.stopped:
            fail(
                str(exc),
                category="profile_active",
                hint="the profile is already stopped; use 'profiledock status' to confirm",
            )
        fail_exception(exc)
    except (
        ProfileNotFoundError,
        AmbiguousProfileError,
        StorageError,
        BrowserLaunchError,
    ) as exc:
        write_log_entry(
            log_dir=paths.logs_dir,
            level="ERROR",
            event="browser_close_failed",
            correlation_id=corr_id,
            result="failed",
            error_category=getattr(exc, "category", type(exc).__name__),
            details={"error": str(exc)},
        )
        fail_exception(exc)
    write_log_entry(
        log_dir=paths.logs_dir,
        level="INFO",
        event="browser_closed",
        profile_id=profile.id,
        correlation_id=corr_id,
        engine=None,
        result="success",
    )
    typer.echo(f"Closed '{profile.name}'.")
