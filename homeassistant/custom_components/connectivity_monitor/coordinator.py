from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import aiohttp
from aiohttp import ClientResponseError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    ATTR_INTERVAL_SECONDS,
    ATTR_TARGETS,
    CONF_BASE_URL,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class ConnectivityPayload:
    latest: list[dict[str, Any]]
    daily: list[dict[str, Any]]


class ConnectivityDataCoordinator(DataUpdateCoordinator[ConnectivityPayload]):
    """Coordinator that fetches /data and /daily from the monitor."""

    def __init__(self, hass: HomeAssistant, config_entry) -> None:
        data = config_entry.data
        options = config_entry.options

        base_url = data[CONF_BASE_URL].rstrip("/")
        username = data.get(CONF_USERNAME) or None
        password = data.get(CONF_PASSWORD) or None
        verify_ssl = options.get(CONF_VERIFY_SSL, data.get(CONF_VERIFY_SSL, False))
        scan_seconds = int(options.get(CONF_SCAN_INTERVAL, data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL.total_seconds())))

        session = async_get_clientsession(hass, verify_ssl=verify_ssl)
        auth = aiohttp.BasicAuth(username, password) if username and password else None

        self._base_url = base_url
        self._auth = auth
        self._session = session

        update_interval = timedelta(seconds=scan_seconds)

        super().__init__(
            hass,
            _LOGGER,
            name="Connectivity Monitor",
            update_interval=update_interval,
        )

    async def _async_update_data(self) -> ConnectivityPayload:
        try:
            data_future = self._session.get(f"{self._base_url}/data", auth=self._auth)
            daily_future = self._session.get(f"{self._base_url}/daily", auth=self._auth)
            resp_data, resp_daily = await asyncio.gather(data_future, daily_future)

            async with resp_data:
                resp_data.raise_for_status()
                latest = await resp_data.json()

            async with resp_daily:
                resp_daily.raise_for_status()
                daily = await resp_daily.json()

        except (aiohttp.ClientError, ClientResponseError) as err:
            raise UpdateFailed(f"Error talking to connectivity monitor: {err}") from err
        except asyncio.TimeoutError as err:
            raise UpdateFailed("Timed out talking to connectivity monitor") from err
        except json.JSONDecodeError as err:
            raise UpdateFailed("Failed to parse connectivity monitor response") from err

        return ConnectivityPayload(latest=latest or [], daily=daily or [])

    async def async_post_config(self, targets: str | None, interval_seconds: int | None) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if targets:
            payload[ATTR_TARGETS] = targets
        if interval_seconds:
            payload[ATTR_INTERVAL_SECONDS] = str(interval_seconds)

        try:
            async with self._session.post(
                f"{self._base_url}/config",
                auth=self._auth,
                json=payload,
            ) as resp:
                resp.raise_for_status()
                return await resp.json()
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Error posting configuration: {err}") from err

    @property
    def latest_record(self) -> dict[str, Any] | None:
        if not self.data or not self.data.latest:
            return None
        return self.data.latest[-1]

    @property
    def most_recent_day(self) -> dict[str, Any] | None:
        if not self.data or not self.data.daily:
            return None
        return self.data.daily[-1]
