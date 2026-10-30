"""Catégories d'entité : réglages en « Configuration », diagnostics à part."""

from __future__ import annotations

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er


async def test_settings_are_in_configuration(hass: HomeAssistant, setup_entry) -> None:
    """Mode, ventilation et arrêt général sont des réglages : catégorie « config »."""
    registry = er.async_get(hass)
    for entity_id in (
        "select.kloodnova_maison_mode",
        "select.kloodnova_maison_fan",
        "switch.kloodnova_maison_general_stop",
    ):
        entry = registry.async_get(entity_id)
        assert entry is not None, entity_id
        assert entry.entity_category is EntityCategory.CONFIG, (entity_id, entry.entity_category)


async def test_thermostats_are_controls(hass: HomeAssistant, setup_entry) -> None:
    registry = er.async_get(hass)
    for entity_id in ("climate.kloodnova_maison_office", "climate.kloodnova_maison_chambre"):
        assert registry.async_get(entity_id).entity_category is None, entity_id


async def test_diagnostics_stay_diagnostic(hass: HomeAssistant, setup_entry) -> None:
    registry = er.async_get(hass)
    for entity_id in ("binary_sensor.kloodnova_maison_connected", "sensor.kloodnova_maison_wi_fi_signal"):
        assert registry.async_get(entity_id).entity_category is EntityCategory.DIAGNOSTIC, entity_id
