"""Entités de base : toutes les entités d'un projet partagent UN seul appareil."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Topic, Zone
from .const import CONFIGURATION_URL, DEVICE_NAME_PREFIX, DOMAIN, MANUFACTURER
from .coordinator import KoolnovaCoordinator


def project_device_identifier(project_id: int) -> tuple[str, str]:
    return (DOMAIN, f"project_{project_id}")


def project_device_info(coordinator: KoolnovaCoordinator) -> DeviceInfo:
    topic = coordinator.topic
    return DeviceInfo(
        identifiers={project_device_identifier(coordinator.project_id)},
        name=f"{DEVICE_NAME_PREFIX} {coordinator.project_name}",
        manufacturer=MANUFACTURER,
        model=topic.device_reference if topic else None,
        configuration_url=CONFIGURATION_URL,
    )


class KoolnovaProjectEntity(CoordinatorEntity[KoolnovaCoordinator]):
    """Réglage ou diagnostic de la centrale d'un projet."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: KoolnovaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_device_info = project_device_info(coordinator)

    @property
    def topic(self) -> Topic | None:
        return self.coordinator.topic

    @property
    def online(self) -> bool:
        topic = self.topic
        return topic is not None and topic.is_online


class KoolnovaZoneEntity(KoolnovaProjectEntity):
    """Entité d'une zone : même appareil que le projet, nommée d'après la zone."""

    def __init__(self, coordinator: KoolnovaCoordinator, zone_id: int) -> None:
        super().__init__(coordinator)
        self._zone_id = zone_id
        zone = self.zone
        self._attr_name = zone.name if zone else f"Zone {zone_id}"

    @property
    def zone(self) -> Zone | None:
        return (self.coordinator.data or {}).get(self._zone_id)

    @property
    def available(self) -> bool:
        zone = self.zone
        if not super().available or zone is None:
            return False
        return zone.topic is None or zone.topic.is_online
