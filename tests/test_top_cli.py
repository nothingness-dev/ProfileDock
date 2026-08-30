"""CLI contract tests for the resource-monitoring surfaces (status --metrics, show, top)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from typer.testing import CliRunner

from profiledock.cli import EXIT_USER_ERROR, app
from profiledock.cli_support import format_cpu_percent
from profiledock.metrics import (
    LiveResourceUsage,
    ProcessResourceUsage,
    ProfileMetrics,
    StorageResourceUsage,
)
from profiledock.profile_manager import ProfileNotFoundError

runner = CliRunner()


def _fake_profile(profile_id="abc123", name="Work", engine="direct", data_dir="/tmp/nonexistent"):
    return SimpleNamespace(
        id=profile_id,
        name=name,
        engine=engine,
        data_dir=data_dir,
        created_at="2026-01-01T00:00:00+00:00",
        last_launched_at=None,
        launch_config=None,
    )


def _canned_metrics(
    profile_id="abc123",
    name="Work",
    engine="direct",
    status="running",
    live="__default__",
    storage=None,
) -> ProfileMetrics:
    if live == "__default__":
        live = LiveResourceUsage(
            status=status,
            total_cpu_percent=12.5,
            total_memory_rss_bytes=1234567.0,
            process_count=4,
            processes=[
                ProcessResourceUsage(
                    pid=1, name="browser", cpu_percent=4.0, memory_rss_bytes=400000, memory_vms_bytes=900000
                ),
                ProcessResourceUsage(
                    pid=2, name="renderer", cpu_percent=8.5, memory_rss_bytes=834567, memory_vms_bytes=900000
                ),
            ],
            tab_count=3,
        )
    if storage is None:
        storage = StorageResourceUsage(
            total_bytes=10000,
            browser_data_bytes=7000,
            cache_bytes=2000,
            cookies_storage_bytes=500,
            logs_bytes=500,
        )
    return ProfileMetrics(
        profile_id=profile_id,
        name=name,
        engine=engine,
        status=status,
        live=live,
        storage=storage,
    )


def _patch_manager(profiles):
    return patch(
        "profiledock.cli.manager",
        return_value=SimpleNamespace(
            list_profiles=lambda: profiles,
            resolve=lambda identifier: (
                profiles[0]
                if identifier
                else (_ for _ in ()).throw(ProfileNotFoundError("profile not found"))
            ),
            runtime_path=lambda profile_id: "/tmp/runtime",
        ),
    )


# ---------------------------------------------------------------------------
# top command


def test_top_json_schema_contract():
    profiles = [_fake_profile()]
    with (
        _patch_manager(profiles),
        patch("profiledock.metrics.get_profile_metrics", return_value=_canned_metrics()),
    ):
        result = runner.invoke(app, ["top", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["output_version"] == 1
    assert payload["command"] == "top"
    row = payload["data"]["profiles"][0]
    for key in (
        "profile_id",
        "name",
        "engine",
        "status",
        "cpu_percent",
        "memory_rss_bytes",
        "process_count",
        "tab_count",
        "disk_total_bytes",
    ):
        assert key in row
    assert row["profile_id"] == "abc123"
    assert row["status"] == "running"
    assert row["cpu_percent"] == 12.5
    assert row["memory_rss_bytes"] == 1234567
    assert row["process_count"] == 4
    assert row["tab_count"] == 3
    assert row["disk_total_bytes"] == 10000


def test_top_table_human_output():
    profiles = [_fake_profile()]
    with (
        _patch_manager(profiles),
        patch("profiledock.metrics.get_profile_metrics", return_value=_canned_metrics()),
    ):
        result = runner.invoke(app, ["top"])
    assert result.exit_code == 0, result.output
    headers = ["ID", "NAME", "ENGINE", "STATUS", "CPU%", "RSS", "PROCS", "TABS", "DISK"]
    for header in headers:
        assert header in result.output
    assert "abc123" in result.output
    assert "Work" in result.output
    assert "12.5%" in result.output
    assert "running" in result.output


def test_top_interval_must_be_positive():
    with _patch_manager([]):
        result = runner.invoke(app, ["top", "--interval", "0"])
    assert result.exit_code == EXIT_USER_ERROR
    assert "interval must be greater than 0" in result.output


def test_top_unknown_profile_not_found():
    with (
        _patch_manager([_fake_profile()]),
        patch(
            "profiledock.cli.manager",
            return_value=SimpleNamespace(
                list_profiles=lambda: [],
                resolve=lambda identifier: (_ for _ in ()).throw(
                    ProfileNotFoundError("profile not found: nope")
                ),
            ),
        ),
    ):
        result = runner.invoke(app, ["top", "nope", "--json"])
    assert result.exit_code == EXIT_USER_ERROR
    assert "Error [not_found]" in result.output


def test_top_stopped_profile_live_is_none():
    profiles = [_fake_profile()]
    metrics = _canned_metrics(status="stopped", live=None)
    with (
        _patch_manager(profiles),
        patch("profiledock.metrics.get_profile_metrics", return_value=metrics),
    ):
        result = runner.invoke(app, ["top", "--json"])
    assert result.exit_code == 0
    row = json.loads(result.output)["data"]["profiles"][0]
    assert row["status"] == "stopped"
    assert row["cpu_percent"] is None
    assert row["memory_rss_bytes"] is None
    assert row["process_count"] is None
    assert row["disk_total_bytes"] == 10000


def test_top_help_lists_options():
    result = runner.invoke(app, ["top", "--help"])
    assert result.exit_code == 0
    for flag in ("--watch", "-w", "--interval", "-i", "--json"):
        assert flag in result.output


# ---------------------------------------------------------------------------
# formatting


def test_format_cpu_percent():
    assert format_cpu_percent(None) == "-"
    assert format_cpu_percent(0.0) == "0.0%"
    assert format_cpu_percent(2.35) == "2.4%"
    assert format_cpu_percent(100.0) == "100.0%"
