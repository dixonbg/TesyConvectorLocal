"""Shared poll and command sender for one convector."""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TesyApiError, TesyLocalApi
from .const import CONF_IP_ADDRESS, CONF_TEMPERATURE_ENTITY, DEFAULT_MODEL, DOMAIN

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=30)

type TesyConfigEntry = ConfigEntry["TesyCoordinator"]


class TesyCoordinator(DataUpdateCoordinator[dict[str, dict]]):
    """Polls getStatus. Data is {command: inner payload}."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=f"Tesy {entry.title}", update_interval=SCAN_INTERVAL
        )
        conf = {**entry.data, **entry.options}
        self.host: str = conf[CONF_IP_ADDRESS]
        self.temperature_entity: str | None = conf.get(CONF_TEMPERATURE_ENTITY)
        self.api = TesyLocalApi(async_get_clientsession(hass), self.host)
        # optional MyTESY cloud telemetry, set up in __init__.py
        self.cloud = None
        self.cloud_account = None
        self.cloud_error: str | None = None
        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Tesy",
            model=DEFAULT_MODEL,
            configuration_url=f"http://{self.host}/",
        )

    async def _async_update_data(self) -> dict[str, dict]:
        try:
            return await self.api.get_status()
        except TesyApiError as err:
            raise UpdateFailed(str(err)) from err

    def value(self, command: str, field: str) -> Any:
        """One field of one setting, e.g. value("setTemp", "temp")."""
        return (self.data or {}).get(command, {}).get(field)

    async def async_command(self, command: str, payload: dict[str, Any]) -> None:
        """Send a setting, show it at once, then re-read the device."""
        try:
            await self.api.request(command, payload)
        except TesyApiError as err:
            raise HomeAssistantError(str(err)) from err
        if self.data is not None:
            new = dict(self.data)
            new[command] = {**new.get(command, {}), **payload}
            self.async_set_updated_data(new)
        await self.async_request_refresh()
