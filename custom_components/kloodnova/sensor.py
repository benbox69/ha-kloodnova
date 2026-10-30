"""Diagnostic : puissance du signal Wi-Fi de la centrale."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, SIGNAL_STRENGTH_DECIBELS_MILLIWATT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import KoolnovaConfigEntry
from .coordinator import KoolnovaCoordinator
from .entity import KoolnovaProjectEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: KoolnovaConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(
        KoolnovaSignalSensor(coordinator)
        for coordinator in entry.runtime_data.coordinators.values()
    )


class KoolnovaSignalSensor(KoolnovaProjectEntity, SensorEntity):
    _attr_translation_key = "rssi"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: KoolnovaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"kloodnova_project_{coordinator.project_id}_rssi"

    @property
    def native_value(self) -> int | None:
        topic = self.topic
        return topic.rssi if topic else None
