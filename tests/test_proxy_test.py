from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


class _FakeOpener:
    def __init__(self, value: Any) -> None:
        self.value = value
        self.calls: list[tuple[str, float]] = []

    def __call__(self, url: str, timeout: float) -> Any:
        self.calls.append((url, timeout))
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


def test_proxy_test_reports_exit_ip_timezone_and_locale() -> None:
    from profiledock.proxy_test import proxy_test

    opener = _FakeOpener(
        b'{"ip":"89.187.171.100","timezone":"Europe/Berlin","country_code":"DE","languages":"de,en"}'
    )
    result = proxy_test("http://127.0.0.1:18080", opener=opener)
    assert result["ok"] is True
    assert result["exit_ip"] == "89.187.171.100"
    assert result["timezone"] == "Europe/Berlin"
    assert result["locale"] == "de-DE"
    assert result["latency_ms"] >= 0
    assert opener.calls == [("https://ipapi.co/json/", 15.0)]


def test_proxy_test_reports_transport_failures() -> None:
    from profiledock.proxy_test import proxy_test

    result = proxy_test("http://127.0.0.1:18080", opener=_FakeOpener(TimeoutError("timed out")))
    assert result["ok"] is False
    assert "timed out" in str(result["error"])


@pytest.mark.parametrize(
    "proxy",
    [None, "socks5://127.0.0.1:9050", "socks5://user:pass@127.0.0.1:9050"],
)
def test_proxy_test_rejects_unsupported_proxy_inputs(proxy: str | None) -> None:
    from profiledock.proxy_test import ProxyTestError, proxy_test

    with pytest.raises((ProxyTestError, ValueError)):
        proxy_test(proxy)


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_proxy_test_rejects_invalid_timeouts(timeout: float) -> None:
    from profiledock.proxy_test import ProxyTestError, proxy_test

    with pytest.raises(ProxyTestError, match="timeout"):
        proxy_test("http://127.0.0.1:18080", timeout=timeout)


def test_proxy_test_rejects_invalid_geo_response() -> None:
    from profiledock.proxy_test import proxy_test

    result = proxy_test("http://127.0.0.1:18080", opener=_FakeOpener(b'{"timezone":"Europe/Berlin"}'))
    assert result["ok"] is False
    assert "exit IP" in str(result["error"])


def test_proxy_test_command_writes_suggested_preset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from typer.testing import CliRunner

    from profiledock.cli import app
    from profiledock.cli_support import _paths, _paths_prepared
    from profiledock.data_root import resolve_data_root
    from profiledock.profile_manager import ProfileManager

    monkeypatch.setenv("PROFILEDOCK_DATA_ROOT", str(tmp_path / "data"))
    _paths.set(None)
    _paths_prepared.set(False)
    paths = resolve_data_root(prepare=True)
    manager = ProfileManager(paths)
    profile = manager.create("ProxyCheck")
    manager.update_launch_config(profile.id, proxy="http://127.0.0.1:18080")
    monkeypatch.setattr(
        "profiledock.proxy_test.proxy_test",
        lambda proxy, timeout: {
            "ok": True,
            "exit_ip": "89.187.171.100",
            "timezone": "Europe/Berlin",
            "locale": "de-DE",
            "latency_ms": 12,
            "error": None,
        },
    )

    result = CliRunner().invoke(app, ["proxy-test", profile.id, "--write"])
    assert result.exit_code == 0, result.output
    assert "proxy: http://127.0.0.1:18080" in result.output
    assert "updated preset" in result.output
    updated = manager.get_launch_config(profile.id)
    assert updated.timezone == "Europe/Berlin"
    assert updated.locale == "de-DE"
