"""Constants for Garath Device Health."""

from datetime import timedelta
from enum import StrEnum


class Feature(StrEnum):
    """Independently configurable device checks."""

    CONFIGURATION = "configuration"
    COMMUNICATION = "communication"

    @property
    def enabled_key(self) -> str:
        """Config entry option controlling this feature."""
        return f"{self}_enabled"

    @property
    def excluded_key(self) -> str:
        """Config entry option listing this feature's excluded devices."""
        return f"{self}_excluded_devices"


DOMAIN = "garath_device_health"
NAME = "Garath Device Health"
CONF_EXCLUDED_DEVICES = "excluded_devices"
CONF_SNOOZED_UNTIL = "snoozed_until"
CONF_UNSNOOZE_DEVICES = "unsnooze_devices"
SCAN_INTERVAL = timedelta(minutes=5)
STALE_AFTER = timedelta(hours=24)
STARTUP_GRACE = timedelta(minutes=5)
