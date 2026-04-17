from __future__ import annotations
import logging
from typing import Any
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN,
    PLATFORMS,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_STATION_ID,
    CONF_SERIAL,
    CONF_BASE_URL,
    CONF_KEY,
    CONF_IV,
    CONF_RSA_PUBLIC_KEY,
    CONF_SCAN_INTERVAL,
    CONF_CONFIG_SCAN_INTERVAL,
    CONF_WORK_MODE_OPTIONS,
)
from .api import HanchuESSApi
from .coordinator import HanchuConfigCoordinator, HanchuTelemetryCoordinator

_LOGGER = logging.getLogger(__name__)

# service constants
SERVICE_FAST_CD = "fast_charge_discharge"
SERVICE_SET_GRID_CHARGE_LIMIT = "set_grid_charge_limit"
ATTR_MODE = "mode"
ATTR_DURATION = "duration"
ATTR_SERIAL = "serial"  # optional, defaults to the current entry
ATTR_PERCENT = "percent"

MODES = {
    "start_charge": 2,
    "stop_charge": -2,
    "start_discharge": 3,
    "stop_discharge": -3,
}

SERVICE_SCHEMA = vol.Schema({
    vol.Required(ATTR_MODE): vol.In(list(MODES.keys())),
    vol.Optional(ATTR_DURATION): vol.All(int, vol.Range(min=1, max=86400)),
    vol.Optional(ATTR_SERIAL): cv.string,
})

GRID_CHARGE_LIMIT_SCHEMA = vol.Schema({
    vol.Required(ATTR_PERCENT): vol.All(int, vol.Range(min=10, max=100)),
    vol.Optional(ATTR_SERIAL): cv.string,
})

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Hanchu ESS from a config entry."""
    data = {**entry.data, **entry.options}
    session = async_get_clientsession(hass)

    api = HanchuESSApi(
        session=session,
        base_url=data[CONF_BASE_URL],
        serial=data.get(CONF_SERIAL),
        key=data[CONF_KEY],
        iv=data.get(CONF_IV),
        username=data[CONF_USERNAME],
        password=data[CONF_PASSWORD],
        rsa_public_key=data.get(CONF_RSA_PUBLIC_KEY),
        station_id=data.get(CONF_STATION_ID),
    )

    if data.get(CONF_STATION_ID):
        await api.resolve_inverter_serial(data[CONF_STATION_ID])

    coordinator = HanchuTelemetryCoordinator(hass, api, data.get(CONF_SCAN_INTERVAL))
    await coordinator.async_config_entry_first_refresh()

    config_coordinator = HanchuConfigCoordinator(hass, api, data.get(CONF_CONFIG_SCAN_INTERVAL))
    try:
        await config_coordinator.async_config_entry_first_refresh()
    except Exception as err:  # pragma: no cover - defensive startup fallback
        _LOGGER.warning(
            "Initial Hanchu ESS settings refresh failed for %s; number entities will load as unavailable until the next refresh: %s",
            api.serial or data.get(CONF_SERIAL) or "unknown device",
            err,
        )
        config_coordinator.async_set_updated_data({})

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "config_coordinator": config_coordinator,
        "serial": api.serial,
        "station_id": api.station_id,
        "rsa_public_key": data.get(CONF_RSA_PUBLIC_KEY),
        "work_mode_options": data.get(CONF_WORK_MODE_OPTIONS, []),
    }

    store = hass.data.setdefault(DOMAIN, {})
    store[entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "config_coordinator": config_coordinator,
        "serial": api.serial,
        "station_id": api.station_id,
        "rsa_public_key": data.get(CONF_RSA_PUBLIC_KEY),
        "work_mode_options": data.get(CONF_WORK_MODE_OPTIONS, []),
    }

    # Register the service once
    if not store.get("_service_registered"):
        async def _resolve_api_for_serial(serial: str | None):
            # If serial provided, find that entry; else use the (first) one
            for d in hass.data[DOMAIN].values():
                if not isinstance(d, dict) or "api" not in d:
                    continue
                if serial is None or d.get("serial") == serial or getattr(d["api"], "_serial", None) == serial:
                    return d["api"], d["coordinator"], d.get("config_coordinator")
            raise ValueError("No matching Hanchu ESS entry found for given serial.")

        async def handle_fast_cd(call):
            mode = call.data[ATTR_MODE]
            duration = call.data.get(ATTR_DURATION)
            serial = call.data.get(ATTR_SERIAL)

            act = MODES[mode]
            # enforce duration for start actions
            if act in (2, 3) and duration is None:
                raise vol.Invalid("duration is required for start_charge/start_discharge")

            api_obj, coord, config_coord = await _resolve_api_for_serial(serial)
            await api_obj.fast_charge_discharge(act=act, duration=duration)
            await coord.async_request_refresh()
            if config_coord is not None:
                await config_coord.async_request_refresh()

        async def handle_set_grid_charge_limit(call):
            percent = call.data[ATTR_PERCENT]
            serial = call.data.get(ATTR_SERIAL)

            api_obj, coord, config_coord = await _resolve_api_for_serial(serial)
            await api_obj.set_grid_charge_limit(percent=percent)
            await coord.async_request_refresh()
            if config_coord is not None:
                await config_coord.async_request_refresh()

        hass.services.async_register(
            DOMAIN,
            SERVICE_FAST_CD,
            handle_fast_cd,
            schema=SERVICE_SCHEMA,
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_GRID_CHARGE_LIMIT,
            handle_set_grid_charge_limit,
            schema=GRID_CHARGE_LIMIT_SCHEMA,
        )
        store["_service_registered"] = True

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok

