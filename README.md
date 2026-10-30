<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="custom_components/kloodnova/brand/dark_logo@2x.png">
    <img src="custom_components/kloodnova/brand/logo@2x.png" alt="Kloodnova" width="420">
  </picture>
</p>

<p align="center">
  <a href="https://hacs.xyz"><img alt="HACS Custom" src="https://img.shields.io/badge/HACS-Custom-orange.svg"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
</p>

# Kloodnova

A Home Assistant integration for **[Koolnova](https://www.koolnova.com) zoned air-conditioning systems**, using the Koolnova cloud — the same account you use in the Koolnova app.

Pick your installation and the zones you want, and Kloodnova creates one thermostat per zone plus the global controls of your unit, all grouped under a single device.

> **Unofficial.** Kloodnova is an independent project, not affiliated with or endorsed by Koolnova. See [Disclaimer](#disclaimer).

## Features

- **One thermostat per zone**: current and target temperature (0.5 °C steps), heat / cool / off, fan speed.
- **Global controls** for the whole unit: operating mode, fan speed and a general stop.
- **One device per installation**, named `Kloodnova <project>`, holding every zone and setting.
- **Guided setup**: sign in, choose your projects, then choose your zones. Change them any time from *Configure*.
- **Adjustable refresh rate** from 15 to 600 seconds (60 s by default).
- Connectivity and Wi-Fi signal diagnostics, English and French translations.

## Installation

### HACS (recommended)

1. In HACS, open the menu (⋮) → **Custom repositories**.
2. Add `https://github.com/benbox69/ha-kloodnova` with the category **Integration**.
3. Install **Kloodnova**, then restart Home Assistant.

### Manual

Copy the `custom_components/kloodnova` folder into the `custom_components` folder of your Home Assistant configuration, then restart Home Assistant.

Requires Home Assistant **2025.1** or newer.

## Setup

1. Go to **Settings → Devices & services → Add integration** and search for **Kloodnova**.
2. Sign in with your **Koolnova account** (email and password of the Koolnova app).
3. Select the **projects** (installations) to add.
4. Select the **zones** to add.

To change your selection or the refresh rate later, open the integration and choose **Configure**.

## Entities

For a project called *Home* with a zone called *Office*:

| Entity | Section | Description |
|---|---|---|
| `climate.kloodnova_home_office` | Controls | One thermostat per selected zone: temperature, target, heat / cool / off, fan speed |
| `select.kloodnova_home_mode` | Configuration | Global operating mode (cooling, heating) |
| `select.kloodnova_home_fan` | Configuration | Global fan speed (low, medium, high, auto) |
| `switch.kloodnova_home_general_stop` | Configuration | General stop of the unit (on = stopped), like the STOP button of the app |
| `binary_sensor.kloodnova_home_connected` | Diagnostic | Whether the unit is online |
| `sensor.kloodnova_home_wi_fi_signal` | Diagnostic | Wi-Fi signal strength (disabled by default) |

### Good to know

- **Mode and fan speed are global.** They apply to the whole unit: changing them from one thermostat changes them for every zone. The fan speed can be set from any thermostat or from the dedicated select.
- **General stop** turns every thermostat off.
- **Updates are polled.** Changes made in the Koolnova app appear within the refresh interval. Lower it in *Configure* if you want them faster; very low values mean more requests to the cloud.
- If the unit loses its connection to the cloud, its entities become *unavailable*.
- Not available through the cloud: engine states, power consumption and global efficiency. A local Modbus integration is a good complement for those.

## Updating

- **HACS:** update from HACS, then restart Home Assistant.
- **Manual:** replace the `custom_components/kloodnova` folder, then restart Home Assistant. Reloading the integration is not enough, Home Assistant must restart to load new code.

Your account, projects and zones do not need to be configured again.

## Disclaimer

Kloodnova is not affiliated with, authorized or endorsed by Koolnova. It relies on the cloud API used by the official Koolnova apps, which is not publicly documented and may change or stop working without notice. "Koolnova" is a trademark of its respective owners; the Kloodnova name and logo are original creations that imply no official relationship. Use it at your own risk and with your own account.

Your Koolnova password is stored in your Home Assistant configuration so the integration can sign in again when its tokens expire.

## License

[MIT](LICENSE)
