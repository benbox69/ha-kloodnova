"""Arrêt général de la climatisation (is_stop)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
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
        KoolnovaStopSwitch(coordinator)
        for coordinator in entry.runtime_data.coordinators.values()
    )


class KoolnovaStopSwitch(KoolnovaProjectEntity, SwitchEntity):
    """Activé = la centrale est à l'arrêt (équivalent du bouton STOP de l'app)."""

    _attr_translation_key = "stop"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:stop-circle-outline"

    def __init__(self, coordinator: KoolnovaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"kloodnova_project_{coordinator.project_id}_stop"

    @property
    def available(self) -> bool:
        return super().available and self.online

    @property
    def is_on(self) -> bool | None:
        topic = self.topic
        return topic.is_stop if topic else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_stop(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_stop(False)
