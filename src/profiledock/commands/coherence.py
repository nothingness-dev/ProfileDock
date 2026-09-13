from __future__ import annotations

from typing import Any

import typer

from ..cli_contract import EXIT_USER_ERROR
from ..cli_support import emit_json, fail_exception, selected_paths
from ..logger import generate_correlation_id, write_log_entry
from ..profile_manager import AmbiguousProfileError, ProfileManager, ProfileNotFoundError
from ..storage import StorageError


def _get_manager() -> ProfileManager:
    from ..cli import manager

    return manager()


def coherence_command(
    profile_id: str = typer.Argument(..., help="Profile ID, prefix, or name to audit."),
    timeout: float = typer.Option(15.0, "--timeout", help="Seconds to wait for each geolocation provider."),
    fix: bool = typer.Option(
        False,
        "--fix",
        help="Align the preset timezone and locale with the verified egress IP.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Output coherence report in JSON format."),
) -> None:
    """Audit a profile's egress/identity coherence and optionally auto-fix presets.

    Resolves the configured proxy exit IP through geolocation providers and
    cross-checks the detected timezone and country against the profile's
    identity preset. Direct-egress profiles are coherent by construction.
    """
    from ..cli_support import redact_proxy
    from ..coherence import CoherenceError, check_coherence, score_report
    from ..validation import ValidationError

    paths = selected_paths()
    manager = _get_manager()
    corr_id = generate_correlation_id()
    try:
        profile = manager.resolve(profile_id)
        cfg = manager.get_launch_config(profile.id)
        proxy = cfg.proxy
        if proxy:
            report = check_coherence(
                proxy=proxy,
                configured_timezone=cfg.timezone,
                configured_locale=cfg.locale,
                timeout=timeout,
            )
        else:
            report = check_coherence(
                proxy=None,
                configured_timezone=cfg.timezone,
                configured_locale=cfg.locale,
                timeout=timeout,
            )
    except (
        ProfileNotFoundError,
        AmbiguousProfileError,
        StorageError,
        ValidationError,
        CoherenceError,
        ValueError,
    ) as exc:
        write_log_entry(
            log_dir=paths.logs_dir,
            level="ERROR",
            event="coherence_failed",
            correlation_id=corr_id,
            result="failed",
            error_category=getattr(exc, "category", type(exc).__name__),
            details={"error": str(exc), "profile": profile_id},
        )
        fail_exception(exc)

    fix_applied = False
    if fix:
        updates: dict[str, Any] = {}
        if report.detected_timezone and cfg.timezone != report.detected_timezone:
            updates["timezone"] = report.detected_timezone
        if report.suggested_locale and cfg.locale != report.suggested_locale:
            updates["locale"] = report.suggested_locale
        try:
            if updates:
                manager.update_launch_config(profile.id, **updates)
        except (StorageError, ValidationError, ValueError) as exc:
            fail_exception(exc)
        fix_applied = bool(updates)
        report = score_report(
            proxy=proxy,
            egress_ip=report.egress_ip,
            detected_timezone=report.detected_timezone,
            detected_country=report.detected_country,
            suggested_locale=report.suggested_locale,
            provider=report.provider,
            configured_timezone=updates.get("timezone", cfg.timezone),
            configured_locale=updates.get("locale", cfg.locale),
        )

    write_log_entry(
        log_dir=paths.logs_dir,
        level="INFO",
        event="coherence_completed",
        correlation_id=corr_id,
        result="success",
        details={
            "profile": profile.id,
            "score": report.score,
            "coherent": report.is_coherent,
            "fix": fix_applied,
        },
    )

    if json_output:
        emit_json(
            "coherence",
            report.to_dict(
                fix_applied=fix_applied,
                profile=profile.id,
            ),
        )
        if report.is_coherent:
            return
        raise typer.Exit(EXIT_USER_ERROR)

    typer.echo(f"profile: {profile.name} ({profile.id})")
    typer.echo(f"proxy: {redact_proxy(proxy) if proxy else '(direct egress)'}")
    typer.echo(f"egress IP: {report.egress_ip or '(unverified)'}")
    typer.echo(f"egress timezone: {report.detected_timezone or '(unknown)'}")
    typer.echo(f"preset timezone: {report.configured_timezone or '(unset)'}")
    typer.echo(f"preset locale: {report.configured_locale or '(unset)'}")
    typer.echo(f"detected country: {report.detected_country or '(unknown)'}")
    typer.echo(f"score: {report.score}/100 {'COHERENT' if report.is_coherent else 'INCOHERENT'}")
    if fix_applied:
        typer.echo("preset updated to match egress.")
    for deduction in report.deductions:
        typer.echo(f"  - {deduction['id']} (-{deduction['penalty']}): {deduction['reason']}")
    if report.remediation_command and not fix_applied:
        typer.echo(f"suggested fix: {report.remediation_command}")
    if not report.is_coherent:
        raise typer.Exit(EXIT_USER_ERROR)
