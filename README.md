# Garath Device Health

A Home Assistant custom integration that creates actionable Repairs for Z-Wave
JS and ZHA devices. All existing and newly discovered end devices are monitored
by default, regardless of manufacturer. Coordinators/controllers and disabled
devices/config entries are not monitored.

## Two independent features

One integration and HACS installation provide two independently enabled features:

| Feature | Purpose | Controls |
| --- | --- | --- |
| Diagnostic configuration | Check expected diagnostic entities, not connectivity | Enable entities after confirmation; configuration-only exclusions |
| Communication monitoring | Check native last-contact times, not entity settings | No-contact snoozes; communication-only exclusions |

Choose either or both during setup. In integration options, select **Diagnostic
configuration** or **Communication monitoring** to enable/disable that feature
and manage its own excluded devices. Turning a feature off clears only its
repairs, retains its settings, and does not change diagnostic entities or the
other feature. Turning it back on evaluates current conditions. Snoozes keep
their absolute expiry times while communication monitoring is off.

| Protocol | Diagnostic configuration | Communication monitoring |
| --- | --- | --- |
| Z-Wave JS | Last seen, Signal strength (node statistics RSSI), Node status | Native node-statistics last-seen timestamp |
| ZHA | RSSI, LQI | Native ZHA device last-seen timestamp |

The diagnostic repair lists disabled entities and offers to enable them, **only
after confirmation**, or stop configuration checks for the device. It changes
Home Assistant's entity registry, not radio/device configuration. It never
writes manufacturer-specific parameters. Enabled diagnostic entities may add
Recorder history.

The independent communication-age repair warns at **24 hours or more**, and
clears when communication resumes. There are no pings, wake-ups, or polls of
physical devices. Checks run every five minutes and after device/entity registry
changes, with a five-minute startup grace period. Detection can therefore lag
the 24-hour boundary by up to one scan interval.

Sleeping battery devices may legitimately be quiet for more than a day.
"No communication observed" does **not** mean "device failed." Snooze temporary
warnings, or exclude devices for which this threshold is inappropriate.
RSSI/LQI are diagnostic clues, not proof of connectivity, and this project does
not warn on signal thresholds.

Missing/blocked diagnostic entities and unknown last-seen times produce
informational warning repairs instead of successful-looking enable actions or
invented timestamps. Disabled diagnostics never block last-seen monitoring.
Repair titles identify the feature. Each feature has its own evaluator, repair
flow, exclusions, and failure handling. Configuration checks never read contact
timestamps; communication monitoring never inspects diagnostic entities.
Both share device discovery and the scan schedule.

If feature data is unusable for one device, that feature raises a device-specific
warning and logs the error; other devices and the other feature continue.
If the protocol source itself is unavailable, the affected feature raises a
protocol-source warning instead. Its outstanding findings are retained until
observations can be trusted again, rather than claiming recovery. A shared
protocol outage can therefore produce a source warning for each enabled feature.

## Opt-out behavior

- **Snooze a no-contact repair:** open the repair and choose **24 hours**,
  **7 days**, or **30 days**. Only the no-contact warning is suppressed;
  configuration checks and unknown-last-seen/source warnings remain active.
  No last-contact timestamp is changed. At expiry, the next scan warns again
  only if communication is still overdue, without pinging or waking the device.
- **Unsnooze early:** open **Communication monitoring** in integration options,
  review the snoozed-until times
  (in your Home Assistant time zone), and select devices under **Unsnooze
  devices**. Leave devices unselected to retain their snoozes.
- **Ignore a configuration repair:** exclude that device only from configuration
  checks and clear its configuration repairs. Communication monitoring continues.
- **Ignore a communication repair:** exclude that device only from communication
  monitoring and clear its communication repairs and snooze. Configuration checks
  continue.
- **Stop checks in a repair flow or exclude in a feature's options:** the same
  feature-specific result. To exclude a device from both, exclude it on both
  options pages.
- **Ignore a protocol-source warning:** no device exclusions are changed.
- **Close/cancel a repair dialog:** no opt-out, and no settings changed.
- **Resume checks for a device:** remove its exclusion on the appropriate
  feature's options page.

Upgrades preserve the old policy choices: existing all-check exclusions are
copied into both features' exclusion lists, and outstanding legacy ignores retain
their original all-check meaning. Existing snoozes remain communication-only.
New ignores are feature-specific; upgrading does not silently re-enroll devices.

Exclusions and snooze expiry times live in Home Assistant's config entry options
and survive restarts and Home Assistant upgrades. They use device registry IDs,
not user-editable names or entity IDs. Removing and pairing a device as a new
Home Assistant device can give it a new ID, in which case it is included by
default again.
Deleting this integration also deletes its exclusions and snoozes.

## Installation

Baseline: **Home Assistant Core 2026.10.4 or newer**, running its required
Python version (3.14.2+ for the baseline). Z-Wave JS and/or ZHA must already be
configured. Zigbee2MQTT and MQTT-only Z-Wave connections are not supported.

1. Copy `custom_components/garath_device_health` into the Home Assistant
   configuration directory's `custom_components` folder.
2. Restart Home Assistant.
3. Go to **Settings > Devices & services > Add integration** and search for
   **Garath Device Health**.
4. Choose the features to enable and review their opt-out policies. Repairs
   appear under **Settings > System > Repairs** after the startup grace period.

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
