from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from profiledock.cli import app
from profiledock.data_root import DataRootError, platform_data_root, resolve_data_root
from profiledock.models import Profile
from profiledock.profile_manager import ProfileManager
from profiledock.process_manager import state_path

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


def test_delete_refuses_another_profile_directory(tmp_path, monkeypatch):
    manager = ProfileManager(tmp_path / "data")
    victim = manager.profiles_dir / "victim" / "browser-data"
    victim.mkdir(parents=True)
    profile = Profile("attacker", "Unsafe", "2026-01-01T00:00:00+00:00", str(victim))
    monkeypatch.setattr(manager, "resolve", lambda identifier: profile)
    with pytest.raises(ValueError, match="refusing to delete"):
        manager.delete(profile.id)
    assert victim.is_dir()


def test_runtime_path_rejects_path_like_profile_id(tmp_path):
    manager = ProfileManager(tmp_path / "data")
    with pytest.raises(ValueError, match="unsafe profile id"):
        manager.runtime_path("../logs")


def test_manager_rejects_filesystem_root():
    with pytest.raises(DataRootError):
        ProfileManager(Path(Path.cwd().anchor))


def test_manager_does_not_revalidate_resolved_paths(tmp_path):
    paths = resolve_data_root(tmp_path / "data")
    with patch.object(type(paths), "prepare", side_effect=AssertionError("revalidated")):
        manager = ProfileManager(paths)
    assert manager.root == paths.root


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


def test_rejects_managed_directory_replaced_by_file(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    (root / "runtime").write_text("unsafe", encoding="utf-8")
    with pytest.raises(DataRootError, match="managed data directory is unsafe"):
        resolve_data_root(root)


def test_rejects_managed_directory_symlink(tmp_path):
    root = tmp_path / "data"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    try:
        (root / "profiles").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")
    with pytest.raises(DataRootError, match="managed data directory is unsafe"):
        resolve_data_root(root)


def test_rejects_runtime_inside_browser_data(tmp_path):
    data_dir = tmp_path / "profiles" / "abc123" / "browser-data"
    data_dir.mkdir(parents=True)
    with pytest.raises(ValueError, match="runtime directory cannot be inside"):
        state_path(str(data_dir), data_dir / "runtime")
