"""Une entité climate par zone Koolnova sélectionnée."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.components.climate.const import (
    FAN_AUTO,
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import KoolnovaConfigEntry
from .api import Mode, Speed
from .const import MAX_TEMP, MIN_TEMP, TEMP_STEP
from .coordinator import KoolnovaCoordinator
from .entity import KoolnovaZoneEntity

_LOGGER = logging.getLogger(__name__)

FAN_BY_SPEED = {
    Speed.LOW.value: FAN_LOW,
    Speed.MEDIUM.value: FAN_MEDIUM,
    Speed.HIGH.value: FAN_HIGH,
    Speed.AUTO.value: FAN_AUTO,
}
SPEED_BY_FAN = {fan: Speed(speed) for speed, fan in FAN_BY_SPEED.items()}

# Le mode est global à la centrale ; les modes « plancher » sont rattachés au plus proche.
HVAC_BY_MODE = {
    Mode.COOL.value: HVACMode.COOL,
    Mode.HEAT.value: HVACMode.HEAT,
    Mode.FLOOR_HEAT.value: HVACMode.HEAT,
    Mode.FLOOR_COOL_AIR.value: HVACMode.COOL,
    Mode.FLOOR_HEAT_AIR.value: HVACMode.HEAT,
}
MODE_BY_HVAC = {HVACMode.COOL: Mode.COOL, HVACMode.HEAT: Mode.HEAT}


async def async_setup_entry(
    hass: HomeAssistant, entry: KoolnovaConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    entities: list[KoolnovaClimate] = []
    for pid_str, zone_ids in entry.runtime_data.selection.items():
        coordinator = entry.runtime_data.coordinators.get(int(pid_str))
        if coordinator is None:
            continue
        for zone_id in zone_ids:
            if zone_id not in coordinator.data:
                _LOGGER.warning("Zone Koolnova %s introuvable dans le projet %s", zone_id, pid_str)
                continue
            entities.append(KoolnovaClimate(coordinator, zone_id))
    async_add_entities(entities)


class KoolnovaClimate(KoolnovaZoneEntity, ClimateEntity):
    """Thermostat d'une zone.

    Le mode chaud/froid est GLOBAL : choisir chauffage ou climatisation sur une
    zone change le mode de la centrale, donc celui de toutes les zones.
    La ventilation est GLOBALE aussi : la changer ici la change pour toutes les
    zones (elle est également réglable au niveau du projet, ainsi que l'arrêt général).
    """

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL]
    _attr_fan_modes = [FAN_LOW, FAN_MEDIUM, FAN_HIGH, FAN_AUTO]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_target_temperature_step = TEMP_STEP

    def __init__(self, coordinator: KoolnovaCoordinator, zone_id: int) -> None:
        super().__init__(coordinator, zone_id)
        self._attr_unique_id = f"kloodnova_zone_{zone_id}"

    @property
    def current_temperature(self) -> float | None:
        zone = self.zone
        return zone.temperature if zone else None

    @property
    def current_humidity(self) -> float | None:
        zone = self.zone
        return zone.humidity if zone else None

    @property
    def target_temperature(self) -> float | None:
        zone = self.zone
        return zone.setpoint if zone else None

    @property
    def fan_mode(self) -> str | None:
        zone = self.zone
        return FAN_BY_SPEED.get(zone.speed) if zone and zone.speed else None

    @property
    def hvac_mode(self) -> HVACMode | None:
        zone = self.zone
        if zone is None:
            return None
        topic = zone.topic
        if not zone.is_on or (topic is not None and topic.is_stop):
            return HVACMode.OFF
        return HVAC_BY_MODE.get(topic.mode if topic else None, HVACMode.HEAT)

    # ------------------------------------------------------------- commandes

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.async_set_zone_power(self._zone_id, False)
            return
        wanted = MODE_BY_HVAC.get(hvac_mode)
        if wanted is None:
            raise ValueError(f"Mode non pris en charge : {hvac_mode}")
        topic = self.coordinator.topic
        current = HVAC_BY_MODE.get(topic.mode) if topic else None
        if current != hvac_mode:
            await self.coordinator.async_set_mode(wanted)  # global
        zone = self.zone
        if zone is None or not zone.is_on:
            await self.coordinator.async_set_zone_power(self._zone_id, True)

    async def async_turn_on(self) -> None:
        await self.coordinator.async_set_zone_power(self._zone_id, True)

    async def async_turn_off(self) -> None:
        await self.coordinator.async_set_zone_power(self._zone_id, False)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (hvac_mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
            await self.async_set_hvac_mode(HVACMode(hvac_mode))
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            await self.coordinator.async_set_setpoint(self._zone_id, temperature)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Ventilation globale : s'applique à toutes les zones du projet."""
        await self.coordinator.async_set_fan_speed(SPEED_BY_FAN[fan_mode])
