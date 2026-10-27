"""Verify discovery against real entity/device registries and native statistics."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from zwave_js_server.model.node.statistics import NodeStatistics

from custom_components.garath_device_health.adapters import (
    SourceUnavailable,
    collect_zha,
    collect_zwave,
)
from custom_components.garath_device_health.models import DiagnosticState

from .conftest import add_entry


async def test_zwave_uses_native_statistics_and_exact_unique_ids(hass):
    source = add_entry(hass, "zwave_js")
    now = dt_util.utcnow() - timedelta(hours=25)
    stats = NodeStatistics(
        Mock(),
        {
            "commandsTX": 0,
            "commandsRX": 0,
            "commandsDroppedRX": 0,
            "commandsDroppedTX": 0,
            "timeoutResponse": 0,
            "lastSeen": now.isoformat(),
        },
    )
    node = SimpleNamespace(node_id=7, statistics=stats)
    controller = SimpleNamespace(
        home_id=1234,
        own_node_id=1,
        nodes={1: SimpleNamespace(node_id=1), 7: node},
    )
    source.runtime_data = SimpleNamespace(
        client=SimpleNamespace(
            connected=True, driver=SimpleNamespace(controller=controller)
        )
    )
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=source.entry_id,
        identifiers={("zwave_js", "1234-7")},
        manufacturer="Not Zooz",
        name="Device",
    )
    registry = er.async_get(hass)
    for suffix in ("statistics_last_seen", "statistics_rssi", "node_status"):
        entity = registry.async_get_or_create(
            "sensor",
            "zwave_js",
            f"1234.7.{suffix}",
            config_entry=source,
            device_id=device.id,
            disabled_by=er.RegistryEntryDisabler.INTEGRATION,
        )
        registry.async_update_entity(
            entity.entity_id,
            new_entity_id=f"sensor.user_renamed_{suffix}",
            name="Unrelated custom display name",
        )
    # A manufacturer signal sensor must not replace the node-statistics sensor.
    registry.async_get_or_create(
        "sensor",
        "zwave_js",
        "other-signal-value",
        config_entry=source,
        device_id=device.id,
        translation_key="signal_strength",
    )
    observed = collect_zwave(hass)
    assert len(observed) == 1
    assert observed[0].device_id == device.id
    assert observed[0].last_seen == now
    assert len(observed[0].diagnostics) == 3
    assert all(
        item.state == DiagnosticState.DISABLED for item in observed[0].diagnostics
    )


async def test_zha_native_timestamp_and_renamed_diagnostics(hass):
    source = add_entry(hass, "zha")
    now = dt_util.utcnow()
    ieee = "00:11:22:33:44:55:66:77"
    node = SimpleNamespace(
        ieee=ieee,
        is_active_coordinator=False,
        last_seen=now.timestamp(),
    )
    hass.data["zha"] = SimpleNamespace(
        gateway_proxy=SimpleNamespace(
            config_entry=source,
            device_proxies={
                ieee: SimpleNamespace(device=node),
                "coordinator": SimpleNamespace(
                    device=SimpleNamespace(is_active_coordinator=True)
                ),
            },
        )
    )
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=source.entry_id,
        identifiers={("zha", ieee)},
        name="Device",
    )
    registry = er.async_get(hass)
    for key in ("rssi", "lqi"):
        registry.async_get_or_create(
            "sensor",
            "zha",
            f"{ieee}-1-0-{key}",
            config_entry=source,
            device_id=device.id,
            disabled_by=er.RegistryEntryDisabler.USER,
            translation_key=key,
            original_name="A translated display name",
        )
    observed = collect_zha(hass)
    assert len(observed) == 1
    assert observed[0].last_seen == now
    assert {item.label for item in observed[0].diagnostics} == {"RSSI", "LQI"}
    assert all(
        item.state == DiagnosticState.DISABLED for item in observed[0].diagnostics
    )
    dr.async_get(hass).async_update_device(
        device.id, disabled_by=dr.DeviceEntryDisabler.USER
    )
    assert collect_zha(hass) == []


async def test_missing_zha_diagnostics_do_not_hide_last_seen(hass):
    source = add_entry(hass, "zha")
    ieee = "00:11:22:33:44:55:66:77"
    dr.async_get(hass).async_get_or_create(
        config_entry_id=source.entry_id,
        identifiers={("zha", ieee)},
    )
    hass.data["zha"] = SimpleNamespace(
        gateway_proxy=SimpleNamespace(
            config_entry=source,
            device_proxies={
                ieee: SimpleNamespace(
                    device=SimpleNamespace(
                        ieee=ieee,
                        is_active_coordinator=False,
                        last_seen=None,
                    )
                )
            },
        )
    )
    observed = collect_zha(hass)[0]
    assert observed.last_seen is None
    assert all(item.state == DiagnosticState.MISSING for item in observed.diagnostics)


async def test_unloaded_sources_and_absent_runtime_are_reported(hass):
    source = add_entry(hass, "zha")
    with pytest.raises(SourceUnavailable, match="runtime"):
        collect_zha(hass)
    source._async_set_state(hass, ConfigEntryState.NOT_LOADED, None)
    with pytest.raises(SourceUnavailable, match="not loaded"):
        collect_zha(hass)
    zwave = add_entry(hass, "zwave_js")
    zwave.runtime_data = SimpleNamespace(
        client=SimpleNamespace(connected=False, driver=None)
    )
    with pytest.raises(SourceUnavailable, match="disconnected"):
        collect_zwave(hass)
