"""Read-only adapters for Home Assistant's protocol-specific runtime data."""

from typing import TYPE_CHECKING, cast

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import Feature
from .models import Diagnostic, DiagnosticState, Observation
from .policy import timestamp

if TYPE_CHECKING:
    from homeassistant.components.zha.helpers import HAZHAData
    from homeassistant.components.zwave_js.models import ZwaveJSConfigEntry


class SourceUnavailable(Exception):
    """An integration's data cannot currently be used."""


def diagnostic(
    registry: er.EntityRegistry, label: str, entity_id: str | None
) -> Diagnostic:
    """Distinguish absent or blocked entities from user/integration-disabled ones."""
    if entity_id is None or (entry := registry.async_get(entity_id)) is None:
        return Diagnostic(label, None, DiagnosticState.MISSING)
    if entry.disabled_by is None:
        state = DiagnosticState.ENABLED
    elif entry.disabled_by in (
        er.RegistryEntryDisabler.USER,
        er.RegistryEntryDisabler.INTEGRATION,
    ):
        state = DiagnosticState.DISABLED
    else:
        state = DiagnosticState.BLOCKED
    return Diagnostic(label, entity_id, state)


def collect_zwave(hass: HomeAssistant, feature: Feature) -> list[Observation]:
    """Read only the Z-Wave JS data required by this feature."""
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    observations: list[Observation] = []
    for entry in hass.config_entries.async_entries("zwave_js"):
        config_entry = cast("ZwaveJSConfigEntry", entry)
        if config_entry.disabled_by is not None:
            continue
        if config_entry.state != ConfigEntryState.LOADED:
            raise SourceUnavailable("Z-Wave JS is not loaded")
        client = config_entry.runtime_data.client
        if not client.connected or client.driver is None:
            raise SourceUnavailable("Z-Wave JS server is disconnected")
        driver = client.driver
        controller = driver.controller
        for node in controller.nodes.values():
            if node.node_id == controller.own_node_id:
                continue
            device = devices.async_get_device_by_identifier(
                ("zwave_js", f"{controller.home_id}-{node.node_id}"),
                config_entry.entry_id,
            )
            if device is None or device.disabled_by is not None:
                continue
            base_id = f"{controller.home_id}.{node.node_id}"
            diagnostics: tuple[Diagnostic, ...] = ()
            last_seen = None
            error = None
            try:
                if feature == Feature.CONFIGURATION:
                    diagnostics = tuple(
                        diagnostic(
                            entities,
                            label,
                            entities.async_get_entity_id(
                                "sensor", "zwave_js", base_id + suffix
                            ),
                        )
                        for label, suffix in (
                            ("Last seen", ".statistics_last_seen"),
                            ("Signal strength", ".statistics_rssi"),
                            ("Node status", ".node_status"),
                        )
                    )
                else:
                    last_seen = timestamp(node.statistics.last_seen)
            except (SourceUnavailable, AttributeError, TypeError, ValueError) as err:
                error = str(err) or type(err).__name__
            observations.append(
                Observation(
                    device.id,
                    device.name_by_user or device.name or f"Z-Wave node {node.node_id}",
                    "zwave_js",
                    last_seen,
                    diagnostics,
                    error,
                )
            )
    return observations


def collect_zha(hass: HomeAssistant, feature: Feature) -> list[Observation]:
    """Read ZHA contact times independently from diagnostic configuration."""
    entries = [
        entry
        for entry in hass.config_entries.async_entries("zha")
        if entry.disabled_by is None
    ]
    if not entries:
        return []
    if any(entry.state != ConfigEntryState.LOADED for entry in entries):
        raise SourceUnavailable("ZHA is not loaded")
    if "zha" not in hass.data:
        raise SourceUnavailable("ZHA runtime data is unavailable")
    gateway = cast("HAZHAData", hass.data["zha"]).gateway_proxy
    if gateway is None:
        raise SourceUnavailable("ZHA gateway is unavailable")
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    observations: list[Observation] = []
    for proxy in gateway.device_proxies.values():
        node = proxy.device
        if node.is_active_coordinator:
            continue
        device = devices.async_get_device_by_identifier(
            ("zha", str(node.ieee)), gateway.config_entry.entry_id
        )
        if device is None or device.disabled_by is not None:
            continue
        diagnostics: list[Diagnostic] = []
        last_seen = None
        error = None
        try:
            if feature == Feature.CONFIGURATION:
                device_entities = er.async_entries_for_device(
                    entities, device.id, include_disabled_entities=True
                )
                for key, label in (("rssi", "RSSI"), ("lqi", "LQI")):
                    candidates = [
                        entity
                        for entity in device_entities
                        if entity.platform == "zha"
                        and entity.domain == "sensor"
                        and entity.translation_key == key
                    ]
                    if len(candidates) > 1:
                        raise SourceUnavailable(
                            f"Ambiguous {key} diagnostic entities for {device.id}"
                        )
                    diagnostics.append(
                        diagnostic(
                            entities,
                            label,
                            candidates[0].entity_id if candidates else None,
                        )
                    )
            else:
                last_seen = timestamp(node.last_seen)
        except (SourceUnavailable, AttributeError, TypeError, ValueError) as err:
            error = str(err) or type(err).__name__
        observations.append(
            Observation(
                device.id,
                device.name_by_user or device.name or str(node.ieee),
                "zha",
                last_seen,
                tuple(diagnostics),
                error,
            )
        )
    return observations
