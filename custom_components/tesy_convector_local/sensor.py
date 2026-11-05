"""Sensors from MyTESY cloud telemetry (only with a cloud login).

- Internal temperature: the convector's own reading (0.5 °C steps).
- Power: the wattage set in the MyTESY app while the element heats, else 0 W. An estimate, like
  the Tesy app's: the convector measures nothing, it only reports when it heats.
- Energy: power added up over time (for the Energy dashboard). Heating time while the cloud is
  disconnected or HA is stopped is not counted.
"""
from __future__ import annotations

from datetime import timedelta
import time
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .coordinator import TesyConfigEntry
from .entity import TesyCloudEntity

STALE_AFTER = 900  # s without a heating report: stop counting


async def async_setup_entry(
    hass: HomeAssistant, entry: TesyConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    if not (cloud := coordinator.cloud):
        return
    entities: list[SensorEntity] = [TesyInternalTemperature(coordinator)]
    if cloud.watt:
        entities += [TesyPower(coordinator), TesyEnergy(coordinator)]
    async_add_entities(entities)


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


class TesyPower(TesyCloudEntity, SensorEntity):
    """App wattage while heating, 0 W while idle."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_translation_key = "power"
    _key = "power"

    @property
    def native_value(self) -> int | None:
        if self.cloud.heating is None:
            return None
        return self.cloud.watt if self.cloud.heating else 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {**super().extra_state_attributes, "rated_power_from_app": self.cloud.watt}


class TesyEnergy(TesyCloudEntity, RestoreSensor):
    """Heating time × app wattage, as a growing total."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2
    _attr_translation_key = "energy"
    _key = "energy"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._kwh = 0.0
        self._since: float | None = None  # counting heating time from here

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_sensor_data()) is not None:
            try:
                self._kwh = float(last.native_value or 0)
            except (TypeError, ValueError):
                self._kwh = 0.0
        self._account()
        self.async_on_remove(
            async_track_time_interval(self.hass, self._tick, timedelta(seconds=60))
        )

    def _counting(self, now: float) -> bool:
        cloud = self.cloud
        return bool(
            cloud.connected and cloud.heating and cloud.watt
            and cloud.last_telemetry and now - cloud.last_telemetry < STALE_AFTER
        )

    def _account(self) -> None:
        now = time.time()
        if self._since is not None:
            self._kwh += self.cloud.watt * (now - self._since) / 3_600_000
        self._since = now if self._counting(now) else None

    @callback
    def _handle_cloud_update(self) -> None:
        self._account()
        self.async_write_ha_state()

    @callback
    def _tick(self, _now) -> None:
        self._account()
        self.async_write_ha_state()

    @property
    def native_value(self) -> float:
        return round(self._kwh, 4)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = {**super().extra_state_attributes, "rated_power_from_app": self.cloud.watt}
        if hour := self.cloud.last_hour:
            # Tesy's own hourly figure, to compare with ours
            attrs["tesy_last_hour"] = f"{hour['from']}-{hour['to']}"
            attrs["tesy_last_hour_heating_s"] = hour["seconds"]
            attrs["tesy_last_hour_kwh"] = round(hour["seconds"] * self.cloud.watt / 3_600_000, 3)
        return attrs
