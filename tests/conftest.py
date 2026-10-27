"""Use real Home Assistant state and registries; simulate only radio backends."""

from collections.abc import AsyncIterator
from types import MappingProxyType

import pytest
from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.garath_device_health.const import DOMAIN


def add_entry(hass: HomeAssistant, domain: str) -> ConfigEntry:
    """Register a real config entry without initializing radio hardware."""
    entry = ConfigEntry(
        domain=domain,
        title=domain,
        data={},
        options={},
        source="user",
        version=1,
        minor_version=1,
        unique_id=domain,
        discovery_keys=MappingProxyType({}),
        subentries_data=[],
        state=ConfigEntryState.LOADED,
    )
    hass.config_entries._entries[entry.entry_id] = entry
    return entry


@pytest.fixture
async def hass(tmp_path) -> AsyncIterator[HomeAssistant]:
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    dr.async_setup(hass)
    await dr.async_load(hass)
    await er.async_load(hass)
    await ir.async_load(hass)
    yield hass
    for entry in hass.config_entries.async_entries():
        await entry._async_process_on_unload(hass)
    await hass.async_stop(force=True)


@pytest.fixture
def entry(hass: HomeAssistant) -> ConfigEntry:
    return add_entry(hass, DOMAIN)
