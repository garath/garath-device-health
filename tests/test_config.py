"""Check onboarding, exclusions and distribution metadata."""

import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from homeassistant.util import dt as dt_util

from custom_components.garath_device_health.config_flow import (
    GarathDeviceHealthConfigFlow,
    GarathDeviceHealthOptionsFlow,
)
from custom_components.garath_device_health.const import (
    CONF_EXCLUDED_DEVICES,
    CONF_SNOOZED_UNTIL,
    CONF_UNSNOOZE_DEVICES,
    DOMAIN,
    Feature,
)

ROOT = Path(__file__).parents[1]


async def test_user_flow_and_options(hass, entry):
    flow = GarathDeviceHealthConfigFlow()
    flow.hass = hass
    flow.context = {}
    with patch.object(flow, "_abort_if_unique_id_configured"):
        result = await flow.async_step_user()
        assert result["type"] == "form"
        result = await flow.async_step_user({})
        assert result["type"] == "create_entry"
    options = GarathDeviceHealthOptionsFlow()
    options.hass = hass
    options.handler = entry.entry_id
    result = await options.async_step_init()
    assert result["type"] == "menu"
    assert result["menu_options"] == ["configuration", "communication"]
    result = await options.async_step_configuration()
    assert result["type"] == "form"
    result = await options.async_step_configuration(
        {"enabled": True, CONF_EXCLUDED_DEVICES: []}
    )
    assert result["data"] == {
        Feature.CONFIGURATION.enabled_key: True,
        Feature.CONFIGURATION.excluded_key: [],
    }


async def test_options_show_expiry_and_unsnooze_only_selected_devices(hass, entry):
    from custom_components.garath_device_health import async_options_updated

    from .conftest import add_entry
    from .test_monitor import findings, observation, ready_monitor

    add_entry(hass, "zha")
    monitor = ready_monitor(hass, entry)
    monitor.async_snooze("device-one", timedelta(days=1))
    monitor.async_snooze("device-two", timedelta(days=7))
    original = monitor.snoozed_until
    options = GarathDeviceHealthOptionsFlow()
    options.hass = hass
    options.handler = entry.entry_id
    result = await options.async_step_communication()
    schema = result["data_schema"].schema
    snooze_selector = next(
        value for key, value in schema.items() if key == CONF_UNSNOOZE_DEVICES
    )
    labels = {
        item["value"]: item["label"] for item in snooze_selector.config["options"]
    }
    assert set(labels) == {"device-one", "device-two"}
    assert (
        dt_util.as_local(dt_util.utc_from_timestamp(original["device-one"])).isoformat()
        in labels["device-one"]
    )
    # A snooze added while this form is open must not be overwritten on submit.
    monitor.async_snooze("device-three", timedelta(days=7))
    result = await options.async_step_communication(
        {
            "enabled": True,
            CONF_EXCLUDED_DEVICES: [],
            CONF_UNSNOOZE_DEVICES: ["device-one"],
        }
    )
    assert CONF_UNSNOOZE_DEVICES not in result["data"]
    assert set(result["data"][CONF_SNOOZED_UNTIL]) == {"device-two", "device-three"}
    hass.config_entries.async_update_entry(entry, options=result["data"])
    with patch.dict(
        "custom_components.garath_device_health.monitor.COLLECTORS",
        {"zha": lambda _, feature: [observation()]},
        clear=True,
    ):
        await async_options_updated(hass, entry)
        assert set(findings(hass)) == {"stale_device-one"}


async def test_options_preserve_snoozes_and_clear_new_exclusions(hass, entry):
    expiry = (dt_util.utcnow() + timedelta(days=1)).timestamp()
    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_SNOOZED_UNTIL: {"device-one": expiry, "device-two": expiry},
            "other_option": True,
        },
    )
    options = GarathDeviceHealthOptionsFlow()
    options.hass = hass
    options.handler = entry.entry_id
    result = await options.async_step_communication(
        {"enabled": True, CONF_EXCLUDED_DEVICES: []}
    )
    assert result["data"][CONF_SNOOZED_UNTIL] == {
        "device-one": expiry,
        "device-two": expiry,
    }
    assert result["data"]["other_option"] is True
    result = await options.async_step_communication(
        {
            "enabled": True,
            CONF_EXCLUDED_DEVICES: ["device-one"],
            CONF_UNSNOOZE_DEVICES: [],
        }
    )
    assert result["data"][CONF_SNOOZED_UNTIL] == {"device-two": expiry}


def test_manifest_and_english_translation_parity():
    component = ROOT / "custom_components" / DOMAIN
    strings = json.loads((component / "strings.json").read_text())
    english = json.loads((component / "translations" / "en.json").read_text())
    assert strings == english
    manifest = json.loads((component / "manifest.json").read_text())
    assert manifest["domain"] == DOMAIN
    assert manifest["codeowners"] == ["@Garath"]
    assert set(strings["issues"]) == {
        "diagnostics",
        "diagnostics_unavailable",
        "last_seen_unknown",
        "stale",
        "source_unavailable",
        "configuration_source_unavailable",
        "communication_source_unavailable",
        "configuration_unavailable",
        "communication_unavailable",
    }


async def test_migration_preserves_both_exclusions_snoozes_and_other_options(
    hass, entry
):
    from custom_components.garath_device_health import async_migrate_entry

    expiry = (dt_util.utcnow() + timedelta(days=30)).timestamp()
    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_EXCLUDED_DEVICES: ["device-one"],
            CONF_SNOOZED_UNTIL: {"device-two": expiry},
            "other_option": True,
        },
    )
    assert entry.version == 1
    assert await async_migrate_entry(hass, entry)
    assert entry.version == 2
    assert CONF_EXCLUDED_DEVICES not in entry.options
    for feature in Feature:
        assert entry.options[feature.excluded_key] == ["device-one"]
        assert entry.options[feature.enabled_key] is True
    assert entry.options[CONF_SNOOZED_UNTIL] == {"device-two": expiry}
    assert entry.options["other_option"] is True
    original = dict(entry.options)
    assert await async_migrate_entry(hass, entry)
    assert entry.options == original
    await hass.config_entries._store.async_save(hass.config_entries._data_to_save())
    saved = await hass.config_entries._store.async_load()
    saved_entry = next(
        item for item in saved["entries"] if item["entry_id"] == entry.entry_id
    )
    assert saved_entry["version"] == 2
    assert saved_entry["options"] == original


async def test_configuration_options_leave_communication_and_snoozes_unchanged(
    hass, entry
):
    expiry = (dt_util.utcnow() + timedelta(days=7)).timestamp()
    initial = {
        Feature.COMMUNICATION.enabled_key: True,
        Feature.COMMUNICATION.excluded_key: ["device-two"],
        CONF_SNOOZED_UNTIL: {"device-one": expiry},
    }
    hass.config_entries.async_update_entry(entry, options=initial)
    options = GarathDeviceHealthOptionsFlow()
    options.hass = hass
    options.handler = entry.entry_id
    form = await options.async_step_configuration()
    assert CONF_UNSNOOZE_DEVICES not in form["data_schema"].schema
    result = await options.async_step_configuration(
        {"enabled": False, CONF_EXCLUDED_DEVICES: ["device-one"]}
    )
    assert result["data"] == {
        **initial,
        Feature.CONFIGURATION.enabled_key: False,
        Feature.CONFIGURATION.excluded_key: ["device-one"],
    }


async def test_communication_options_leave_configuration_unchanged(hass, entry):
    initial = {
        Feature.CONFIGURATION.enabled_key: False,
        Feature.CONFIGURATION.excluded_key: ["device-one"],
    }
    hass.config_entries.async_update_entry(entry, options=initial)
    options = GarathDeviceHealthOptionsFlow()
    options.hass = hass
    options.handler = entry.entry_id
    result = await options.async_step_communication(
        {"enabled": True, CONF_EXCLUDED_DEVICES: ["device-two"]}
    )
    assert result["data"] == {
        **initial,
        Feature.COMMUNICATION.enabled_key: True,
        Feature.COMMUNICATION.excluded_key: ["device-two"],
    }


async def test_onboarding_selects_features_independently(hass):
    flow = GarathDeviceHealthConfigFlow()
    flow.hass = hass
    flow.context = {}
    with patch.object(flow, "_abort_if_unique_id_configured"):
        form = await flow.async_step_user()
        assert set(form["data_schema"].schema) == {
            feature.enabled_key for feature in Feature
        }
        selected = {
            Feature.CONFIGURATION.enabled_key: False,
            Feature.COMMUNICATION.enabled_key: True,
        }
        result = await flow.async_step_user(selected)
        assert result["options"] == selected
