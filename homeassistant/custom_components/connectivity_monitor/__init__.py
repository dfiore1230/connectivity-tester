from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import ATTR_INTERVAL_SECONDS, ATTR_TARGETS, DOMAIN, SERVICE_SET_CONFIG
from .coordinator import ConnectivityDataCoordinator

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = ConnectivityDataCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    async def handle_set_config(call: ServiceCall) -> None:
        targets = call.data.get(ATTR_TARGETS)
        interval = call.data.get(ATTR_INTERVAL_SECONDS)

        coordinator_map = hass.data.get(DOMAIN, {})
        if not coordinator_map:
            raise ValueError("Connectivity Monitor is not loaded")

        target_coordinator = coordinator_map.get(call.data.get("entry_id")) or next(iter(coordinator_map.values()))

        await target_coordinator.async_post_config(targets, interval)
        await target_coordinator.async_request_refresh()

    if not hass.services.has_service(DOMAIN, SERVICE_SET_CONFIG):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_CONFIG,
            handle_set_config,
            schema=vol.Schema(
                {
                    vol.Optional(ATTR_TARGETS): cv.string,
                    vol.Optional(ATTR_INTERVAL_SECONDS): cv.positive_int,
                    vol.Optional("entry_id"): cv.string,
                }
            ),
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(domain=DOMAIN, service=SERVICE_SET_CONFIG)
    return unload_ok
