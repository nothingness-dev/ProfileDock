from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _make_data_dir(tmp_path: Path, name: str = "p3") -> Path:
    data_dir = tmp_path / name / "browser-data"
    data_dir.mkdir(parents=True)
    return data_dir


def _dummy_executable(tmp_path: Path) -> Path:
    executable = tmp_path / "chrome-dummy.exe"
    executable.write_text("dummy", encoding="utf-8")
    return executable


def test_direct_launch_args_include_ephemeral_loopback_cdp_port(tmp_path: Path) -> None:
    from profiledock.process_manager import start_direct_chrome

    data_dir = _make_data_dir(tmp_path)
    captured: dict[str, list[str]] = {}

    def fake_popen(args: list[str], **_: object) -> MagicMock:
        captured["args"] = list(args)
        return MagicMock(pid=4242, poll=lambda: None)

    with (
        patch("profiledock.process.direct.subprocess.Popen", side_effect=fake_popen),
        patch("profiledock.process_manager._get_process_create_time", return_value=100.0),
        patch("profiledock.process_manager._atomic_private_json"),
        patch("profiledock.process_manager._stop_process"),
        patch("profiledock.process.direct.time.monotonic", side_effect=[0.0, 3.0]),
    ):
        state = start_direct_chrome(str(data_dir), tabs=1, executable_path=_dummy_executable(tmp_path))

    assert "--remote-debugging-address=127.0.0.1" in captured["args"]
    assert "--remote-debugging-port=0" in captured["args"]
    assert state["cdp_port"] is None


def test_direct_launch_reads_new_devtools_active_port(tmp_path: Path) -> None:
    from profiledock.process_manager import start_direct_chrome

    data_dir = _make_data_dir(tmp_path)

    def fake_popen(*_: object, **__: object) -> MagicMock:
        (data_dir / "DevToolsActivePort").write_text("9223\n/browser/debug", encoding="utf-8")
        return MagicMock(pid=4242, poll=lambda: None)

    with (
        patch("profiledock.process.direct.subprocess.Popen", side_effect=fake_popen),
        patch("profiledock.process_manager._get_process_create_time", return_value=100.0),
        patch("profiledock.process_manager._atomic_private_json") as atomic,
        patch("profiledock.process_manager._stop_process"),
    ):
        state = start_direct_chrome(str(data_dir), tabs=1, executable_path=_dummy_executable(tmp_path))

    assert state["cdp_port"] == 9223
    assert atomic.call_args.args[1]["cdp_port"] == 9223


def test_direct_launch_ignores_stale_devtools_active_port(tmp_path: Path) -> None:
    from profiledock.process_manager import start_direct_chrome

    data_dir = _make_data_dir(tmp_path)
    (data_dir / "DevToolsActivePort").write_text("9223\n/stale", encoding="utf-8")

    with (
        patch(
            "profiledock.process.direct.subprocess.Popen",
            return_value=MagicMock(pid=4242, poll=lambda: None),
        ),
        patch("profiledock.process_manager._get_process_create_time", return_value=100.0),
        patch("profiledock.process_manager._atomic_private_json"),
        patch("profiledock.process_manager._stop_process"),
        patch("profiledock.process.direct.time.monotonic", side_effect=[0.0, 3.0]),
    ):
        state = start_direct_chrome(str(data_dir), tabs=1, executable_path=_dummy_executable(tmp_path))

    assert state["cdp_port"] is None
    assert not (data_dir / "DevToolsActivePort").exists()


def test_cdp_port_from_file_parses_first_line(tmp_path: Path) -> None:
    from profiledock.process.direct import _cdp_port_from_active_port_file

    assert _cdp_port_from_active_port_file(tmp_path) is None
    path = tmp_path / "DevToolsActivePort"
    path.write_text("9223\n/browser/debug", encoding="utf-8")
    assert _cdp_port_from_active_port_file(tmp_path) == 9223
    path.write_text("not-a-port\n", encoding="utf-8")
    assert _cdp_port_from_active_port_file(tmp_path) is None
    path.write_text("0\n", encoding="utf-8")
    assert _cdp_port_from_active_port_file(tmp_path) is None


def _direct_state(profile_id: str, cdp_port: int | None = 9225) -> dict[str, object]:
    return {
        "protocol_version": 2,
        "engine": "direct",
        "profile_id": profile_id,
        "pid": 100,
        "launcher_pid": 1,
        "process_create_time": 1.0,
        "tabs": 1,
        "channel": "chrome",
        "started_at": "2026-01-01T00:00:00+00:00",
        "status": "running",
        "cdp_port": cdp_port,
    }


def test_direct_state_without_cdp_port_still_valid() -> None:
    from profiledock.process_manager import _valid_direct_state

    state = _direct_state("p3", None)
    assert _valid_direct_state(state, "p3")
    state["cdp_port"] = 0
    assert not _valid_direct_state(state, "p3")


def test_ipc_routes_direct_commands_with_the_validated_cdp_port(tmp_path: Path) -> None:
    from profiledock.process_manager import send_controller_command

    data_dir = _make_data_dir(tmp_path)
    (data_dir.parent / "running.json").write_text(
        json.dumps(_direct_state(data_dir.parent.name)), encoding="utf-8"
    )

    with (
        patch("profiledock.process_manager._is_matching_process", return_value=True),
        patch(
            "profiledock.process.direct_bridge.run_direct_cdp_command",
            return_value={"status": "ok", "tabs": []},
        ) as run_direct,
    ):
        result = send_controller_command(str(data_dir), "tabs", auto_start_headless=False)

    assert result == {"status": "ok", "tabs": []}
    assert run_direct.call_args.kwargs["cdp_port"] == 9225


def test_ipc_raises_for_direct_bridge_error_response(tmp_path: Path) -> None:
    from profiledock.process_manager import BrowserLaunchError, send_controller_command

    data_dir = _make_data_dir(tmp_path)
    (data_dir.parent / "running.json").write_text(
        json.dumps(_direct_state(data_dir.parent.name)), encoding="utf-8"
    )

    with (
        patch("profiledock.process_manager._is_matching_process", return_value=True),
        patch(
            "profiledock.process.direct_bridge.run_direct_cdp_command",
            return_value={"status": "error", "message": "bridge failure"},
        ),
    ):
        with pytest.raises(BrowserLaunchError, match="bridge failure"):
            send_controller_command(str(data_dir), "tabs", auto_start_headless=False)


def test_bridge_requires_cdp_port(tmp_path: Path) -> None:
    from profiledock.process_manager import ProfileRunningError, send_controller_command

    data_dir = _make_data_dir(tmp_path)
    (data_dir.parent / "running.json").write_text(
        json.dumps(_direct_state(data_dir.parent.name, None)), encoding="utf-8"
    )

    with pytest.raises(ProfileRunningError, match="DevTools endpoint"):
        send_controller_command(str(data_dir), "tabs", auto_start_headless=False)


def test_bridge_rejects_direct_auto_start(tmp_path: Path) -> None:
    from profiledock.process_manager import ProfileRunningError, send_controller_command

    data_dir = _make_data_dir(tmp_path)
    (data_dir.parent / "running.json").write_text(
        json.dumps(_direct_state(data_dir.parent.name, None)), encoding="utf-8"
    )

    with patch("profiledock.process_manager.start_controller") as start_controller_mock:
        with pytest.raises(ProfileRunningError, match="DevTools endpoint"):
            send_controller_command(str(data_dir), "tabs", auto_start_headless=True)
    start_controller_mock.assert_not_called()


def test_bridge_stops_playwright_without_closing_the_browser() -> None:
    pytest.importorskip("playwright.sync_api")
    from profiledock.process.direct_bridge import run_direct_cdp_command

    page = MagicMock(url="about:blank")
    page.title.return_value = ""
    context = MagicMock(pages=[page])
    browser = MagicMock(contexts=[context])
    playwright = MagicMock()
    playwright.chromium.connect_over_cdp.return_value = browser
    factory = MagicMock()
    factory.start.return_value = playwright

    with patch("playwright.sync_api.sync_playwright", return_value=factory):
        result = run_direct_cdp_command("unused", "tabs", cdp_port=9225)

    assert result["status"] == "ok"
    browser.close.assert_not_called()
    playwright.stop.assert_called_once()


def test_bridge_imports_cookies_from_the_standard_request_field() -> None:
    from profiledock.process import direct_bridge

    context = MagicMock(pages=[MagicMock()])
    context.cookies.return_value = []

    with patch.object(
        direct_bridge,
        "_with_connection",
        side_effect=lambda _port, handler: handler(context, context.pages[0]),
    ):
        result = direct_bridge.run_direct_cdp_command(
            "unused",
            "set_cookies",
            {"set_cookies": [{"name": "session", "value": "token", "url": "https://example.com"}]},
            cdp_port=9225,
        )

    assert result["added"] == 1
    context.add_cookies.assert_called_once()


def test_bridge_honors_selected_read_tab() -> None:
    from profiledock.process import direct_bridge

    first = MagicMock(url="https://first.example")
    second = MagicMock(url="https://second.example")
    second.content.return_value = "<title>Second</title>"
    second.title.return_value = "Second"
    context = MagicMock(pages=[first, second])

    with (
        patch.object(
            direct_bridge,
            "_with_connection",
            side_effect=lambda _port, handler: handler(context, first),
        ),
        patch.object(
            direct_bridge,
            "extract_page_markdown",
            return_value={"title": "Second", "content": "body", "links": []},
        ),
    ):
        result = direct_bridge.run_direct_cdp_command(
            "unused", "read_page", {"tab": 1, "url": None}, cdp_port=9225
        )

    assert result["url"] == "https://second.example"
    second.content.assert_called_once()
    first.content.assert_not_called()


def test_bridge_rejects_capture_inside_browser_data(tmp_path: Path) -> None:
    from profiledock.process import direct_bridge
    from profiledock.process_manager import BrowserLaunchError

    data_dir = _make_data_dir(tmp_path)
    page = MagicMock(url="about:blank")
    context = MagicMock(pages=[page])

    with patch.object(
        direct_bridge,
        "_with_connection",
        side_effect=lambda _port, handler: handler(context, page),
    ):
        with pytest.raises(BrowserLaunchError, match="inside the profile data directory"):
            direct_bridge.run_direct_cdp_command(
                str(data_dir),
                "screenshot",
                {"tab": 0, "output": str(data_dir / "capture.png")},
                cdp_port=9225,
            )
