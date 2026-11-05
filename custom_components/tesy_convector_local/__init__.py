"""Tesy convector, local HTTP API (+ optional read-only MyTESY cloud telemetry)."""
from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .cloud import TesyCloudAuthError, TesyCloudError, async_get_hub, async_release_hub
from .const import (
    CONF_CLOUD_DEVICE,
    CONF_CLOUD_EMAIL,
    CONF_CLOUD_PASSWORD,
    CONF_IP_ADDRESS,
    DOMAIN,
)
from .coordinator import TesyConfigEntry, TesyCoordinator
from .net import async_mac_for_ip

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CLIMATE, Platform.SWITCH, Platform.NUMBER, Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: TesyConfigEntry) -> bool:
    """Set up a convector from a config entry."""
    _migrate_ip_to_data(hass, entry)
    coordinator = TesyCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    await _async_learn_mac(hass, entry)
    coordinator.set_mac(entry.unique_id)
    entry.runtime_data = coordinator
    await _async_setup_cloud(hass, entry, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


def _migrate_ip_to_data(hass: HomeAssistant, entry: TesyConfigEntry) -> None:
    """Up to 2.1 the IP could live in options; it belongs in data (discovery updates data)."""
    if CONF_IP_ADDRESS in entry.options:
        options = dict(entry.options)
        ip = options.pop(CONF_IP_ADDRESS)
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_IP_ADDRESS: ip}, options=options)


async def _async_learn_mac(hass: HomeAssistant, entry: TesyConfigEntry) -> None:
    """Entries made by IP before MAC identity: learn the MAC once we've talked to the convector."""
    if entry.unique_id:
        return
    mac = await async_mac_for_ip(hass, entry.data[CONF_IP_ADDRESS])
    if mac and not any(e.unique_id == mac for e in hass.config_entries.async_entries(DOMAIN)):
        hass.config_entries.async_update_entry(entry, unique_id=mac)


async def _async_setup_cloud(hass: HomeAssistant, entry: TesyConfigEntry, coordinator: TesyCoordinator) -> None:
    """Start cloud telemetry when a MyTESY login is set. Failures never block local control."""
    opts = entry.options
    if not opts.get(CONF_CLOUD_EMAIL):
        return
    try:
        hub = await async_get_hub(hass, opts[CONF_CLOUD_EMAIL], opts.get(CONF_CLOUD_PASSWORD, ""))
    except (TesyCloudAuthError, TesyCloudError) as err:
        coordinator.cloud_error = str(err)
        _LOGGER.warning("MyTESY cloud unavailable, local control only: %s", err)
        return
    coordinator.cloud_account = hub.account
    key = hub.account.find_device(entry.unique_id) or hub.account.find_device(opts.get(CONF_CLOUD_DEVICE))
    if key is None:
        coordinator.cloud_error = "this convector is not on the MyTESY account"
        return
    try:
        coordinator.cloud = await hub.async_add_device(key)
    except Exception as err:  # noqa: BLE001 - cloud problems must not break the entry
        coordinator.cloud_error = f"mqtt: {err}"
        _LOGGER.warning("MyTESY telemetry not started: %s", err)
        return
    coordinator.cloud_hub = hub

    async def _release() -> None:
        await async_release_hub(hass, hub, key)

    entry.async_on_unload(_release)


async def _async_reload(hass: HomeAssistant, entry: TesyConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: TesyConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
