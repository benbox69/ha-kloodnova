"""Tests de l'assistant : installation, erreurs, réauthentification, options."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.kloodnova.api import KoolnovaAuthError, KoolnovaConnectionError
from custom_components.kloodnova.const import CONF_PROJECTS, CONF_SCAN_INTERVAL, CONF_ZONES, DOMAIN

from .conftest import PROJECT_1, PROJECT_2, make_entry

CREDS = {CONF_EMAIL: "demo@example.com", CONF_PASSWORD: "secret"}


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})


async def test_full_flow(hass: HomeAssistant, fake_api) -> None:
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["step_id"] == "projects"
    # Les deux projets du compte sont proposés
    schema_key = next(k for k in result["data_schema"].schema if k == CONF_PROJECTS)
    assert set(schema_key.default()) == {str(PROJECT_1), str(PROJECT_2)}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PROJECTS: [str(PROJECT_1)]}
    )
    assert result["step_id"] == "zones"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ZONES: [f"{PROJECT_1}:300001", f"{PROJECT_1}:300002"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "demo@example.com"
    assert result["data"][CONF_EMAIL] == "demo@example.com"
    assert result["data"]["access_token"] == "access-xyz"
    assert result["data"]["refresh_token"] == "refresh-xyz"
    assert result["options"] == {CONF_PROJECTS: {str(PROJECT_1): [300001, 300002]}}
    assert result["result"].unique_id == "1"

    await hass.async_block_till_done()
    await hass.config_entries.async_unload(result["result"].entry_id)


async def test_two_projects(hass: HomeAssistant, fake_api) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PROJECTS: [str(PROJECT_1), str(PROJECT_2)]}
    )
    schema_key = next(k for k in result["data_schema"].schema if k == CONF_ZONES)
    # Avec plusieurs projets, le libellé indique le projet ; tout est coché par défaut
    assert len(schema_key.default()) == 4
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ZONES: [f"{PROJECT_1}:300003", f"{PROJECT_2}:60001"]}
    )
    assert result["options"] == {CONF_PROJECTS: {str(PROJECT_1): [300003], str(PROJECT_2): [60001]}}
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(result["result"].entry_id)


async def test_invalid_auth(hass: HomeAssistant, fake_api) -> None:
    fake_api.login_error = KoolnovaAuthError("bad")
    result = await hass.config_entries.flow.async_configure((await _start(hass))["flow_id"], CREDS)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_cannot_connect(hass: HomeAssistant, fake_api) -> None:
    fake_api.login_error = KoolnovaConnectionError("down")
    result = await hass.config_entries.flow.async_configure((await _start(hass))["flow_id"], CREDS)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_unknown_error(hass: HomeAssistant, fake_api) -> None:
    fake_api.login_error = RuntimeError("boom")
    result = await hass.config_entries.flow.async_configure((await _start(hass))["flow_id"], CREDS)
    assert result["errors"] == {"base": "unknown"}


async def test_no_project_on_account(hass: HomeAssistant, fake_api) -> None:
    fake_api.projects = []
    result = await hass.config_entries.flow.async_configure((await _start(hass))["flow_id"], CREDS)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "no_projects"


async def test_no_project_selected(hass: HomeAssistant, fake_api) -> None:
    result = await hass.config_entries.flow.async_configure((await _start(hass))["flow_id"], CREDS)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PROJECTS: []})
    assert result["step_id"] == "projects" and result["errors"] == {"base": "no_project"}


async def test_no_zone_selected(hass: HomeAssistant, fake_api) -> None:
    result = await hass.config_entries.flow.async_configure((await _start(hass))["flow_id"], CREDS)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PROJECTS: [str(PROJECT_1)]})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ZONES: []})
    assert result["step_id"] == "zones" and result["errors"] == {"base": "no_zone"}


async def test_already_configured(hass: HomeAssistant, fake_api) -> None:
    make_entry().add_to_hass(hass)
    result = await hass.config_entries.flow.async_configure((await _start(hass))["flow_id"], CREDS)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"


async def test_reauth(hass: HomeAssistant, fake_api) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    fake_api.login_error = KoolnovaAuthError("bad")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "wrong"})
    assert result["errors"] == {"base": "invalid_auth"}

    fake_api.login_error = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "new-secret"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new-secret"
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(entry.entry_id)


async def test_options_flow_changes_selection(hass: HomeAssistant, fake_api) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_SCAN_INTERVAL: 45})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {"selection", "interval"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "selection"}
    )
    assert result["step_id"] == "projects"
    # La sélection actuelle est pré-cochée
    key = next(k for k in result["data_schema"].schema if k == CONF_PROJECTS)
    assert key.default() == [str(PROJECT_1)]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_PROJECTS: [str(PROJECT_1), str(PROJECT_2)]}
    )
    assert result["step_id"] == "zones"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ZONES: [f"{PROJECT_1}:300003", f"{PROJECT_2}:60001"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert entry.options[CONF_PROJECTS] == {str(PROJECT_1): [300003], str(PROJECT_2): [60001]}
    assert entry.options[CONF_SCAN_INTERVAL] == 45  # l'intervalle déjà réglé est conservé
    # L'entrée a été rechargée avec la nouvelle sélection
    assert hass.states.get("climate.kloodnova_maison_salon") is not None
    assert hass.states.get("climate.kloodnova_chalet_mezzanine") is not None
    assert hass.states.get("climate.kloodnova_maison_office") is None

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
