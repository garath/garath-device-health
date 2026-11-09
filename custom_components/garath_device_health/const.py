"""Constants for Garath Device Health."""

from datetime import timedelta

DOMAIN = "garath_device_health"
NAME = "Garath Device Health"
CONF_EXCLUDED_DEVICES = "excluded_devices"
CONF_SNOOZED_UNTIL = "snoozed_until"
CONF_UNSNOOZE_DEVICES = "unsnooze_devices"
SCAN_INTERVAL = timedelta(minutes=5)
STALE_AFTER = timedelta(hours=24)
STARTUP_GRACE = timedelta(minutes=5)
