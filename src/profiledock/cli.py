from pathlib import Path
from typing import Optional

import typer

from .process_manager import BrowserLaunchError, ProfileRunningError, close_controller, is_running, start_controller
from .profile_manager import ProfileManager, ProfileNotFoundError
from .storage import StorageError
from .version import __version__

app = typer.Typer(add_completion=False, help="Manage isolated persistent Chromium profiles.")


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"profiledock {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-V",
        help="Show version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    pass


def manager() -> ProfileManager:
    return ProfileManager(Path.cwd())


def fail(message: str) -> None:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(1)


@app.command()
def create(name: str) -> None:
    try:
        profile = manager().create(name)
    except (StorageError, ValueError) as exc:
        fail(str(exc))
    typer.echo(f"Created profile '{profile.name}' ({profile.id})")


@app.command("list")
def list_profiles() -> None:
    try:
        profiles = manager().list_profiles()
    except StorageError as exc:
        fail(str(exc))
    if not profiles:
        typer.echo("No profiles found.")
        return
    for profile in profiles:
        status = "running" if is_running(profile.data_dir) else "stopped"
        typer.echo(f"{profile.id}\t{profile.name}\t{status}")


@app.command()
def launch(profile_id: str, tabs: int = typer.Option(None, "--tabs", "-t")) -> None:
    try:
        profile = manager().get(profile_id)
        if tabs is None:
            tabs = typer.prompt("How many tabs do you want to open?", type=int)
        if tabs < 1:
            fail("tab count must be at least 1")
        if not Path(profile.data_dir).exists():
            fail("profile data directory is missing")
        start_controller(profile.data_dir, tabs)
        manager().mark_launched(profile.id)
    except (ProfileNotFoundError, StorageError, ProfileRunningError, BrowserLaunchError, ValueError) as exc:
        fail(str(exc))
    typer.echo(f"Launched '{profile.name}' with {tabs} tab(s).")


@app.command()
def close(profile_id: str) -> None:
    try:
        profile = manager().get(profile_id)
        close_controller(profile.data_dir)
    except (ProfileNotFoundError, StorageError, ProfileRunningError, BrowserLaunchError) as exc:
        fail(str(exc))
    typer.echo(f"Closed '{profile.name}'.")


@app.command()
def delete(profile_id: str, yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation.")) -> None:
    try:
        profile = manager().get(profile_id)
        if is_running(profile.data_dir):
            fail("profile is running; close it first")
        if not yes and not typer.confirm(f"Delete profile '{profile.name}' and all browser data?"):
            raise typer.Abort()
        manager().delete(profile_id)
    except (ProfileNotFoundError, StorageError, OSError) as exc:
        fail(str(exc))
    typer.echo(f"Deleted '{profile.name}'.")


if __name__ == "__main__":
    app()
