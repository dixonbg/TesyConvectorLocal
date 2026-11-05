"""Heating on/off from MyTESY cloud telemetry (only with a cloud login)."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import TesyConfigEntry
from .entity import TesyCloudEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: TesyConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    if coordinator.cloud:
        async_add_entities([TesyHeating(coordinator)])


class TesyHeating(TesyCloudEntity, BinarySensorEntity):
    """On while the heating element is on (cloud telemetry)."""

    _attr_device_class = BinarySensorDeviceClass.HEAT
    _attr_translation_key = "heating"
    _key = "heating"

    @property
    def is_on(self) -> bool | None:
        return self.cloud.heating
