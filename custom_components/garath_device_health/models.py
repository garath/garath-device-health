"""Protocol-neutral observations and repair decisions."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class DiagnosticState(StrEnum):
    """Whether an expected diagnostic entity can be enabled."""

    ENABLED = "enabled"
    DISABLED = "disabled"
    MISSING = "missing"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class Diagnostic:
    """One diagnostic entity, identified without its display name."""

    label: str
    entity_id: str | None
    state: DiagnosticState


@dataclass(frozen=True)
class Observation:
    """A device observed through its owning integration."""

    device_id: str
    name: str
    protocol: str
    last_seen: datetime | None
    diagnostics: tuple[Diagnostic, ...]
    error: str | None = None


@dataclass(frozen=True)
class Finding:
    """A repair to reconcile with the issue registry."""

    issue_id: str
    kind: str
    device_id: str
    device_name: str
    details: str


def issue_id(kind: str, device_id: str) -> str:
    """Use registry identity, not a mutable device or entity name."""
    return f"{kind}_{device_id}"
