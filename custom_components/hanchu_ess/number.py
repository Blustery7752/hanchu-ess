from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import (
    DEVICE_SETTING_GRID_CHARGE_LIMIT,
    HanchuESSApi,
)
from .const import DOMAIN, CONF_SERIAL
from .coordinator import HanchuConfigCoordinator


@dataclass(frozen=True, kw_only=True)
class HanchuNumberDescription(NumberEntityDescription):
    key: str


NUMBER_DESCRIPTIONS: tuple[HanchuNumberDescription, ...] = (
    HanchuNumberDescription(
        key="CHG_PWR_LMT",
        name="Charge power limit",
        native_min_value=0,
        native_max_value=100000,
        native_step=1,
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
    ),
    HanchuNumberDescription(
        key="DSCHG_PWR_LMT",
        name="Discharge power limit",
        native_min_value=0,
        native_max_value=100000,
        native_step=1,
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
    ),
    HanchuNumberDescription(
        key=DEVICE_SETTING_GRID_CHARGE_LIMIT,
        name="Grid charge limit",
        native_min_value=10,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
    ),
    HanchuNumberDescription(
        key="CHG_BAT_SOC_LMT",
        name="Maximum charge SoC",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
    ),
    HanchuNumberDescription(
        key="DSCHG_BAT_SOC_LMT",
        name="Minimum discharge SoC",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    entry_data = hass.data[DOMAIN][entry.entry_id]
    api: HanchuESSApi = entry_data["api"]
    config_coordinator: HanchuConfigCoordinator = entry_data["config_coordinator"]
    serial = entry_data.get("serial") or entry.data.get(CONF_SERIAL)

    entities = [
        HanchuSettingNumber(api, config_coordinator, serial, description)
        for description in NUMBER_DESCRIPTIONS
    ]
    async_add_entities(entities)


class HanchuSettingNumber(CoordinatorEntity[HanchuConfigCoordinator], NumberEntity):
    def __init__(
        self,
        api: HanchuESSApi,
        coordinator: HanchuConfigCoordinator,
        serial: str,
        description: HanchuNumberDescription,
    ) -> None:
        super().__init__(coordinator)
        self._api = api
        self._serial = serial
        self.entity_description = description
        self._key = description.key
        self._attr_has_entity_name = True
        self._attr_name = description.name
        self._attr_unique_id = f"{DOMAIN}_{serial}_{description.key.lower()}"
        self._attr_native_min_value = description.native_min_value
        self._attr_native_max_value = description.native_max_value
        self._attr_native_step = description.native_step
        self._attr_mode = NumberMode.BOX
        self._attr_native_unit_of_measurement = description.native_unit_of_measurement
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=f"Hanchu ESS {serial}",
            manufacturer="Hanchu",
            model="ESS Inverter",
        )

    @property
    def native_value(self) -> float | None:
        value = (self.coordinator.data or {}).get(self._key)
        try:
            if value is None:
                return None
            numeric_value = float(value)
            if self.entity_description.native_step == 1:
                return int(round(numeric_value))
            return numeric_value
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        await self._api.set_device_setting(self._key, int(round(value)))
        await self.coordinator.async_request_refresh()

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None
