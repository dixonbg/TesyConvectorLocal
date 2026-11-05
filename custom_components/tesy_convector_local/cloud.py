"""Optional, read-only MyTESY cloud telemetry.

The local API can't tell whether the element is heating, nor the convector's own temperature.
The convector pushes both to the MyTESY MQTT broker (`setTempStatistic` messages). With a
MyTESY login this module:

1. logs in (POST /app-user-login),
2. reads the broker address and broker login from /get-app-settings (nothing is hardcoded),
3. lists the account's devices (GET /get-my-devices, keyed by MAC),
4. keeps ONE MQTT/WebSocket/TLS connection per account and subscribes to the response topics
   of every convector set up with that account.

It never publishes, so it can't change a convector. The approach follows the investigation in
Tazmania0/TesyConvectorLocal (MIT), docs/mytesy-api-investigation.md.
"""
from __future__ import annotations

from collections.abc import Callable
import json
import logging
import math
import time
from typing import Any
import uuid

import aiohttp

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.ssl import client_context

from .const import DOMAIN
from .net import norm_mac

_LOGGER = logging.getLogger(__name__)

API_BASE = "https://ad.mytesy.com/rest"
ORIGIN = "https://v4.mytesy.com"
LANG = "en"
ALLOWED_BROKER_SUFFIX = ".tesy.com"
TELEMETRY = "setTempStatistic"
HOURLY = "setStatistic"  # once an hour: seconds the element heated in that hour


class TesyCloudAuthError(Exception):
    """Wrong MyTESY email or password."""


class TesyCloudError(Exception):
    """MyTESY did not answer as expected."""


class TesyCloudAccount:
    """REST part: login, app settings, devices."""

    def __init__(self, session: aiohttp.ClientSession, email: str, password: str) -> None:
        self._session = session
        self.email = email
        self._password = password
        self.user: dict[str, Any] = {}
        self.app_settings: dict[str, Any] = {}
        self.devices: dict[str, dict] = {}

    @property
    def _headers(self) -> dict[str, str]:
        return {"Origin": ORIGIN, "Referer": f"{ORIGIN}/", "Accept": "application/json"}

    def _params(self) -> dict[str, str]:
        return {
            "userID": str(self.user.get("userID", "")),
            "userEmail": str(self.user.get("email") or self.email),
            "userPass": str(self.user.get("password") or self._password),
            "lang": LANG,
        }

    async def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            async with self._session.request(
                method, f"{API_BASE}/{path}", headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=20), **kwargs,
            ) as resp:
                if resp.status in (401, 403):
                    raise TesyCloudAuthError(f"{path}: HTTP {resp.status}")
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise TesyCloudError(f"{path}: {err}") from err

    async def load(self) -> None:
        """Log in, then read app settings and devices."""
        data = await self._json(
            "POST", "app-user-login",
            json={"email": self.email, "password": self._password, "lang": LANG},
        )
        if not isinstance(data, dict) or data.get("errors") or not data.get("userID"):
            raise TesyCloudAuthError("login rejected")
        self.user = data
        settings = await self._json("GET", "get-app-settings", params=self._params())
        self.app_settings = settings if isinstance(settings, dict) else {}
        devices = await self._json("GET", "get-my-devices", params=self._params())
        if isinstance(devices, dict) and str(devices.get("error")) == "1":
            raise TesyCloudAuthError("get-my-devices rejected the login")
        self.devices = (
            {mac: dev for mac, dev in devices.items() if isinstance(dev, dict)}
            if isinstance(devices, dict) else {}
        )

    def find_device(self, mac: str | None) -> str | None:
        """The account's own key for a MAC in any format, or None."""
        wanted = norm_mac(mac)
        if not wanted:
            return None
        for key, dev in self.devices.items():
            if wanted in (norm_mac(key), norm_mac(dev.get("mac")), norm_mac((dev.get("state") or {}).get("mac"))):
                return key
        return None

    def broker(self) -> tuple[str, int, str, str]:
        """Broker host, port, username, password from the app settings."""
        s = self.app_settings
        host = str(s.get("MQTT_PROD_URL") or "").replace("wss://", "").replace("https://", "").strip("/")
        if not host.endswith(ALLOWED_BROKER_SUFFIX):
            raise TesyCloudError(f"unexpected broker host {host!r}")
        return host, int(s.get("MQTT_PORT") or 8083), str(s.get("MQTT_USERNAME") or ""), str(s.get("MQTT_PASS") or "")


class TesyDeviceTelemetry:
    """Latest telemetry of one convector; entities listen to it."""

    def __init__(self, hub: TesyCloudHub, key: str) -> None:
        self.hub = hub
        self.key = key
        device = hub.account.devices.get(key, {})
        self.model = str(device.get("model") or "").lower()
        self.token = str(device.get("token") or "")
        state = device.get("state") if isinstance(device.get("state"), dict) else {}
        # what the owner entered in the MyTESY app; read at login, so a change needs a reload
        self.watt: int | None = _int(state.get("watt"))
        self.model_type: str | None = device.get("model_type") or None  # e.g. "Convector CN06AS"
        self.firmware: str | None = device.get("firmware_version") or None
        self.last_hour: dict[str, Any] | None = None  # {"from", "to", "seconds"}
        self.heating: bool | None = None
        self.current_temp: float | None = None
        self.last_telemetry: float | None = None
        self.messages: dict[str, dict] = {}  # last payload per command, for diagnostics
        self._listeners: list[Callable[[], None]] = []

    @property
    def topic(self) -> str:
        return f"v1/{self.key}/response/{self.model}/{self.token}"

    @property
    def connected(self) -> bool:
        return self.hub.connected

    @callback
    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    def handle(self, command: str, retained: bool, data: Any) -> None:
        """Runs in paho's thread; the hub schedules notify() on the event loop."""
        self.messages[command] = {"at": time.time(), "retained": retained, "data": data}
        if retained or not isinstance(data, dict):
            return
        if command == HOURLY:
            self._handle_hourly(data)
            return
        if command != TELEMETRY:
            return
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else data
        if payload.get("heating") in ("on", "off"):
            self.heating = payload["heating"] == "on"
            self.last_telemetry = time.time()
        try:
            temp = float(payload.get("currentTemp"))
        except (TypeError, ValueError):
            return
        if math.isfinite(temp) and -40 <= temp <= 80:
            self.current_temp = temp


    def _handle_hourly(self, data: dict) -> None:
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else data
        if (seconds := _int(payload.get("time"))) is not None and 0 <= seconds <= 3600:
            self.last_hour = {"from": payload.get("from"), "to": payload.get("to"), "seconds": seconds}
        if payload.get("ver"):
            self.firmware = str(payload["ver"])


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class TesyCloudHub:
    """One login and one MQTT connection per MyTESY account, shared by its convectors."""

    def __init__(self, hass: HomeAssistant, account: TesyCloudAccount) -> None:
        self.hass = hass
        self.account = account
        self.devices: dict[str, TesyDeviceTelemetry] = {}
        self.connected = False
        self.last_error: str | None = None
        self.subscriptions: dict[str, list[str]] = {}
        self._client = None

    async def async_add_device(self, key: str) -> TesyDeviceTelemetry:
        telemetry = self.devices.get(key) or TesyDeviceTelemetry(self, key)
        self.devices[key] = telemetry
        if self._client is None:
            await self._async_connect()
        elif self.connected:
            self._subscribe(telemetry)
        return telemetry

    async def async_remove_device(self, key: str) -> None:
        telemetry = self.devices.pop(key, None)
        if telemetry and self._client and self.connected:
            self._client.unsubscribe(f"{telemetry.topic}/#")
        if not self.devices:
            await self.async_stop()

    async def _async_connect(self) -> None:
        import paho.mqtt.client as mqtt  # only needed with a cloud login

        host, port, user, password = self.account.broker()
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"ha-tesy-{uuid.uuid4().hex[:12]}",
            transport="websockets",
        )
        client.ws_set_options(path="/")
        client.tls_set_context(client_context())
        client.username_pw_set(user, password)
        # paho's own logger stays off: its debug lines would contain device tokens
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.on_subscribe = self._on_subscribe
        client.reconnect_delay_set(5, 300)
        self._client = client
        client.connect_async(host, port, keepalive=60)
        client.loop_start()

    async def async_stop(self) -> None:
        if client := self._client:
            self._client = None
            self.connected = False
            await self.hass.async_add_executor_job(_stop_client, client)

    def _subscribe(self, telemetry: TesyDeviceTelemetry) -> None:
        for topic in (f"{telemetry.topic}/{TELEMETRY}", f"{telemetry.topic}/#"):
            _, mid = self._client.subscribe(topic)
            self.subscriptions[str(mid)] = [topic.replace(telemetry.token, "<token>").replace(telemetry.key, "<mac>")]

    def _notify_all(self) -> None:
        for telemetry in list(self.devices.values()):
            telemetry.notify()

    # paho callbacks run in paho's thread
    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        self.connected = not reason_code.is_failure
        self.last_error = None if self.connected else str(reason_code)
        if self.connected:
            for telemetry in list(self.devices.values()):
                self._subscribe(telemetry)
        self.hass.loop.call_soon_threadsafe(self._notify_all)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        self.connected = False
        self.last_error = str(reason_code)
        self.hass.loop.call_soon_threadsafe(self._notify_all)

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties) -> None:
        if str(mid) in self.subscriptions:
            self.subscriptions[str(mid)].append(",".join(str(rc) for rc in reason_codes))

    def _on_message(self, client, userdata, msg) -> None:
        parts = msg.topic.split("/")  # v1/<mac>/response/<model>/<token>/<command>
        if len(parts) < 6 or (telemetry := self.devices.get(parts[1])) is None:
            return
        try:
            data = json.loads(msg.payload)
        except ValueError:
            data = {"raw": msg.payload[:200].decode(errors="replace")}
        telemetry.handle(parts[-1], msg.retain, data)
        self.hass.loop.call_soon_threadsafe(telemetry.notify)


def _stop_client(client) -> None:
    client.disconnect()
    client.loop_stop()


async def async_get_hub(hass: HomeAssistant, email: str, password: str) -> TesyCloudHub:
    """The shared hub for this account (logs in on first use)."""
    hubs: dict[str, TesyCloudHub] = hass.data.setdefault(DOMAIN, {}).setdefault("hubs", {})
    key = email.strip().lower()
    if (hub := hubs.get(key)) is None:
        account = TesyCloudAccount(async_get_clientsession(hass), email, password)
        await account.load()
        hub = hubs[key] = TesyCloudHub(hass, account)
    return hub


async def async_release_hub(hass: HomeAssistant, hub: TesyCloudHub, device_key: str) -> None:
    await hub.async_remove_device(device_key)
    if not hub.devices:
        hubs = hass.data.get(DOMAIN, {}).get("hubs", {})
        hubs.pop(hub.account.email.strip().lower(), None)
