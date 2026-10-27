"""Check onboarding, exclusions and distribution metadata."""

import json
from pathlib import Path
from unittest.mock import patch

from custom_components.garath_device_health.config_flow import (
    GarathDeviceHealthConfigFlow,
    GarathDeviceHealthOptionsFlow,
)
from custom_components.garath_device_health.const import CONF_EXCLUDED_DEVICES, DOMAIN

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
