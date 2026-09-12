from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from .validation import validate_proxy

_GEOIP_ENDPOINT = "https://ipapi.co/json/"
_MAX_RESPONSE_BYTES = 1024 * 1024


class ProxyTestError(ValueError):
    pass


def _default_opener(proxy_url: str) -> Callable[[str, float], Any]:
    handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    opener = urllib.request.build_opener(handler)

    def open_proxy(url: str, timeout: float) -> Any:
        return opener.open(url, timeout=timeout)

    return open_proxy


def _locale_from_payload(payload: dict[str, Any]) -> str | None:
    languages = payload.get("languages")
    country = payload.get("country_code")
    if not isinstance(languages, str) or not isinstance(country, str):
        return None
    language = languages.split(",", 1)[0].split("-", 1)[0].strip()
    country = country.strip().upper()
    if len(language) not in (2, 3) or len(country) != 2:
        return None
    return f"{language}-{country}"


def proxy_test(
    proxy: str | None,
    timeout: float = 15.0,
    opener: Callable[[str, float], Any] | None = None,
) -> dict[str, Any]:
    if not proxy:
        raise ProxyTestError("proxy URL is required")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ProxyTestError("timeout must be a positive finite number")
    validate_proxy(proxy)
    if urlparse(proxy).scheme.lower() not in {"http", "https"}:
        raise ProxyTestError("proxy-test supports HTTP and HTTPS proxies only")

    result: dict[str, Any] = {
        "ok": False,
        "exit_ip": None,
        "timezone": None,
        "locale": None,
        "latency_ms": None,
        "error": None,
    }
    do_open = opener if opener is not None else _default_opener(proxy)
    started = time.monotonic()
    try:
        response = do_open(_GEOIP_ENDPOINT, float(timeout))
        try:
            body = response.read(_MAX_RESPONSE_BYTES + 1) if hasattr(response, "read") else response
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        if isinstance(body, bytes):
            if len(body) > _MAX_RESPONSE_BYTES:
                raise ProxyTestError("geo response exceeds the maximum allowed size")
            body = body.decode("utf-8", errors="strict")
        if not isinstance(body, str):
            raise ProxyTestError("geo response is not text")
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ProxyTestError("geo response is not a JSON object")
        exit_ip = payload.get("ip")
        if not isinstance(exit_ip, str) or not exit_ip.strip():
            raise ProxyTestError("geo response did not contain an exit IP")
        timezone = payload.get("timezone")
        result.update(
            ok=True,
            exit_ip=exit_ip.strip(),
            timezone=timezone if isinstance(timezone, str) and "/" in timezone else None,
            locale=_locale_from_payload(payload),
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    except urllib.error.URLError as exc:
        result["error"] = f"proxy check failed: {getattr(exc, 'reason', exc)}"
    except TimeoutError as exc:
        result["error"] = f"proxy check timed out: {exc}"
    except Exception as exc:
        result["error"] = f"proxy check failed: {exc}"
    return result
