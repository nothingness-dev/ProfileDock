from __future__ import annotations

from unittest.mock import MagicMock


class _RecordingPlaywright:
    def __init__(self) -> None:
        self.calls: list[dict] = []

        def launch(data_dir, **kwargs):
            self.calls.append(kwargs)
            return MagicMock(), "chromium"

        self.chromium = MagicMock()
        self.chromium.launch_persistent_context = launch


def test_playwright_context_suppresses_automation_defaults():
    from profiledock.process.controller import _launch_context

    instance = _RecordingPlaywright()
    _launch_context(instance, "unused", headless=False)
    kwargs = instance.calls[0]
    ignored = list(kwargs.get("ignore_default_args") or [])
    assert "--enable-automation" in ignored, kwargs
    args = list(kwargs.get("args") or [])
    assert "--disable-blink-features=AutomationControlled" in args, kwargs


def test_playwright_context_never_injects_navigator_patches():
    from profiledock.process.controller import _launch_context

    instance = _RecordingPlaywright()
    _launch_context(instance, "unused", headless=True)
    kwargs = instance.calls[0]
    assert "init_script" not in kwargs
    for arg in kwargs.get("args") or []:
        assert "webdriver" not in arg.lower(), f"JS-level webdriver patch leaked into args: {arg}"


def test_webrtc_sealed_when_proxy_playwright():
    from profiledock.process.controller import _launch_context

    instance = _RecordingPlaywright()
    _launch_context(instance, "unused", headless=True, proxy="http://127.0.0.1:18080")
    args = list(instance.calls[0].get("args") or [])
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in args, args


def test_webrtc_not_sealed_without_proxy_playwright():
    from profiledock.process.controller import _launch_context

    instance = _RecordingPlaywright()
    _launch_context(instance, "unused", headless=True)
    args = list(instance.calls[0].get("args") or [])
    assert not any("webrtc" in a.lower() for a in args), args


def test_direct_launch_options_translate_identity_presets():

    from profiledock.launch_service import direct_launch_options

    plan = MagicMock()
    plan.browser = None
    plan.urls = []
    plan.window_width = None
    plan.window_height = None
    plan.proxy = None
    plan.user_agent = "TestUA/9.9"
    plan.locale = "de-DE"
    plan.timezone = "Europe/Berlin"

    options = direct_launch_options(plan)
    args = list(options.get("extra_args") or [])
    assert "--user-agent=TestUA/9.9" in args, options
    assert any(a.startswith("--lang=") for a in args), options
    assert any(a.startswith("--accept-lang=") for a in args), options


def test_direct_launch_options_omit_identity_when_unset():
    from profiledock.launch_service import direct_launch_options

    plan = MagicMock()
    plan.browser = None
    plan.urls = []
    plan.window_width = None
    plan.window_height = None
    plan.proxy = None
    plan.user_agent = None
    plan.locale = None
    plan.timezone = None

    options = direct_launch_options(plan)
    args = list(options.get("extra_args") or [])
    assert not any(a.startswith("--user-agent") for a in args)
    assert not any(a.startswith("--lang") for a in args)


def test_direct_engine_seals_webrtc_with_proxy():
    from profiledock.launch_service import direct_launch_options

    plan = MagicMock()
    plan.browser = None
    plan.urls = []
    plan.window_width = None
    plan.window_height = None
    plan.proxy = "socks5://127.0.0.1:9050"
    plan.user_agent = None
    plan.locale = None
    plan.timezone = None

    options = direct_launch_options(plan)
    args = list(options.get("extra_args") or [])
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in args, options


def test_direct_engine_no_webrtc_flags_without_proxy():
    from profiledock.launch_service import direct_launch_options

    plan = MagicMock()
    plan.browser = None
    plan.urls = []
    plan.window_width = None
    plan.window_height = None
    plan.proxy = None
    plan.user_agent = None
    plan.locale = None
    plan.timezone = None

    options = direct_launch_options(plan)
    args = list(options.get("extra_args") or [])
    assert not any("webrtc" in a.lower() for a in args), options
