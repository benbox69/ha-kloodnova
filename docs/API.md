# API cloud Koolnova — ce qui est établi

Rétro-ingénierie de la web app (`app.koolnova.com`, bundle du 28 mai 2026) et de
l'app iOS 1.7.5 (Flutter), **vérifiée sur un compte réel le 29 octobre 2026**.
Non officielle : elle peut changer sans préavis.

Légende : ✅ testé sur le compte · 📖 vu dans le code des apps, non testé.

## Accès

| | |
|---|---|
| REST | `https://api.koolnova.com` (Django REST, nginx) |
| WebSocket | `wss://api.koolnova.com/ws/<ressource>/?token=<access>&<filtres>` — 📖, voir plus bas |
| Web app | `https://app.koolnova.com` (Angular, statique sur CloudFront) |
| MQTT | la centrale parle à `koolnovaserver.com` (champ `mqtt_address`), hors périmètre |

### ⚠️ Filtrage du User-Agent

Le serveur renvoie un **faux 404 Django** (page HTML « Not Found ») aux
User-Agent par défaut de `curl`, `python-urllib`, etc. Tout User-Agent
personnalisé passe (`ha-koolnova-integration/0.1`). Ce n'est **pas** lié à
`Origin`, `Referer` ni à `Accept-Language`. Si une route « n'existe pas »,
vérifier d'abord ça. Un 429 (nginx) apparaît si l'on enchaîne trop de requêtes.

## Authentification ✅

| | Requête | Réponse |
|---|---|---|
| Login | `POST /auth/v2/login/` `{"email","password"}` | `{access_token, refresh_token, user{pk,username,email,…}}` |
| Refresh | `POST /auth/v2/token/refresh/` `{"refresh"}` | `{access, access_token_expiration}` |
| Utilisation | en-tête `Authorization: Bearer <access>` | |

- JWT HS256, durée de vie **500 jours** pour l'access comme pour le refresh.
- Mauvais mot de passe : HTTP 400 `{"non_field_errors": ["Unable to log in with provided credentials."]}`.
- Token invalide sur une route protégée : HTTP 401.
- La web app n'utilise pas le refresh ; il fonctionne quand même.
- Autres : `POST /auth/v2/password/{change,reset,reset/confirm}/`, `POST /auth/emails/`, `GET /auth/emails/{code}/` 📖.

## Pagination ✅

Les listes renvoient `{lastPage, currentPage, perPage, total, data[]}`
(`perPage` vaut 10 par défaut). Paramètres : `page` (base 1), `page_size`.

## Lecture ✅

| Route | Contenu |
|---|---|
| `GET /users/me/` | profil, `num_projects`, `num_sensors`, `timezone`… |
| `GET /projects/` | projets ; chacun embarque son `topic` (la centrale) |
| `GET /topics/sensors/?project_id=N` | **toutes les zones d'un projet, avec l'état de la centrale** : c'est la route de polling |
| `GET /topics/sensors/{id}/` | une zone |
| `GET /topics/{id}/` | la centrale (topic) |

### Zone (« sensor »)

```json
{
  "id": 300002, "name": "Office",
  "temperature": 18.5, "setpoint_temperature": 22.0,
  "speed": "4", "status": "02",
  "humidity": null, "co2": null, "is_trv": false,
  "control_unit": "31", "zone": "02",
  "room": {"id": …, "name": …}, "floor": {"id": …, "name": …},
  "topic_info": { …la centrale, voir ci-dessous… }
}
```

| Champ | Valeurs |
|---|---|
| `speed` (texte) | `"1"` faible · `"2"` moyenne · `"3"` forte · `"4"` auto — **globale** : voir plus bas |
| `status` (texte) | `"02"` OFF · `"03"` ON |

### Centrale (topic)

`id`, `topic` (code court, ex. `ABCD1234`), `name`, `mode`, `is_stop`,
`is_online`, `last_sync`, `ssid`, `rssi`, `device_reference` (ex. `H19`), `uc`,
`eco`, `anti_frost`, `humidity`, `mqtt_address`, `configurations[]`
(`{key, value}` : `TopicModes`, `AllowEco`, `AllowAntiFrost`, `MDA_Com*`, `CLK_Com*`, `DIL_Com*`).

| `mode` (texte) | |
|---|---|
| `"1"` | climatisation ✅ |
| `"2"` | chauffage ✅ |
| `"4"`, `"5"`, `"6"` | plancher chauffant / plancher rafraîchissant + air froid / plancher chauffant + air chaud 📖 (vus dans l'app, non testés ; `TopicModes` = `0110000` sur cette installation) |

**Le mode et `is_stop` sont globaux** : ils s'appliquent à toutes les zones.

## Écriture

Tous en `PATCH`, réponse 200 avec l'objet mis à jour.

| Action | Requête | |
|---|---|---|
| Consigne | `PATCH /topics/sensors/{id}/` `{"setpoint_temperature": 21.5}` | ✅ décimales acceptées |
| Ventilation | `PATCH /topics/sensors/{id}/` `{"speed": "3"}` | ✅ **globale** : une seule zone suffit, les autres suivent en < 1 s |
| Zone ON / OFF | `PATCH /topics/sensors/{id}/` `{"status": "03"}` / `"02"` | ✅ |
| Mode global | `PATCH /topics/{topic_id}/` `{"mode": "1"}` (texte ou entier) | ✅ |
| Arrêt global | `PATCH /topics/{topic_id}/` `{"is_stop": true}` | ✅ fiable (voir ci-dessous) |
| Réglages d'une zone | `PATCH /topics/sensors/{id}/settings/` | 📖 corps inconnu |
| Config centrale | `PATCH /topics/{id}/config/{n}/` | 📖 corps inconnu |

La web app refuse d'écrire quand `topic.is_online` est faux.

### Comportement à connaître

- **La ventilation est globale.** Régler `speed` sur une zone la règle sur toutes
  les zones du projet (vérifié le 2026-10-29 : une zone passée à `"1"`, les cinq
  zones à `"1"` moins d'une seconde plus tard). Une seule requête suffit.
- **`is_stop` est fiable.** Après `PATCH is_stop`, `GET /topics/{id}/` et le
  `topic_info` intégré aux zones concordent immédiatement et restent stables
  (testé sur 15 s). Les zones ne changent pas de `status`.
- **À la reprise (`is_stop` → false), la centrale remet la ventilation sur
  `"4"` (auto)** environ 6 s plus tard, quelle que soit la valeur précédente.
  C'est ce qui avait été pris pour une anomalie lors d'un premier test.
- Le cloud reflète ce que la centrale physique remonte : une relecture
  immédiate après une commande peut différer brièvement. Pour les consignes,
  vitesses, ON/OFF et modes, la valeur envoyée est relue au bout de ~1,5 s.
  Dans l'intégration : état optimiste, puis polling pour confirmer.

## Ce que le cloud n'expose PAS (vérifié le 2026-10-30)

Comparaison avec l'intégration Modbus locale (`koolnova_bms`), qui lit les registres
du port BMS de la centrale : **moteurs, débit, puissance et efficacité globale n'existent
pas côté cloud** pour un compte « particulier ».

| Donnée (Modbus) | Cloud |
|---|---|
| État / débit / consigne des moteurs (AC1–AC4) | ❌ aucun champ. `/domotic/mdzs/` et `/domotic/peripherals/` répondent 200 mais **vides** (`is_domotic: false`) |
| Puissance (W) d'une zone | ❌ `project.has_consumption` vaut `false` ; aucun champ de puissance dans les zones |
| Historique (`/topics/record/`) | ❌ HTTP 403 : réservé aux comptes techniciens/OEM (idem `/topics/models/`, `/topics/software_versions/`) |
| **Efficacité globale** (5 niveaux) | ❌ rien d'équivalent trouvé. Candidats les plus proches : `eco` et `anti_frost` (booléens de la centrale) |

Le cloud donne en revanche, par zone : température mesurée, consigne, ventilation,
état ON/OFF, humidité et CO₂ (si capteurs) ; et pour la centrale : mode, arrêt,
connexion, RSSI, `eco`, `anti_frost`, `humidity`.

### Configuration de la centrale 📖

Les réglages techniques sont des lignes de `configurations[]` modifiées **par
l'id de la ligne**, pas par la clé :

`PATCH /topics/{topic_id}/config/{config_id}/` avec `{"value": "…"}`

| Clé | Rôle (déduit du code de l'app) |
|---|---|
| `TopicModes` | modes actifs : un drapeau `0`/`1` par numéro de mode (`"0110000"` = modes 1 et 2). Confirmé par l'écran « Modes » de la web app, qui écrit `"1"`/`"0"` à l'index du mode |
| `AllowEco`, `AllowAntiFrost` | autorisent les fonctions éco / antigel (les champs `eco` et `anti_frost` de la centrale) — non testé |
| `MDA_Com*`, `CLK_Com*`, `DIL_Com*` | configuration de communication/horloge, sens exact inconnu |
| (écrans « Moteurs », « pertes de débit », températures chaud/froid) | existent dans l'app mais leur clé n'est pas identifiée ; la valeur est encodée (`apzEncode`) |

## Autres routes 📖

`/projects/{id}/`, `/projects/{id}/floors/`, `/projects/floors/…/rooms/`,
`/projects/rooms/{id}/`, `PUT /projects/{id}/topics`, `/domotic/{mdzs,peripherals,intrusions}/`,
`/stages/` (programmes horaires), `/notifications/`, `/topics/record/`,
`/topics/models/`, `/topics/software_versions/`, `/oems/`, `/audit/`,
`/alexa/auth/`, `/smartthing/auth/`, `/users/…`.

## WebSocket : inutilisable, et inutile (enquête du 2026-10-30)

**Conclusion : pas de temps réel par WebSocket. Le polling REST est la bonne approche.**

- Le code de la web app (bundle du 28 mai 2026) contient bien des méthodes
  `connectSocket()` qui construisent `wss://api.koolnova.com/ws/{sensors|mdzs|peripherals|intrusion}/?<filtres>&token=<access>`
  (message `{"action":"list","request_id":"…"}`), mais **aucune page ne les appelle** (0 appel dans
  tout le bundle). Pour rafraîchir, la web app appelle `GET /topics/sensors/?project=<id>` et
  **relance cet appel toutes les 50 s** (`interval(50000)`). L'app officielle fait donc du polling.
- Côté serveur, **toutes** les requêtes WebSocket renvoient le même `404` nginx que n'importe quel
  chemin inexistant (`/ws/doesnotexist/` compris) : `/ws/sensors/`, `/ws/topics/`, `/ws/rooms/`,
  `/ws/`, avec ou sans slash final, jeton en paramètre ou en en-tête (`Bearer`, `Token`),
  paramètres `project`, `project_id`, `topic`. Les routes WebSocket ne sont pas servies sur `api.koolnova.com`.
- L'app iOS contient des chaînes `/ws/…` et un client `IOWebSocketChannel`, sur le même hôte
  `api.koolnova.com` ; ses paramètres exacts ne sont pas connus (le pinning empêche d'observer
  son trafic). Rien ne prouve qu'elle les utilise encore.
- `api2.koolnova.com` pointe sur le même serveur mais présente un certificat d'un autre nom
  (aucun service). `broker.koolnova.com` existe (probablement le broker MQTT des centrales,
  `mqtt_address: koolnovaserver.com` dans le topic) : **non sondé**, ce n'est pas une API publique.
- Piste restante, non explorée : décompiler le code Dart de l'app iOS (`blutter`) pour lire
  la construction exacte de l'URL WebSocket. À ne faire que si le polling s'avère insuffisant.

**Conséquence pour l'intégration** : le polling REST, réglable de 15 à 600 s dans « Configurer »,
est déjà plus réactif que l'app officielle (50 s).
