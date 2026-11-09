"""Configuration, persistent exclusions and no-contact snooze management."""

from typing import Any

import probatio as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import selector
from homeassistant.util import dt as dt_util

from .const import (
    CONF_EXCLUDED_DEVICES,
    CONF_SNOOZED_UNTIL,
    CONF_UNSNOOZE_DEVICES,
    DOMAIN,
    NAME,
)


class GarathDeviceHealthConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set up a single monitor for all configured ZHA and Z-Wave JS networks."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title=NAME, data={})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> GarathDeviceHealthOptionsFlow:
        return GarathDeviceHealthOptionsFlow()


class GarathDeviceHealthOptionsFlow(config_entries.OptionsFlow):
    """Manage permanent exclusions and end no-contact snoozes early."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        now = dt_util.utcnow().timestamp()
        if user_input is not None:
            updated_options = {
                **self.config_entry.options,
                CONF_EXCLUDED_DEVICES: user_input.get(CONF_EXCLUDED_DEVICES, []),
            }
            if CONF_SNOOZED_UNTIL in updated_options:
                removed = set(user_input.get(CONF_UNSNOOZE_DEVICES, [])) | set(
                    updated_options[CONF_EXCLUDED_DEVICES]
                )
                updated_options[CONF_SNOOZED_UNTIL] = {
                    device_id: until
                    for device_id, until in updated_options[CONF_SNOOZED_UNTIL].items()
                    if device_id not in removed and until > now
                }
            return self.async_create_entry(title="", data=updated_options)
        excluded = self.config_entry.options.get(CONF_EXCLUDED_DEVICES, [])
        snoozes = {
            device_id: until
            for device_id, until in self.config_entry.options.get(
                CONF_SNOOZED_UNTIL, {}
            ).items()
            if until > now
        }
        devices = {
            device.id: device.name_by_user or device.name or device.id
            for entry in self.hass.config_entries.async_entries()
            if entry.domain in ("zha", "zwave_js")
            for device in dr.async_entries_for_config_entry(
                dr.async_get(self.hass), entry.entry_id
            )
        }
        for device_id in set(excluded) | snoozes.keys():
            devices.setdefault(device_id, f"Removed device ({device_id})")
        options = [
            selector.SelectOptionDict(label=f"{name} ({device_id})", value=device_id)
            for device_id, name in sorted(devices.items(), key=lambda item: item[1])
        ]
        schema = {
            vol.Optional(
                CONF_EXCLUDED_DEVICES, default=excluded
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options,
                    multiple=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        }
        if snoozes:
            schema[vol.Optional(CONF_UNSNOOZE_DEVICES, default=[])] = (
                selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(
                                label=(
                                    f"{devices[device_id]} ({device_id}): "
                                    f"{dt_util.as_local(dt_util.utc_from_timestamp(until)).isoformat()}"
                                ),
                                value=device_id,
                            )
                            for device_id, until in sorted(snoozes.items())
                        ],
                        multiple=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema),
        )
