import json
from pathlib import Path
from unittest.mock import patch

import pytest

from profiledock.cli import CLI_JSON_OUTPUT_VERSION
from profiledock.models import LaunchConfig, MetadataDocument, migrate_launch_config, migrate_metadata_value
from profiledock.process_manager import RUNNING_STATE_PROTOCOL_VERSION, _upgrade_legacy_state, _valid_direct_state, _valid_state
from profiledock.storage import migrate_metadata
import profiledock.storage as storage


FIXTURES = Path(__file__).parent / "fixtures" / "formats"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_metadata_historical_fixtures_migrate_idempotently():
    for name in ("metadata-v0.json", "metadata-v1.json"):
        migrated = migrate_metadata_value(fixture(name))
        assert migrate_metadata_value(migrated) == migrated
        assert MetadataDocument.from_dict(migrated).profiles[0].engine in (None, "direct")


def test_metadata_v1_requires_engine():
    value = fixture("metadata-v1.json")
    del value["profiles"][0]["engine"]
    with pytest.raises(ValueError, match="missing"):
        MetadataDocument.from_dict(value)


def test_metadata_future_version_is_rejected():
    with pytest.raises(ValueError, match="unsupported"):
        migrate_metadata_value({"schema_version": 2, "profiles": []})


def test_metadata_migration_backup_and_rollback(tmp_path):
    path = tmp_path / "profiles.json"
    profile_root = tmp_path / "profiles"
    profile_root.mkdir()
    path.write_text(json.dumps([]), encoding="utf-8")
    original = path.read_bytes()
    atomic_write = storage._atomic_write

    def interrupt(target, content, root=None):
        if target == path:
            raise OSError("interrupted")
        return atomic_write(target, content, root)

    with patch("profiledock.storage._atomic_write", side_effect=interrupt):
        with pytest.raises(OSError, match="interrupted"):
            migrate_metadata(path, profile_root)
    assert path.read_bytes() == original
    assert path.with_suffix(".json.bak").read_bytes() == original


def test_launch_config_historical_fixtures_migrate_idempotently():
    for name in ("launch-config-v0.json", "launch-config-v1.json"):
        migrated = migrate_launch_config(fixture(name))
        assert migrate_launch_config(migrated) == migrated
        assert LaunchConfig.from_dict(migrated).default_tabs == 3


def test_launch_config_rejects_future_and_unknown_fields():
    value = fixture("launch-config-v1.json")
    value["schema_version"] = 2
    with pytest.raises(ValueError, match="unsupported"):
        migrate_launch_config(value)
    value = fixture("launch-config-v1.json")
    value["unexpected"] = True
    with pytest.raises(ValueError, match="unknown"):
        migrate_launch_config(value)


def test_runtime_historical_fixtures_migrate_sequentially(tmp_path):
    for name in ("runtime-playwright-v1.json", "runtime-direct-v0.json"):
        path = tmp_path / name
        value = fixture(name)
        path.write_text(json.dumps(value), encoding="utf-8")
        migrated = _upgrade_legacy_state(path, value, "abc123")
        assert migrated["protocol_version"] == RUNNING_STATE_PROTOCOL_VERSION
        assert _upgrade_legacy_state(path, migrated, "abc123") == migrated
        if migrated["engine"] == "direct":
            assert _valid_direct_state(migrated, "abc123")
        else:
            assert _valid_state(migrated, "abc123")


def test_runtime_future_version_is_not_rewritten(tmp_path):
    path = tmp_path / "running.json"
    value = {"protocol_version": RUNNING_STATE_PROTOCOL_VERSION + 1, "engine": "direct"}
    path.write_text(json.dumps(value), encoding="utf-8")
    assert _upgrade_legacy_state(path, value, "abc123") == value
    assert json.loads(path.read_text(encoding="utf-8")) == value


def test_runtime_interrupted_migration_preserves_original_and_backup(tmp_path):
    path = tmp_path / "running.json"
    value = fixture("runtime-playwright-v1.json")
    original = json.dumps(value).encode("utf-8")
    path.write_bytes(original)
    with patch("profiledock.process_manager._atomic_private_json", side_effect=OSError("interrupted")):
        assert _upgrade_legacy_state(path, value, "abc123") == value
    assert path.read_bytes() == original
    assert json.loads((tmp_path / "running.json.v1.bak").read_text(encoding="utf-8")) == value


def test_cli_output_fixture_has_current_version():
    value = fixture("cli-v1.json")
    assert set(value) == {"output_version", "command", "data"}
    assert value["output_version"] == CLI_JSON_OUTPUT_VERSION


def test_backup_archive_v1_fixture_is_complete():
    value = fixture("backup-v1.json")
    assert set(value) == {
        "format_version", "profiledock_version", "created_at", "total_profiles",
        "total_files", "total_bytes", "profiles",
    }
    assert value["format_version"] == 1
