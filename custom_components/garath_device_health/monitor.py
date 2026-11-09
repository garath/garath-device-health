"""Reconcile observations with Repairs, persistent exclusions and snoozes."""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from .adapters import SourceUnavailable, collect_zha, collect_zwave
from .const import CONF_EXCLUDED_DEVICES, CONF_SNOOZED_UNTIL, DOMAIN, STARTUP_GRACE
from .models import DiagnosticState, Observation, issue_id
from .policy import evaluate

_LOGGER = logging.getLogger(__name__)
COLLECTORS: dict[str, Callable[[HomeAssistant], list[Observation]]] = {
    "zha": collect_zha,
    "zwave_js": collect_zwave,
}


class DeviceHealthMonitor:
    """Keep repair state in sync without generating radio traffic."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.started_at = dt_util.utcnow()
        self._errors: dict[str, str] = {}

    @property
    def excluded(self) -> set[str]:
        """Return exclusions persisted in the config entry's options."""
        return set(self.entry.options.get(CONF_EXCLUDED_DEVICES, []))

    @property
    def snoozed_until(self) -> dict[str, float]:
        """Return persisted UTC expiry timestamps for no-contact warnings."""
        return dict(self.entry.options.get(CONF_SNOOZED_UNTIL, {}))

    @callback
    def async_snooze(self, device_id: str, duration: timedelta) -> None:
        """Suppress only the stale repair without changing last-contact time."""
        if device_id in self.excluded:
            raise HomeAssistantError("This device is excluded from monitoring")
        self.hass.config_entries.async_update_entry(
            self.entry,
            options={
                **self.entry.options,
                CONF_SNOOZED_UNTIL: {
                    **self.snoozed_until,
                    device_id: (dt_util.utcnow() + duration).timestamp(),
                },
            },
        )
        ir.async_delete_issue(self.hass, DOMAIN, issue_id("stale", device_id))

    @callback
    def async_exclude(self, device_id: str) -> None:
        """Persist a device-level opt-out before removing its repair issues."""
        excluded = self.excluded | {device_id}
        options = {
            **self.entry.options,
            CONF_EXCLUDED_DEVICES: sorted(excluded),
        }
        if CONF_SNOOZED_UNTIL in options:
            options[CONF_SNOOZED_UNTIL] = {
                key: until
                for key, until in self.snoozed_until.items()
                if key != device_id
            }
        self.hass.config_entries.async_update_entry(
            self.entry,
            options=options,
        )
        for issue in list(ir.async_get(self.hass).issues.values()):
            if (
                issue.domain == DOMAIN
                and issue.data
                and issue.data.get("device_id") == device_id
            ):
                ir.async_delete_issue(self.hass, DOMAIN, issue.issue_id)

    @callback
    def async_issue_changed(
        self, event: Event[ir.EventIssueRegistryUpdatedData]
    ) -> None:
        """Ignoring any device repair opts that device out of both checks."""
        if event.data["domain"] != DOMAIN or event.data["action"] != "update":
            return
        issue = ir.async_get(self.hass).async_get_issue(DOMAIN, event.data["issue_id"])
        if issue and issue.dismissed_version is not None and issue.data:
            device_id = issue.data.get("device_id")
            if isinstance(device_id, str):
                self.async_exclude(device_id)

    @callback
    def async_scan(self, now: datetime | None = None) -> None:
        """Reconcile every five minutes and after relevant registry changes."""
        now = now or dt_util.utcnow()
        registry = ir.async_get(self.hass)
        # Preserve ignores even if Home Assistant stopped before our listener ran.
        for issue in list(registry.issues.values()):
            if (
                issue.domain == DOMAIN
                and issue.dismissed_version is not None
                and issue.data
                and isinstance(device_id := issue.data.get("device_id"), str)
            ):
                self.async_exclude(device_id)
        snoozed_until = self.snoozed_until
        active_snoozes = {
            device_id: until
            for device_id, until in snoozed_until.items()
            if until > now.timestamp() and device_id not in self.excluded
        }
        if active_snoozes != snoozed_until:
            self.hass.config_entries.async_update_entry(
                self.entry,
                options={
                    **self.entry.options,
                    CONF_SNOOZED_UNTIL: active_snoozes,
                },
            )
        if now - self.started_at < STARTUP_GRACE:
            return

        wanted: set[str] = set()
        failed: set[str] = set()
        for protocol, collector in COLLECTORS.items():
            if not self.hass.config_entries.async_entries(protocol):
                self._errors.pop(protocol, None)
                continue
            try:
                observations = collector(self.hass)
            except (SourceUnavailable, AttributeError, TypeError, ValueError) as err:
                message = str(err) or type(err).__name__
                if self._errors.get(protocol) != message:
                    _LOGGER.error(
                        "Cannot monitor %s: %s", protocol, message, exc_info=True
                    )
                self._errors[protocol] = message
                failed.add(protocol)
                source_issue_id = f"source_unavailable_{protocol}"
                wanted.add(source_issue_id)
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    source_issue_id,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="source_unavailable",
                    translation_placeholders={"protocol": protocol, "details": message},
                    data={"protocol": protocol},
                )
                continue
            self._errors.pop(protocol, None)
            for finding in evaluate(observations, self.excluded, now):
                if finding.kind == "stale" and finding.device_id in active_snoozes:
                    continue
                wanted.add(finding.issue_id)
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    finding.issue_id,
                    is_fixable=finding.kind in ("diagnostics", "stale"),
                    is_persistent=True,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=finding.kind,
                    translation_placeholders={
                        "device": finding.device_name,
                        "details": finding.details,
                    },
                    data={
                        "device_id": finding.device_id,
                        "protocol": protocol,
                        "entry_id": self.entry.entry_id,
                    },
                )

        for issue in list(registry.issues.values()):
            if issue.domain != DOMAIN or issue.issue_id in wanted:
                continue
            data = issue.data or {}
            if data.get("device_id") in self.excluded or (
                issue.translation_key == "stale"
                and data.get("device_id") in active_snoozes
            ):
                ir.async_delete_issue(self.hass, DOMAIN, issue.issue_id)
            elif data.get("protocol") not in failed:
                ir.async_delete_issue(self.hass, DOMAIN, issue.issue_id)

    @callback
    def async_enable_diagnostics(self, device_id: str, protocol: str) -> None:
        """Revalidate ownership and permissions before enabling diagnostic entities."""
        if device_id in self.excluded:
            raise HomeAssistantError("This device is excluded from monitoring")
        try:
            observation = next(
                (
                    item
                    for item in COLLECTORS[protocol](self.hass)
                    if item.device_id == device_id
                ),
                None,
            )
        except (
            SourceUnavailable,
            AttributeError,
            TypeError,
            ValueError,
            KeyError,
        ) as err:
            raise HomeAssistantError(f"Cannot read current diagnostics: {err}") from err
        if observation is None:
            raise HomeAssistantError("The device is no longer available")

        registry = er.async_get(self.hass)
        to_enable: list[str] = []
        for diagnostic in observation.diagnostics:
            if diagnostic.state != DiagnosticState.DISABLED:
                continue
            assert diagnostic.entity_id is not None
            entity = registry.async_get(diagnostic.entity_id)
            if (
                entity is None
                or entity.device_id != device_id
                or entity.platform != protocol
                or entity.disabled_by
                not in (
                    er.RegistryEntryDisabler.USER,
                    er.RegistryEntryDisabler.INTEGRATION,
                )
            ):
                raise HomeAssistantError("Diagnostic configuration changed; retry")
            to_enable.append(entity.entity_id)
        for entity_id in to_enable:
            registry.async_update_entity(entity_id, disabled_by=None)
        if not to_enable and any(
            item.state in (DiagnosticState.MISSING, DiagnosticState.BLOCKED)
            for item in observation.diagnostics
        ):
            raise HomeAssistantError(
                "The diagnostics are missing or blocked; check the owning integration"
            )

    @callback
    def async_clear_issues(self) -> None:
        """Remove issues on unload, retaining exclusions and snoozes."""
        for domain, repair_issue_id in list(ir.async_get(self.hass).issues):
            if domain == DOMAIN:
                ir.async_delete_issue(self.hass, DOMAIN, repair_issue_id)
