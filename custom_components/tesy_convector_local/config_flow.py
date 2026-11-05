"""Config and options flow.

Setup: IP address + optional room temperature sensor.
Configure (options): the same, plus an optional MyTESY login for read-only cloud telemetry
(heating on/off, the convector's own temperature), then the matching cloud device.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
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

TEMP_SELECTOR = EntitySelector(EntitySelectorConfig(domain="sensor", device_class="temperature"))
EMAIL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL))
PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
NO_CLOUD_DEVICE = "none"


async def _validate(hass: HomeAssistant, host: str) -> str | None:
    """Return an error key, or None when the convector answers."""
    try:
        await TesyLocalApi(async_get_clientsession(hass), host).get_status()
    except TesyApiError as err:
        return "unsupported" if "not a supported" in str(err) else "cannot_connect"
    return None


def _optional(key: str, value: Any) -> vol.Optional:
    return vol.Optional(key, description={"suggested_value": value}) if value else vol.Optional(key)


def _local_schema(defaults: dict[str, Any]) -> dict:
    return {
        vol.Required(CONF_IP_ADDRESS, default=defaults.get(CONF_IP_ADDRESS, "")): str,
        _optional(CONF_TEMPERATURE_ENTITY, defaults.get(CONF_TEMPERATURE_ENTITY)): TEMP_SELECTOR,
    }


class TesyConvectorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a convector."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_IP_ADDRESS].strip()
            user_input[CONF_IP_ADDRESS] = host
            self._async_abort_entries_match({CONF_IP_ADDRESS: host})
            if not (error := await _validate(self.hass, host)):
                return self.async_create_entry(title=DEFAULT_NAME, data=user_input)
            errors["base"] = error
        return self.async_show_form(
            step_id="user", data_schema=vol.Schema(_local_schema(user_input or {})), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return TesyConvectorOptionsFlow()


class TesyConvectorOptionsFlow(OptionsFlow):
    """Change the IP, the room sensor, or the optional MyTESY login."""

    def __init__(self) -> None:
        self._options: dict[str, Any] = {}
        self._devices: dict[str, dict] = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        current = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            host = user_input[CONF_IP_ADDRESS].strip()
            user_input[CONF_IP_ADDRESS] = host
            email = (user_input.get(CONF_CLOUD_EMAIL) or "").strip()
            password = user_input.get(CONF_CLOUD_PASSWORD) or ""
            if email and not password and email == current.get(CONF_CLOUD_EMAIL):
                password = current.get(CONF_CLOUD_PASSWORD, "")  # blank = keep the saved one
            if error := await _validate(self.hass, host):
                errors["base"] = error
            elif email and not password:
                errors["base"] = "cloud_password_missing"
            elif not email:
                return self.async_create_entry(
                    data={CONF_IP_ADDRESS: host, CONF_TEMPERATURE_ENTITY: user_input.get(CONF_TEMPERATURE_ENTITY)}
                )
            else:
                account = TesyCloudAccount(async_get_clientsession(self.hass), email, password)
                try:
                    await account.load()
                except TesyCloudAuthError:
                    errors["base"] = "invalid_auth"
                except TesyCloudError:
                    errors["base"] = "cloud_cannot_connect"
                else:
                    self._options = {
                        CONF_IP_ADDRESS: host,
                        CONF_TEMPERATURE_ENTITY: user_input.get(CONF_TEMPERATURE_ENTITY),
                        CONF_CLOUD_EMAIL: email,
                        CONF_CLOUD_PASSWORD: password,
                    }
                    self._devices = account.devices
                    return await self.async_step_cloud_device()

        defaults = user_input or current
        schema = _local_schema(defaults)
        schema[_optional(CONF_CLOUD_EMAIL, defaults.get(CONF_CLOUD_EMAIL))] = EMAIL_SELECTOR
        schema[vol.Optional(CONF_CLOUD_PASSWORD)] = PASSWORD_SELECTOR
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema), errors=errors)

    async def async_step_cloud_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick which device on the MyTESY account is this convector."""
        if user_input is not None:
            device = user_input.get(CONF_CLOUD_DEVICE)
            self._options[CONF_CLOUD_DEVICE] = None if device == NO_CLOUD_DEVICE else device
            return self.async_create_entry(data=self._options)

        options = [
            SelectOptionDict(
                value=mac,
                label=f"{dev.get('deviceName') or dev.get('name') or 'Tesy'} – {dev.get('model', '?')} – {mac}",
            )
            for mac, dev in self._devices.items()
        ]
        options.append(SelectOptionDict(value=NO_CLOUD_DEVICE, label="—"))
        current = self.config_entry.options.get(CONF_CLOUD_DEVICE)
        default = current if current in self._devices else options[0]["value"]
        return self.async_show_form(
            step_id="cloud_device",
            data_schema=vol.Schema(
                {vol.Required(CONF_CLOUD_DEVICE, default=default): SelectSelector(
                    SelectSelectorConfig(options=options)
                )}
            ),
            description_placeholders={"count": str(len(self._devices))},
        )
