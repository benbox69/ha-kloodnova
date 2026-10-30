"""Test de bout en bout contre le VRAI cloud Koolnova (lecture seule, facultatif).

    KOOLNOVA_EMAIL=... KOOLNOVA_PASSWORD=... .venv-ha/bin/python -m pytest tests/test_live.py -s

Ignoré tant que KOOLNOVA_EMAIL n'est pas défini. Ne modifie rien sur l'installation.
"""

from __future__ import annotations

import os

import pytest
import pytest_socket
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.kloodnova.const import CONF_PROJECTS, CONF_ZONES, DOMAIN

pytestmark = pytest.mark.skipif(
    not os.environ.get("KOOLNOVA_EMAIL"), reason="KOOLNOVA_EMAIL non défini"
)


@pytest.fixture
def expected_lingering_timers() -> bool:
    """La vraie connexion TLS laisse un minuteur de fermeture SSL : attendu ici."""
    return True


async def test_live_install_and_read(hass: HomeAssistant) -> None:
    pytest_socket._remove_restrictions()  # le plugin HA bloque le réseau par défaut
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: os.environ["KOOLNOVA_EMAIL"], CONF_PASSWORD: os.environ["KOOLNOVA_PASSWORD"]},
    )
    assert result["step_id"] == "projects", result
    key = next(k for k in result["data_schema"].schema if k == CONF_PROJECTS)
    all_projects = key.default()
    print("\nprojets proposés :", result["data_schema"].schema[key].options)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PROJECTS: all_projects}
    )
    assert result["step_id"] == "zones", result
    key = next(k for k in result["data_schema"].schema if k == CONF_ZONES)
    print("zones proposées  :", result["data_schema"].schema[key].options)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ZONES: key.default()}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    await hass.async_block_till_done()

    climates = hass.states.async_all("climate")
    print(f"{len(climates)} thermostat(s) :")
    for s in climates:
        a = s.attributes
        print(f"  {s.entity_id:32} {s.state:5} mesure {a.get('current_temperature')} "
              f"consigne {a.get('temperature')} ventilation {a.get('fan_mode')}")
    assert climates, "aucun thermostat créé"
    assert all(s.state != "unavailable" for s in climates)

    for domain in ("select", "switch", "binary_sensor"):
        for s in hass.states.async_all(domain):
            print(f"  {s.entity_id:32} {s.state}")

    from homeassistant.helpers import device_registry as dr

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), result["result"].entry_id)
    print("appareils :", [d.name for d in devices])
    assert len(devices) == len(all_projects), "un seul appareil par projet attendu"

    await hass.config_entries.async_unload(result["result"].entry_id)
    await hass.async_block_till_done()
