"""Explicit, confirmation-only diagnostic and no-contact repair actions."""

from datetime import timedelta
from typing import Any

import probatio as vol
from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import selector

from .const import DOMAIN
from .monitor import DeviceHealthMonitor

_SNOOZE_DAYS = {"snooze_day": 1, "snooze_week": 7, "snooze_30_days": 30}


class DeviceHealthRepairFlow(RepairsFlow):
    """Enable diagnostics, snooze a stale warning, or exclude the device."""

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
        if device_id in monitor.excluded:
            return self.async_abort(reason="excluded")
        if issue.translation_key == "stale":
            actions = [*_SNOOZE_DAYS, "exclude"]
        elif issue.translation_key == "diagnostics":
            actions = ["enable", "exclude"]
        else:
            return self.async_abort(reason="invalid_issue")
        errors: dict[str, str] = {}
        error_details = ""
        if user_input is not None:
            action = user_input.get("action")
            if action not in actions:
                errors["base"] = "invalid_action"
            elif action == "exclude":
                monitor.async_exclude(device_id)
                return self.async_create_entry(data={})
            elif action in _SNOOZE_DAYS:
                monitor.async_snooze(device_id, timedelta(days=_SNOOZE_DAYS[action]))
                return self.async_create_entry(data={})
            elif action == "enable":
                try:
                    monitor.async_enable_diagnostics(device_id, protocol)
                except HomeAssistantError as err:
                    errors["base"] = "enable_failed"
                    error_details = str(err)
                else:
                    return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(
                {
                    vol.Required("action", default=actions[0]): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=actions,
                            translation_key="repair_action",
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


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the confirmation flow for this device repair."""
    return DeviceHealthRepairFlow(issue_id)
