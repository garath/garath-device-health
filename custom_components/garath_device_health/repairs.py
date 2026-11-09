"""Explicit, confirmation-only diagnostic and no-contact repair actions."""

from datetime import timedelta
from typing import Any

import probatio as vol
from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import selector

from .const import DOMAIN, Feature
from .monitor import DeviceHealthMonitor

_SNOOZE_DAYS = {"snooze_day": 1, "snooze_week": 7, "snooze_30_days": 30}


class FeatureRepairFlow(RepairsFlow):
    """Shared confirmation and validation for a feature-specific repair."""

    feature: Feature
    actions: list[str]

    def __init__(self, issue_id: str) -> None:
        self.issue_id = issue_id

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        # The flow manager supplies issue metadata here; this is not consent.
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        issue = ir.async_get(self.hass).async_get_issue(DOMAIN, self.issue_id)
        monitor: DeviceHealthMonitor | None = self.hass.data.get(DOMAIN)
        if issue is None or issue.data is None:
            return self.async_abort(reason="issue_gone")
        if monitor is None:
            return self.async_abort(reason="not_loaded")
        device_id = issue.data.get("device_id")
        protocol = issue.data.get("protocol")
        if not isinstance(device_id, str) or not isinstance(protocol, str):
            return self.async_abort(reason="invalid_issue")
        if monitor.issue_feature(issue) != self.feature:
            return self.async_abort(reason="invalid_issue")
        if not monitor.enabled(self.feature):
            return self.async_abort(reason="feature_disabled")
        if device_id in monitor.excluded(self.feature):
            return self.async_abort(reason="excluded")
        errors: dict[str, str] = {}
        error_details = ""
        if user_input is not None:
            action = user_input.get("action")
            if action not in self.actions:
                errors["base"] = "invalid_action"
            elif action == "exclude":
                monitor.async_exclude(device_id, self.feature)
                return self.async_create_entry(data={})
            else:
                try:
                    self._perform_action(monitor, device_id, protocol, action)
                except HomeAssistantError as err:
                    errors["base"] = "action_failed"
                    error_details = str(err)
                else:
                    return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "action", default=self.actions[0]
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=self.actions,
                            translation_key=f"{self.feature}_action",
                        )
                    )
                }
            ),
            errors=errors,
            description_placeholders={
                **(issue.translation_placeholders or {}),
                "error_details": error_details,
            },
        )

    def _perform_action(
        self, monitor: DeviceHealthMonitor, device_id: str, protocol: str, action: str
    ) -> None:
        raise NotImplementedError


class ConfigurationRepairFlow(FeatureRepairFlow):
    """Enable diagnostic entities without changing communication monitoring."""

    feature = Feature.CONFIGURATION
    actions = ["enable", "exclude"]

    def _perform_action(
        self, monitor: DeviceHealthMonitor, device_id: str, protocol: str, action: str
    ) -> None:
        monitor.async_enable_diagnostics(device_id, protocol)


class CommunicationRepairFlow(FeatureRepairFlow):
    """Snooze no-contact warnings without changing diagnostic configuration."""

    feature = Feature.COMMUNICATION
    actions = [*_SNOOZE_DAYS, "exclude"]

    def _perform_action(
        self, monitor: DeviceHealthMonitor, device_id: str, protocol: str, action: str
    ) -> None:
        monitor.async_snooze(device_id, timedelta(days=_SNOOZE_DAYS[action]))


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the confirmation flow for this device repair."""
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    if (
        issue is not None
        and DeviceHealthMonitor.issue_feature(issue) == Feature.COMMUNICATION
    ):
        return CommunicationRepairFlow(issue_id)
    return ConfigurationRepairFlow(issue_id)
