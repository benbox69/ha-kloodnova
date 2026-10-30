"""Kloodnova : intégration Home Assistant pour les systèmes de climatisation par zones Koolnova (cloud)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
import re

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import slugify

from .api import KoolnovaApi, KoolnovaAuthError, KoolnovaError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_PROJECTS,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import KoolnovaCoordinator
from .entity import project_device_identifier, project_device_info

_LOGGER = logging.getLogger(__name__)


@dataclass
class KoolnovaData:
    """Données d'exécution d'une entrée."""

    api: KoolnovaApi
    coordinators: dict[int, KoolnovaCoordinator]
    selection: dict[str, list[int]]  # projets/zones choisis au démarrage


type KoolnovaConfigEntry = ConfigEntry[KoolnovaData]


async def async_setup_entry(hass: HomeAssistant, entry: KoolnovaConfigEntry) -> bool:
    session = async_get_clientsession(hass)

    def _store_tokens(access: str, refresh: str | None) -> None:
        # Persiste les tokens renouvelés, sans toucher aux options (pas de rechargement).
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_ACCESS_TOKEN: access,
                CONF_REFRESH_TOKEN: refresh or entry.data.get(CONF_REFRESH_TOKEN),
            },
        )

    api = KoolnovaApi(
        session,
        email=entry.data[CONF_EMAIL],
        password=entry.data[CONF_PASSWORD],
        access_token=entry.data.get(CONF_ACCESS_TOKEN),
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
        on_tokens=_store_tokens,
    )

    try:
        projects = {project.id: project for project in await api.get_projects()}
    except KoolnovaAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except KoolnovaError as err:
        raise ConfigEntryNotReady(str(err)) from err

    selection: dict[str, list[int]] = {
        str(pid): list(zones) for pid, zones in entry.options.get(CONF_PROJECTS, {}).items()
    }

    coordinators: dict[int, KoolnovaCoordinator] = {}
    device_registry = dr.async_get(hass)
    for pid_str in selection:
        pid = int(pid_str)
        project = projects.get(pid)
        if project is None:
            _LOGGER.warning("Projet Koolnova %s introuvable sur ce compte, ignoré", pid)
            continue
        coordinator = KoolnovaCoordinator(hass, entry, api, pid, project.name or f"Projet {pid}")
        await coordinator.async_config_entry_first_refresh()
        coordinators[pid] = coordinator

        # L'appareil « centrale » doit exister avant les zones qui y sont rattachées.
        info = project_device_info(coordinator)
        device_registry.async_get_or_create(config_entry_id=entry.entry_id, **info)

    _async_cleanup_registries(hass, entry, selection, coordinators)
    _async_migrate_entity_ids(hass, entry, coordinators)

    entry.runtime_data = KoolnovaData(api=api, coordinators=coordinators, selection=selection)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


_ZONE_UID = re.compile(r"_zone_(\d+)$")
_PROJECT_UID = re.compile(r"_project_(\d+)_")


def _async_cleanup_registries(
    hass: HomeAssistant,
    entry: ConfigEntry,
    selection: dict[str, list[int]],
    coordinators: dict[int, KoolnovaCoordinator],
) -> None:
    """Un seul appareil par projet ; nettoie ce qui n'est plus sélectionné.

    - les anciens appareils « zone » disparaissent : leurs thermostats encore
      sélectionnés sont rattachés à l'appareil du projet (identifiant conservé) ;
    - les entités des zones ou projets désélectionnés sont supprimées.
    """
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    project_devices = {}
    for pid in coordinators:
        device = device_registry.async_get_device(identifiers={project_device_identifier(pid)})
        if device is not None:
            project_devices[pid] = device
    zone_project = {
        zone_id: int(pid_str)
        for pid_str, zone_ids in selection.items()
        if int(pid_str) in coordinators
        for zone_id in zone_ids
    }
    keep_devices = {f"project_{pid}" for pid in coordinators}

    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        ours = {ident for domain, ident in device.identifiers if domain == DOMAIN}
        if not ours or ours & keep_devices:
            continue
        for entity in er.async_entries_for_device(
            entity_registry, device.id, include_disabled_entities=True
        ):
            match = _ZONE_UID.search(entity.unique_id or "")
            target = project_devices.get(zone_project.get(int(match.group(1)))) if match else None
            if target is not None:
                entity_registry.async_update_entity(entity.entity_id, device_id=target.id)
        _LOGGER.debug("Suppression de l'appareil Koolnova obsolète : %s", device.name)
        device_registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)

    for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        uid = entity.unique_id or ""
        if (match := _ZONE_UID.search(uid)) and int(match.group(1)) not in zone_project:
            entity_registry.async_remove(entity.entity_id)
        elif (match := _PROJECT_UID.search(uid)) and int(match.group(1)) not in coordinators:
            entity_registry.async_remove(entity.entity_id)


# suffixe d'identifiant des entités de projet : clé de l'unique_id -> fin de l'entity_id
_PROJECT_ENTITY_SUFFIX = {
    "mode": "mode",
    "fan_speed": "fan",
    "stop": "general_stop",
    "connected": "connected",
    "rssi": "wi_fi_signal",
}


def _async_migrate_entity_ids(
    hass: HomeAssistant, entry: ConfigEntry, coordinators: dict[int, KoolnovaCoordinator]
) -> None:
    """Renomme `climate.office` / `select.maison_mode`... en `kloodnova_<projet>_…`.

    Seuls les identifiants générés automatiquement par une version précédente sont
    touchés : un identifiant personnalisé par l'utilisateur est laissé tel quel.
    """
    registry = er.async_get(hass)
    by_uid = {e.unique_id: e for e in er.async_entries_for_config_entry(registry, entry.entry_id)}

    def rename(entity: er.RegistryEntry | None, old_ids: set[str], new_object_id: str) -> None:
        if entity is None:
            return
        domain, object_id = entity.entity_id.split(".", 1)
        new_id = f"{domain}.{new_object_id}"
        if object_id in old_ids and registry.async_get(new_id) is None:
            _LOGGER.debug("Entité renommée : %s -> %s", entity.entity_id, new_id)
            registry.async_update_entity(entity.entity_id, new_entity_id=new_id)

    for pid, coordinator in coordinators.items():
        project = slugify(coordinator.project_name)
        for zone_id, zone in (coordinator.data or {}).items():
            zone_slug = slugify(zone.name)
            rename(
                by_uid.get(f"kloodnova_zone_{zone_id}"),
                {zone_slug, f"{project}_{zone_slug}", f"koolnova_{project}_{zone_slug}"},
                f"kloodnova_{project}_{zone_slug}",
            )
        for key, suffix in _PROJECT_ENTITY_SUFFIX.items():
            rename(
                by_uid.get(f"kloodnova_project_{pid}_{key}"),
                {f"{project}_{suffix}", f"koolnova_{project}_{suffix}"},
                f"kloodnova_{project}_{suffix}",
            )


async def _async_options_updated(hass: HomeAssistant, entry: KoolnovaConfigEntry) -> None:
    """Réagit aux options : recharge si le choix projets/zones change, sinon applique
    l'intervalle à chaud (et ignore les simples mises à jour de tokens)."""
    current = {str(p): list(z) for p, z in entry.options.get(CONF_PROJECTS, {}).items()}
    if current != entry.runtime_data.selection:
        await hass.config_entries.async_reload(entry.entry_id)
        return
    interval = timedelta(seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
    for coordinator in entry.runtime_data.coordinators.values():
        if coordinator.update_interval != interval:
            coordinator.update_interval = interval


async def async_unload_entry(hass: HomeAssistant, entry: KoolnovaConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
