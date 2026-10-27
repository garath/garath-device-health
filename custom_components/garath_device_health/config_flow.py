"""Configuration and persistent exclusions for Garath Device Health."""

from typing import Any

import probatio as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import selector

from .const import CONF_EXCLUDED_DEVICES, DOMAIN, NAME


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
    """Choose device-level exclusions, including opt-outs created by ignores."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        excluded = self.config_entry.options.get(CONF_EXCLUDED_DEVICES, [])
        devices = {
            device.id: device.name_by_user or device.name or device.id
            for entry in self.hass.config_entries.async_entries()
            if entry.domain in ("zha", "zwave_js")
            for device in dr.async_entries_for_config_entry(
                dr.async_get(self.hass), entry.entry_id
            )
        }
        for device_id in excluded:
            devices.setdefault(device_id, f"Removed device ({device_id})")
        options = [
            selector.SelectOptionDict(label=f"{name} ({device_id})", value=device_id)
            for device_id, name in sorted(devices.items(), key=lambda item: item[1])
        ]
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
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
            ),
        )
