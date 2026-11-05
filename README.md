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
| Numbers | Comfort / eco / sleep temperature, eco and sleep duration, delayed-start temperature and delay, temperature correction (−4…+4 °C, slider) |

Durations are in minutes. The Program preset follows the weekly schedule set in the Tesy app;
the schedule itself can't be edited over the local API.

### Optional: MyTESY cloud telemetry (beta)

Under **Configure** you can add the MyTESY login of the account the convector is registered to.
The integration then reads, without ever sending commands to the cloud:

| Entity | Source |
|---|---|
| Heating (binary sensor) | `heating` in the convector's `setTempStatistic` MQTT messages |
| Internal temperature | `currentTemp` in the same messages (the convector's own sensor) |

The broker address and broker login are read from MyTESY's `/get-app-settings` at runtime;
nothing is hardcoded. Local control keeps working if the cloud is down. **Diagnostics**
(device page → ⋮ → Download diagnostics) shows what the cloud returned, with secrets masked.
Approach based on the investigation in [Tazmania0/TesyConvectorLocal](https://github.com/Tazmania0/TesyConvectorLocal) (MIT).

### What the local API does not provide

The convector does not report its room temperature, whether the element is heating right now,
its power, firmware or Wi-Fi signal over the local API (only the Tesy cloud gets those). So:

- choose a **room temperature sensor** during setup to show the current temperature
  (it updates the climate entity whenever the sensor changes);
- for real heating state and energy, put the convector on a metering smart plug.

## Installation

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/dixonbg/TesyConvectorLocal`,
   type **Integration**. Install **Tesy Convector (local)** and restart Home Assistant.
2. Convectors on your network are **discovered by themselves**: a "Discovered: Tesy convector"
   card appears under **Settings → Devices & services**. Click **Add** for each one you want
   (**Ignore** the others). Adding 2 of 4 convectors is just 2 clicks.
3. Optional, per convector: pick a room temperature sensor (the convector doesn't report one).

Not discovered (other VLAN, no DHCP traffic seen yet)? **Add integration → Tesy Convector
(local)** and type its IP. Home Assistant reads the convector's MAC by itself.

**No IP reservation needed.** Each convector is identified by its MAC; when it gets a new IP,
discovery updates it.

**Configure** (per convector): room sensor, optional MyTESY login (see above), IP. The MyTESY
device is matched by MAC automatically. One MyTESY login serves several convectors: enter it on
the first one; on the next ones the email is suggested and the password can be left blank. All
convectors of one account share one cloud connection. A convector registered to someone else's
account can use that account instead.

Upgrading from alexanderpp 1.0: replace the repository in HACS with this one and install. The
existing entry and `climate.*` entity are kept; the MAC is learned on first start.

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
| `setTCorrection` | `{"temp": …}` (−4…4; outside is clamped) |
| `setAdaptiveStart`, `setOpenedWindow`, `setAntiFrost`, `setUV`, `setLockDevice` | `{"status": "on"\|"off"}` |
| `getSSID` | settings plus `wifiinfo` (list of visible networks) |

Temperatures are whole °C: decimals are accepted but rounded on store (23.5 → 24). Send
`Content-Type: application/json`; a form-encoded body is read as `temp: 0`.

The web server handles one request at a time and resets reused or overlapping connections; the
integration sends one request at a time with `Connection: close` and retries.

## License

MIT, see [LICENSE](LICENSE). Original work © alexanderpp.
