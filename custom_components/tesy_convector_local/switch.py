"""On/off device features. Each is a {"status": "on"|"off"} command."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import TesyConfigEntry, TesyCoordinator


@dataclass(frozen=True, kw_only=True)
class TesySwitchDescription(SwitchEntityDescription):
    command: str


SWITCHES = (
    TesySwitchDescription(key="adaptive_start", translation_key="adaptive_start",
                          command="setAdaptiveStart", entity_category=EntityCategory.CONFIG),
    TesySwitchDescription(key="open_window_detection", translation_key="open_window_detection",
                          command="setOpenedWindow", entity_category=EntityCategory.CONFIG),
    TesySwitchDescription(key="anti_frost", translation_key="anti_frost",
                          command="setAntiFrost", entity_category=EntityCategory.CONFIG),
    # AirSafe UVA LEDs; only "AS" models have them, others accept and ignore it
    TesySwitchDescription(key="uv", translation_key="uv", command="setUV",
                          entity_category=EntityCategory.CONFIG),
    TesySwitchDescription(key="child_lock", translation_key="child_lock",
                          command="setLockDevice", entity_category=EntityCategory.CONFIG),
    TesySwitchDescription(key="delayed_start", translation_key="delayed_start",
                          command="setDelayedStart"),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: TesyConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(TesySwitch(coordinator, description) for description in SWITCHES)


class TesySwitch(CoordinatorEntity[TesyCoordinator], SwitchEntity):
    _attr_has_entity_name = True
    entity_description: TesySwitchDescription

    def __init__(self, coordinator: TesyCoordinator, description: TesySwitchDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{description.command}"
        self._attr_device_info = coordinator.device_info

    @property
    def is_on(self) -> bool:
        return self.coordinator.value(self.entity_description.command, "status") == "on"

    async def _set(self, status: str) -> None:
        command = self.entity_description.command
        payload: dict[str, Any] = {"status": status}
        if command == "setDelayedStart":
            # the device stores time (minutes) and temperature with the status
            payload["time"] = self.coordinator.value(command, "time")
            payload["temp"] = self.coordinator.value(command, "temp")
        await self.coordinator.async_command(command, payload)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set("on")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set("off")
