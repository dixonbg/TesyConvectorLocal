"""Climate entity: on/off, target temperature and the device modes as presets.

Device modes (setMode "name", tested on a CN06AS): heating (normal, uses setTemp),
comfort / eco / sleep (use setComfortTemp / setEcoTemp / setSleepTemp) and program
(weekly schedule set in the Tesy app). Unknown names are answered "OK" but ignored.

With a MyTESY cloud login, the cloud adds what the local API lacks: whether the element is
heating (shown as the HVAC action) and the convector's own temperature (0.5 °C steps). The
room sensor, when set, stays the current temperature; without one the convector's own
reading is used.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_NONE,
    PRESET_SLEEP,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import TesyConfigEntry, TesyCoordinator

PRESET_PROGRAM = "program"
PRESET_TO_MODE = {
    PRESET_NONE: "heating",
    PRESET_COMFORT: "comfort",
    PRESET_ECO: "eco",
    PRESET_SLEEP: "sleep",
    PRESET_PROGRAM: "program",
}
MODE_TO_PRESET = {mode: preset for preset, mode in PRESET_TO_MODE.items()}
MODE_TEMP_COMMAND = {
    "heating": "setTemp",
    "comfort": "setComfortTemp",
    "eco": "setEcoTemp",
    "sleep": "setSleepTemp",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: TesyConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([TesyClimate(entry.runtime_data)])


class TesyClimate(CoordinatorEntity[TesyCoordinator], ClimateEntity):
    """The convector."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_translation_key = "convector"
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TURN_ON
    )
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_preset_modes = list(PRESET_TO_MODE)
    _attr_min_temp = 10
    _attr_max_temp = 30
    _attr_target_temperature_step = 1

    def __init__(self, coordinator: TesyCoordinator) -> None:
        super().__init__(coordinator)
        # entry id as unique id: stays stable when the convector gets a new IP
        self._attr_unique_id = coordinator.config_entry.entry_id
        self._attr_device_info = coordinator.device_info

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if cloud := self.coordinator.cloud:
            self.async_on_remove(cloud.add_listener(self.async_write_ha_state))
        if self.coordinator.temperature_entity:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [self.coordinator.temperature_entity], self._room_temp_changed
                )
            )

    @callback
    def _room_temp_changed(self, _event: Event) -> None:
        self.async_write_ha_state()

    @property
    def _mode(self) -> str | None:
        return self.coordinator.value("setMode", "name")

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.HEAT if self.coordinator.value("onOff", "status") == "on" else HVACMode.OFF

    @property
    def preset_mode(self) -> str | None:
        return MODE_TO_PRESET.get(self._mode)

    @property
    def native_target_temperature(self) -> float | None:
        command = MODE_TEMP_COMMAND.get(self._mode)
        return self.coordinator.value(command, "temp") if command else None

    @property
    def _internal_temperature(self) -> float | None:
        """The convector's own reading from the cloud, while the cloud is connected."""
        cloud = self.coordinator.cloud
        return cloud.current_temp if cloud and cloud.connected else None

    @property
    def hvac_action(self) -> HVACAction | None:
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        cloud = self.coordinator.cloud
        if not cloud or not cloud.connected or cloud.heating is None:
            return None  # local API alone can't tell
        return HVACAction.HEATING if cloud.heating else HVACAction.IDLE

    @property
    def native_current_temperature(self) -> float | None:
        if not (entity_id := self.coordinator.temperature_entity):
            return self._internal_temperature
        state = self.hass.states.get(entity_id)
        try:
            return float(state.state)
        except (AttributeError, TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {"device_mode": self._mode}
        if self.coordinator.cloud:
            attrs["internal_temperature"] = self._internal_temperature
        return attrs

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        status = "on" if hvac_mode == HVACMode.HEAT else "off"
        await self.coordinator.async_command("onOff", {"status": status})

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        await self.coordinator.async_command("setMode", {"name": PRESET_TO_MODE[preset_mode]})

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        temp = int(round(temp))
        mode = self._mode
        if mode == "comfort":
            await self.coordinator.async_command("setComfortTemp", {"temp": temp})
        elif mode in ("eco", "sleep"):
            command = MODE_TEMP_COMMAND[mode]
            await self.coordinator.async_command(
                command, {"temp": temp, "time": self.coordinator.value(command, "time")}
            )
        else:
            # in program mode a manual setpoint switches back to normal heating
            if mode != "heating":
                await self.coordinator.async_command("setMode", {"name": "heating"})
            await self.coordinator.async_command("setTemp", {"temp": temp})
