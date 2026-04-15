from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ApiCallError, HanchuESSApi, UnauthorizedError
from .const import (
    CONF_BASE_URL,
    CONF_IV,
    CONF_KEY,
    CONF_PASSWORD,
    CONF_RSA_PUBLIC_KEY,
    CONF_SCAN_INTERVAL,
    CONF_SERIAL,
    CONF_STATION_ID,
    CONF_USERNAME,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)


class HanchuConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._user_input: dict[str, Any] = {}
        self._station_choices: dict[str, dict[str, Any]] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._async_prepare_account(user_input)
            if not errors:
                if len(self._station_choices) == 1:
                    only_station_id = next(iter(self._station_choices))
                    return await self._async_create_entry_for_station(only_station_id)
                return await self.async_step_select_station()

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
                vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): int,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_select_station(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            station_id = user_input[CONF_STATION_ID]
            if station_id not in self._station_choices:
                errors["base"] = "invalid_station"
            else:
                return await self._async_create_entry_for_station(station_id)

        station_options = {
            station_id: self._station_label(station)
            for station_id, station in self._station_choices.items()
        }
        schema = vol.Schema({vol.Required(CONF_STATION_ID): vol.In(station_options)})
        return self.async_show_form(step_id="select_station", data_schema=schema, errors=errors)

    async def _async_prepare_account(self, user_input: dict[str, Any]) -> dict[str, str]:
        session = async_get_clientsession(self.hass)
        discovery_api = HanchuESSApi(
            session=session,
            base_url=user_input[CONF_BASE_URL],
            serial=None,
            key="",
            username=user_input[CONF_USERNAME],
            password=user_input[CONF_PASSWORD],
        )

        try:
            key, iv = await discovery_api.discover_crypto_material()
            rsa_public_key = await discovery_api.discover_rsa_public_key()
        except ApiCallError:
            return {"base": "cannot_resolve_crypto"}

        api = HanchuESSApi(
            session=session,
            base_url=user_input[CONF_BASE_URL],
            serial=None,
            key=key,
            iv=iv,
            username=user_input[CONF_USERNAME],
            password=user_input[CONF_PASSWORD],
            rsa_public_key=rsa_public_key,
        )
        try:
            await api.async_login()
            stations = await api.query_station_list()
        except UnauthorizedError:
            return {"base": "invalid_auth"}
        except ApiCallError:
            return {"base": "cannot_connect"}

        if not stations:
            return {"base": "no_stations"}

        self._user_input = {
            **user_input,
            CONF_KEY: key,
            CONF_IV: iv,
            CONF_RSA_PUBLIC_KEY: rsa_public_key,
        }
        self._station_choices = {
            station["stationId"]: station
            for station in stations
            if isinstance(station.get("stationId"), str) and station.get("stationId")
        }
        if not self._station_choices:
            return {"base": "no_stations"}
        return {}

    async def _async_create_entry_for_station(self, station_id: str) -> FlowResult:
        session = async_get_clientsession(self.hass)
        api = HanchuESSApi(
            session=session,
            base_url=self._user_input[CONF_BASE_URL],
            serial=None,
            key=self._user_input[CONF_KEY],
            iv=self._user_input[CONF_IV],
            username=self._user_input[CONF_USERNAME],
            password=self._user_input[CONF_PASSWORD],
            rsa_public_key=self._user_input[CONF_RSA_PUBLIC_KEY],
            station_id=station_id,
        )
        station_info = await api.fetch_station_info(station_id)
        serial = await api.resolve_inverter_serial(station_id)
        station_name = (
            station_info.get("stationName")
            or self._station_choices.get(station_id, {}).get("stationName")
            or station_id
        )
        return self.async_create_entry(
            title=f"Hanchu {station_name}".strip(),
            data={**self._user_input, CONF_STATION_ID: station_id, CONF_SERIAL: serial},
        )

    @staticmethod
    def _station_label(station: dict[str, Any]) -> str:
        station_name = str(station.get("stationName") or "").strip() or str(station.get("stationId") or "")
        station_id = str(station.get("stationId") or "").strip()
        return f"{station_name} ({station_id})" if station_id else station_name

    async def async_step_import(self, user_input: dict[str, Any]) -> FlowResult:
        return await self.async_step_user(user_input)

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return HanchuOptionsFlowHandler(config_entry)


class HanchuOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry
        self._pending_input: dict[str, Any] = {}
        self._station_choices: dict[str, dict[str, Any]] = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            merged = {**self._entry.data, **self._entry.options, **user_input}
            errors = await self._async_prepare_account(merged, user_input)
            if not errors:
                requested_station_id = merged.get(CONF_STATION_ID)
                if requested_station_id in self._station_choices:
                    return await self._async_create_options_entry(requested_station_id)
                if len(self._station_choices) == 1:
                    return await self._async_create_options_entry(next(iter(self._station_choices)))
                return await self.async_step_select_station()
            return await self._show_form(errors=errors, last_input=user_input)
        return await self._show_form()

    async def async_step_select_station(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            station_id = user_input[CONF_STATION_ID]
            if station_id not in self._station_choices:
                errors["base"] = "invalid_station"
            else:
                return await self._async_create_options_entry(station_id)

        station_options = {
            station_id: HanchuConfigFlow._station_label(station)
            for station_id, station in self._station_choices.items()
        }
        schema = vol.Schema({vol.Required(CONF_STATION_ID): vol.In(station_options)})
        return self.async_show_form(step_id="select_station", data_schema=schema, errors=errors)

    async def _async_prepare_account(
        self, merged: dict[str, Any], raw_input: dict[str, Any]
    ) -> dict[str, str]:
        session = async_get_clientsession(self.hass)
        discovery_api = HanchuESSApi(
            session=session,
            base_url=merged.get(CONF_BASE_URL, DEFAULT_BASE_URL),
            serial=None,
            key="",
            username=merged.get(CONF_USERNAME),
            password=merged.get(CONF_PASSWORD),
        )
        try:
            key, iv = await discovery_api.discover_crypto_material()
            rsa_public_key = await discovery_api.discover_rsa_public_key()
        except ApiCallError:
            return {"base": "cannot_resolve_crypto"}

        api = HanchuESSApi(
            session=session,
            base_url=merged.get(CONF_BASE_URL, DEFAULT_BASE_URL),
            serial=None,
            key=key,
            iv=iv,
            username=merged.get(CONF_USERNAME),
            password=merged.get(CONF_PASSWORD),
            rsa_public_key=rsa_public_key,
        )
        try:
            await api.async_login()
            stations = await api.query_station_list()
        except UnauthorizedError:
            return {"base": "invalid_auth"}
        except ApiCallError:
            return {"base": "cannot_connect"}

        self._pending_input = {
            **raw_input,
            CONF_KEY: key,
            CONF_IV: iv,
            CONF_RSA_PUBLIC_KEY: rsa_public_key,
        }
        self._station_choices = {
            station["stationId"]: station
            for station in stations
            if isinstance(station.get("stationId"), str) and station.get("stationId")
        }
        if not self._station_choices:
            return {"base": "no_stations"}
        return {}

    async def _async_create_options_entry(self, station_id: str) -> FlowResult:
        merged = {**self._entry.data, **self._entry.options, **self._pending_input}
        session = async_get_clientsession(self.hass)
        api = HanchuESSApi(
            session=session,
            base_url=merged.get(CONF_BASE_URL, DEFAULT_BASE_URL),
            serial=None,
            key=merged.get(CONF_KEY, ""),
            iv=merged.get(CONF_IV),
            username=merged.get(CONF_USERNAME, ""),
            password=merged.get(CONF_PASSWORD, ""),
            rsa_public_key=merged.get(CONF_RSA_PUBLIC_KEY),
            station_id=station_id,
        )
        await api.resolve_inverter_serial(station_id)
        return self.async_create_entry(
            title="Options",
            data={**self._pending_input, CONF_STATION_ID: station_id, CONF_SERIAL: api.serial},
        )

    async def _show_form(self, errors=None, last_input=None) -> FlowResult:
        data = {**self._entry.data, **self._entry.options}
        source = last_input or data
        schema = vol.Schema(
            {
                vol.Optional(CONF_USERNAME, default=source.get(CONF_USERNAME, "")): str,
                vol.Optional(CONF_PASSWORD, default=source.get(CONF_PASSWORD, "")): str,
                vol.Optional(CONF_BASE_URL, default=source.get(CONF_BASE_URL, DEFAULT_BASE_URL)): str,
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=source.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): int,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors or {})
