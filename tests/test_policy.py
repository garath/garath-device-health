"""Boundary tests for protocol-neutral monitoring policy."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.garath_device_health.models import (
    Diagnostic,
    DiagnosticState,
    Observation,
)
from custom_components.garath_device_health.policy import (
    evaluate_communication,
    evaluate_configuration,
    timestamp,
)

NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (timedelta(hours=24) - timedelta(microseconds=1), False),
        (timedelta(hours=24), True),
        (timedelta(hours=25), True),
        (timedelta(seconds=-1), False),
    ],
)
def test_exact_stale_boundary(age, expected):
    observed = Observation("one", "Device", "zha", NOW - age, ())
    assert bool(evaluate_communication([observed], set(), NOW)) == expected


def test_diagnostics_and_stale_are_independent():
    observed = Observation(
        "one",
        "Device",
        "zha",
        NOW - timedelta(days=2),
        (Diagnostic("RSSI", "sensor.renamed", DiagnosticState.DISABLED),),
    )
    assert {item.kind for item in evaluate_configuration([observed], set())} == {
        "diagnostics"
    }
    assert {item.kind for item in evaluate_communication([observed], set(), NOW)} == {
        "stale"
    }
    assert evaluate_configuration([observed], {"one"}) == []
    assert evaluate_communication([observed], {"one"}, NOW) == []


def test_unknown_missing_and_blocked_are_explicit():
    observed = Observation(
        "one",
        "Device",
        "zha",
        None,
        (
            Diagnostic("RSSI", None, DiagnosticState.MISSING),
            Diagnostic("LQI", "sensor.lqi", DiagnosticState.BLOCKED),
        ),
    )
    assert {item.kind for item in evaluate_configuration([observed], set())} == {
        "diagnostics_unavailable"
    }
    assert {item.kind for item in evaluate_communication([observed], set(), NOW)} == {
        "last_seen_unknown"
    }


def test_timestamp_normalization():
    assert timestamp(NOW.timestamp()) == NOW
    assert timestamp(NOW) == NOW
    assert timestamp(None) is None
    assert timestamp(0) is None
    with pytest.raises(ValueError, match="timezone"):
        timestamp(NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="finite"):
        timestamp(float("nan"))
    with pytest.raises(ValueError, match="supported range"):
        timestamp(1e30)
