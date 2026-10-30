"""Constantes de l'intégration Koolnova."""

from homeassistant.const import Platform

DOMAIN = "kloodnova"

PLATFORMS = [
    Platform.CLIMATE,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
]

# Données de l'entrée (identifiants et tokens)
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"

# Options de l'entrée : {"<project_id>": [zone_id, ...]}
CONF_PROJECTS = "projects"
CONF_ZONES = "zones"
CONF_SCAN_INTERVAL = "scan_interval"  # option : secondes entre deux relevés

DEFAULT_SCAN_INTERVAL = 60  # secondes
MIN_SCAN_INTERVAL = 15  # en dessous, risque de dépasser les limites de requêtes du cloud
MAX_SCAN_INTERVAL = 600
CONFIRM_REFRESH_DELAY = 5  # secondes entre une commande et la relecture de confirmation

MIN_TEMP = 15.0
MAX_TEMP = 30.0
TEMP_STEP = 0.5

MANUFACTURER = "Koolnova"
DEVICE_NAME_PREFIX = "Kloodnova"  # appareil « Kloodnova <Projet> » -> entity_id kloodnova_<projet>_…
CONFIGURATION_URL = "https://app.koolnova.com"
