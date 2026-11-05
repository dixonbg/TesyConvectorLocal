"""Config and options flow: IP address + optional room temperature sensor."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import EntitySelector, EntitySelectorConfig

from .api import TesyApiError, TesyLocalApi
from .const import CONF_IP_ADDRESS, CONF_TEMPERATURE_ENTITY, DEFAULT_NAME, DOMAIN

TEMP_SELECTOR = EntitySelector(EntitySelectorConfig(domain="sensor", device_class="temperature"))


async def _validate(hass: HomeAssistant, host: str) -> str | None:
    """Return an error key, or None when the convector answers."""
    try:
        await TesyLocalApi(async_get_clientsession(hass), host).get_status()
    except TesyApiError as err:
        return "unsupported" if "not a supported" in str(err) else "cannot_connect"
    return None


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    temp = defaults.get(CONF_TEMPERATURE_ENTITY)
    temp_key = (
        vol.Optional(CONF_TEMPERATURE_ENTITY, description={"suggested_value": temp})
        if temp
        else vol.Optional(CONF_TEMPERATURE_ENTITY)
    )
    return vol.Schema(
        {
            vol.Required(CONF_IP_ADDRESS, default=defaults.get(CONF_IP_ADDRESS, "")): str,
            temp_key: TEMP_SELECTOR,
        }
    )


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
            step_id="user", data_schema=_schema(user_input or {}), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return TesyConvectorOptionsFlow()


class TesyConvectorOptionsFlow(OptionsFlow):
    """Change the IP address or the room temperature sensor."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_IP_ADDRESS].strip()
            user_input[CONF_IP_ADDRESS] = host
            if not (error := await _validate(self.hass, host)):
                return self.async_create_entry(data=user_input)
            errors["base"] = error
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init", data_schema=_schema(user_input or current), errors=errors
        )
