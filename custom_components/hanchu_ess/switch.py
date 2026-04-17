from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import HanchuESSApi
from .const import CONF_SERIAL, DOMAIN
from .coordinator import HanchuConfigCoordinator
from .slot_definitions import (
    DEFAULT_ENABLED_SLOT_END_SECONDS,
    SLOT_DEFINITIONS,
    coerce_seconds,
    slot_is_enabled,
)


@dataclass(frozen=True, kw_only=True)
class HanchuSlotSwitchDescription(SwitchEntityDescription):
    start_key: str
    end_key: str


SWITCH_DESCRIPTIONS: tuple[HanchuSlotSwitchDescription, ...] = tuple(
    HanchuSlotSwitchDescription(
        key=f"{slot.key}_enabled",
        name=f"{slot.name} enabled",
        start_key=slot.start_key,
        end_key=slot.end_key,
        entity_registry_enabled_default=False,
    )
    for slot in SLOT_DEFINITIONS
)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    entry_data = hass.data[DOMAIN][entry.entry_id]
    api: HanchuESSApi = entry_data["api"]
    config_coordinator: HanchuConfigCoordinator = entry_data["config_coordinator"]
    serial = entry_data.get("serial") or entry.data.get(CONF_SERIAL)

    async_add_entities(
        HanchuSlotEnableSwitch(api, config_coordinator, serial, description)
        for description in SWITCH_DESCRIPTIONS
    )


class HanchuSlotEnableSwitch(CoordinatorEntity[HanchuConfigCoordinator], SwitchEntity):
    entity_description: HanchuSlotSwitchDescription

    def __init__(
        self,
        api: HanchuESSApi,
        coordinator: HanchuConfigCoordinator,
        serial: str,
        description: HanchuSlotSwitchDescription,
    ) -> None:
        super().__init__(coordinator)
        self._api = api
        self._serial = serial
        self.entity_description = description
        self._attr_has_entity_name = True
        self._attr_name = description.name
        self._attr_unique_id = f"{DOMAIN}_{serial}_{description.key}"
        self._last_enabled_start = 0
        self._last_enabled_end = DEFAULT_ENABLED_SLOT_END_SECONDS
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=f"Hanchu ESS {serial}",
            manufacturer="Hanchu",
            model="ESS Inverter",
        )

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data or {}
        start_value = data.get(self.entity_description.start_key)
        end_value = data.get(self.entity_description.end_key)
        enabled = slot_is_enabled(start_value, end_value)
        if enabled:
            start = coerce_seconds(start_value)
            end = coerce_seconds(end_value)
            if start is not None:
                self._last_enabled_start = start
            if end is not None:
                self._last_enabled_end = end
        return enabled

    async def async_turn_on(self, **kwargs) -> None:
        start = self._last_enabled_start
        end = self._last_enabled_end
        if start == 0 and end == 0:
            end = DEFAULT_ENABLED_SLOT_END_SECONDS
        await self._api.set_device_setting(self.entity_description.start_key, start)
        await self._api.set_device_setting(self.entity_description.end_key, end)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        if self.is_on:
            data = self.coordinator.data or {}
            start = coerce_seconds(data.get(self.entity_description.start_key))
            end = coerce_seconds(data.get(self.entity_description.end_key))
            if start is not None:
                self._last_enabled_start = start
            if end is not None:
                self._last_enabled_end = end
        await self._api.set_device_setting(self.entity_description.start_key, 0)
        await self._api.set_device_setting(self.entity_description.end_key, 0)
        await self.coordinator.async_request_refresh()

    @property
    def available(self) -> bool:
        data = self.coordinator.data or {}
        return (
            super().available
            and self.entity_description.start_key in data
            and self.entity_description.end_key in data
        )
