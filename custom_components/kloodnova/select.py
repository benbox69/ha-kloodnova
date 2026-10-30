"""Réglages globaux de la centrale : mode de fonctionnement et ventilation."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import KoolnovaConfigEntry
from .api import Mode, Speed, Topic
from .coordinator import KoolnovaCoordinator
from .entity import KoolnovaProjectEntity

DEFAULT_MODES = (Mode.COOL, Mode.HEAT)


def _option(value: Mode | Speed) -> str:
    return value.name.lower()


def available_modes(topic: Topic | None) -> list[Mode]:
    """Modes activés sur cette centrale.

    Hypothèse déduite d'une installation réelle : la configuration `TopicModes`
    est une chaîne de drapeaux indexée par numéro de mode ("0110000" = modes 1
    et 2 actifs). Repli sur climatisation/chauffage si absente.
    """
    flags = (topic.configurations.get("TopicModes") if topic else None) or ""
    modes = [
        mode
        for mode in Mode
        if int(mode.value) < len(flags) and flags[int(mode.value)] == "1"
    ]
    return modes or list(DEFAULT_MODES)


async def async_setup_entry(
    hass: HomeAssistant, entry: KoolnovaConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    entities: list[SelectEntity] = []
    for coordinator in entry.runtime_data.coordinators.values():
        entities.append(KoolnovaModeSelect(coordinator))
        entities.append(KoolnovaFanSelect(coordinator))
    async_add_entities(entities)


class KoolnovaModeSelect(KoolnovaProjectEntity, SelectEntity):
    """Mode de fonctionnement de la centrale (concerne toutes les zones)."""

    _attr_translation_key = "mode"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: KoolnovaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"kloodnova_project_{coordinator.project_id}_mode"

    @property
    def available(self) -> bool:
        return super().available and self.online

    @property
    def options(self) -> list[str]:
        modes = available_modes(self.topic)
        # Le mode courant reste sélectionnable même s'il n'est pas déclaré actif.
        current = self._current_mode()
        if current is not None and current not in modes:
            modes.append(current)
        return [_option(mode) for mode in modes]

    @property
    def current_option(self) -> str | None:
        current = self._current_mode()
        return _option(current) if current is not None else None

    def _current_mode(self) -> Mode | None:
        topic = self.topic
        try:
            return Mode(topic.mode) if topic and topic.mode else None
        except ValueError:
            return None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_mode(Mode[option.upper()])


class KoolnovaFanSelect(KoolnovaProjectEntity, SelectEntity):
    """Ventilation de la centrale : un seul réglage pour toutes les zones."""

    _attr_translation_key = "fan_speed"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = [_option(speed) for speed in Speed]

    def __init__(self, coordinator: KoolnovaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"kloodnova_project_{coordinator.project_id}_fan_speed"

    @property
    def available(self) -> bool:
        return super().available and self.online and bool(self.coordinator.data)

    @property
    def current_option(self) -> str | None:
        for zone in (self.coordinator.data or {}).values():
            try:
                return _option(Speed(zone.speed)) if zone.speed else None
            except ValueError:
                return None
        return None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_fan_speed(Speed[option.upper()])
