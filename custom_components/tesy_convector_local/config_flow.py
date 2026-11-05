"""Setup and Configure.

Identity is the convector's MAC (config entry unique_id). It comes from DHCP discovery, or,
when the IP is typed in, from the host's neighbour table. With the MAC known:
- a new IP is picked up automatically when the convector is rediscovered (no DHCP reservation needed),
- the MyTESY cloud device is matched automatically (the device list is only a fallback).

Configure: room temperature sensor, IP (rarely needed), optional MyTESY login. One login can be
shared by several convectors: leave the password blank to reuse a saved one for that email.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from .api import TesyApiError, TesyLocalApi
from .cloud import TesyCloudAccount, TesyCloudAuthError, TesyCloudError
from .const import (
    CONF_CLOUD_DEVICE,
    CONF_CLOUD_EMAIL,
    CONF_CLOUD_PASSWORD,
    CONF_IP_ADDRESS,
    CONF_TEMPERATURE_ENTITY,
    DEFAULT_NAME,
    DOMAIN,
)
from .net import async_mac_for_ip

TEMP_SELECTOR = EntitySelector(EntitySelectorConfig(domain="sensor", device_class="temperature"))
EMAIL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL))
PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
NO_CLOUD_DEVICE = "none"


async def _probe(hass: HomeAssistant, host: str) -> str | None:
    """Return an error key, or None when a Tesy convector answers at host."""
    try:
        await TesyLocalApi(async_get_clientsession(hass), host).get_status()
    except TesyApiError as err:
        return "unsupported" if "not a supported" in str(err) else "cannot_connect"
    return None


def _optional(key: str, value: Any) -> vol.Optional:
    return vol.Optional(key, description={"suggested_value": value}) if value else vol.Optional(key)


class TesyConvectorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a convector: discovered, or by IP."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> ConfigFlowResult:
        """An Espressif device asked for an address: is it a Tesy convector?"""
        host = discovery_info.ip
        mac = format_mac(discovery_info.macaddress)
        await self.async_set_unique_id(mac)
        self._abort_if_unique_id_configured(updates={CONF_IP_ADDRESS: host})
        # entries made before MAC identity: adopt the one at this IP
        for entry in self._async_current_entries(include_ignore=False):
            if entry.unique_id is None and entry.data.get(CONF_IP_ADDRESS) == host:
                self.hass.config_entries.async_update_entry(entry, unique_id=mac)
                return self.async_abort(reason="already_configured")
        if await _probe(self.hass, host):
            return self.async_abort(reason="not_tesy_device")
        self._host = host
        self.context["title_placeholders"] = {"host": host}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title=DEFAULT_NAME,
                data={CONF_IP_ADDRESS: self._host},
                options={CONF_TEMPERATURE_ENTITY: user_input.get(CONF_TEMPERATURE_ENTITY)},
            )
        return self.async_show_form(
            step_id="discovery_confirm",
            data_schema=vol.Schema({vol.Optional(CONF_TEMPERATURE_ENTITY): TEMP_SELECTOR}),
            description_placeholders={"host": self._host or ""},
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_IP_ADDRESS].strip()
            self._async_abort_entries_match({CONF_IP_ADDRESS: host})
            if not (error := await _probe(self.hass, host)):
                if mac := await async_mac_for_ip(self.hass, host):
                    await self.async_set_unique_id(mac)
                    self._abort_if_unique_id_configured(updates={CONF_IP_ADDRESS: host})
                return self.async_create_entry(
                    title=DEFAULT_NAME,
                    data={CONF_IP_ADDRESS: host},
                    options={CONF_TEMPERATURE_ENTITY: user_input.get(CONF_TEMPERATURE_ENTITY)},
                )
            errors["base"] = error
        defaults = user_input or {}
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_IP_ADDRESS, default=defaults.get(CONF_IP_ADDRESS, "")): str,
                    _optional(CONF_TEMPERATURE_ENTITY, defaults.get(CONF_TEMPERATURE_ENTITY)): TEMP_SELECTOR,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return TesyConvectorOptionsFlow()


class TesyConvectorOptionsFlow(OptionsFlow):
    """Room sensor, IP, optional MyTESY login."""

    def __init__(self) -> None:
        self._options: dict[str, Any] = {}
        self._devices: dict[str, dict] = {}

    def _saved_password(self, email: str) -> str:
        """A saved password for this email, from this or another convector."""
        entries = [self.config_entry, *self.hass.config_entries.async_entries(DOMAIN)]
        for entry in entries:
            if (entry.options.get(CONF_CLOUD_EMAIL) or "").lower() == email.lower():
                if password := entry.options.get(CONF_CLOUD_PASSWORD):
                    return password
        return ""

    def _other_email(self) -> str | None:
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.entry_id != self.config_entry.entry_id and entry.options.get(CONF_CLOUD_EMAIL):
                return entry.options[CONF_CLOUD_EMAIL]
        return None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self.config_entry
        if user_input is not None:
            host = user_input[CONF_IP_ADDRESS].strip()
            email = (user_input.get(CONF_CLOUD_EMAIL) or "").strip()
            password = user_input.get(CONF_CLOUD_PASSWORD) or (self._saved_password(email) if email else "")
            if host != entry.data.get(CONF_IP_ADDRESS) and (error := await _probe(self.hass, host)):
                errors["base"] = error
            elif email and not password:
                errors["base"] = "cloud_password_missing"
            else:
                if host != entry.data.get(CONF_IP_ADDRESS):
                    self.hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_IP_ADDRESS: host})
                self._options = {CONF_TEMPERATURE_ENTITY: user_input.get(CONF_TEMPERATURE_ENTITY)}
                if not email:
                    return self.async_create_entry(data=self._options)
                account = TesyCloudAccount(async_get_clientsession(self.hass), email, password)
                try:
                    await account.load()
                except TesyCloudAuthError:
                    errors["base"] = "invalid_auth"
                except TesyCloudError:
                    errors["base"] = "cloud_cannot_connect"
                else:
                    self._options |= {CONF_CLOUD_EMAIL: email, CONF_CLOUD_PASSWORD: password}
                    if matched := account.find_device(entry.unique_id):
                        self._options[CONF_CLOUD_DEVICE] = matched
                        return self.async_create_entry(data=self._options)
                    if not account.devices:
                        self._options[CONF_CLOUD_DEVICE] = None
                        return self.async_create_entry(data=self._options)
                    self._devices = account.devices
                    return await self.async_step_cloud_device()

        defaults = {**entry.options, **(user_input or {})}
        email = defaults.get(CONF_CLOUD_EMAIL) or self._other_email()
        schema = {
            _optional(CONF_TEMPERATURE_ENTITY, defaults.get(CONF_TEMPERATURE_ENTITY)): TEMP_SELECTOR,
            _optional(CONF_CLOUD_EMAIL, email): EMAIL_SELECTOR,
            vol.Optional(CONF_CLOUD_PASSWORD): PASSWORD_SELECTOR,
            vol.Required(CONF_IP_ADDRESS, default=entry.data.get(CONF_IP_ADDRESS, "")): str,
        }
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={"mac": entry.unique_id or "?"},
        )

    async def async_step_cloud_device(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Fallback when the MAC isn't known or isn't on the account."""
        if user_input is not None:
            device = user_input.get(CONF_CLOUD_DEVICE)
            self._options[CONF_CLOUD_DEVICE] = None if device == NO_CLOUD_DEVICE else device
            return self.async_create_entry(data=self._options)
        options = [
            SelectOptionDict(
                value=key,
                label=f"{dev.get('deviceName') or dev.get('name') or 'Tesy'} – {dev.get('model', '?')} – {key}",
            )
            for key, dev in self._devices.items()
        ]
        options.append(SelectOptionDict(value=NO_CLOUD_DEVICE, label="—"))
        return self.async_show_form(
            step_id="cloud_device",
            data_schema=vol.Schema(
                {vol.Required(CONF_CLOUD_DEVICE, default=options[0]["value"]): SelectSelector(
                    SelectSelectorConfig(options=options)
                )}
            ),
            description_placeholders={"count": str(len(self._devices)), "mac": self.config_entry.unique_id or "?"},
        )
