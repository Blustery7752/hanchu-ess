from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    DEFAULT_DEVICE_SETTING_KEYS,
    HanchuESSApi,
    ApiCallError,
    UnauthorizedError,
)
from .const import DOMAIN, DEFAULT_CONFIG_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class HanchuTelemetryCoordinator(DataUpdateCoordinator[Dict[str, Any]]):
    """Coordinator to fetch live Hanchu ESS telemetry on a schedule."""

    def __init__(self, hass: HomeAssistant, api: HanchuESSApi, scan_interval: int | None = None) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} telemetry coordinator",
            update_interval=timedelta(seconds=scan_interval or DEFAULT_SCAN_INTERVAL),
        )
        self.api = api

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch latest data from the API."""
        try:
            data = await self.api.fetch_power_chart()
            grid_charge_limit = await self.api.get_grid_charge_limit()
            if grid_charge_limit is not None:
                data["dtu_ac_chg_soc_lmt"] = grid_charge_limit
            return data
        except UnauthorizedError as err:
        # This is what will make sensors become unavailable
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except ApiCallError as err:
            # Surfaces as a retryable coordinator failure (shows in HA logs/UI)
            raise UpdateFailed(str(err)) from err


class HanchuConfigCoordinator(DataUpdateCoordinator[Dict[str, Any]]):
    """Coordinator to fetch slower-changing Hanchu ESS device settings."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: HanchuESSApi,
        scan_interval: int | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} config coordinator",
            update_interval=timedelta(seconds=scan_interval or DEFAULT_CONFIG_SCAN_INTERVAL),
        )
        self.api = api

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch configured device settings using the standard DTU key list."""
        try:
            settings = await self.api.get_device_settings(DEFAULT_DEVICE_SETTING_KEYS)
            return settings if isinstance(settings, dict) else {}
        except UnauthorizedError as err:
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except ApiCallError as err:
            raise UpdateFailed(str(err)) from err
