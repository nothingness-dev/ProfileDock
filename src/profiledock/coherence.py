"""Auditable egress/fingerprint coherence scoring.

``profiledock coherence <profile>`` resolves the profile's configured proxy
exit IP through one of several geolocation providers, then cross-checks the
detected egress against the profile's identity preset. The report is a
deterministic 0-100 score with per-deduction reasons and a suggested
``--fix`` command.

Scoring rules (penalties are fixed, not heuristic):

- ``egress_unreachable``  -50: every geolocation provider failed.
- ``timezone_mismatch``   -30: detected egress timezone differs from the
  configured preset. The single most common geo leak.
- ``timezone_unset``      -15: proxy configured but no timezone pinned.
- ``locale_mismatch``     -15: configured locale's region contradicts the
  detected egress country.
- ``socks5_unverified``   -10: ``socks5://`` proxies are accepted at launch
  but this checker cannot tunnel stdlib HTTP through them, so the exit IP
  could not be verified live.

A profile without a proxy is coherent by definition (direct egress matches
the host), with no deductions.
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from .validation import validate_locale, validate_proxy, validate_time_zone

GEO_ENDPOINTS: tuple[str, ...] = (
    "https://ipapi.co/json/",
    "https://ipinfo.io/json",
    "https://api.myip.com",
)
_MAX_RESPONSE_BYTES = 1024 * 1024

PENALTY_WEIGHTS: dict[str, int] = {
    "egress_unreachable": 50,
    "timezone_mismatch": 30,
    "timezone_unset": 15,
    "locale_mismatch": 15,
    "socks5_unverified": 10,
}


class CoherenceError(ValueError):
    pass


@dataclass(frozen=True)
class CoherenceReport:
    score: int
    is_coherent: bool
    egress_ip: str | None
    detected_timezone: str | None
    configured_timezone: str | None
    detected_country: str | None
    configured_locale: str | None
    deductions: list[dict[str, Any]] = field(default_factory=list)
    remediation_command: str | None = None
    suggested_locale: str | None = None
    provider: str | None = None

    def to_dict(self, *, fix_applied: bool = False, profile: str | None = None) -> dict[str, Any]:
        return {
            "score": self.score,
            "is_coherent": self.is_coherent,
            "egress_ip": self.egress_ip,
            "detected_timezone": self.detected_timezone,
            "configured_timezone": self.configured_timezone,
            "detected_country": self.detected_country,
            "configured_locale": self.configured_locale,
            "deductions": [dict(d) for d in self.deductions],
            "remediation_command": self.remediation_command,
            "suggested_locale": self.suggested_locale,
            "provider": self.provider,
            "fix_applied": fix_applied,
            "profile": profile,
        }


def _default_opener(proxy_url: str) -> Callable[[str, float], Any]:
    handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    opener = urllib.request.build_opener(handler)

    def open_proxy(url: str, timeout: float) -> Any:
        return opener.open(url, timeout=timeout)

    return open_proxy


def _read_payload(response: Any) -> dict[str, Any]:
    try:
        body = response.read(_MAX_RESPONSE_BYTES + 1)
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()
    if len(body) > _MAX_RESPONSE_BYTES:
        raise CoherenceError("geo response exceeds the maximum allowed size")
    try:
        payload = json.loads(body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CoherenceError(f"geo response is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise CoherenceError("geo response is not a JSON object")
    return payload


def resolve_egress(
    proxy: str,
    timeout: float = 15.0,
    opener: Callable[[str, float], Any] | None = None,
) -> dict[str, Any]:
    """Resolve the proxy exit IP via geolocation providers with fallback."""
    do_open = opener if opener is not None else _default_opener(proxy)
    last_error: str | None = None
    for endpoint in GEO_ENDPOINTS:
        started = time.monotonic()
        try:
            response = do_open(endpoint, float(timeout))
            payload = _read_payload(response)
        except (urllib.error.URLError, TimeoutError, CoherenceError, OSError) as exc:
            last_error = f"{getattr(exc, 'reason', exc)}"
            continue
        exit_ip = payload.get("ip")
        if not isinstance(exit_ip, str) or not exit_ip.strip():
            last_error = "geo response did not contain an exit IP"
            continue
        timezone = payload.get("timezone") or payload.get("tz")
        country = payload.get("country_code") or payload.get("country")
        languages = payload.get("languages") or payload.get("loc_language") or ""
        locale = None
        if isinstance(country, str) and len(country.strip()) == 2:
            country = country.strip().upper()
            primary = ""
            if isinstance(languages, str) and languages.strip():
                primary = languages.split(",", 1)[0].split("-", 1)[0].strip().lower()
            if len(primary) in (2, 3):
                locale = f"{primary}-{country}"
        return {
            "egress_ip": exit_ip.strip(),
            "timezone": timezone if isinstance(timezone, str) and "/" in timezone else None,
            "country": country if isinstance(country, str) and len(country) == 2 else None,
            "locale": locale,
            "provider": endpoint,
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    raise CoherenceError(f"all geolocation providers failed: {last_error}")


def score_report(
    *,
    proxy: str | None,
    egress_ip: str | None,
    detected_timezone: str | None,
    detected_country: str | None,
    configured_timezone: str | None,
    configured_locale: str | None,
    remediation_hint: str | None = None,
    suggested_locale: str | None = None,
    provider: str | None = None,
) -> CoherenceReport:
    if configured_timezone is not None:
        validate_time_zone(configured_timezone)
    if configured_locale is not None:
        validate_locale(configured_locale)
    if proxy is not None:
        validate_proxy(proxy)

    deductions: list[dict[str, Any]] = []

    def deduct(deduction_id: str, reason: str) -> None:
        deductions.append(
            {
                "id": deduction_id,
                "penalty": PENALTY_WEIGHTS[deduction_id],
                "reason": reason,
            }
        )

    if proxy is None:
        # Direct egress: the host IP is the browser IP by construction.
        return CoherenceReport(
            score=100,
            is_coherent=True,
            egress_ip=None,
            detected_timezone=None,
            configured_timezone=configured_timezone,
            detected_country=None,
            configured_locale=configured_locale,
            deductions=[],
            remediation_command=None,
        )

    socks5 = urlparse(proxy).scheme.lower() == "socks5"
    if socks5 and egress_ip is None:
        deduct(
            "socks5_unverified",
            "socks5 proxies cannot be probed by this checker; the exit IP was not verified",
        )
    elif egress_ip is None:
        deduct("egress_unreachable", "every geolocation provider failed through the proxy")
    else:
        # Only the preset timezone is judged: a provider that cannot report a
        # timezone is a checker limitation, not a profile defect.
        if detected_timezone is not None and configured_timezone is None:
            deduct(
                "timezone_unset",
                f"proxy exit is in {detected_timezone} but no timezone is pinned in the preset",
            )
        elif (
            detected_timezone is not None
            and configured_timezone is not None
            and detected_timezone.strip() != configured_timezone.strip()
        ):
            deduct(
                "timezone_mismatch",
                f"proxy exit is in {detected_timezone} but the preset pins {configured_timezone}",
            )
        locale_region = next(
            (
                part.upper()
                for part in (configured_locale or "").split("-")[1:]
                if len(part) == 2 and part.isalpha()
            ),
            None,
        )
        if (
            detected_country is not None
            and locale_region is not None
            and locale_region != detected_country.upper()
        ):
            deduct(
                "locale_mismatch",
                f"proxy exit country is {detected_country} but the preset locale is {configured_locale}",
            )

    score = max(0, 100 - sum(d["penalty"] for d in deductions))
    is_coherent = not any(
        d["id"] in {"egress_unreachable", "timezone_mismatch", "timezone_unset", "locale_mismatch"}
        for d in deductions
    )
    remediation = remediation_hint
    if remediation is None and any(
        d["id"] in {"timezone_mismatch", "timezone_unset", "locale_mismatch"} for d in deductions
    ):
        remediation = f"profiledock coherence <profile> --fix  # align preset with egress {egress_ip}"
    return CoherenceReport(
        score=score,
        is_coherent=is_coherent,
        egress_ip=egress_ip,
        detected_timezone=detected_timezone,
        configured_timezone=configured_timezone,
        detected_country=detected_country,
        configured_locale=configured_locale,
        deductions=deductions,
        remediation_command=remediation,
        suggested_locale=suggested_locale,
        provider=provider,
    )


def check_coherence(
    proxy: str | None,
    configured_timezone: str | None,
    configured_locale: str | None,
    timeout: float = 15.0,
    opener: Callable[[str, float], Any] | None = None,
) -> CoherenceReport:
    """Full coherence check: egress resolution then deterministic scoring."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise CoherenceError("timeout must be a finite positive number")
    provider: str | None = None
    if proxy is not None:
        validate_proxy(proxy)
        socks5 = urlparse(proxy).scheme.lower() == "socks5"
        if not socks5:
            try:
                egress = resolve_egress(proxy, timeout=timeout, opener=opener)
            except CoherenceError:
                egress = {}
            egress_ip = egress.get("egress_ip")
            detected_timezone = egress.get("timezone")
            detected_country = egress.get("country")
            suggested_locale = egress.get("locale")
            provider = egress.get("provider")
        else:
            egress_ip = None
            detected_timezone = None
            detected_country = None
            suggested_locale = None
    else:
        egress_ip = None
        detected_timezone = None
        detected_country = None
        suggested_locale = None

    return score_report(
        proxy=proxy,
        egress_ip=egress_ip,
        detected_timezone=detected_timezone,
        detected_country=detected_country,
        configured_timezone=configured_timezone,
        configured_locale=configured_locale,
        suggested_locale=suggested_locale,
        provider=provider,
    )
