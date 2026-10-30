"""Coordinateur de données : un par projet Koolnova."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    KoolnovaApi,
    KoolnovaAuthError,
    KoolnovaError,
    Mode,
    Speed,
    Topic,
    Zone,
)
from .const import CONF_SCAN_INTERVAL, CONFIRM_REFRESH_DELAY, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class KoolnovaCoordinator(DataUpdateCoordinator[dict[int, Zone]]):
    """Relève toutes les zones d'un projet en une seule requête.

    `data` est un dict {zone_id: Zone}. Chaque zone embarque l'état de la
    centrale dans `zone.topic` (mode et arrêt globaux, connexion...).
    """

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: KoolnovaApi,
        project_id: int,
        project_name: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {project_id}",
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.api = api
        self.project_id = project_id
        self.project_name = project_name
        self._cancel_confirm: CALLBACK_TYPE | None = None

    async def _async_update_data(self) -> dict[int, Zone]:
        try:
            zones = await self.api.get_zones(self.project_id)
        except KoolnovaAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except KoolnovaError as err:
            raise UpdateFailed(str(err)) from err
        return {zone.id: zone for zone in zones}

    async def async_shutdown(self) -> None:
        if self._cancel_confirm:
            self._cancel_confirm()
            self._cancel_confirm = None
        await super().async_shutdown()

    @property
    def topic(self) -> Topic | None:
        """La centrale du projet (identique dans toutes les zones)."""
        for zone in (self.data or {}).values():
            if zone.topic is not None:
                return zone.topic
        return None

    # ------------------------------------------------------------- commandes

    async def async_set_setpoint(self, zone_id: int, temperature: float) -> None:
        zone = await self._run(self.api.set_setpoint(zone_id, temperature))
        self._apply_zone(zone)

    async def async_set_speed(self, zone_id: int, speed: Speed) -> None:
        zone = await self._run(self.api.set_speed(zone_id, speed))
        self._apply_zone(zone)

    async def async_set_zone_power(self, zone_id: int, on: bool) -> None:
        zone = await self._run(self.api.set_zone_power(zone_id, on))
        self._apply_zone(zone)

    async def async_set_fan_speed(self, speed: Speed) -> None:
        """Ventilation GLOBALE : régler une zone règle toutes les zones (vérifié)."""
        if not self.data:
            raise HomeAssistantError("Aucune zone Koolnova disponible")
        zone_id = next(iter(self.data))
        await self._run(self.api.set_speed(zone_id, speed))
        data = {zid: replace(zone, speed=Speed(speed).value) for zid, zone in self.data.items()}
        self._publish(data)

    async def async_set_stop(self, stop: bool) -> None:
        """Arrêt GÉNÉRAL de la centrale (is_stop)."""
        topic = self.topic
        if topic is None:
            raise HomeAssistantError("Centrale Koolnova inconnue")
        updated = await self._run(self.api.set_stop(topic.id, stop))
        self._apply_topic(is_stop=updated.is_stop)

    async def async_set_mode(self, mode: Mode) -> None:
        """Mode GLOBAL : concerne toutes les zones de la centrale."""
        topic = self.topic
        if topic is None:
            raise HomeAssistantError("Centrale Koolnova inconnue")
        updated = await self._run(self.api.set_mode(topic.id, mode))
        self._apply_topic(mode=updated.mode)

    # --------------------------------------------------------------- interne

    async def _run(self, call: Awaitable[Any]) -> Any:
        """Exécute une commande et traduit les erreurs de l'API."""
        try:
            return await call
        except KoolnovaAuthError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError("Authentification Koolnova refusée") from err
        except KoolnovaError as err:
            raise HomeAssistantError(f"Commande Koolnova refusée : {err}") from err

    def _apply_zone(self, zone: Zone) -> None:
        """État optimiste : on publie la réponse de la commande tout de suite."""
        data = dict(self.data or {})
        old = data.get(zone.id)
        if zone.topic is None and old is not None:
            zone = replace(zone, topic=old.topic)
        data[zone.id] = zone
        self._publish(data)

    def _apply_topic(self, **changes: Any) -> None:
        data = {
            zid: replace(z, topic=replace(z.topic, **changes)) if z.topic else z
            for zid, z in (self.data or {}).items()
        }
        self._publish(data)

    def _publish(self, data: dict[int, Zone]) -> None:
        self.async_set_updated_data(data)
        # Le cloud suit la centrale physique : on confirme par une relecture différée.
        if self._cancel_confirm:
            self._cancel_confirm()
        self._cancel_confirm = async_call_later(
            self.hass, CONFIRM_REFRESH_DELAY, self._confirm_refresh
        )

    async def _confirm_refresh(self, _now: Any) -> None:
        self._cancel_confirm = None
        await self.async_request_refresh()
