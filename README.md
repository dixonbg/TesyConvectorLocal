# Tesy Convector (local) for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/docs/faq/custom_repositories/)

Local control of Wi-Fi Tesy convectors (CN06AS family) through the convector's built-in
HTTP API. No Tesy cloud account and no internet connection are needed.

This is a continuation of [alexanderpp/TesyConvectorLocal](https://github.com/alexanderpp/TesyConvectorLocal),
which is no longer maintained. Version 2.0 fixes its open issues (unique ID, broken
options dialog, the convector dropping offline) and exposes every setting the local API offers.

## Entities

| Entity | What it does |
|---|---|
| Climate | On/off, target temperature, presets **Heating** (none), **Comfort**, **Eco**, **Sleep**, **Program**. The target shown is the active mode's temperature. |
| Switches | Adaptive start, open window detection, anti-frost, UV, child lock, delayed start |
| Numbers | Comfort / eco / sleep temperature, eco and sleep duration, delayed-start temperature and delay, temperature correction (−4…+4 °C) |

Durations are in minutes. The Program preset follows the weekly schedule set in the Tesy app;
the schedule itself can't be edited over the local API.

### What the local API does not provide

The convector does not report its room temperature, whether the element is heating right now,
its power, firmware or Wi-Fi signal over the local API (only the Tesy cloud gets those). So:

- choose a **room temperature sensor** during setup to show the current temperature
  (it updates the climate entity whenever the sensor changes);
- for real heating state and energy, put the convector on a metering smart plug.

## Installation

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/dixonbg/TesyConvectorLocal`,
   type **Integration**.
2. Install **Tesy Convector (local)** and restart Home Assistant.
3. **Settings → Devices & services → Add integration → Tesy Convector (local)**, enter the
   convector's IP address and optionally a room temperature sensor.

Give the convector a fixed IP (a DHCP reservation in your router). The IP and the sensor can be
changed later under **Configure**.

Upgrading from alexanderpp 1.0: replace the repository in HACS with this one and install. The
existing entry and `climate.*` entity are kept (the climate entity now has a unique ID).

## Supported devices

Tested on a **Tesy CN06AS** (CN06AS-D060). Other convectors that answer
`POST http://<ip>/getStatus` with `{"payload": {"onOff": …, "setMode": …, "setTemp": …}}`
should work. Older Tesy units with the `GET /status` API (Atheros module) are not supported;
setup reports them as "not a supported Tesy convector".

## Local API notes

`POST http://<ip>/<command>` with a JSON body; the reply echoes the command and a `payload`.

| Command | Body |
|---|---|
| `getStatus` | `{}` → every setting below with its current values |
| `onOff` | `{"status": "on"\|"off"}` |
| `setMode` | `{"name": "heating"\|"comfort"\|"eco"\|"sleep"\|"program"}` (other names answer OK but are ignored) |
| `setTemp`, `setComfortTemp` | `{"temp": 10…30}` |
| `setEcoTemp`, `setSleepTemp` | `{"temp": …, "time": minutes}` |
| `setDelayedStart` | `{"status": …, "time": minutes, "temp": …}` |
| `setTCorrection` | `{"temp": …}` |
| `setAdaptiveStart`, `setOpenedWindow`, `setAntiFrost`, `setUV`, `setLockDevice` | `{"status": "on"\|"off"}` |
| `getSSID` | settings plus `wifiinfo` (list of visible networks) |

The web server handles one request at a time and resets reused or overlapping connections; the
integration sends one request at a time with `Connection: close` and retries.

## License

MIT, see [LICENSE](LICENSE). Original work © alexanderpp.
