"""The convector's own temperature from MyTESY cloud telemetry (only with a cloud login)."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import TesyConfigEntry
from .entity import TesyCloudEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: TesyConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    if coordinator.cloud:
        async_add_entities([TesyInternalTemperature(coordinator)])


class TesyInternalTemperature(TesyCloudEntity, SensorEntity):
    """Temperature measured by the convector itself (0.5 °C steps)."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_translation_key = "internal_temperature"
    _key = "internal_temperature"

    @property
    def native_value(self) -> float | None:
        return self.cloud.current_temp
