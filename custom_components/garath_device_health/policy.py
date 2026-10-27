"""Pure evaluation of diagnostic and last-seen repairs."""

from datetime import datetime
from math import isfinite

from .const import STALE_AFTER
from .models import DiagnosticState, Finding, Observation, issue_id


def evaluate(
    devices: list[Observation], excluded: set[str], now: datetime
) -> list[Finding]:
    """Evaluate the two independent checks without changing devices."""
    findings: list[Finding] = []
    for device in devices:
        if device.device_id in excluded:
            continue
        disabled = [
            item.label
            for item in device.diagnostics
            if item.state == DiagnosticState.DISABLED
        ]
        if disabled:
            findings.append(
                Finding(
                    issue_id("diagnostics", device.device_id),
                    "diagnostics",
                    device.device_id,
                    device.name,
                    ", ".join(disabled),
                )
            )
        unsupported = [
            item.label
            for item in device.diagnostics
            if item.state in (DiagnosticState.MISSING, DiagnosticState.BLOCKED)
        ]
        if unsupported:
            findings.append(
                Finding(
                    issue_id("diagnostics_unavailable", device.device_id),
                    "diagnostics_unavailable",
                    device.device_id,
                    device.name,
                    ", ".join(unsupported),
                )
            )
        if device.last_seen is None:
            findings.append(
                Finding(
                    issue_id("last_seen_unknown", device.device_id),
                    "last_seen_unknown",
                    device.device_id,
                    device.name,
                    "No last-seen timestamp has been received.",
                )
            )
        elif now - device.last_seen >= STALE_AFTER:
            findings.append(
                Finding(
                    issue_id("stale", device.device_id),
                    "stale",
                    device.device_id,
                    device.name,
                    device.last_seen.isoformat(),
                )
            )
    return findings


def timestamp(value: datetime | float | int | None) -> datetime | None:
    """Normalize native timestamps to aware UTC, preserving unknown values."""
    from datetime import UTC

    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("Last-seen datetime must have a timezone")
        return value.astimezone(UTC)
    if not isfinite(value):
        raise ValueError("Last-seen timestamp must be finite")
    if value <= 0:
        return None
    return datetime.fromtimestamp(value, UTC)
