"""Optional, read-only MyTESY cloud telemetry.

The local API can't tell whether the element is heating, nor the convector's own temperature.
The convector pushes both to the MyTESY MQTT broker (`setTempStatistic` messages). With the
user's MyTESY login this module:

1. logs in (POST /app-user-login),
2. reads the broker address and broker login from /get-app-settings (nothing is hardcoded),
3. lists the account's devices (GET /get-my-devices),
4. subscribes to the chosen device's response topics over MQTT/WebSocket/TLS.

It never publishes, so it can't change the convector. The approach follows the investigation
in Tazmania0/TesyConvectorLocal (MIT), docs/mytesy-api-investigation.md.
"""
from __future__ import annotations

import json
import logging
import math
import time
from collections.abc import Callable
from typing import Any
import uuid

import aiohttp

from homeassistant.core import HomeAssistant, callback
from homeassistant.util.ssl import client_context

_LOGGER = logging.getLogger(__name__)

API_BASE = "https://ad.mytesy.com/rest"
ORIGIN = "https://v4.mytesy.com"
LANG = "en"
ALLOWED_BROKER_SUFFIX = ".tesy.com"
TELEMETRY = "setTempStatistic"


class TesyCloudAuthError(Exception):
    """Wrong MyTESY email or password."""


class TesyCloudError(Exception):
    """MyTESY did not answer as expected."""


class TesyCloudAccount:
    """REST part: login, app settings, devices."""

    def __init__(self, session: aiohttp.ClientSession, email: str, password: str) -> None:
        self._session = session
        self._email = email
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
            "userEmail": str(self.user.get("email") or self._email),
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

    async def login(self) -> None:
        data = await self._json(
            "POST", "app-user-login",
            json={"email": self._email, "password": self._password, "lang": LANG},
        )
        if not isinstance(data, dict) or data.get("errors") or not data.get("userID"):
            raise TesyCloudAuthError("login rejected")
        self.user = data

    async def load(self) -> None:
        """Log in, then read app settings and devices."""
        await self.login()
        settings = await self._json("GET", "get-app-settings", params=self._params())
        self.app_settings = settings if isinstance(settings, dict) else {}
        devices = await self._json("GET", "get-my-devices", params=self._params())
        if isinstance(devices, dict) and str(devices.get("error")) == "1":
            raise TesyCloudAuthError("get-my-devices rejected the login")
        self.devices = (
            {mac: dev for mac, dev in devices.items() if isinstance(dev, dict)}
            if isinstance(devices, dict) else {}
        )

    def broker(self) -> tuple[str, int, str, str]:
        """Broker host, port, username, password from the app settings."""
        s = self.app_settings
        host = str(s.get("MQTT_PROD_URL") or "").replace("wss://", "").replace("https://", "").strip("/")
        if not host.endswith(ALLOWED_BROKER_SUFFIX):
            raise TesyCloudError(f"unexpected broker host {host!r}")
        return host, int(s.get("MQTT_PORT") or 8083), str(s.get("MQTT_USERNAME") or ""), str(s.get("MQTT_PASS") or "")


class TesyCloudTelemetry:
    """MQTT part: subscribe to one device and keep its latest telemetry."""

    def __init__(self, hass: HomeAssistant, account: TesyCloudAccount, mac: str) -> None:
        self.hass = hass
        self.account = account
        self.mac = mac
        self.device = account.devices.get(mac, {})
        self.model = str(self.device.get("model") or "").lower()
        self._token = str(self.device.get("token") or "")
        self._client = None
        self._listeners: list[Callable[[], None]] = []
        self.connected = False
        self.last_error: str | None = None
        self.heating: bool | None = None
        self.current_temp: float | None = None
        self.last_telemetry: float | None = None
        self.messages: dict[str, dict] = {}  # last payload per command, for diagnostics
        self.subscribe_results: dict[str, Any] = {}

    @property
    def _base(self) -> str:
        return f"v1/{self.mac}/response/{self.model}/{self._token}"

    @callback
    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    async def async_start(self) -> None:
        import paho.mqtt.client as mqtt  # imported late: only needed with a cloud login

        host, port, user, password = self.account.broker()
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"ha-tesy-{uuid.uuid4().hex[:12]}",
            transport="websockets",
        )
        client.ws_set_options(path="/")
        client.tls_set_context(client_context())
        client.username_pw_set(user, password)
        # paho's own logger stays off: its debug lines would contain the device token
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
            await self.hass.async_add_executor_job(_stop_client, client)

    # paho callbacks run in paho's thread
    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        self.connected = not reason_code.is_failure
        self.last_error = None if self.connected else str(reason_code)
        if self.connected:
            for topic in (f"{self._base}/{TELEMETRY}", f"{self._base}/#"):
                _, mid = client.subscribe(topic)
                self.subscribe_results[topic.replace(self._token, "<token>")] = {"mid": mid}
        self.hass.loop.call_soon_threadsafe(self._notify)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        self.connected = False
        self.last_error = str(reason_code)
        self.hass.loop.call_soon_threadsafe(self._notify)

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties) -> None:
        for topic, info in self.subscribe_results.items():
            if info.get("mid") == mid:
                info["granted"] = [str(rc) for rc in reason_codes]

    def _on_message(self, client, userdata, msg) -> None:
        command = msg.topic.rsplit("/", 1)[-1]
        try:
            data = json.loads(msg.payload)
        except ValueError:
            data = {"raw": msg.payload[:200].decode(errors="replace")}
        self.messages[command] = {"at": time.time(), "retained": msg.retain, "data": data}
        if command == TELEMETRY and not msg.retain and isinstance(data, dict):
            payload = data.get("payload") if isinstance(data.get("payload"), dict) else data
            heating = payload.get("heating")
            if heating in ("on", "off"):
                self.heating = heating == "on"
                self.last_telemetry = time.time()
            try:
                temp = float(payload.get("currentTemp"))
                if math.isfinite(temp) and -40 <= temp <= 80:
                    self.current_temp = temp
            except (TypeError, ValueError):
                pass
        self.hass.loop.call_soon_threadsafe(self._notify)


def _stop_client(client) -> None:
    client.disconnect()
    client.loop_stop()
