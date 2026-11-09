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
from .const import (
    CONF_EXCLUDED_DEVICES,
    CONF_SNOOZED_UNTIL,
    DOMAIN,
    STARTUP_GRACE,
    Feature,
)
from .models import DiagnosticState, Observation, issue_id
from .policy import evaluate_communication, evaluate_configuration

_LOGGER = logging.getLogger(__name__)
COLLECTORS: dict[str, Callable[[HomeAssistant, Feature], list[Observation]]] = {
    "zha": collect_zha,
    "zwave_js": collect_zwave,
}


class DeviceHealthMonitor:
    """Keep repair state in sync without generating radio traffic."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.started_at = dt_util.utcnow()
        self._errors: dict[tuple[Feature, str, str | None], str] = {}

    def enabled(self, feature: Feature) -> bool:
        """Return whether this feature is enabled independently."""
        return self.entry.options.get(feature.enabled_key, True)

    def excluded(self, feature: Feature) -> set[str]:
        """Use legacy exclusions for both features until migration completes."""
        return set(
            self.entry.options.get(
                feature.excluded_key,
                self.entry.options.get(CONF_EXCLUDED_DEVICES, []),
            )
        )

    @staticmethod
    def issue_feature(issue: ir.IssueEntry) -> Feature | None:
        """Resolve legacy device repairs as well as explicitly tagged repairs."""
        feature = (issue.data or {}).get("feature")
        if feature in Feature:
            return Feature(feature)
        if issue.translation_key in ("diagnostics", "diagnostics_unavailable"):
            return Feature.CONFIGURATION
        if issue.translation_key in ("stale", "last_seen_unknown"):
            return Feature.COMMUNICATION
        return None

    @property
    def snoozed_until(self) -> dict[str, float]:
        """Return persisted UTC expiry timestamps for no-contact warnings."""
        return dict(self.entry.options.get(CONF_SNOOZED_UNTIL, {}))

    @callback
    def async_snooze(self, device_id: str, duration: timedelta) -> None:
        """Suppress only the stale repair without changing last-contact time."""
        if not self.enabled(Feature.COMMUNICATION):
            raise HomeAssistantError("Communication monitoring is disabled")
        if device_id in self.excluded(Feature.COMMUNICATION):
            raise HomeAssistantError(
                "This device is excluded from communication monitoring"
            )
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
    def async_exclude(self, device_id: str, feature: Feature) -> None:
        """Persist an opt-out for only the feature whose repair was ignored."""
        excluded = self.excluded(feature) | {device_id}
        options = {
            **self.entry.options,
            feature.excluded_key: sorted(excluded),
        }
        if feature == Feature.COMMUNICATION and CONF_SNOOZED_UNTIL in options:
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
                and self.issue_feature(issue) == feature
            ):
                ir.async_delete_issue(self.hass, DOMAIN, issue.issue_id)

    @callback
    def async_issue_changed(
        self, event: Event[ir.EventIssueRegistryUpdatedData]
    ) -> None:
        """Ignoring a device repair opts out only its owning feature."""
        if event.data["domain"] != DOMAIN or event.data["action"] != "update":
            return
        issue = ir.async_get(self.hass).async_get_issue(DOMAIN, event.data["issue_id"])
        if issue and issue.dismissed_version is not None:
            self._async_apply_ignore(issue)

    @callback
    def _async_apply_ignore(self, issue: ir.IssueEntry) -> None:
        device_id = (issue.data or {}).get("device_id")
        if not isinstance(device_id, str):
            return
        # Old ignores meant "exclude from both"; retain that intent on upgrade.
        if "feature" not in (issue.data or {}):
            for feature in Feature:
                self.async_exclude(device_id, feature)
        elif (feature := self.issue_feature(issue)) is not None:
            self.async_exclude(device_id, feature)

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
            ):
                self._async_apply_ignore(issue)
        snoozed_until = self.snoozed_until
        active_snoozes = {
            device_id: until
            for device_id, until in snoozed_until.items()
            if until > now.timestamp()
            and device_id not in self.excluded(Feature.COMMUNICATION)
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

        for feature in Feature:
            self._async_scan_feature(feature, now, active_snoozes)

    @callback
    def _async_scan_feature(
        self, feature: Feature, now: datetime, active_snoozes: dict[str, float]
    ) -> None:
        registry = ir.async_get(self.hass)
        wanted: set[str] = set()
        failed: set[tuple[Feature, str, str | None]] = set()
        excluded = self.excluded(feature)
        seen_errors: set[tuple[Feature, str, str | None]] = set()
        for protocol, collector in COLLECTORS.items():
            if not self.enabled(feature) or not self.hass.config_entries.async_entries(
                protocol
            ):
                continue
            try:
                observations = collector(self.hass, feature)
            except (SourceUnavailable, AttributeError, TypeError, ValueError) as err:
                message = str(err) or type(err).__name__
                error_key = (feature, protocol, None)
                failed.add(error_key)
                seen_errors.add(error_key)
                self._log_error(error_key, message)
                source_issue_id = f"{feature}_source_unavailable_{protocol}"
                wanted.add(source_issue_id)
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    source_issue_id,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=f"{feature}_source_unavailable",
                    translation_placeholders={"protocol": protocol, "details": message},
                    data={"protocol": protocol, "feature": feature},
                )
                continue
            for observation in observations:
                if observation.error is None or observation.device_id in excluded:
                    continue
                error_key = (feature, protocol, observation.device_id)
                failed.add(error_key)
                seen_errors.add(error_key)
                self._log_error(error_key, observation.error)
                error_issue_id = f"{feature}_unavailable_{observation.device_id}"
                wanted.add(error_issue_id)
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    error_issue_id,
                    is_fixable=False,
                    is_persistent=True,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=f"{feature}_unavailable",
                    translation_placeholders={
                        "device": observation.name,
                        "details": observation.error,
                    },
                    data={
                        "device_id": observation.device_id,
                        "protocol": protocol,
                        "feature": feature,
                    },
                )
            findings = (
                evaluate_configuration(observations, excluded)
                if feature == Feature.CONFIGURATION
                else evaluate_communication(observations, excluded, now)
            )
            for finding in findings:
                if finding.kind == "stale" and finding.device_id in active_snoozes:
                    continue
                wanted.add(finding.issue_id)
                placeholders = {
                    "device": finding.device_name,
                    "details": finding.details,
                }
                if finding.last_seen is not None:
                    local_contact = dt_util.as_local(finding.last_seen)
                    days, remainder = divmod(
                        int((now - finding.last_seen).total_seconds()), 86400
                    )
                    hours, remainder = divmod(remainder, 3600)
                    placeholders.update(
                        last_observed=local_contact.strftime("%x %X %Z"),
                        days=str(days),
                        hours=str(hours),
                        minutes=str(remainder // 60),
                    )
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    finding.issue_id,
                    is_fixable=finding.kind in ("diagnostics", "stale"),
                    is_persistent=True,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=finding.kind,
                    translation_placeholders=placeholders,
                    data={
                        "device_id": finding.device_id,
                        "protocol": protocol,
                        "entry_id": self.entry.entry_id,
                        "feature": feature,
                    },
                )

        for issue in list(registry.issues.values()):
            if issue.domain != DOMAIN or issue.issue_id in wanted:
                continue
            owner = self.issue_feature(issue)
            if owner != feature:
                if owner is None and issue.translation_key == "source_unavailable":
                    ir.async_delete_issue(self.hass, DOMAIN, issue.issue_id)
                continue
            data = issue.data or {}
            device_id = data.get("device_id")
            protocol = data.get("protocol")
            if (
                not self.enabled(feature)
                or device_id in excluded
                or (
                    issue.translation_key == "stale"
                    and data.get("device_id") in active_snoozes
                )
            ):
                ir.async_delete_issue(self.hass, DOMAIN, issue.issue_id)
            elif (feature, protocol, None) not in failed and (
                feature,
                protocol,
                device_id,
            ) not in failed:
                ir.async_delete_issue(self.hass, DOMAIN, issue.issue_id)
        for key in list(self._errors):
            if key[0] == feature and key not in seen_errors:
                del self._errors[key]

    def _log_error(self, key: tuple[Feature, str, str | None], message: str) -> None:
        """Log each distinct failure without repeating it on every scan."""
        if self._errors.get(key) != message:
            _LOGGER.error("Cannot check %s for %s (%s): %s", *key, message)
        self._errors[key] = message

    @callback
    def async_enable_diagnostics(self, device_id: str, protocol: str) -> None:
        """Revalidate ownership and permissions before enabling diagnostic entities."""
        if not self.enabled(Feature.CONFIGURATION):
            raise HomeAssistantError("Diagnostic configuration checks are disabled")
        if device_id in self.excluded(Feature.CONFIGURATION):
            raise HomeAssistantError(
                "This device is excluded from configuration checks"
            )
        try:
            observation = next(
                (
                    item
                    for item in COLLECTORS[protocol](self.hass, Feature.CONFIGURATION)
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
        if observation.error is not None:
            raise HomeAssistantError(
                f"Cannot read current diagnostics: {observation.error}"
            )

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
