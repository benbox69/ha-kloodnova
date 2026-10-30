"""Diagnostic : la centrale est-elle connectée au cloud ?"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import KoolnovaConfigEntry
from .coordinator import KoolnovaCoordinator
from .entity import KoolnovaProjectEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: KoolnovaConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(
        KoolnovaConnectedSensor(coordinator)
        for coordinator in entry.runtime_data.coordinators.values()
    )


class KoolnovaConnectedSensor(KoolnovaProjectEntity, BinarySensorEntity):
    _attr_translation_key = "connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: KoolnovaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"kloodnova_project_{coordinator.project_id}_connected"

    @property
    def is_on(self) -> bool | None:
        topic = self.topic
        return topic.is_online if topic else None
