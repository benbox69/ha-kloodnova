#!/usr/bin/env python3
"""Teste koolnova_api.py contre le vrai cloud Koolnova.

    pip install aiohttp
    export KOOLNOVA_EMAIL=... KOOLNOVA_PASSWORD=...      # sinon demandés au clavier
    python test_koolnova_api.py                  # lecture seule
    python test_koolnova_api.py --write          # + écritures réversibles
    python test_koolnova_api.py --write --with-stop   # + arrêt global (à surveiller)

Les écritures sont réversibles : instantané avant, restauration dans un
`finally`, puis vérification champ par champ. Elles ciblent une zone ÉTEINTE
(--zone-id pour en choisir une) ; le test ON abaisse d'abord la consigne sous la
température mesurée pour ne pas déclencher de chauffe. Le test de mode global
n'est joué que si toutes les zones sont éteintes.

Code retour : 0 si tout passe, 1 sinon.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
import time
from collections.abc import Awaitable, Callable

import aiohttp

from koolnova_api import (
    KoolnovaApi,
    KoolnovaAuthError,
    Mode,
    Speed,
    Zone,
)

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


class Report:
    def __init__(self) -> None:
        self.passed = self.failed = self.skipped = 0

    def ok(self, label: str, detail: object = "") -> None:
        self.passed += 1
        print(f"  {GREEN}✔{RESET} {label} {DIM}{detail}{RESET}")

    def fail(self, label: str, detail: object = "") -> None:
        self.failed += 1
        print(f"  {RED}✘ {label}{RESET} {detail}")

    def skip(self, label: str, why: str) -> None:
        self.skipped += 1
        print(f"  {YELLOW}- {label}{RESET} {DIM}(ignoré : {why}){RESET}")

    async def check(self, label: str, coro: Awaitable, show: Callable = lambda r: "") -> object:
        """Exécute `coro`; ✔ si pas d'exception, sinon ✘. Retourne le résultat ou None."""
        try:
            result = await coro
        except Exception as err:  # noqa: BLE001 - on veut tout rapporter
            self.fail(label, f"{type(err).__name__}: {err}")
            return None
        self.ok(label, show(result))
        return result


def section(title: str) -> None:
    print(f"\n{title}")


def new_api(session: aiohttp.ClientSession, email: str, password: str, **kw) -> KoolnovaApi:
    return KoolnovaApi(session, email=email, password=password, **kw)


# --------------------------------------------------------------------- lecture


async def read_tests(session, email, password, rep: Report):
    section("Authentification")
    events: list[str] = []
    api = new_api(session, email, password, on_tokens=lambda a, r: events.append("tokens"))
    user = await rep.check("login", api.login(), lambda u: f"utilisateur {u.get('username')}")
    if user is None:
        return api, None, []
    rep.ok("tokens reçus", f"access {len(api.access_token or '')} car., refresh {len(api.refresh_token or '')} car.") \
        if api.access_token and api.refresh_token else rep.fail("tokens reçus")
    rep.ok("callback on_tokens appelé", events) if events else rep.fail("callback on_tokens appelé")

    old = api.access_token
    await rep.check("refresh du token", api.refresh())
    if api.access_token:
        rep.ok("access token renouvelé", "nouveau jeton" if api.access_token != old else "même jeton (rotation absente)")

    # Client sans mot de passe : il doit fonctionner avec les seuls tokens.
    tok_only = KoolnovaApi(session, access_token=api.access_token, refresh_token=api.refresh_token)
    await rep.check("client avec tokens seuls (sans mot de passe)", tok_only.get_me(), lambda m: m.get("email"))

    # Token d'accès corrompu : le client doit se rétablir seul (refresh puis login).
    broken = KoolnovaApi(
        session, email=email, password=password,
        access_token="x.y.z", refresh_token=api.refresh_token,
    )
    await rep.check("reprise après access token invalide", broken.get_me(), lambda m: m.get("email"))

    bad = KoolnovaApi(session, email=email, password="mot-de-passe-faux-test")
    try:
        await bad.login()
        rep.fail("mauvais mot de passe rejeté", "login accepté !")
    except KoolnovaAuthError as err:
        rep.ok("mauvais mot de passe rejeté", f"KoolnovaAuthError: {err}")
    except Exception as err:  # noqa: BLE001
        rep.fail("mauvais mot de passe rejeté", f"{type(err).__name__}: {err}")

    section("Lecture")
    await rep.check("GET /users/me/", api.get_me(), lambda m: f"{m.get('num_projects')} projet(s), {m.get('num_sensors')} zone(s)")
    projects = await rep.check("GET /projects/", api.get_projects(), lambda p: ", ".join(f"{x.name} ({x.id})" for x in p)) or []
    if not projects:
        rep.fail("au moins un projet")
        return api, None, []
    project = projects[0]
    if project.topic:
        rep.ok("topic du projet", f"id {project.topic.id}, en ligne={project.topic.is_online}, mode={project.topic.mode}")
    else:
        rep.fail("topic du projet")

    zones = await rep.check(
        f"GET /topics/sensors/?project_id={project.id}", api.get_zones(project.id), lambda z: f"{len(z)} zone(s)"
    ) or []
    for z in zones:
        bad_fields = [
            n for n, v, t in (
                ("temperature", z.temperature, float), ("setpoint", z.setpoint, float),
                ("speed", z.speed, str), ("status", z.status, str),
            ) if not isinstance(v, t)
        ]
        label = f"zone {z.id} « {z.name} »"
        info = f"{z.temperature}°C → {z.setpoint}°C, vitesse {z.speed}, {'ON' if z.is_on else 'OFF'}"
        rep.fail(label, f"champs invalides: {bad_fields}") if bad_fields else rep.ok(label, info)
        if z.topic is None:
            rep.fail(f"zone {z.id}: topic_info présent")
    if zones:
        z = await rep.check("GET /topics/sensors/{id}/", api.get_zone(zones[0].id), lambda z: z.name)
        if z and z.id != zones[0].id:
            rep.fail("zone relue = zone demandée")
    if project.topic:
        t = await rep.check("GET /topics/{id}/", api.get_topic(project.topic.id), lambda t: f"mode {t.mode}, stop={t.is_stop}, ssid={t.ssid}")
        if t and t.configurations:
            rep.ok("configurations de la centrale", ", ".join(sorted(t.configurations)))
    return api, project, zones


# --------------------------------------------------------------------- écriture


async def wait_for(api: KoolnovaApi, zone_id: int, predicate: Callable[[Zone], bool], timeout: float = 12) -> Zone | None:
    """Relit la zone jusqu'à ce que `predicate` soit vrai (le cloud suit la centrale)."""
    deadline = time.monotonic() + timeout
    zone = None
    while True:
        await asyncio.sleep(1.5)
        zone = await api.get_zone(zone_id)
        if predicate(zone):
            return zone
        if time.monotonic() > deadline:
            return None


async def write_tests(api: KoolnovaApi, project, zones: list[Zone], zone_id: int | None, with_stop: bool, rep: Report):
    section("Écritures réversibles")
    candidates = [z for z in zones if not z.is_on and z.temperature is not None]
    target = next((z for z in candidates if z.id == zone_id), None) if zone_id else (candidates[0] if candidates else None)
    if target is None or project.topic is None:
        rep.skip("toutes les écritures", "aucune zone éteinte exploitable" if zone_id is None else f"zone {zone_id} introuvable ou allumée")
        return
    topic_id = project.topic.id
    snap_zones = {z.id: z for z in zones}
    snap_topic = (await api.get_topic(topic_id))
    all_off = all(not z.is_on for z in zones)
    print(f"  {DIM}cible: zone {target.id} « {target.name} » (consigne {target.setpoint}, vitesse {target.speed}), "
          f"mode global {snap_topic.mode}{RESET}")

    async def step(label: str, send: Awaitable, predicate: Callable[[Zone], bool]) -> None:
        try:
            await send
        except Exception as err:  # noqa: BLE001
            rep.fail(label, f"commande refusée: {type(err).__name__}: {err}")
            return
        seen = await wait_for(api, target.id, predicate)
        rep.ok(label, "relu") if seen else rep.fail(label, "valeur non reflétée après 12 s")

    try:
        step_up = round(target.setpoint + 0.5, 1) if target.setpoint + 0.5 <= 30 else round(target.setpoint - 0.5, 1)
        await step(f"consigne → {step_up}", api.set_setpoint(target.id, step_up), lambda z: z.setpoint == step_up)

        for sp in (s for s in Speed if s != target.speed):
            await step(f"vitesse → {sp.name.lower()} ({sp.value})", api.set_speed(target.id, sp), lambda z, sp=sp: z.speed == sp.value)

        low = round(target.temperature - 3, 1)
        await step(f"consigne → {low} (sous la mesure, évite de chauffer)", api.set_setpoint(target.id, low), lambda z: z.setpoint == low)
        await step("zone ON", api.set_zone_power(target.id, True), lambda z: z.is_on)
        await asyncio.sleep(2)
        await step("zone OFF", api.set_zone_power(target.id, False), lambda z: not z.is_on)

        if all_off and snap_topic.mode in (Mode.COOL, Mode.HEAT):
            other = Mode.COOL if snap_topic.mode == Mode.HEAT else Mode.HEAT
            await step(f"mode global → {other.name.lower()}", api.set_mode(topic_id, other),
                       lambda z: z.topic is not None and z.topic.mode == other.value)
        else:
            rep.skip("mode global", "au moins une zone est allumée ou mode inattendu")

        if with_stop:
            await step("arrêt global ON", api.set_stop(topic_id, True), lambda z: z.topic is not None and z.topic.is_stop)
            await step("arrêt global OFF", api.set_stop(topic_id, False), lambda z: z.topic is not None and not z.topic.is_stop)
        else:
            rep.skip("arrêt global (is_stop)", "ajoutez --with-stop")
    finally:
        section("Restauration")
        try:
            orig = snap_zones[target.id]
            await api.set_zone_power(target.id, orig.is_on)
            await api.set_setpoint(target.id, orig.setpoint)
            await api.set_speed(target.id, orig.speed)
            if snap_topic.is_stop is not None:
                await api.set_stop(topic_id, snap_topic.is_stop) if with_stop else None
            if snap_topic.mode:
                await api.set_mode(topic_id, snap_topic.mode)
            await asyncio.sleep(3)
            now = {z.id: z for z in await api.get_zones(project.id)}
            now_topic = await api.get_topic(topic_id)
            diffs = []
            for zid, z0 in snap_zones.items():
                for f in ("setpoint", "speed", "status"):
                    if getattr(now[zid], f) != getattr(z0, f):
                        diffs.append(f"zone {zid}.{f}: {getattr(z0, f)} → {getattr(now[zid], f)}")
            for f in ("mode", "is_stop"):
                if getattr(now_topic, f) != getattr(snap_topic, f):
                    diffs.append(f"topic.{f}: {getattr(snap_topic, f)} → {getattr(now_topic, f)}")
            rep.fail("état restauré", "; ".join(diffs)) if diffs else rep.ok("état restauré à l'identique", "5 zones + centrale vérifiées" if len(snap_zones) == 5 else f"{len(snap_zones)} zone(s) + centrale")
        except Exception as err:  # noqa: BLE001
            rep.fail("restauration", f"{type(err).__name__}: {err} — VÉRIFIEZ L'ÉTAT MANUELLEMENT")


# ------------------------------------------------------------------------ main


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="joue aussi les écritures réversibles")
    ap.add_argument("--with-stop", action="store_true", help="avec --write : teste l'arrêt global")
    ap.add_argument("--zone-id", type=int, help="zone cible des écritures (doit être éteinte)")
    args = ap.parse_args()

    email = os.environ.get("KOOLNOVA_EMAIL") or input("Email: ")
    password = os.environ.get("KOOLNOVA_PASSWORD") or getpass.getpass("Mot de passe: ")

    rep = Report()
    async with aiohttp.ClientSession() as session:
        api, project, zones = await read_tests(session, email, password, rep)
        if args.write:
            if project is None or not zones:
                rep.skip("écritures", "lecture impossible")
            else:
                await write_tests(api, project, zones, args.zone_id, args.with_stop, rep)

    print(f"\n{rep.passed} réussi(s), {rep.failed} échec(s), {rep.skipped} ignoré(s)")
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
