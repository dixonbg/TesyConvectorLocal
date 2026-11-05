"""Mode temperatures, durations and sensor correction.

Durations are minutes (device defaults: eco 480, sleep 360, delayed start 120). Commands
that carry several fields are sent with all of them, using the current values for the rest.
"""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import TesyConfigEntry, TesyCoordinator


@dataclass(frozen=True, kw_only=True)
class TesyNumberDescription(NumberEntityDescription):
    command: str
    field: str
    extra: tuple[str, ...] = ()


def _temp(
    key: str, command: str, extra: tuple[str, ...] = (), enabled: bool = True
) -> TesyNumberDescription:
    return TesyNumberDescription(
        key=key, translation_key=key, command=command, field="temp", extra=extra,
        entity_registry_enabled_default=enabled,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=10, native_max_value=30, native_step=1,
        mode=NumberMode.BOX, entity_category=EntityCategory.CONFIG,
    )


def _minutes(
    key: str, command: str, extra: tuple[str, ...], enabled: bool = True
) -> TesyNumberDescription:
    return TesyNumberDescription(
        key=key, translation_key=key, command=command, field="time", extra=extra,
        entity_registry_enabled_default=enabled,
        device_class=NumberDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        native_min_value=0, native_max_value=1440, native_step=30,
        mode=NumberMode.BOX, entity_category=EntityCategory.CONFIG,
    )


NUMBERS = (
    _temp("comfort_temperature", "setComfortTemp"),
    _temp("eco_temperature", "setEcoTemp", ("time",)),
    _minutes("eco_duration", "setEcoTemp", ("temp",)),
    _temp("sleep_temperature", "setSleepTemp", ("time",)),
    _minutes("sleep_duration", "setSleepTemp", ("temp",)),
    # delayed start is rarely used, and the firmware copies the setpoint into its temperature,
    # so these change whenever the target does: off by default, like the delayed-start switch
    _temp("delayed_start_temperature", "setDelayedStart", ("status", "time"), enabled=False),
    _minutes("delayed_start_after", "setDelayedStart", ("status", "temp"), enabled=False),
    TesyNumberDescription(
        key="temperature_correction", translation_key="temperature_correction",
        command="setTCorrection", field="temp",
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        # device range, tested: values outside are clamped, decimals rounded.
        # Slider, as phone number pads often lack a minus key.
        native_min_value=-4, native_max_value=4, native_step=1,
        mode=NumberMode.SLIDER, entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: TesyConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(TesyNumber(coordinator, description) for description in NUMBERS)


class TesyNumber(CoordinatorEntity[TesyCoordinator], NumberEntity):
    _attr_has_entity_name = True
    entity_description: TesyNumberDescription

    def __init__(self, coordinator: TesyCoordinator, description: TesyNumberDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{description.command}_{description.field}"
        )
        self._attr_device_info = coordinator.device_info

    @property
    def native_value(self) -> float | None:
        d = self.entity_description
        return self.coordinator.value(d.command, d.field)

    async def async_set_native_value(self, value: float) -> None:
        d = self.entity_description
        payload = {field: self.coordinator.value(d.command, field) for field in d.extra}
        payload[d.field] = int(round(value))
        await self.coordinator.async_command(d.command, payload)
