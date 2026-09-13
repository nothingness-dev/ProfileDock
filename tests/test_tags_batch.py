import pytest


def test_profile_from_dict_defaults_tags_to_empty_list():
    from profiledock.models import Profile

    profile = Profile.from_dict(
        {
            "id": "abc123",
            "name": "Work",
            "created_at": "2026-01-01T00:00:00+00:00",
            "data_dir": "/x/browser-data",
            "engine": "direct",
        }
    )
    assert profile.tags == []


def test_profile_rejects_unknown_tags_type():
    from profiledock.models import Profile

    with pytest.raises(ValueError, match="tags"):
        Profile.from_dict(
            {
                "id": "abc123",
                "name": "Work",
                "created_at": "2026-01-01T00:00:00+00:00",
                "data_dir": "/x/browser-data",
                "engine": "direct",
                "tags": "not-a-list",
            }
        )


def test_current_metadata_requires_tags():
    from profiledock.models import METADATA_SCHEMA_VERSION, MetadataDocument

    with pytest.raises(ValueError, match="tags"):
        MetadataDocument.from_dict(
            {
                "schema_version": METADATA_SCHEMA_VERSION,
                "profiles": [
                    {
                        "id": "abc123",
                        "name": "Work",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "data_dir": "/x/browser-data",
                        "engine": "direct",
                    }
                ],
            }
        )


def test_metadata_validation_rejects_unsafe_tags():
    from profiledock.models import Profile
    from profiledock.validation import ValidationError, validate_required_fields

    profile = Profile(
        "abc123",
        "Work",
        "2026-01-01T00:00:00+00:00",
        "/x/browser-data",
        engine="direct",
        tags=["has space"],
    )
    with pytest.raises(ValidationError, match="unsafe tag"):
        validate_required_fields(profile)


def test_migrate_metadata_defaults_tags_and_keeps_roundtrip():
    from profiledock.models import (
        METADATA_SCHEMA_VERSION,
        MetadataDocument,
        migrate_metadata_value,
    )

    migrated = migrate_metadata_value(
        {
            "schema_version": 1,
            "profiles": [
                {
                    "id": "abc123",
                    "name": "Work",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "data_dir": "/x/browser-data",
                    "engine": "direct",
                }
            ],
        }
    )
    assert migrated["schema_version"] == METADATA_SCHEMA_VERSION
    assert migrated["profiles"][0]["tags"] == []

    doc = MetadataDocument.from_dict(
        {
            "schema_version": METADATA_SCHEMA_VERSION,
            "profiles": [
                {
                    "id": "abc123",
                    "name": "Work",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "data_dir": "/x/browser-data",
                    "engine": "direct",
                    "tags": ["work", "social"],
                }
            ],
        }
    )
    assert doc.profiles[0].tags == ["work", "social"]
    assert MetadataDocument.from_dict(doc.to_dict()).profiles[0].tags == ["work", "social"]


def test_manager_persists_tags_and_lists_by_tag(tmp_path, monkeypatch):
    from profiledock.cli_support import _paths, _paths_prepared
    from profiledock.data_root import resolve_data_root
    from profiledock.profile_manager import ProfileManager

    monkeypatch.setenv("PROFILEDOCK_DATA_ROOT", str(tmp_path))
    _paths.set(None)
    _paths_prepared.set(False)
    try:
        paths = resolve_data_root(prepare=True)
        manager = ProfileManager(paths)
        manager.create("A")
        manager.create("B")
        manager.create("C")
        manager.set_tags("A", ["work", "core"])
        manager.set_tags("B", ["work"])
        assert manager.get_tags("A") == ["work", "core"]
        tagged = {p.name for p in manager.list_by_tag("work")}
        assert tagged == {"A", "B"}
        fresh = ProfileManager(paths)
        assert fresh.get_tags("A") == ["work", "core"]
    finally:
        _paths.set(None)
        _paths_prepared.set(False)


def test_tag_validation_rejects_bad_tags(tmp_path):
    from profiledock.data_root import DataPaths
    from profiledock.profile_manager import ProfileManager
    from profiledock.validation import ValidationError

    paths = DataPaths.from_root(tmp_path)
    paths.prepare()
    manager = ProfileManager(paths)
    profile = manager.create("A")
    with pytest.raises(ValidationError):
        manager.set_tags(profile.id, ["has space"])
    with pytest.raises(ValidationError):
        manager.set_tags(profile.id, ["", "ok"])
    manager.set_tags(profile.id, ["work", "work"])
    assert manager.get_tags(profile.id) == ["work"]
    manager.set_tags(profile.id, ["UPPER"])
    assert manager.get_tags(profile.id) == ["UPPER"]
    assert [item.id for item in manager.list_by_tag(" UPPER ")] == [profile.id]
    with pytest.raises(ValidationError):
        manager.list_by_tag("has space")


def test_launch_batch_tag_selects_subset_and_reports_per_profile_outcomes(monkeypatch, tmp_path):
    from unittest.mock import MagicMock, patch

    from typer.testing import CliRunner

    from profiledock.cli import app
    from profiledock.cli_support import _paths, _paths_prepared
    from profiledock.data_root import resolve_data_root
    from profiledock.profile_manager import ProfileManager, ProfileNotFoundError

    monkeypatch.setenv("PROFILEDOCK_DATA_ROOT", str(tmp_path / "batch"))
    _paths.set(None)
    _paths_prepared.set(False)
    paths = resolve_data_root(prepare=True)
    manager = ProfileManager(paths)
    a = manager.create("BatchA")
    b = manager.create("BatchB")
    manager.set_tags(a.id, ["fleet"])
    manager.set_tags(b.id, ["fleet"])
    manager.create("Solo")

    good_plan = MagicMock(
        engine="direct", tabs=1, urls=[], browser=None, window_width=None, window_height=None
    )
    runner = CliRunner()
    with (
        patch(
            "profiledock.commands.browser._resolve_launch_options",
            side_effect=[
                MagicMock(
                    profile=a,
                    plan=good_plan,
                    engine="direct",
                    tabs=1,
                    urls=[],
                    browser=None,
                    width=None,
                    height=None,
                ),
                ProfileNotFoundError("boom"),
            ],
        ),
        patch("profiledock.cli.start_direct_chrome", return_value={"pid": 123}),
    ):
        result = runner.invoke(app, ["launch", "--tag", "fleet", "--tabs", "1"])
    assert result.exit_code == 0, result.output
    assert "Launched 'BatchA'." in result.output
    assert "boom" in result.output
    assert "1 started, 1 failed." in result.output
    assert "Solo" not in result.output


def test_launch_batch_all_reports_json_outcomes(monkeypatch, tmp_path):
    import json

    from typer.testing import CliRunner

    from profiledock.cli import app
    from profiledock.cli_support import _paths, _paths_prepared
    from profiledock.data_root import resolve_data_root
    from profiledock.profile_manager import ProfileManager

    monkeypatch.setenv("PROFILEDOCK_DATA_ROOT", str(tmp_path / "batchjson"))
    _paths.set(None)
    _paths_prepared.set(False)
    paths = resolve_data_root(prepare=True)
    manager = ProfileManager(paths)
    manager.create("JsonA")
    manager.create("JsonB")

    from unittest.mock import MagicMock, patch

    runner = CliRunner()
    with (
        patch("profiledock.cli.start_direct_chrome", return_value={"pid": 1}),
        patch("profiledock.commands.browser._resolve_launch_options") as resolver,
    ):

        def _resolve(profile_id, *args, **kwargs):
            profile = manager.resolve(profile_id)
            return MagicMock(
                profile=profile,
                plan=MagicMock(engine="direct", tabs=1),
                engine="direct",
                tabs=1,
                urls=[],
                browser=None,
                width=None,
                height=None,
            )

        resolver.side_effect = _resolve
        result = runner.invoke(app, ["launch", "--all", "--json", "--tabs", "1"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["command"] == "launch"
    assert len(payload["data"]["outcomes"]) == 2
    assert all(item["status"] == "started" for item in payload["data"]["outcomes"])
    assert payload["data"]["started"] == 2
    assert payload["data"]["failed"] == 0


def test_launch_rejects_profile_with_batch_flags(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from profiledock.cli import app
    from profiledock.cli_support import _paths, _paths_prepared
    from profiledock.data_root import resolve_data_root
    from profiledock.profile_manager import ProfileManager

    monkeypatch.setenv("PROFILEDOCK_DATA_ROOT", str(tmp_path / "batchmix"))
    _paths.set(None)
    _paths_prepared.set(False)
    paths = resolve_data_root(prepare=True)
    manager = ProfileManager(paths)
    manager.create("MixA")

    runner = CliRunner()
    result = runner.invoke(app, ["launch", "MixA", "--tag", "fleet"])
    assert result.exit_code != 0
    assert "cannot specify both" in result.output.lower()
    result = runner.invoke(app, ["launch", "MixA", "--all"])
    assert result.exit_code != 0
    assert "cannot specify both" in result.output.lower()


def test_tags_command_requires_explicit_replacement_or_clear(tmp_path):
    from typer.testing import CliRunner

    from profiledock.cli import app

    runner = CliRunner()
    assert runner.invoke(app, ["--data-root", str(tmp_path), "create", "Work"]).exit_code == 0
    missing = runner.invoke(app, ["--data-root", str(tmp_path), "tags", "Work"])
    assert missing.exit_code != 0
    assert "at least one tag" in missing.output
    clear = runner.invoke(app, ["--data-root", str(tmp_path), "tags", "Work", "--clear"])
    assert clear.exit_code == 0, clear.output
    mixed = runner.invoke(app, ["--data-root", str(tmp_path), "tags", "Work", "fleet", "--clear"])
    assert mixed.exit_code != 0
    assert "cannot be combined" in mixed.output


def test_launch_requires_one_target_and_limits_json_to_batch(tmp_path):
    from typer.testing import CliRunner

    from profiledock.cli import app

    runner = CliRunner()
    no_target = runner.invoke(app, ["--data-root", str(tmp_path), "launch", "--tabs", "1"])
    assert no_target.exit_code != 0
    assert "must specify a profile identifier" in no_target.output
    assert runner.invoke(app, ["--data-root", str(tmp_path), "create", "Work"]).exit_code == 0
    single_json = runner.invoke(
        app, ["--data-root", str(tmp_path), "launch", "Work", "--tabs", "1", "--json"]
    )
    assert single_json.exit_code != 0
    assert "requires --tag or --all" in single_json.output
