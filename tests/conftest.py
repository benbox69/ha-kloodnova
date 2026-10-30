"""Faux client Koolnova et fixtures communes."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

import pytest
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kloodnova.api import (
    KoolnovaAuthError,
    KoolnovaConnectionError,
    Mode,
    Project,
    Speed,
    Topic,
    Zone,
)
from custom_components.kloodnova.const import (
    CONF_ACCESS_TOKEN,
    CONF_PROJECTS,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)

pytest_plugins = "pytest_homeassistant_custom_component"

PROJECT_1 = 100001
PROJECT_2 = 400001
TOPIC_1 = 200001
TOPIC_2 = 500002


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def make_topic(topic_id: int, **kw) -> Topic:
    base = dict(
        id=topic_id, code="ABCD1234", name="project", mode=Mode.HEAT.value,
        is_stop=False, is_online=True, rssi=-66, device_reference="H19",
        configurations={"TopicModes": "0110000"},
    )
    return Topic(**{**base, **kw})


def make_zone(zone_id: int, name: str, topic: Topic, **kw) -> Zone:
    base = dict(
        id=zone_id, name=name, temperature=19.5, setpoint=22.0,
        speed=Speed.AUTO.value, status="02", topic=topic,
    )
    return Zone(**{**base, **kw})


class FakeApi:
    """Même interface que KoolnovaApi, sans réseau, avec un état modifiable."""

    def __init__(self) -> None:
        self.access_token = "access-xyz"
        self.refresh_token = "refresh-xyz"
        self.login_error: Exception | None = None
        self.projects_error: Exception | None = None
        self.calls: list[tuple] = []
        t1, t2 = make_topic(TOPIC_1), make_topic(TOPIC_2)
        self.projects = [
            Project(id=PROJECT_1, name="Maison", topic=t1),
            Project(id=PROJECT_2, name="Chalet", topic=t2),
        ]
        self.zones: dict[int, list[Zone]] = {
            PROJECT_1: [
                make_zone(300001, "Office", t1),
                make_zone(300002, "Chambre", t1, setpoint=18.0, status="03"),
                make_zone(300003, "Salon", t1),
            ],
            PROJECT_2: [make_zone(60001, "Mezzanine", t2)],
        }

    # -- état
    def _all(self) -> dict[int, Zone]:
        return {z.id: z for zs in self.zones.values() for z in zs}

    def _update_zone(self, zone_id: int, **changes) -> Zone:
        for zs in self.zones.values():
            for i, z in enumerate(zs):
                if z.id == zone_id:
                    zs[i] = replace(z, **changes)
                    return zs[i]
        raise KeyError(zone_id)

    def set_topic(self, project_id: int, **changes) -> None:
        for p in self.projects:
            if p.id == project_id:
                p.topic = replace(p.topic, **changes)
        self.zones[project_id] = [
            replace(z, topic=replace(z.topic, **changes)) for z in self.zones[project_id]
        ]

    # -- API
    async def login(self):
        if self.login_error:
            raise self.login_error
        return {"pk": 1, "username": "demo", "email": "demo@example.com"}

    async def get_projects(self):
        if self.projects_error:
            raise self.projects_error
        return list(self.projects)

    async def get_zones(self, project_id):
        if self.projects_error:
            raise self.projects_error
        return list(self.zones[project_id])

    async def set_setpoint(self, zone_id, temperature):
        self.calls.append(("setpoint", zone_id, temperature))
        return self._update_zone(zone_id, setpoint=temperature)

    async def set_speed(self, zone_id, speed):
        """La ventilation est GLOBALE : le cloud l'applique à toutes les zones du projet."""
        self.calls.append(("speed", zone_id, speed))
        value = str(speed.value if hasattr(speed, "value") else speed)
        project = next(pid for pid, zs in self.zones.items() if any(z.id == zone_id for z in zs))
        self.zones[project] = [replace(z, speed=value) for z in self.zones[project]]
        return next(z for z in self.zones[project] if z.id == zone_id)

    async def set_zone_power(self, zone_id, on):
        self.calls.append(("power", zone_id, on))
        return self._update_zone(zone_id, status="03" if on else "02")

    async def set_stop(self, topic_id, stop):
        self.calls.append(("stop", topic_id, stop))
        pid = PROJECT_1 if topic_id == TOPIC_1 else PROJECT_2
        self.set_topic(pid, is_stop=bool(stop))
        return make_topic(topic_id, is_stop=bool(stop))

    async def set_mode(self, topic_id, mode):
        self.calls.append(("mode", topic_id, mode))
        pid = PROJECT_1 if topic_id == TOPIC_1 else PROJECT_2
        self.set_topic(pid, mode=str(mode.value if hasattr(mode, "value") else mode))
        return make_topic(topic_id, mode=str(mode.value if hasattr(mode, "value") else mode))


@pytest.fixture
def fake_api():
    api = FakeApi()
    with (
        patch("custom_components.kloodnova.KoolnovaApi", return_value=api),
        patch("custom_components.kloodnova.config_flow.KoolnovaApi", return_value=api),
    ):
        yield api


def make_entry(selection: dict[str, list[int]] | None = None) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="1",
        title="demo@example.com",
        data={
            CONF_EMAIL: "demo@example.com",
            CONF_PASSWORD: "secret",
            CONF_ACCESS_TOKEN: "access-xyz",
            CONF_REFRESH_TOKEN: "refresh-xyz",
        },
        options={CONF_PROJECTS: selection if selection is not None else {str(PROJECT_1): [300001, 300002]}},
    )


@pytest.fixture
async def setup_entry(hass: HomeAssistant, fake_api):
    """Installe une entrée (projet 1, zones Office et Chambre) et la décharge à la fin."""
    entry = make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


__all__ = ["KoolnovaAuthError", "KoolnovaConnectionError"]
