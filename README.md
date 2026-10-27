# Garath Device Health

A Home Assistant custom integration that creates actionable Repairs for Z-Wave
JS and ZHA devices. All existing and newly discovered end devices are monitored
by default, regardless of manufacturer. Coordinators/controllers and disabled
devices/config entries are not monitored.

## Independent checks

| Protocol | Diagnostic enable repair | Communication-age repair |
| --- | --- | --- |
| Z-Wave JS | Last seen, Signal strength (node statistics RSSI), Node status | Native node-statistics last-seen timestamp |
| ZHA | RSSI, LQI | Native ZHA device last-seen timestamp |

The diagnostic repair lists disabled entities and offers to enable them, **only
after confirmation**, or exclude the device. It changes Home Assistant's entity
registry, not radio/device configuration. It never writes manufacturer-specific
parameters. Enabled diagnostic entities may add Recorder history.

The independent communication-age repair warns at **24 hours or more**, and
clears when communication resumes. There are no pings, wake-ups, or polls of
physical devices. Checks run every five minutes and after device/entity registry
changes, with a five-minute startup grace period. Detection can therefore lag
the 24-hour boundary by up to one scan interval.

Sleeping battery devices may legitimately be quiet for more than a day.
"No communication observed" does **not** mean "device failed." Exclude devices
for which this threshold is inappropriate. RSSI/LQI are diagnostic clues, not
proof of connectivity, and this project does not warn on signal thresholds.

Missing/blocked diagnostic entities and unknown last-seen times produce
informational warning repairs instead of successful-looking enable actions or
invented timestamps. Disabled diagnostics never block last-seen monitoring.
If an owning integration disconnects or its interface changes, monitoring for
that protocol pauses, a source warning is raised, and the error is logged.
Outstanding device issues are retained until observations can be trusted again.

## Opt-out behavior

- **Ignore any device repair:** persistently exclude that device from *both*
  checks and clear all its outstanding repairs.
- **Exclude in a diagnostic repair flow or integration options:** same result.
- **Close/cancel a repair dialog:** no opt-out, and no settings changed.
- **Re-enable monitoring:** open the integration options and remove the device
  from **Excluded devices**.

Exclusions live in Home Assistant's config entry options and survive restarts
and Home Assistant upgrades. They use device registry IDs, not user-editable
names or entity IDs. Removing and pairing a device as a new Home Assistant
device can give it a new ID, in which case it is included by default again.
Deleting this integration also deletes its exclusions.

## Installation

Baseline: **Home Assistant Core 2026.10.4 or newer**, running its required
Python version (3.14.2+ for the baseline). Z-Wave JS and/or ZHA must already be
configured. Zigbee2MQTT and MQTT-only Z-Wave connections are not supported.

1. Copy `custom_components/garath_device_health` into the Home Assistant
   configuration directory's `custom_components` folder.
2. Restart Home Assistant.
3. Go to **Settings > Devices & services > Add integration** and search for
   **Garath Device Health**.
4. Review the opt-out policy and submit. Repairs appear under
   **Settings > System > Repairs** after the startup grace period.

HACS metadata is included for a future GitHub custom-repository installation.
The project has not been published; the GitHub URL in the manifest is the
intended location, not a claim that the repository exists.

The namespace/domain is `garath_device_health`, prefixed with the GitHub alias
**Garath**. Home Assistant requires the outer `custom_components` package.
There is no separate Python library to install in Home Assistant.

## Development

Windows PowerShell, from this project directory:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check .
.\.venv\Scripts\pyright.exe
```

Tests use the real Home Assistant registries and repair flow manager with
simulated protocol runtime objects. They do not require a radio or connect to a
running Home Assistant. Before deploying to a production instance, confirm
device discovery, enabling diagnostics, ignoring/re-enrolling, and recovery on
your own devices. Protocol adapters access integration-specific runtime APIs;
compatibility with future Home Assistant releases must be checked.
