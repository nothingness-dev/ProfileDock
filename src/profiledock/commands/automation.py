"""Playwright session automation commands: tabs, open-tab, close-tab, read, eval, cookies."""

import json
from pathlib import Path
from typing import Optional

import typer

from ..cli_support import emit_json, fail, fail_exception
from ..fsops import write_private_json
from ..process_manager import (
    BrowserLaunchError,
    ProfileRunningError,
)
from ..profile_manager import AmbiguousProfileError, ProfileManager, ProfileNotFoundError
from ..storage import StorageError
from ..validation import ValidationError, validate_url


def _get_manager() -> ProfileManager:
    from ..cli import manager

    return manager()


def list_tabs_command(
    profile_id: str = typer.Argument(..., help="Profile ID, prefix, or name."),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format."),
) -> None:
    """List open browser tabs, URLs, and titles in a running Playwright session."""
    from ..cli import _render_table, runtime_path, send_controller_command

    try:
        profile = _get_manager().resolve(profile_id)
        res = send_controller_command(
            profile.data_dir,
            cmd="tabs",
            runtime_dir=runtime_path(profile),
            auto_start_headless=False,
        )
    except (
        ProfileNotFoundError,
        AmbiguousProfileError,
        StorageError,
        ProfileRunningError,
        BrowserLaunchError,
        ValueError,
    ) as exc:
        fail_exception(exc)

    tabs = res.get("tabs", [])
    if json_output:
        emit_json("tabs", tabs)
        return

    if not tabs:
        typer.echo("No open tabs.")
        return

    rows = [["INDEX", "TITLE", "URL"]]
    for item in tabs:
        rows.append([str(item["index"]), item.get("title", "") or "(untitled)", item.get("url", "")])
    typer.echo(_render_table(rows))


def open_tab_command(
    profile_id: str = typer.Argument(..., help="Profile ID, prefix, or name."),
    url: str = typer.Argument("about:blank", help="URL to open in the new tab."),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format."),
) -> None:
    """Open a new tab dynamically in an active Playwright browser session."""
    from ..cli import runtime_path, send_controller_command

    if url != "about:blank":
        try:
            validate_url(url)
        except ValidationError as exc:
            fail_exception(exc)

    try:
        profile = _get_manager().resolve(profile_id)
        res = send_controller_command(
            profile.data_dir,
            cmd="open_tab",
            args={"url": url},
            runtime_dir=runtime_path(profile),
            auto_start_headless=True,
        )
    except (
        ProfileNotFoundError,
        AmbiguousProfileError,
        StorageError,
        ProfileRunningError,
        BrowserLaunchError,
        ValueError,
    ) as exc:
        fail_exception(exc)

    tab_data = res.get("tab", {})
    if json_output:
        emit_json("open-tab", tab_data)
        return

    typer.echo(f"Opened tab [{tab_data.get('index', 0)}] with URL: {tab_data.get('url', url)}")


def close_tab_command(
    profile_id: str = typer.Argument(..., help="Profile ID, prefix, or name."),
    index: int = typer.Argument(..., help="0-based tab index to close."),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format."),
) -> None:
    """Close a specific tab by its index in an active Playwright session."""
    from ..cli import runtime_path, send_controller_command

    if index < 0:
        fail("tab index must be at least 0")
    try:
        profile = _get_manager().resolve(profile_id)
        res = send_controller_command(
            profile.data_dir,
            cmd="close_tab",
            args={"index": index},
            runtime_dir=runtime_path(profile),
            auto_start_headless=False,
        )
    except (
        ProfileNotFoundError,
        AmbiguousProfileError,
        StorageError,
        ProfileRunningError,
        BrowserLaunchError,
        ValueError,
    ) as exc:
        fail_exception(exc)

    if json_output:
        emit_json(
            "close-tab",
            {"index": index, "remaining_tabs": res.get("remaining_tabs", 0)},
        )
        return

    typer.echo(f"Closed tab [{index}]. Remaining open tab(s): {res.get('remaining_tabs', 0)}")


def read_page_command(
    profile_id: str = typer.Argument(..., help="Profile ID, prefix, or name."),
    url: Optional[str] = typer.Argument(None, help="Optional URL to navigate to before reading."),
    tab: int = typer.Option(0, "--tab", "-t", help="0-based tab index to read (default: 0)."),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format."),
) -> None:
    """Read a page's content as formatted Markdown in the terminal (headless web reader)."""
    from ..cli import runtime_path, send_controller_command

    if tab < 0:
        fail("tab index must be at least 0")
    if url:
        try:
            validate_url(url)
        except ValidationError as exc:
            fail_exception(exc)

    try:
        profile = _get_manager().resolve(profile_id)
        res = send_controller_command(
            profile.data_dir,
            cmd="read_page",
            args={"url": url, "tab": tab},
            runtime_dir=runtime_path(profile),
            auto_start_headless=True,
            timeout=40.0,
        )
    except (
        ProfileNotFoundError,
        AmbiguousProfileError,
        StorageError,
        ProfileRunningError,
        BrowserLaunchError,
        ValueError,
    ) as exc:
        fail_exception(exc)

    if json_output:
        emit_json(
            "read",
            {
                "url": res.get("url", ""),
                "title": res.get("title", ""),
                "content": res.get("content", ""),
                "links": res.get("links", []),
            },
        )
        return

    title = res.get("title", "")
    content = res.get("content", "")
    page_url = res.get("url", "")
    try:
        from rich.console import Console
        from rich.markdown import Markdown

        console = Console()
        if title:
            console.print(f"# {title}", style="bold cyan", markup=False)
            console.print(page_url, style="dim", markup=False)
            console.print()
        if content:
            console.print(Markdown(content))
        else:
            typer.echo("(empty page content)")
    except ImportError:
        if title:
            typer.echo(f"# {title}")
            typer.echo(page_url)
            typer.echo()
        typer.echo(content or "(empty page content)")


def eval_script_command(
    profile_id: str = typer.Argument(..., help="Profile ID, prefix, or name."),
    script: str = typer.Argument(..., help="JavaScript expression to evaluate in the page."),
    tab: int = typer.Option(0, "--tab", "-t", help="0-based tab index to evaluate on (default: 0)."),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format."),
) -> None:
    """Evaluate a JavaScript expression in the active page context."""
    from ..cli import runtime_path, send_controller_command

    if tab < 0:
        fail("tab index must be at least 0")
    try:
        profile = _get_manager().resolve(profile_id)
        res = send_controller_command(
            profile.data_dir,
            cmd="eval",
            args={"script": script, "tab": tab},
            runtime_dir=runtime_path(profile),
            auto_start_headless=True,
        )
    except (
        ProfileNotFoundError,
        AmbiguousProfileError,
        StorageError,
        ProfileRunningError,
        BrowserLaunchError,
        ValueError,
    ) as exc:
        fail_exception(exc)

    result_val = res.get("result")
    if json_output:
        emit_json("eval", {"result": result_val})
        return

    if isinstance(result_val, (dict, list)):
        typer.echo(json.dumps(result_val, indent=2))
    else:
        typer.echo(str(result_val))


def export_cookies_command(
    profile_id: str = typer.Argument(..., help="Profile ID, prefix, or name."),
    output_file: Optional[Path] = typer.Option(
        None, "--output", "-o", help="File to write exported JSON cookies."
    ),
    url: Optional[list[str]] = typer.Option(None, "--url", "-u", help="URL filter(s) for cookies."),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format."),
) -> None:
    """Export live session cookies directly from browser RAM, bypassing SQLite locks."""
    from ..cli import runtime_path, send_controller_command

    if url:
        for u in url:
            try:
                validate_url(u)
            except ValidationError as exc:
                fail_exception(exc)

    try:
        profile = _get_manager().resolve(profile_id)
        res = send_controller_command(
            profile.data_dir,
            cmd="cookies",
            args={"urls": url} if url else {},
            runtime_dir=runtime_path(profile),
            auto_start_headless=True,
        )
    except (
        ProfileNotFoundError,
        AmbiguousProfileError,
        StorageError,
        ProfileRunningError,
        BrowserLaunchError,
        ValueError,
    ) as exc:
        fail_exception(exc)

    cookies_list = res.get("cookies", [])
    if output_file:
        try:
            write_private_json(output_file, cookies_list)
            if json_output:
                emit_json(
                    "cookies",
                    {"output_file": str(output_file.expanduser().absolute()), "count": len(cookies_list)},
                )
            else:
                typer.echo(f"Exported {len(cookies_list)} cookie(s) to '{output_file}'.")
        except OSError as exc:
            fail(f"could not write cookies to '{output_file}': {exc}")
        return

    if json_output:
        emit_json("cookies", cookies_list)
        return

    typer.echo(json.dumps(cookies_list, indent=2))
