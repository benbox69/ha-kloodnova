"""Client asynchrone du cloud Koolnova (https://api.koolnova.com).

Aucune dépendance à Home Assistant : seulement aiohttp, pour pouvoir être
copié tel quel dans l'intégration (custom_components/kloodnova/api.py) et testé
en ligne de commande (voir test_koolnova_api.py).

Points établis par rétro-ingénierie (web app + app mobile) et vérifiés sur un
compte réel le 2026-10-29 :

* Le serveur renvoie un faux 404 aux User-Agent par défaut (curl, python-urllib,
  aiohttp...). Tout User-Agent personnalisé passe : toujours envoyer le nôtre.
* Authentification : POST /auth/v2/login/ {email, password}
  -> {access_token, refresh_token, user}. Les deux tokens durent ~500 jours.
  Rafraîchissement : POST /auth/v2/token/refresh/ {refresh} -> {access, ...}.
* Les listes sont paginées : {lastPage, currentPage, perPage, total, data[]}
  avec les paramètres `page` (base 1) et `page_size`.
* Une zone (« sensor ») embarque son topic (la centrale) dans `topic_info` :
  un seul GET /topics/sensors/?project_id=N suffit pour tout l'état.
* Le mode (clim / chauffage) et l'arrêt (`is_stop`) sont GLOBAUX : ils se
  règlent sur le topic, pas sur une zone.
* Le cloud suit l'état remonté par la centrale physique : après une commande,
  une relecture immédiate peut être temporairement différente.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

API_URL = "https://api.koolnova.com"
USER_AGENT = "ha-koolnova-integration/0.1"
REQUEST_TIMEOUT = 20
TOKEN_LEEWAY = 60  # secondes : on renouvelle un token qui expire dans moins que ça
PAGE_SIZE = 50
RATE_LIMIT_WAIT = 3.0  # secondes d'attente avant l'unique réessai sur HTTP 429
RATE_LIMIT_MAX_WAIT = 10.0


class Speed(StrEnum):
    """Vitesse de ventilation d'une zone (valeurs texte côté API)."""

    LOW = "1"
    MEDIUM = "2"
    HIGH = "3"
    AUTO = "4"


class ZoneStatus(StrEnum):
    """État marche/arrêt d'une zone."""

    OFF = "02"
    ON = "03"


class Mode(StrEnum):
    """Mode global de la centrale. 4, 5 et 6 non vérifiés (dépend de TopicModes)."""

    COOL = "1"
    HEAT = "2"
    FLOOR_HEAT = "4"
    FLOOR_COOL_AIR = "5"
    FLOOR_HEAT_AIR = "6"


class KoolnovaError(Exception):
    """Erreur de base."""


class KoolnovaConnectionError(KoolnovaError):
    """Réseau indisponible, délai dépassé ou réponse illisible."""


class KoolnovaAuthError(KoolnovaError):
    """Identifiants ou tokens refusés."""


class KoolnovaApiError(KoolnovaError):
    """Réponse HTTP d'erreur."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


class KoolnovaRateLimitError(KoolnovaApiError):
    """HTTP 429 : trop de requêtes."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(429, message)
        self.retry_after = retry_after


def _float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _str(value: Any) -> str | None:
    return None if value is None else str(value)


@dataclass
class Topic:
    """La centrale (un par projet)."""

    id: int
    code: str | None = None
    name: str | None = None
    mode: str | None = None
    is_stop: bool = False
    is_online: bool = False
    last_sync: str | None = None
    ssid: str | None = None
    rssi: int | None = None
    device_reference: str | None = None
    eco: bool = False
    anti_frost: bool = False
    configurations: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Topic:
        return cls(
            id=d["id"],
            code=d.get("topic"),
            name=d.get("name"),
            mode=_str(d.get("mode")),
            is_stop=bool(d.get("is_stop")),
            is_online=bool(d.get("is_online")),
            last_sync=d.get("last_sync"),
            ssid=d.get("ssid"),
            rssi=d.get("rssi"),
            device_reference=d.get("device_reference"),
            eco=bool(d.get("eco")),
            anti_frost=bool(d.get("anti_frost")),
            configurations={
                c["key"]: c.get("value")
                for c in d.get("configurations") or []
                if "key" in c
            },
        )


@dataclass
class Zone:
    """Une zone (thermostat) ; `topic` donne l'état global de la centrale."""

    id: int
    name: str
    temperature: float | None
    setpoint: float | None
    speed: str | None
    status: str | None
    humidity: float | None = None
    co2: float | None = None
    is_trv: bool = False
    room: str | None = None
    floor: str | None = None
    topic: Topic | None = None

    @property
    def is_on(self) -> bool:
        return self.status == ZoneStatus.ON

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Zone:
        ti = d.get("topic_info")
        return cls(
            id=d["id"],
            name=d.get("name") or f"Zone {d['id']}",
            temperature=_float(d.get("temperature")),
            setpoint=_float(d.get("setpoint_temperature")),
            speed=_str(d.get("speed")),
            status=_str(d.get("status")),
            humidity=_float(d.get("humidity")),
            co2=_float(d.get("co2")),
            is_trv=bool(d.get("is_trv")),
            room=(d.get("room") or {}).get("name"),
            floor=(d.get("floor") or {}).get("name"),
            topic=Topic.from_dict(ti) if isinstance(ti, dict) and "id" in ti else None,
        )


@dataclass
class Project:
    id: int
    name: str | None
    town: str | None = None
    topic: Topic | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Project:
        t = d.get("topic")
        return cls(
            id=d["id"],
            name=d.get("name"),
            town=d.get("town"),
            topic=Topic.from_dict(t) if isinstance(t, dict) and "id" in t else None,
        )


def _jwt_exp(token: str | None) -> float | None:
    """Date d'expiration (epoch) d'un JWT, sans vérifier la signature."""
    try:
        payload = token.split(".")[1]  # type: ignore[union-attr]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (AttributeError, IndexError, KeyError, ValueError, TypeError):
        return None


class KoolnovaApi:
    """Client de l'API cloud Koolnova.

    Renseigner soit (email, password), soit des tokens déjà obtenus, soit les
    deux : en cas de 401 le client essaie d'abord le refresh token, puis se
    reconnecte avec email/password. `on_tokens(access, refresh)` est appelé à
    chaque renouvellement pour que l'appelant puisse les persister.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        email: str | None = None,
        password: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        base_url: str = API_URL,
        user_agent: str = USER_AGENT,
        on_tokens: Callable[[str, str | None], None] | None = None,
        request_timeout: float = REQUEST_TIMEOUT,
    ) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._access = access_token
        self._refresh = refresh_token
        self._base = base_url.rstrip("/")
        self._ua = user_agent
        self._on_tokens = on_tokens
        self._timeout = request_timeout
        self._auth_lock = asyncio.Lock()

    # ------------------------------------------------------------------ tokens

    @property
    def access_token(self) -> str | None:
        return self._access

    @property
    def refresh_token(self) -> str | None:
        return self._refresh

    def _set_tokens(self, access: str, refresh: str | None = None) -> None:
        self._access = access
        if refresh:
            self._refresh = refresh
        if self._on_tokens:
            self._on_tokens(self._access, self._refresh)

    def _expiring(self, token: str | None) -> bool:
        exp = _jwt_exp(token)
        return exp is not None and exp - time.time() < TOKEN_LEEWAY

    async def login(self) -> dict[str, Any]:
        """Authentification email/mot de passe. Retourne le profil utilisateur."""
        if not (self._email and self._password):
            raise KoolnovaAuthError("Email et mot de passe requis")
        data = await self._request(
            "POST",
            "/auth/v2/login/",
            json={"email": self._email, "password": self._password},
            auth=False,
        )
        if not isinstance(data, dict) or "access_token" not in data:
            raise KoolnovaAuthError("Réponse de login inattendue")
        self._set_tokens(data["access_token"], data.get("refresh_token"))
        return data.get("user") or {}

    async def refresh(self) -> None:
        """Renouvelle l'access token avec le refresh token."""
        if not self._refresh:
            raise KoolnovaAuthError("Pas de refresh token")
        data = await self._request(
            "POST",
            "/auth/v2/token/refresh/",
            json={"refresh": self._refresh},
            auth=False,
        )
        if not isinstance(data, dict) or "access" not in data:
            raise KoolnovaAuthError("Réponse de refresh inattendue")
        self._set_tokens(data["access"], data.get("refresh"))

    async def _reauthenticate(self, stale_token: str | None) -> None:
        async with self._auth_lock:
            if self._access and self._access != stale_token:
                return  # une autre tâche a déjà renouvelé le token
            if self._refresh:
                try:
                    await self.refresh()
                    return
                except KoolnovaAuthError:
                    _LOGGER.debug("Refresh token refusé, reconnexion complète")
            if self._email and self._password:
                await self.login()
                return
            raise KoolnovaAuthError("Session expirée et aucun identifiant disponible")

    async def _ensure_token(self) -> None:
        if self._access and not self._expiring(self._access):
            return
        await self._reauthenticate(self._access)

    # ----------------------------------------------------------------- requêtes

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        auth: bool = True,
        _retry: bool = True,
        _rate_retry: bool = True,
    ) -> Any:
        headers = {"User-Agent": self._ua, "Accept": "application/json"}
        used_token = None
        if auth:
            await self._ensure_token()
            used_token = self._access
            headers["Authorization"] = f"Bearer {used_token}"
        try:
            async with asyncio.timeout(self._timeout):
                async with self._session.request(
                    method, self._base + path, json=json, params=params, headers=headers
                ) as resp:
                    status = resp.status
                    ctype = resp.headers.get("Content-Type", "")
                    retry_after = resp.headers.get("Retry-After")
                    body = await resp.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise KoolnovaConnectionError(f"{method} {path}: {err}") from err

        data: Any = None
        if "json" in ctype and body:
            try:
                data = _json_loads(body)
            except ValueError as err:
                raise KoolnovaConnectionError(f"{method} {path}: JSON invalide") from err

        if status == 401 and auth and _retry:
            await self._reauthenticate(used_token)
            return await self._request(
                method, path, json=json, params=params, auth=True, _retry=False
            )
        if status in (400, 401, 403) and (not auth or status == 401):
            raise KoolnovaAuthError(_message(data, body))
        if status == 429:
            wait = float(retry_after) if retry_after and retry_after.isdigit() else None
            if _rate_retry:
                # Un seul réessai, après une courte attente (plafonnée).
                await asyncio.sleep(min(wait or RATE_LIMIT_WAIT, RATE_LIMIT_MAX_WAIT))
                return await self._request(
                    method, path, json=json, params=params, auth=auth,
                    _retry=_retry, _rate_retry=False,
                )
            raise KoolnovaRateLimitError("Trop de requêtes", wait)
        if status == 404 and "html" in ctype:
            raise KoolnovaApiError(
                404,
                f"{method} {path} : route inconnue, ou requête filtrée "
                "(User-Agent par défaut d'un client HTTP ?)",
            )
        if status >= 400:
            raise KoolnovaApiError(status, _message(data, body))
        return data

    async def _get_all(self, path: str, params: dict[str, Any] | None = None) -> list[dict]:
        """Parcourt toutes les pages d'une liste paginée."""
        items: list[dict] = []
        page = 1
        while True:
            data = await self._request(
                "GET", path, params={**(params or {}), "page": page, "page_size": PAGE_SIZE}
            )
            if isinstance(data, list):
                return data
            chunk = (data or {}).get("data") or []
            items.extend(chunk)
            if not chunk or (data or {}).get("currentPage", page) >= (data or {}).get("lastPage", 1):
                return items
            page += 1

    # ------------------------------------------------------------------ lecture

    async def get_me(self) -> dict[str, Any]:
        return await self._request("GET", "/users/me/")

    async def get_projects(self) -> list[Project]:
        return [Project.from_dict(p) for p in await self._get_all("/projects/")]

    async def get_zones(self, project_id: int) -> list[Zone]:
        """Toutes les zones d'un projet, avec l'état de la centrale (`zone.topic`)."""
        rows = await self._get_all("/topics/sensors/", {"project_id": project_id})
        return [Zone.from_dict(z) for z in rows]

    async def get_zone(self, zone_id: int) -> Zone:
        return Zone.from_dict(await self._request("GET", f"/topics/sensors/{zone_id}/"))

    async def get_topic(self, topic_id: int) -> Topic:
        return Topic.from_dict(await self._request("GET", f"/topics/{topic_id}/"))

    # ------------------------------------------------------------------ commandes

    async def _patch_zone(self, zone_id: int, body: dict[str, Any]) -> Zone:
        return Zone.from_dict(
            await self._request("PATCH", f"/topics/sensors/{zone_id}/", json=body)
        )

    async def set_setpoint(self, zone_id: int, temperature: float) -> Zone:
        return await self._patch_zone(zone_id, {"setpoint_temperature": round(float(temperature), 1)})

    async def set_speed(self, zone_id: int, speed: Speed | str) -> Zone:
        return await self._patch_zone(zone_id, {"speed": str(Speed(str(speed)).value)})

    async def set_zone_power(self, zone_id: int, on: bool) -> Zone:
        status = ZoneStatus.ON if on else ZoneStatus.OFF
        return await self._patch_zone(zone_id, {"status": status.value})

    async def set_mode(self, topic_id: int, mode: Mode | str) -> Topic:
        """Mode GLOBAL de la centrale (toutes les zones)."""
        return Topic.from_dict(
            await self._request(
                "PATCH", f"/topics/{topic_id}/", json={"mode": str(Mode(str(mode)).value)}
            )
        )

    async def set_stop(self, topic_id: int, stop: bool) -> Topic:
        """Arrêt GLOBAL de la centrale. Comportement de relecture à surveiller."""
        return Topic.from_dict(
            await self._request("PATCH", f"/topics/{topic_id}/", json={"is_stop": bool(stop)})
        )


def _json_loads(body: str) -> Any:
    return json.loads(body)


def _message(data: Any, body: str) -> str:
    if isinstance(data, dict):
        for key in ("detail", "message", "error"):
            if data.get(key):
                return str(data[key])
        return json.dumps(data, ensure_ascii=False)[:200]
    return body[:200]
