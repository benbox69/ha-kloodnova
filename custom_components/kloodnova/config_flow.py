"""Assistant de configuration : identifiants -> projets -> zones (et mêmes étapes en options)."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import KoolnovaApi, KoolnovaAuthError, KoolnovaError, Project, Zone
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_PROJECTS,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_ZONES,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


class KoolnovaSelectionMixin:
    """Étapes communes « choix des projets » puis « choix des zones ».

    Utilisé par l'installation initiale et par le menu Configurer. La classe
    hôte fournit `_api`, `_projects`, `_previous` et `_async_finish(selection)`.
    """

    _api: KoolnovaApi
    _projects: dict[int, Project]
    _previous: dict[str, list[int]]  # sélection déjà enregistrée (options)
    _chosen: list[int]
    _zones: dict[int, list[Zone]]

    async def async_step_projects(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            chosen = [int(pid) for pid in user_input[CONF_PROJECTS]]
            if not chosen:
                errors["base"] = "no_project"
            else:
                try:
                    self._zones = {pid: await self._api.get_zones(pid) for pid in chosen}
                except KoolnovaAuthError:
                    errors["base"] = "invalid_auth"
                except KoolnovaError:
                    errors["base"] = "cannot_connect"
                else:
                    self._chosen = chosen
                    return await self.async_step_zones()

        choices = {
            str(pid): project.name or f"Projet {pid}"
            for pid, project in self._projects.items()
        }
        default = [pid for pid in self._previous if pid in choices] or list(choices)
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="projects",
            data_schema=vol.Schema(
                {vol.Required(CONF_PROJECTS, default=default): cv.multi_select(choices)}
            ),
            errors=errors,
        )

    async def async_step_zones(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        several = len(self._chosen) > 1
        choices: dict[str, str] = {}
        for pid in self._chosen:
            project_name = self._projects[pid].name or f"Projet {pid}"
            for zone in self._zones.get(pid, []):
                label = f"{project_name} – {zone.name}" if several else zone.name
                choices[f"{pid}:{zone.id}"] = label

        if user_input is not None:
            selection: dict[str, list[int]] = {}
            for key in user_input[CONF_ZONES]:
                pid, zid = key.split(":")
                selection.setdefault(pid, []).append(int(zid))
            if not selection:
                errors["base"] = "no_zone"
            else:
                return await self._async_finish(selection)  # type: ignore[attr-defined]

        previous = {f"{pid}:{zid}" for pid, zids in self._previous.items() for zid in zids}
        default = [key for key in choices if key in previous] or list(choices)
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="zones",
            data_schema=vol.Schema(
                {vol.Required(CONF_ZONES, default=default): cv.multi_select(choices)}
            ),
            errors=errors,
        )


class KoolnovaConfigFlow(KoolnovaSelectionMixin, ConfigFlow, domain=DOMAIN):
    """Installation : identifiants, projets, zones."""

    VERSION = 1

    def __init__(self) -> None:
        self._email = ""
        self._password = ""
        self._previous = {}
        self._projects = {}
        self._zones = {}
        self._chosen = []

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> KoolnovaOptionsFlow:
        return KoolnovaOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._email = user_input[CONF_EMAIL].strip()
            self._password = user_input[CONF_PASSWORD]
            api = KoolnovaApi(
                async_get_clientsession(self.hass), email=self._email, password=self._password
            )
            try:
                user = await api.login()
                projects = await api.get_projects()
            except KoolnovaAuthError:
                errors["base"] = "invalid_auth"
            except KoolnovaError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Erreur inattendue pendant la connexion à Koolnova")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(str(user.get("pk") or self._email.lower()))
                self._abort_if_unique_id_configured()
                if not projects:
                    return self.async_abort(reason="no_projects")
                self._api = api
                self._projects = {project.id: project for project in projects}
                return await self.async_step_projects()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL, default=self._email): str,
                    vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
                }
            ),
            errors=errors,
        )

    async def _async_finish(self, selection: dict[str, list[int]]) -> ConfigFlowResult:
        return self.async_create_entry(
            title=self._email,
            data={
                CONF_EMAIL: self._email,
                CONF_PASSWORD: self._password,
                CONF_ACCESS_TOKEN: self._api.access_token,
                CONF_REFRESH_TOKEN: self._api.refresh_token,
            },
            options={CONF_PROJECTS: selection},
        )

    # ------------------------------------------------------------- réauth

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            api = KoolnovaApi(
                async_get_clientsession(self.hass),
                email=entry.data[CONF_EMAIL],
                password=user_input[CONF_PASSWORD],
            )
            try:
                await api.login()
            except KoolnovaAuthError:
                errors["base"] = "invalid_auth"
            except KoolnovaError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_ACCESS_TOKEN: api.access_token,
                        CONF_REFRESH_TOKEN: api.refresh_token,
                    },
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR}),
            description_placeholders={"email": entry.data[CONF_EMAIL]},
            errors=errors,
        )


class KoolnovaOptionsFlow(KoolnovaSelectionMixin, OptionsFlow):
    """« Configurer » : projets et zones, ou intervalle de rafraîchissement."""

    def __init__(self) -> None:
        self._previous = {}
        self._projects = {}
        self._zones = {}
        self._chosen = []

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["selection", "interval"])

    async def async_step_selection(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self.config_entry
        self._api = KoolnovaApi(
            async_get_clientsession(self.hass),
            email=entry.data[CONF_EMAIL],
            password=entry.data[CONF_PASSWORD],
            access_token=entry.data.get(CONF_ACCESS_TOKEN),
            refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
        )
        try:
            self._projects = {p.id: p for p in await self._api.get_projects()}
        except KoolnovaAuthError:
            return self.async_abort(reason="invalid_auth")
        except KoolnovaError:
            return self.async_abort(reason="cannot_connect")
        self._previous = {
            str(pid): list(zones) for pid, zones in entry.options.get(CONF_PROJECTS, {}).items()
        }
        return await self.async_step_projects()

    async def async_step_interval(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        options = self.config_entry.options
        if user_input is not None:
            return self.async_create_entry(
                data={**options, CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])}
            )
        current = options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        return self.async_show_form(
            step_id="interval",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                            step=5,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="s",
                        )
                    )
                }
            ),
        )

    async def _async_finish(self, selection: dict[str, list[int]]) -> ConfigFlowResult:
        # Conserve les autres options (intervalle) en ne remplaçant que la sélection.
        return self.async_create_entry(
            data={**self.config_entry.options, CONF_PROJECTS: selection}
        )
