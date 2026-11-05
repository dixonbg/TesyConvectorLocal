"""Tesy convector, local HTTP API (+ optional read-only MyTESY cloud telemetry)."""
from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .cloud import TesyCloudAccount, TesyCloudAuthError, TesyCloudError, TesyCloudTelemetry
from .const import CONF_CLOUD_DEVICE, CONF_CLOUD_EMAIL, CONF_CLOUD_PASSWORD
from .coordinator import TesyConfigEntry, TesyCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CLIMATE, Platform.SWITCH, Platform.NUMBER, Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: TesyConfigEntry) -> bool:
    """Set up a convector from a config entry."""
    coordinator = TesyCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await _async_setup_cloud(hass, entry, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_setup_cloud(hass: HomeAssistant, entry: TesyConfigEntry, coordinator: TesyCoordinator) -> None:
    """Start cloud telemetry when a MyTESY login is set. Failures never block local control."""
    opts = entry.options
    if not opts.get(CONF_CLOUD_EMAIL):
        return
    account = TesyCloudAccount(
        async_get_clientsession(hass), opts[CONF_CLOUD_EMAIL], opts.get(CONF_CLOUD_PASSWORD, "")
    )
    coordinator.cloud_account = account
    try:
        await account.load()
    except (TesyCloudAuthError, TesyCloudError) as err:
        coordinator.cloud_error = str(err)
        _LOGGER.warning("MyTESY cloud unavailable, local control only: %s", err)
        return
    mac = opts.get(CONF_CLOUD_DEVICE)
    if not mac or mac not in account.devices:
        coordinator.cloud_error = "no cloud device chosen" if not mac else "chosen device not on this account"
        return
    telemetry = TesyCloudTelemetry(hass, account, mac)
    try:
        await telemetry.async_start()
    except Exception as err:  # noqa: BLE001 - cloud problems must not break the entry
        coordinator.cloud_error = f"mqtt: {err}"
        _LOGGER.warning("MyTESY telemetry not started: %s", err)
        return
    coordinator.cloud = telemetry
    entry.async_on_unload(telemetry.async_stop)


async def _async_reload(hass: HomeAssistant, entry: TesyConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: TesyConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
