"""Garath's device-health repairs for Z-Wave JS and ZHA."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers import (
    issue_registry as ir,
)
from homeassistant.helpers.event import async_track_time_interval

from .const import CONF_EXCLUDED_DEVICES, DOMAIN, SCAN_INTERVAL, Feature
from .monitor import DeviceHealthMonitor

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Preserve old all-check exclusions when introducing separate features."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        options = dict(entry.options)
        legacy_excluded = options.pop(CONF_EXCLUDED_DEVICES, [])
        for feature in Feature:
            options.setdefault(feature.excluded_key, list(legacy_excluded))
            options.setdefault(feature.enabled_key, True)
        hass.config_entries.async_update_entry(entry, options=options, version=2)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Start the single monitor, with a grace period for integration startup."""
    monitor = DeviceHealthMonitor(hass, entry)
    hass.data[DOMAIN] = monitor
    entry.async_on_unload(
        hass.bus.async_listen(
            ir.EVENT_REPAIRS_ISSUE_REGISTRY_UPDATED, monitor.async_issue_changed
        )
    )
    entry.async_on_unload(
        async_track_time_interval(hass, monitor.async_scan, SCAN_INTERVAL)
    )

    @callback
    def async_entity_changed(event: Event[er.EventEntityRegistryUpdatedData]) -> None:
        monitor.async_scan()

    @callback
    def async_device_changed(event: Event[dr.EventDeviceRegistryUpdatedData]) -> None:
        monitor.async_scan()

    entry.async_on_unload(
        hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, async_entity_changed)
    )
    entry.async_on_unload(
        hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, async_device_changed)
    )
    entry.async_on_unload(entry.add_update_listener(async_options_updated))

    @callback
    def async_started(event: Event) -> None:
        from homeassistant.util import dt as dt_util

        monitor.started_at = dt_util.utcnow()
        monitor.async_scan()

    if hass.is_running:
        monitor.async_scan()
    else:
        entry.async_on_unload(
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, async_started)
        )
    return True


async def async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply feature settings without reloading protocol integrations."""
    if (monitor := hass.data.get(DOMAIN)) is not None:
        monitor.async_scan()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Stop listeners and clear repairs; Home Assistant runs unload callbacks."""
    monitor: DeviceHealthMonitor = hass.data.pop(DOMAIN)
    monitor.async_clear_issues()
    return True
