from pathlib import Path

import pytest
from typer.testing import CliRunner

from profiledock.cli import app
from profiledock.data_root import DataRootError, platform_data_root, resolve_data_root
from profiledock.models import Profile
from profiledock.profile_manager import ProfileManager

runner = CliRunner()


def test_windows_default_path(tmp_path):
    result = platform_data_root("win32", {"LOCALAPPDATA": str(tmp_path)}, tmp_path / "home")
    assert result == tmp_path / "ProfileDock"


def test_macos_default_path(tmp_path):
    home = tmp_path / "home"
    result = platform_data_root("darwin", {}, home)
    assert result == home / "Library" / "Application Support" / "ProfileDock"


def test_linux_xdg_default_path(tmp_path):
    result = platform_data_root("linux", {"XDG_DATA_HOME": str(tmp_path / "xdg")}, tmp_path / "home")
    assert result == tmp_path / "xdg" / "profiledock"


def test_linux_home_default_path(tmp_path):
    home = tmp_path / "home"
    result = platform_data_root("linux", {}, home)
    assert result == home / ".local" / "share" / "profiledock"


def test_cli_path_precedes_environment(tmp_path):
    cli_root = tmp_path / "cli"
    env_root = tmp_path / "env"
    result = resolve_data_root(cli_root, {"PROFILEDOCK_DATA_ROOT": str(env_root)}, prepare=False)
    assert result.root == cli_root.resolve()


def test_environment_precedes_platform_default(tmp_path):
    env_root = tmp_path / "env"
    result = resolve_data_root(
        environ={
            "PROFILEDOCK_DATA_ROOT": str(env_root),
            "LOCALAPPDATA": str(tmp_path / "local"),
        },
        platform="win32",
        home=tmp_path / "home",
        prepare=False,
    )
    assert result.root == env_root.resolve()


def test_platform_default_is_used_without_overrides(tmp_path):
    local = tmp_path / "local"
    result = resolve_data_root(
        environ={"LOCALAPPDATA": str(local)},
        platform="win32",
        home=tmp_path / "home",
        prepare=False,
    )
    assert result.root == (local / "ProfileDock").resolve()


def test_layout_directories_are_separate(tmp_path):
    result = resolve_data_root(tmp_path / "data")
    assert result.metadata_dir.is_dir()
    assert result.profiles_dir.is_dir()
    assert result.runtime_dir.is_dir()
    assert result.logs_dir.is_dir()
    assert result.backups_dir.is_dir()
    assert len({result.metadata_dir, result.profiles_dir, result.runtime_dir, result.logs_dir, result.backups_dir}) == 5


def test_rejects_filesystem_root():
    with pytest.raises(DataRootError):
        resolve_data_root(Path(Path.cwd().anchor), prepare=False)


def test_rejects_home_directory(tmp_path):
    home = tmp_path / "home"
    with pytest.raises(DataRootError):
        resolve_data_root(home, home=home, prepare=False)


def test_rejects_existing_file(tmp_path):
    target = tmp_path / "file"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(DataRootError):
        resolve_data_root(target, prepare=False)


def test_delete_refuses_data_root(tmp_path, monkeypatch):
    manager = ProfileManager(tmp_path / "data")
    profile = Profile("unsafe", "Unsafe", "2026-01-01T00:00:00+00:00", str(manager.root / "browser-data"))
    monkeypatch.setattr(manager, "resolve", lambda identifier: profile)
    with pytest.raises(ValueError, match="refusing to delete"):
        manager.delete(profile.id)


def test_cli_option_controls_command_storage(tmp_path, monkeypatch):
    env_root = tmp_path / "env"
    cli_root = tmp_path / "cli"
    monkeypatch.setenv("PROFILEDOCK_DATA_ROOT", str(env_root))
    result = runner.invoke(app, ["--data-root", str(cli_root), "create", "Work"])
    assert result.exit_code == 0
    assert (cli_root / "metadata" / "profiles.json").is_file()
    assert not (env_root / "metadata" / "profiles.json").exists()


def test_runtime_path_is_outside_browser_data(tmp_path):
    manager = ProfileManager(tmp_path / "data")
    profile = manager.create("Work")
    runtime = manager.runtime_path(profile.id)
    assert runtime.parent == manager.runtime_dir
    assert runtime not in Path(profile.data_dir).parents
    assert Path(profile.data_dir) not in runtime.parents
