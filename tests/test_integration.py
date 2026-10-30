"""Tests de l'intégration : appareil unique, entités, commandes, migration."""

from __future__ import annotations

from homeassistant.components.climate import (
    ATTR_FAN_MODE,
    ATTR_HVAC_MODE,
    DOMAIN as CLIMATE,
    SERVICE_SET_FAN_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACMode,
)
from homeassistant.components.select import (
    ATTR_OPTION,
    DOMAIN as SELECT,
    SERVICE_SELECT_OPTION,
)
from homeassistant.components.switch import DOMAIN as SWITCH
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.kloodnova.api import (
    KoolnovaAuthError,
    KoolnovaConnectionError,
    Mode,
    Speed,
)
from custom_components.kloodnova.const import DOMAIN

from .conftest import PROJECT_1, TOPIC_1, make_entry

OFFICE, CHAMBRE, SALON = 300001, 300002, 300003
OFFICE_ID, CHAMBRE_ID = "climate.kloodnova_maison_office", "climate.kloodnova_maison_chambre"
MODE_ID, FAN_ID = "select.kloodnova_maison_mode", "select.kloodnova_maison_fan"
STOP_ID, ONLINE_ID = "switch.kloodnova_maison_general_stop", "binary_sensor.kloodnova_maison_connected"


def _calls(fake_api, kind: str) -> list[tuple]:
    return [c for c in fake_api.calls if c[0] == kind]


# ------------------------------------------------------------------ structure


async def test_one_device_per_project(hass: HomeAssistant, setup_entry) -> None:
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    devices = dr.async_entries_for_config_entry(device_registry, setup_entry.entry_id)
    assert [d.name for d in devices] == ["Kloodnova Maison"]  # pas d'appareil par zone

    entities = er.async_entries_for_config_entry(entity_registry, setup_entry.entry_id)
    assert {e.device_id for e in entities} == {devices[0].id}
    assert {e.domain for e in entities} == {"climate", "select", "switch", "binary_sensor", "sensor"}
    assert len([e for e in entities if e.domain == "climate"]) == 2


async def test_entities_created_for_selected_zones_only(hass: HomeAssistant, setup_entry) -> None:
    assert hass.states.get(OFFICE_ID) is not None
    assert hass.states.get(CHAMBRE_ID) is not None
    assert hass.states.get("climate.kloodnova_maison_salon") is None  # zone non sélectionnée
    for entity_id in (MODE_ID, FAN_ID, STOP_ID, ONLINE_ID):
        assert hass.states.get(entity_id) is not None, entity_id
    assert hass.states.get(ONLINE_ID).state == STATE_ON


async def test_climate_state(hass: HomeAssistant, setup_entry) -> None:
    office = hass.states.get(OFFICE_ID)
    assert office.state == HVACMode.OFF
    assert office.attributes["current_temperature"] == 19.5
    assert office.attributes[ATTR_TEMPERATURE] == 22.0
    assert office.attributes[ATTR_FAN_MODE] == "auto"
    assert set(office.attributes["hvac_modes"]) == {"off", "heat", "cool"}
    assert set(office.attributes["fan_modes"]) == {"low", "medium", "high", "auto"}

    chambre = hass.states.get(CHAMBRE_ID)
    assert chambre.state == HVACMode.HEAT  # allumée, centrale en chauffage
    assert chambre.attributes[ATTR_TEMPERATURE] == 18.0


async def test_settings_state(hass: HomeAssistant, setup_entry) -> None:
    mode = hass.states.get(MODE_ID)
    assert mode.state == "heat"
    assert mode.attributes["options"] == ["cool", "heat"]  # d'après TopicModes "0110000"
    fan = hass.states.get(FAN_ID)
    assert fan.state == "auto"
    assert fan.attributes["options"] == ["low", "medium", "high", "auto"]
    assert hass.states.get(STOP_ID).state == STATE_OFF


# ------------------------------------------------------------------- thermostat


async def test_set_temperature(hass: HomeAssistant, setup_entry, fake_api) -> None:
    await hass.services.async_call(
        CLIMATE, SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: OFFICE_ID, ATTR_TEMPERATURE: 23.5}, blocking=True,
    )
    assert ("setpoint", OFFICE, 23.5) in fake_api.calls
    assert hass.states.get(OFFICE_ID).attributes[ATTR_TEMPERATURE] == 23.5  # état optimiste


async def test_climate_fan_mode_is_global(hass: HomeAssistant, setup_entry, fake_api) -> None:
    """Régler la ventilation depuis UN thermostat la change pour tous (une seule commande)."""
    await hass.services.async_call(
        CLIMATE, SERVICE_SET_FAN_MODE,
        {ATTR_ENTITY_ID: CHAMBRE_ID, ATTR_FAN_MODE: "high"}, blocking=True,
    )
    speed_calls = _calls(fake_api, "speed")
    assert len(speed_calls) == 1 and speed_calls[0][2] == Speed.HIGH
    for entity_id in (OFFICE_ID, CHAMBRE_ID):
        assert hass.states.get(entity_id).attributes[ATTR_FAN_MODE] == "high"
    assert hass.states.get(FAN_ID).state == "high"


async def test_turn_off(hass: HomeAssistant, setup_entry, fake_api) -> None:
    await hass.services.async_call(
        CLIMATE, SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: CHAMBRE_ID, ATTR_HVAC_MODE: "off"}, blocking=True,
    )
    assert fake_api.calls == [("power", CHAMBRE, False)]
    assert hass.states.get(CHAMBRE_ID).state == HVACMode.OFF


async def test_heat_when_mode_already_heat_only_powers_on(hass: HomeAssistant, setup_entry, fake_api) -> None:
    await hass.services.async_call(
        CLIMATE, SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: OFFICE_ID, ATTR_HVAC_MODE: "heat"}, blocking=True,
    )
    assert fake_api.calls == [("power", OFFICE, True)]  # pas de changement du mode global
    assert hass.states.get(OFFICE_ID).state == HVACMode.HEAT


async def test_cool_changes_global_mode_then_powers_on(hass: HomeAssistant, setup_entry, fake_api) -> None:
    await hass.services.async_call(
        CLIMATE, SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: OFFICE_ID, ATTR_HVAC_MODE: "cool"}, blocking=True,
    )
    assert fake_api.calls == [("mode", TOPIC_1, Mode.COOL), ("power", OFFICE, True)]
    assert hass.states.get(OFFICE_ID).state == HVACMode.COOL
    assert hass.states.get(CHAMBRE_ID).state == HVACMode.COOL  # le mode est global
    assert hass.states.get(MODE_ID).state == "cool"


# -------------------------------------------------------------------- réglages


async def test_select_mode(hass: HomeAssistant, setup_entry, fake_api) -> None:
    await hass.services.async_call(
        SELECT, SERVICE_SELECT_OPTION, {ATTR_ENTITY_ID: MODE_ID, ATTR_OPTION: "cool"}, blocking=True
    )
    assert ("mode", TOPIC_1, Mode.COOL) in fake_api.calls
    assert hass.states.get(MODE_ID).state == "cool"


async def test_select_fan_speed_is_global(hass: HomeAssistant, setup_entry, fake_api) -> None:
    await hass.services.async_call(
        SELECT, SERVICE_SELECT_OPTION, {ATTR_ENTITY_ID: FAN_ID, ATTR_OPTION: "low"}, blocking=True
    )
    assert len(_calls(fake_api, "speed")) == 1  # une seule requête pour toutes les zones
    assert hass.states.get(FAN_ID).state == "low"
    assert hass.states.get(OFFICE_ID).attributes[ATTR_FAN_MODE] == "low"
    assert hass.states.get(CHAMBRE_ID).attributes[ATTR_FAN_MODE] == "low"


async def test_stop_switch(hass: HomeAssistant, setup_entry, fake_api) -> None:
    assert hass.states.get(CHAMBRE_ID).state == HVACMode.HEAT

    await hass.services.async_call(SWITCH, SERVICE_TURN_ON, {ATTR_ENTITY_ID: STOP_ID}, blocking=True)
    assert ("stop", TOPIC_1, True) in fake_api.calls
    assert hass.states.get(STOP_ID).state == STATE_ON
    assert hass.states.get(CHAMBRE_ID).state == HVACMode.OFF  # la centrale est à l'arrêt

    await hass.services.async_call(SWITCH, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: STOP_ID}, blocking=True)
    assert ("stop", TOPIC_1, False) in fake_api.calls
    assert hass.states.get(STOP_ID).state == STATE_OFF
    assert hass.states.get(CHAMBRE_ID).state == HVACMode.HEAT


async def test_stop_state_is_read_from_cloud(hass: HomeAssistant, fake_api) -> None:
    fake_api.set_topic(PROJECT_1, is_stop=True)
    entry = make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(STOP_ID).state == STATE_ON
    assert hass.states.get(CHAMBRE_ID).state == HVACMode.OFF  # allumée mais centrale à l'arrêt
    await hass.config_entries.async_unload(entry.entry_id)


async def test_offline_centrale_makes_entities_unavailable(hass: HomeAssistant, fake_api) -> None:
    fake_api.set_topic(PROJECT_1, is_online=False)
    entry = make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for entity_id in (OFFICE_ID, MODE_ID, FAN_ID, STOP_ID):
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE, entity_id
    assert hass.states.get(ONLINE_ID).state == STATE_OFF  # le diagnostic reste lisible
    await hass.config_entries.async_unload(entry.entry_id)


# ------------------------------------------------------------ démarrage, erreurs


async def test_setup_auth_failure(hass: HomeAssistant, fake_api) -> None:
    fake_api.projects_error = KoolnovaAuthError("expired")
    entry = make_entry()
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_setup_retry_when_cloud_unreachable(hass: HomeAssistant, fake_api) -> None:
    fake_api.projects_error = KoolnovaConnectionError("down")
    entry = make_entry()
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_unknown_selected_project_is_ignored(hass: HomeAssistant, fake_api) -> None:
    entry = make_entry({"999999": [1], str(PROJECT_1): [OFFICE]})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(OFFICE_ID) is not None
    await hass.config_entries.async_unload(entry.entry_id)


# -------------------------------------------------------------------- migration


async def test_migrates_old_per_zone_devices(hass: HomeAssistant, fake_api) -> None:
    """Ancien modèle : un appareil par zone. Les thermostats sélectionnés sont
    rattachés à l'appareil du projet ; le reste disparaît."""
    entry = make_entry()
    entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    old_office = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"zone_{OFFICE}")}, name="Office"
    )
    entity_registry.async_get_or_create(
        "climate", DOMAIN, f"kloodnova_zone_{OFFICE}", config_entry=entry,
        device_id=old_office.id, suggested_object_id="office",
    )
    old_salon = device_registry.async_get_or_create(  # zone qui n'est plus sélectionnée
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"zone_{SALON}")}, name="Salon"
    )
    entity_registry.async_get_or_create(
        "climate", DOMAIN, f"kloodnova_zone_{SALON}", config_entry=entry,
        device_id=old_salon.id, suggested_object_id="salon",
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    devices = dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    assert [d.name for d in devices] == ["Kloodnova Maison"]
    assert device_registry.async_get(old_office.id) is None
    assert device_registry.async_get(old_salon.id) is None

    # L'identifiant automatique « climate.office » devient kloodnova_<projet>_<zone>
    assert entity_registry.async_get("climate.office") is None
    migrated = entity_registry.async_get("climate.kloodnova_maison_office")
    assert migrated is not None and migrated.device_id == devices[0].id
    assert hass.states.get("climate.kloodnova_maison_office").state == HVACMode.OFF
    assert entity_registry.async_get("climate.salon") is None  # zone désélectionnée

    await hass.config_entries.async_unload(entry.entry_id)


# ------------------------------------------------------ intervalle de rafraîchissement


async def test_default_scan_interval_is_60s(hass: HomeAssistant, setup_entry) -> None:
    from datetime import timedelta

    coordinator = setup_entry.runtime_data.coordinators[PROJECT_1]
    assert coordinator.update_interval == timedelta(seconds=60)


async def test_scan_interval_option_is_used_at_startup(hass: HomeAssistant, fake_api) -> None:
    from datetime import timedelta

    entry = make_entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={**entry.options, "scan_interval": 30})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.coordinators[PROJECT_1].update_interval == timedelta(seconds=30)
    await hass.config_entries.async_unload(entry.entry_id)


async def test_scan_interval_changes_live_without_reload(hass: HomeAssistant, setup_entry) -> None:
    from datetime import timedelta

    coordinator = setup_entry.runtime_data.coordinators[PROJECT_1]
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "interval"}
    )
    assert result["step_id"] == "interval"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"scan_interval": 20})
    assert result["type"].value == "create_entry"
    await hass.async_block_till_done()

    assert setup_entry.options["scan_interval"] == 20
    assert setup_entry.options["projects"] == {str(PROJECT_1): [OFFICE, CHAMBRE]}  # sélection intacte
    assert setup_entry.state is ConfigEntryState.LOADED
    # Même coordinateur : appliqué à chaud, sans rechargement
    assert setup_entry.runtime_data.coordinators[PROJECT_1] is coordinator
    assert coordinator.update_interval == timedelta(seconds=20)


async def test_scan_interval_out_of_range_is_rejected(hass: HomeAssistant, setup_entry) -> None:
    import pytest
    from homeassistant.data_entry_flow import InvalidData

    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "interval"}
    )
    for bad in (5, 1000):
        with pytest.raises(InvalidData):
            await hass.config_entries.options.async_configure(result["flow_id"], {"scan_interval": bad})


# ------------------------------------------------- noms kloodnova_<projet>_… (migration)


async def test_old_project_entity_ids_are_renamed(hass: HomeAssistant, fake_api) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for domain, uid, object_id in (
        ("select", f"kloodnova_project_{PROJECT_1}_mode", "maison_mode"),
        ("switch", f"kloodnova_project_{PROJECT_1}_stop", "maison_general_stop"),
        ("select", f"kloodnova_project_{PROJECT_1}_fan_speed", "maison_fan"),
    ):
        registry.async_get_or_create(
            domain, DOMAIN, uid, config_entry=entry, suggested_object_id=object_id
        )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    for old, new in (
        ("select.maison_mode", "select.kloodnova_maison_mode"),
        ("switch.maison_general_stop", "switch.kloodnova_maison_general_stop"),
        ("select.maison_fan", "select.kloodnova_maison_fan"),
    ):
        assert registry.async_get(old) is None, old
        assert hass.states.get(new) is not None, new
    await hass.config_entries.async_unload(entry.entry_id)


async def test_customised_entity_id_is_kept(hass: HomeAssistant, fake_api) -> None:
    """Un identifiant choisi par l'utilisateur n'est jamais renommé."""
    entry = make_entry()
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "climate", DOMAIN, f"kloodnova_zone_{OFFICE}", config_entry=entry, suggested_object_id="bureau"
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("climate.bureau") is not None
    assert registry.async_get("climate.kloodnova_maison_office") is None
    await hass.config_entries.async_unload(entry.entry_id)


async def test_rename_skipped_when_target_id_is_taken(hass: HomeAssistant, fake_api) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "climate", DOMAIN, f"kloodnova_zone_{OFFICE}", config_entry=entry, suggested_object_id="office"
    )
    registry.async_get_or_create(  # l'identifiant cible est déjà pris par une autre entité
        "climate", "autre_integration", "x", suggested_object_id="kloodnova_maison_office"
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get("climate.office") is not None  # pas de collision, pas de renommage
    assert registry.async_get("climate.kloodnova_maison_office").platform == "autre_integration"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_intermediate_koolnova_prefixed_ids_are_renamed(hass: HomeAssistant, fake_api) -> None:
    """Les identifiants koolnova_<projet>_… d'une version intermédiaire passent en kloodnova_…"""
    entry = make_entry()
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "climate", DOMAIN, f"kloodnova_zone_{OFFICE}", config_entry=entry,
        suggested_object_id="koolnova_maison_office",
    )
    registry.async_get_or_create(
        "select", DOMAIN, f"kloodnova_project_{PROJECT_1}_mode", config_entry=entry,
        suggested_object_id="koolnova_maison_mode",
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    for old in ("climate.koolnova_maison_office", "select.koolnova_maison_mode"):
        assert registry.async_get(old) is None, old
    assert hass.states.get("climate.kloodnova_maison_office") is not None
    assert hass.states.get("select.kloodnova_maison_mode") is not None
    await hass.config_entries.async_unload(entry.entry_id)
