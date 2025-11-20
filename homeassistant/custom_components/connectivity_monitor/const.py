from __future__ import annotations

from datetime import timedelta

DOMAIN = "connectivity_monitor"
DEFAULT_NAME = "Connectivity Monitor"
DEFAULT_SCAN_INTERVAL = timedelta(seconds=30)

CONF_BASE_URL = "base_url"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_VERIFY_SSL = "verify_ssl"
CONF_SCAN_INTERVAL = "scan_interval"

SERVICE_SET_CONFIG = "set_config"

ATTR_TARGETS = "targets"
ATTR_INTERVAL_SECONDS = "interval_seconds"
