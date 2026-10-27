"""Exercise reconciliation, persistence and confirmation with real HA registries."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.repairs import RepairsFlowManager
from homeassistant.core import Event
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from custom_components.garath_device_health import repairs
from custom_components.garath_device_health.adapters import SourceUnavailable
from custom_components.garath_device_health.const import CONF_EXCLUDED_DEVICES, DOMAIN
from custom_components.garath_device_health.models import (
    Diagnostic,
    DiagnosticState,
    Observation,
)
from custom_components.garath_device_health.monitor import DeviceHealthMonitor

from .conftest import add_entry


def observation(*, last_seen=None, diagnostics=()) -> Observation:
    return Observation(
        "device-one",
        "Device One",
        "zha",
        last_seen or dt_util.utcnow() - timedelta(days=2),
        diagnostics,
    )


def ready_monitor(hass, entry):
    monitor = DeviceHealthMonitor(hass, entry)
    monitor.started_at -= timedelta(minutes=6)
    hass.data[DOMAIN] = monitor
    return monitor


def findings(hass):
    return {
        issue.issue_id: issue
        for issue in ir.async_get(hass).issues.values()
        if issue.domain == DOMAIN
    }


async def test_stale_recovery_and_new_devices(hass, entry):
    add_entry(hass, "zha")
    monitor = ready_monitor(hass, entry)
    with patch.dict(
        "custom_components.garath_device_health.monitor.COLLECTORS",
        {"zha": lambda _: [observation()]},
        clear=True,
    ):
        monitor.async_scan()
        assert "stale_device-one" in findings(hass)
        first = findings(hass)["stale_device-one"].created
        monitor.async_scan()
        assert findings(hass)["stale_device-one"].created == first
    with patch.dict(
        "custom_components.garath_device_health.monitor.COLLECTORS",
        {"zha": lambda _: [observation(last_seen=dt_util.utcnow())]},
        clear=True,
    ):
        monitor.async_scan()
        assert findings(hass) == {}
    with patch.dict(
        "custom_components.garath_device_health.monitor.COLLECTORS",
        {"zha": lambda _: []},
        clear=True,
    ):
        monitor.async_scan()
        assert findings(hass) == {}


async def test_ignore_persists_both_check_opt_out_and_reenroll(hass, entry):
    add_entry(hass, "zha")
    monitor = ready_monitor(hass, entry)
    unsubscribe = hass.bus.async_listen(
        ir.EVENT_REPAIRS_ISSUE_REGISTRY_UPDATED, monitor.async_issue_changed
    )
    disabled = Diagnostic("RSSI", "sensor.rssi", DiagnosticState.DISABLED)
    with patch.dict(
        "custom_components.garath_device_health.monitor.COLLECTORS",
        {"zha": lambda _: [observation(diagnostics=(disabled,))]},
        clear=True,
    ):
        monitor.async_scan()
        assert len(findings(hass)) == 2
        ir.async_get(hass).async_ignore(DOMAIN, "diagnostics_device-one", True)
        await hass.async_block_till_done()
        assert entry.options[CONF_EXCLUDED_DEVICES] == ["device-one"]
        assert findings(hass) == {}
        restarted = ready_monitor(hass, entry)
        restarted.async_scan()
        assert findings(hass) == {}
        hass.config_entries.async_update_entry(
            entry, options={CONF_EXCLUDED_DEVICES: []}
        )
        restarted.async_scan()
        assert len(findings(hass)) == 2
    unsubscribe()


async def test_restart_recovers_ignore_before_first_scan(hass, entry):
    monitor = DeviceHealthMonitor(hass, entry)
    ir.async_create_issue(
        hass,
        DOMAIN,
        "stale_device-one",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="stale",
        data={"device_id": "device-one", "protocol": "zha"},
    )
    ir.async_get(hass).async_ignore(DOMAIN, "stale_device-one", True)
    monitor.async_scan()
    assert monitor.excluded == {"device-one"}
    assert findings(hass) == {}


async def test_startup_grace(hass, entry):
    add_entry(hass, "zha")
    monitor = DeviceHealthMonitor(hass, entry)
    with patch.dict(
        "custom_components.garath_device_health.monitor.COLLECTORS",
        {"zha": lambda _: [observation()]},
        clear=True,
    ):
        monitor.async_scan()
        assert findings(hass) == {}


async def test_source_error_does_not_claim_recovery(hass, entry, caplog):
    add_entry(hass, "zha")
    monitor = ready_monitor(hass, entry)
    with patch.dict(
        "custom_components.garath_device_health.monitor.COLLECTORS",
        {"zha": lambda _: [observation()]},
        clear=True,
    ):
        monitor.async_scan()

    def offline(_):
        raise SourceUnavailable("Disconnected")

    with patch.dict(
        "custom_components.garath_device_health.monitor.COLLECTORS",
        {"zha": offline},
        clear=True,
    ):
        monitor.async_scan()
        assert set(findings(hass)) == {"stale_device-one", "source_unavailable_zha"}
        assert "Disconnected" in caplog.text
    with patch.dict(
        "custom_components.garath_device_health.monitor.COLLECTORS",
        {"zha": lambda _: [observation(last_seen=dt_util.utcnow())]},
        clear=True,
    ):
        monitor.async_scan()
        assert findings(hass) == {}


async def test_real_flow_manager_requires_confirmation(hass, entry):
    source = add_entry(hass, "zha")
    registry = er.async_get(hass)
    entity = registry.async_get_or_create(
        "sensor",
        "zha",
        "ieee-rssi",
        config_entry=source,
        disabled_by=er.RegistryEntryDisabler.USER,
        translation_key="rssi",
    )
    # A real device registry entry is needed for ownership validation.
    from homeassistant.helpers import device_registry as dr

    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=source.entry_id,
        identifiers={("zha", "00:11:22:33:44:55:66:77")},
        name="Device",
    )
    entity = registry.async_update_entity(entity.entity_id, device_id=device.id)
    monitor = ready_monitor(hass, entry)

    def observed(_):
        current = registry.async_get(entity.entity_id)
        state = (
            DiagnosticState.DISABLED if current.disabled_by else DiagnosticState.ENABLED
        )
        return [
            Observation(
                device.id,
                "Device",
                "zha",
                dt_util.utcnow(),
                (Diagnostic("RSSI", entity.entity_id, state),),
            )
        ]

    manager = RepairsFlowManager(hass)
    hass.data["repairs"] = {
        "flow_manager": manager,
        "platforms": AsyncMock(async_get_platform=AsyncMock(return_value=repairs)),
    }
    with patch.dict(
        "custom_components.garath_device_health.monitor.COLLECTORS",
        {"zha": observed},
        clear=True,
    ):
        monitor.async_scan()
        issue_id = f"diagnostics_{device.id}"
        result = await manager.async_init(DOMAIN, context={"issue_id": issue_id})
        assert result["type"] == "form"
        assert registry.async_get(entity.entity_id).disabled_by is not None
        assert monitor.excluded == set()
        # Aborting/closing the dialog must not opt out or change the entity.
        manager.async_abort(result["flow_id"])
        assert monitor.excluded == set()
        assert issue_id in findings(hass)
        result = await manager.async_init(DOMAIN, context={"issue_id": issue_id})
        result = await manager.async_configure(
            result["flow_id"], user_input={"action": "enable"}
        )
        assert result["type"] == "create_entry"
        assert registry.async_get(entity.entity_id).disabled_by is None
        monitor.async_scan()
        assert issue_id not in findings(hass)


async def test_fix_cannot_reenable_excluded_device(hass, entry):
    monitor = ready_monitor(hass, entry)
    monitor.async_exclude("device-one")
    from homeassistant.exceptions import HomeAssistantError

    with pytest.raises(HomeAssistantError, match="excluded"):
        monitor.async_enable_diagnostics("device-one", "zha")


async def test_ignore_other_integration_does_not_exclude(hass, entry):
    monitor = ready_monitor(hass, entry)
    monitor.async_issue_changed(
        Event(
            ir.EVENT_REPAIRS_ISSUE_REGISTRY_UPDATED,
            {"domain": "other", "action": "update", "issue_id": "anything"},
        )
    )
    assert monitor.excluded == set()


async def test_ignore_survives_real_issue_registry_reload(hass, entry):
    """Keep enough metadata on disk to recover an ignore across restart."""
    add_entry(hass, "zha")
    monitor = ready_monitor(hass, entry)
    with patch.dict(
        "custom_components.garath_device_health.monitor.COLLECTORS",
        {"zha": lambda _: [observation()]},
        clear=True,
    ):
        monitor.async_scan()
    registry = ir.async_get(hass)
    registry.async_ignore(DOMAIN, "stale_device-one", True)
    await registry._store.async_save(registry._data_to_save())
    del hass.data[ir.DATA_REGISTRY]
    await ir.async_load(hass)
    recovered = ir.async_get(hass).async_get_issue(DOMAIN, "stale_device-one")
    assert recovered.data["device_id"] == "device-one"
    restarted = DeviceHealthMonitor(hass, entry)
    restarted.async_scan()
    assert restarted.excluded == {"device-one"}
    assert findings(hass) == {}
    await hass.config_entries._store.async_save(hass.config_entries._data_to_save())
    saved = await hass.config_entries._store.async_load()
    saved_entry = next(
        item for item in saved["entries"] if item["entry_id"] == entry.entry_id
    )
    assert saved_entry["options"][CONF_EXCLUDED_DEVICES] == ["device-one"]


async def test_setup_unload_and_options_listener(hass, entry):
    from custom_components.garath_device_health import (
        async_options_updated,
        async_setup_entry,
        async_unload_entry,
    )

    assert await async_setup_entry(hass, entry)
    monitor = hass.data[DOMAIN]
    assert isinstance(monitor, DeviceHealthMonitor)
    assert await async_unload_entry(hass, entry)
    await entry._async_process_on_unload(hass)
    assert DOMAIN not in hass.data
    # A queued options callback is harmless after unloading.
    await async_options_updated(hass, entry)


async def test_flow_exclude_and_failed_enable_are_explicit(hass, entry):
    from homeassistant.exceptions import HomeAssistantError

    add_entry(hass, "zha")
    monitor = ready_monitor(hass, entry)
    disabled = Diagnostic("RSSI", "sensor.rssi", DiagnosticState.DISABLED)
    with patch.dict(
        "custom_components.garath_device_health.monitor.COLLECTORS",
        {"zha": lambda _: [observation(diagnostics=(disabled,))]},
        clear=True,
    ):
        monitor.async_scan()
    manager = RepairsFlowManager(hass)
    hass.data["repairs"] = {
        "flow_manager": manager,
        "platforms": AsyncMock(async_get_platform=AsyncMock(return_value=repairs)),
    }
    result = await manager.async_init(
        DOMAIN, context={"issue_id": "diagnostics_device-one"}
    )
    with patch.object(
        monitor,
        "async_enable_diagnostics",
        side_effect=HomeAssistantError("Disconnected"),
    ):
        result = await manager.async_configure(
            result["flow_id"], user_input={"action": "enable"}
        )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "enable_failed"}
    assert result["description_placeholders"]["error_details"] == "Disconnected"
    assert "diagnostics_device-one" in findings(hass)
    result = await manager.async_configure(
        result["flow_id"], user_input={"action": "exclude"}
    )
    assert result["type"] == "create_entry"
    assert monitor.excluded == {"device-one"}
    assert findings(hass) == {}
