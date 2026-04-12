from __future__ import annotations
from typing import Any
import base64
import json
import time

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ApiCallError, HanchuESSApi
from .const import (
    DOMAIN,
    CONF_JWT,
    CONF_STATION_ID,
    CONF_SERIAL,
    CONF_BASE_URL,
    CONF_KEY,
    CONF_SCAN_INTERVAL,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL,
)

def _jwt_is_expired(jwt: str) -> bool:
    try:
        parts = jwt.split(".")
        if len(parts) != 3:
            return True
        s = parts[1]
        s += "=" * (-len(s) % 4)  # padding
        payload = json.loads(base64.urlsafe_b64decode(s).decode("utf-8"))
        exp = int(payload.get("exp", 0))
        return exp <= int(time.time()) + 60
    except Exception:
        return True


class HanchuConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._user_input: dict[str, Any] = {}
        self._station_choices: dict[str, dict[str, Any]] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if _jwt_is_expired(user_input[CONF_JWT]):
                errors[CONF_JWT] = "token_expired"
            else:
                session = async_get_clientsession(self.hass)
                api = HanchuESSApi(
                    session=session,
                    base_url=user_input[CONF_BASE_URL],
                    serial=None,
                    jwt=user_input[CONF_JWT],
                    key=user_input[CONF_KEY],
                )
                try:
                    stations = await api.query_station_list()
                except ApiCallError:
                    errors["base"] = "cannot_connect"
                else:
                    if not stations:
                        errors["base"] = "no_stations"
                    else:
                        self._user_input = user_input
                        self._station_choices = {
                            station["stationId"]: station
                            for station in stations
                            if isinstance(station.get("stationId"), str) and station.get("stationId")
                        }
                        if not self._station_choices:
                            errors["base"] = "no_stations"
                        elif len(self._station_choices) == 1:
                            only_station_id = next(iter(self._station_choices))
                            return await self._async_create_entry_for_station(only_station_id)
                        else:
                            return await self.async_step_select_station()

        schema = vol.Schema(
            {
                vol.Required(CONF_JWT): str,
                vol.Required(CONF_KEY): str,
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
        schema = vol.Schema(
            {
                vol.Required(CONF_STATION_ID): vol.In(station_options),
            }
        )
        return self.async_show_form(step_id="select_station", data_schema=schema, errors=errors)

    async def _async_create_entry_for_station(self, station_id: str) -> FlowResult:
        session = async_get_clientsession(self.hass)
        api = HanchuESSApi(
            session=session,
            base_url=self._user_input[CONF_BASE_URL],
            serial=None,
            jwt=self._user_input[CONF_JWT],
            key=self._user_input[CONF_KEY],
            station_id=station_id,
        )
        station_info = await api.fetch_station_info(station_id)
        serial = await api.resolve_inverter_serial(station_id)
        station_name = station_info.get("stationName") or self._station_choices.get(station_id, {}).get("stationName") or station_id
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
            jwt = merged.get(CONF_JWT)
            if jwt and _jwt_is_expired(jwt):
                return await self._show_form(errors={CONF_JWT: "token_expired"}, last_input=user_input)
            session = async_get_clientsession(self.hass)
            api = HanchuESSApi(
                session=session,
                base_url=merged.get(CONF_BASE_URL, DEFAULT_BASE_URL),
                serial=None,
                jwt=jwt,
                key=merged.get(CONF_KEY, ""),
            )
            try:
                stations = await api.query_station_list()
            except ApiCallError:
                return await self._show_form(errors={"base": "cannot_connect"}, last_input=user_input)

            self._pending_input = user_input
            self._station_choices = {
                station["stationId"]: station
                for station in stations
                if isinstance(station.get("stationId"), str) and station.get("stationId")
            }
            if not self._station_choices:
                return await self._show_form(errors={"base": "no_stations"}, last_input=user_input)

            requested_station_id = merged.get(CONF_STATION_ID)
            if requested_station_id in self._station_choices and len(self._station_choices) == 1:
                return await self._async_create_options_entry(requested_station_id)

            if requested_station_id in self._station_choices and len(self._station_choices) > 1:
                return await self.async_step_select_station(
                    {CONF_STATION_ID: requested_station_id}
                )

            if len(self._station_choices) == 1:
                return await self._async_create_options_entry(next(iter(self._station_choices)))

            return await self.async_step_select_station()
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
        schema = vol.Schema(
            {
                vol.Required(CONF_STATION_ID): vol.In(station_options),
            }
        )
        return self.async_show_form(step_id="select_station", data_schema=schema, errors=errors)

    async def _async_create_options_entry(self, station_id: str) -> FlowResult:
        merged = {**self._entry.data, **self._entry.options, **self._pending_input}
        session = async_get_clientsession(self.hass)
        api = HanchuESSApi(
            session=session,
            base_url=merged.get(CONF_BASE_URL, DEFAULT_BASE_URL),
            serial=None,
            jwt=merged.get(CONF_JWT, ""),
            key=merged.get(CONF_KEY, ""),
            station_id=station_id,
        )
        await api.resolve_inverter_serial(station_id)
        return self.async_create_entry(
            title="Options",
            data={**self._pending_input, CONF_STATION_ID: station_id, CONF_SERIAL: api.serial},
        )

    async def _show_form(self, errors=None, last_input=None) -> FlowResult:
        data = {**self._entry.data, **self._entry.options}
        schema = vol.Schema(
            {
                vol.Optional(CONF_JWT, default=(last_input or data).get(CONF_JWT, "")): str,
                vol.Optional(CONF_KEY, default=(last_input or data).get(CONF_KEY, "")): str,
                vol.Optional(CONF_SCAN_INTERVAL, default=(last_input or data).get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)): int,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors or {})
