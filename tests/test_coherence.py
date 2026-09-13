"""Tests for profiledock.coherence: auditable egress/fingerprint scoring.

The scorer is fully deterministic: every dependency (egress opener, geo
provider payload) is injected, so no network access happens in tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from profiledock.cli import app
from profiledock.coherence import (
    CoherenceError,
    check_coherence,
)
from profiledock.data_root import DataPaths
from profiledock.models import METADATA_SCHEMA_VERSION, LaunchConfig, MetadataDocument, Profile
from profiledock.profile_manager import ProfileManager
from profiledock.storage import save_metadata

runner = CliRunner()

DE_PAYLOAD = {
    "ip": "89.187.171.100",
    "timezone": "Europe/Berlin",
    "country_code": "DE",
    "languages": "de,en",
}
JP_PAYLOAD = {
    "ip": "203.0.113.7",
    "timezone": "Asia/Tokyo",
    "country_code": "JP",
    "languages": "ja",
}


class FakeOpener:
    """Maps URL -> payload; raises KeyError entries as URLError-like failures."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def __call__(self, url: str, timeout: float) -> Any:
        self.calls.append(url)
        value = self.responses[url]
        if isinstance(value, Exception):
            raise value
        return value


def _payload_response(payload: dict[str, Any]) -> Any:
    class _Resp:
        def read(self, size: int = -1) -> bytes:
            return json.dumps(payload).encode("utf-8")

        def close(self) -> None:
            pass

    return _Resp()


def make_profile(
    paths: DataPaths,
    *,
    pid: str = "p1",
    name: str = "Coherent",
    launch_config: LaunchConfig | None = None,
) -> Profile:
    p_data = paths.profiles_dir / pid / "browser-data"
    p_data.mkdir(parents=True, exist_ok=True)
    profile = Profile(
        id=pid,
        name=name,
        created_at="2026-01-01T00:00:00+00:00",
        data_dir=str(p_data),
        engine="direct",
        launch_config=launch_config,
    )
    doc = MetadataDocument(schema_version=METADATA_SCHEMA_VERSION, profiles=[profile])
    save_metadata(doc, paths.profiles_file, paths.profiles_dir)
    return profile


# --- provider fallback ------------------------------------------------------


def test_first_provider_success_no_fallback() -> None:
    opener = FakeOpener(
        {p: _payload_response(DE_PAYLOAD) for p in check_coherence.__globals__["GEO_ENDPOINTS"]}
    )
    report = check_coherence(
        proxy="http://127.0.0.1:18080",
        configured_timezone=None,
        configured_locale=None,
        opener=opener,
    )
    assert report.egress_ip == "89.187.171.100"
    assert report.detected_timezone == "Europe/Berlin"
    assert report.detected_country == "DE"
    assert len(opener.calls) == 1


def test_falls_back_to_next_provider_on_failure() -> None:
    endpoints = check_coherence.__globals__["GEO_ENDPOINTS"]
    responses: dict[str, Any] = {endpoints[0]: TimeoutError("timed out")}
    for endpoint in endpoints[1:]:
        responses[endpoint] = _payload_response(DE_PAYLOAD)
    opener = FakeOpener(responses)
    report = check_coherence(
        proxy="http://127.0.0.1:18080",
        configured_timezone=None,
        configured_locale=None,
        opener=opener,
    )
    assert report.egress_ip == "89.187.171.100"
    assert len(opener.calls) == 2


def test_all_providers_failing_yields_low_score() -> None:
    opener = FakeOpener({p: TimeoutError("down") for p in check_coherence.__globals__["GEO_ENDPOINTS"]})
    report = check_coherence(
        proxy="http://127.0.0.1:18080",
        configured_timezone="Europe/Berlin",
        configured_locale="de-DE",
        opener=opener,
    )
    assert report.egress_ip is None
    assert report.is_coherent is False
    assert any(d["id"] == "egress_unreachable" for d in report.deductions)


# --- timezone / locale cross-checks ----------------------------------------


def test_timezone_mismatch_deducts() -> None:
    opener = FakeOpener(
        {p: _payload_response(JP_PAYLOAD) for p in check_coherence.__globals__["GEO_ENDPOINTS"]}
    )
    report = check_coherence(
        proxy="http://127.0.0.1:18080",
        configured_timezone="Europe/Berlin",
        configured_locale="de-DE",
        opener=opener,
    )
    ids = {d["id"] for d in report.deductions}
    assert "timezone_mismatch" in ids
    assert report.is_coherent is False
    assert report.score < 100


def test_matching_timezone_no_deduction() -> None:
    opener = FakeOpener(
        {p: _payload_response(DE_PAYLOAD) for p in check_coherence.__globals__["GEO_ENDPOINTS"]}
    )
    report = check_coherence(
        proxy="http://127.0.0.1:18080",
        configured_timezone="Europe/Berlin",
        configured_locale="de-DE",
        opener=opener,
    )
    ids = {d["id"] for d in report.deductions}
    assert "timezone_mismatch" not in ids
    assert report.is_coherent is True
    assert report.score == 100


def test_missing_timezone_deducts() -> None:
    opener = FakeOpener(
        {p: _payload_response(DE_PAYLOAD) for p in check_coherence.__globals__["GEO_ENDPOINTS"]}
    )
    report = check_coherence(
        proxy="http://127.0.0.1:18080",
        configured_timezone=None,
        configured_locale=None,
        opener=opener,
    )
    ids = {d["id"] for d in report.deductions}
    assert "timezone_unset" in ids
    assert report.is_coherent is False


def test_locale_country_mismatch_deducts() -> None:
    opener = FakeOpener(
        {p: _payload_response(JP_PAYLOAD) for p in check_coherence.__globals__["GEO_ENDPOINTS"]}
    )
    report = check_coherence(
        proxy="http://127.0.0.1:18080",
        configured_timezone="Asia/Tokyo",
        configured_locale="en-US",
        opener=opener,
    )
    ids = {d["id"] for d in report.deductions}
    assert "locale_mismatch" in ids
    assert "timezone_mismatch" not in ids


def test_socks5_proxy_noted_not_scored_down() -> None:
    opener = FakeOpener(
        {p: _payload_response(DE_PAYLOAD) for p in check_coherence.__globals__["GEO_ENDPOINTS"]}
    )
    report = check_coherence(
        proxy="socks5://127.0.0.1:9050",
        configured_timezone="Europe/Berlin",
        configured_locale="de-DE",
        opener=opener,
    )
    ids = {d["id"] for d in report.deductions}
    assert "socks5_unverified" in ids
    # The note reduces the score but a single note alone cannot sink coherence.
    assert report.is_coherent is True
    assert report.score == 100 - next(
        d["penalty"] for d in report.deductions if d["id"] == "socks5_unverified"
    )


# --- deterministic scoring --------------------------------------------------


def test_penalty_weights_are_known() -> None:
    opener = FakeOpener(
        {p: _payload_response(JP_PAYLOAD) for p in check_coherence.__globals__["GEO_ENDPOINTS"]}
    )
    report = check_coherence(
        proxy="http://127.0.0.1:18080",
        configured_timezone="Europe/Berlin",
        configured_locale="en-US",
        opener=opener,
    )
    penalties = {d["id"]: d["penalty"] for d in report.deductions}
    assert penalties["timezone_mismatch"] == 30
    assert penalties["locale_mismatch"] == 15
    assert report.score == 100 - 30 - 15
    assert 0 <= report.score <= 100


def test_report_remediation_command_targets_fix() -> None:
    opener = FakeOpener(
        {p: _payload_response(JP_PAYLOAD) for p in check_coherence.__globals__["GEO_ENDPOINTS"]}
    )
    report = check_coherence(
        proxy="http://127.0.0.1:18080",
        configured_timezone="Europe/Berlin",
        configured_locale=None,
        opener=opener,
    )
    assert report.remediation_command is not None
    assert "--fix" in report.remediation_command


def test_no_proxy_reports_special_state() -> None:
    report = check_coherence(
        proxy=None,
        configured_timezone="Europe/Berlin",
        configured_locale="de-DE",
        opener=FakeOpener({}),
    )
    assert report.egress_ip is None
    assert report.score == 100
    assert report.is_coherent is True
    assert any(d["id"] == "no_proxy" for d in report.deductions) is False


def test_invalid_proxy_raises() -> None:
    with pytest.raises((CoherenceError, ValueError)):
        check_coherence(
            proxy="gopher://nope",
            configured_timezone=None,
            configured_locale=None,
            opener=FakeOpener({}),
        )


# --- CLI --------------------------------------------------------------------


@pytest.fixture()
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DataPaths:
    from profiledock.cli_support import _paths, _paths_prepared
    from profiledock.data_root import resolve_data_root

    monkeypatch.setenv("PROFILEDOCK_DATA_ROOT", str(tmp_path / "data"))
    _paths.set(None)
    _paths_prepared.set(False)
    return resolve_data_root(prepare=True)


def test_cli_coherence_json(data_root: DataPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = ProfileManager(data_root)
    profile = manager.create("CoherentWork")
    manager.update_launch_config(
        profile.id,
        proxy="http://127.0.0.1:18080",
        timezone="Europe/Berlin",
        locale="de-DE",
    )
    endpoints = check_coherence.__globals__["GEO_ENDPOINTS"]
    payload = {e: _payload_response(DE_PAYLOAD) for e in endpoints}
    import profiledock.coherence as coherence_module

    monkeypatch.setattr(coherence_module, "_default_opener", lambda proxy_url: FakeOpener(payload))
    result = runner.invoke(app, ["coherence", "CoherentWork", "--json"])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["command"] == "coherence"
    assert envelope["data"]["score"] == 100
    assert envelope["data"]["is_coherent"] is True


def test_cli_coherence_fix_updates_preset(data_root: DataPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = ProfileManager(data_root)
    profile = manager.create("FixableWork")
    manager.update_launch_config(profile.id, proxy="http://127.0.0.1:18080")
    endpoints = check_coherence.__globals__["GEO_ENDPOINTS"]
    payload = {e: _payload_response(JP_PAYLOAD) for e in endpoints}
    import profiledock.coherence as coherence_module

    opener = FakeOpener(payload)
    monkeypatch.setattr(coherence_module, "_default_opener", lambda proxy_url: opener)
    result = runner.invoke(app, ["coherence", "FixableWork", "--fix", "--json"])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["data"]["fix_applied"] is True
    updated = manager.get_launch_config(profile.id)
    assert updated.timezone == "Asia/Tokyo"
    assert updated.locale == "ja-JP"
    assert len(opener.calls) == 1
    repeated = runner.invoke(app, ["coherence", "FixableWork", "--fix", "--json"])
    assert repeated.exit_code == 0, repeated.output
    assert json.loads(repeated.output)["data"]["fix_applied"] is False


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_invalid_timeout_rejected_before_network(timeout: float) -> None:
    with pytest.raises(CoherenceError, match="finite positive"):
        check_coherence(None, None, None, timeout=timeout, opener=FakeOpener({}))


@pytest.mark.parametrize("locale", ["de", "de-DE", "de-Latn-DE"])
def test_locale_region_comparison(locale: str) -> None:
    opener = FakeOpener(
        {p: _payload_response(DE_PAYLOAD) for p in check_coherence.__globals__["GEO_ENDPOINTS"]}
    )
    report = check_coherence("http://127.0.0.1:18080", "Europe/Berlin", locale, opener=opener)
    assert report.is_coherent
