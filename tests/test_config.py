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
    assert result["type"] == "form"
    result = await options.async_step_init({CONF_EXCLUDED_DEVICES: []})
    assert result["data"] == {CONF_EXCLUDED_DEVICES: []}


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
    result = await options.async_step_init()
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
    result = await options.async_step_init(
        {CONF_EXCLUDED_DEVICES: [], CONF_UNSNOOZE_DEVICES: ["device-one"]}
    )
    assert CONF_UNSNOOZE_DEVICES not in result["data"]
    assert set(result["data"][CONF_SNOOZED_UNTIL]) == {"device-two", "device-three"}
    hass.config_entries.async_update_entry(entry, options=result["data"])
    with patch.dict(
        "custom_components.garath_device_health.monitor.COLLECTORS",
        {"zha": lambda _: [observation()]},
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
    result = await options.async_step_init({CONF_EXCLUDED_DEVICES: []})
    assert result["data"][CONF_SNOOZED_UNTIL] == {
        "device-one": expiry,
        "device-two": expiry,
    }
    assert result["data"]["other_option"] is True
    result = await options.async_step_init(
        {CONF_EXCLUDED_DEVICES: ["device-one"], CONF_UNSNOOZE_DEVICES: []}
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
    }
