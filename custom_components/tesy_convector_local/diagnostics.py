"""Diagnostics download: local status and what the MyTESY cloud returned, secrets masked."""
from __future__ import annotations

import time
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_CLOUD_EMAIL, CONF_CLOUD_PASSWORD
from .coordinator import TesyConfigEntry

REDACT = {
    CONF_CLOUD_EMAIL, CONF_CLOUD_PASSWORD, "email", "password", "userEmail", "userPass", "userID",
    "token", "MQTT_USERNAME", "MQTT_PASS", "app_id", "wifi_pass", "ssid", "wifi_ssid", "ip",
    "acc_session", "session", "lat", "lng", "address", "mac", "MAC", "wan_ip", "deviceName",
    "firstName", "lastName", "oldAppEmail", "oldAppPassword",
}


def _mask_mac(mac: str) -> str:
    return mac[:8] + ":**:**:**" if len(mac) >= 8 else "**"


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: TesyConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    out: dict[str, Any] = {
        "mac_known": bool(entry.unique_id),
        "options": async_redact_data(dict(entry.options), REDACT),
        "local_status": coordinator.data,
        "cloud_error": coordinator.cloud_error,
    }
    if account := coordinator.cloud_account:
        out["cloud_login_keys"] = sorted(account.user)
        # app-wide vendor settings hold other keys and passwords: show only the broker address
        settings = account.app_settings
        out["app_settings"] = {
            "MQTT_PROD_URL": settings.get("MQTT_PROD_URL"),
            "MQTT_PORT": settings.get("MQTT_PORT"),
            "has_broker_login": bool(settings.get("MQTT_USERNAME") and settings.get("MQTT_PASS")),
            "keys": sorted(settings),
        }
        out["devices"] = {
            _mask_mac(mac): async_redact_data(dev, REDACT) for mac, dev in account.devices.items()
        }
    if cloud := coordinator.cloud:
        now = time.time()
        out["mqtt"] = {
            "connected": cloud.connected,
            "last_error": cloud.hub.last_error,
            "devices_on_connection": len(cloud.hub.devices),
            "model": cloud.model,
            "subscriptions": cloud.hub.subscriptions,
            "heating": cloud.heating,
            "current_temp": cloud.current_temp,
            "seconds_since_telemetry": round(now - cloud.last_telemetry) if cloud.last_telemetry else None,
            "last_messages": {
                cmd: {**msg, "age_s": round(now - msg["at"]), "data": async_redact_data(msg["data"], REDACT)}
                for cmd, msg in cloud.messages.items()
            },
        }
    return out
