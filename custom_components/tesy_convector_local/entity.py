"""Base for entities fed by cloud telemetry."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.entity import Entity

from .cloud import TesyDeviceTelemetry
from .coordinator import TesyCoordinator


class TesyCloudEntity(Entity):
    """Pushed by MQTT messages; unavailable while the broker connection is down."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _key: str

    def __init__(self, coordinator: TesyCoordinator) -> None:
        self.cloud: TesyDeviceTelemetry = coordinator.cloud
        self._coordinator = coordinator
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_cloud_{self._key}"
        self._attr_device_info = coordinator.device_info

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.cloud.add_listener(self._handle_cloud_update))

    @callback
    def _handle_cloud_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self.cloud.connected

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ts = self.cloud.last_telemetry
        return {"last_telemetry": datetime.fromtimestamp(ts, UTC).isoformat() if ts else None}
