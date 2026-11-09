"""Use real Home Assistant state and registries; simulate only radio backends."""

from collections.abc import AsyncIterator
from types import MappingProxyType, SimpleNamespace

import pytest
from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

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


@pytest.fixture
def zha_network(hass):
    """Two devices with independent native timestamps and diagnostic metadata."""
    source = add_entry(hass, "zha")
    nodes = {}
    devices = {}
    for index in (1, 2):
        ieee = f"00:11:22:33:44:55:66:{index:02}"
        nodes[index] = SimpleNamespace(
            ieee=ieee,
            is_active_coordinator=False,
            last_seen=dt_util.utcnow().timestamp(),
        )
        devices[index] = dr.async_get(hass).async_get_or_create(
            config_entry_id=source.entry_id,
            identifiers={("zha", ieee)},
            name=f"Device {index}",
        )
        for key in ("rssi", "lqi"):
            er.async_get(hass).async_get_or_create(
                "sensor",
                "zha",
                f"{ieee}-{key}",
                config_entry=source,
                device_id=devices[index].id,
                disabled_by=er.RegistryEntryDisabler.USER,
                translation_key=key,
            )
    hass.data["zha"] = SimpleNamespace(
        gateway_proxy=SimpleNamespace(
            config_entry=source,
            device_proxies={
                node.ieee: SimpleNamespace(device=node) for node in nodes.values()
            },
        )
    )
    return source, nodes, devices
