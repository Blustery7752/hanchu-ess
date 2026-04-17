from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from homeassistant.components.time import TimeEntity, TimeEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import HanchuESSApi
from .const import CONF_SERIAL, DOMAIN
from .coordinator import HanchuConfigCoordinator
from .slot_definitions import SECONDS_PER_DAY, SLOT_DEFINITIONS, coerce_seconds


@dataclass(frozen=True, kw_only=True)
class HanchuTimeDescription(TimeEntityDescription):
    slot_key: str
    setting_key: str


TIME_DESCRIPTIONS: tuple[HanchuTimeDescription, ...] = tuple(
    description
    for slot in SLOT_DEFINITIONS
    for description in (
        HanchuTimeDescription(
            key=f"{slot.key}_start",
            name=f"{slot.name} start",
            slot_key=slot.key,
            setting_key=slot.start_key,
            entity_registry_enabled_default=False,
        ),
        HanchuTimeDescription(
            key=f"{slot.key}_end",
            name=f"{slot.name} end",
            slot_key=slot.key,
            setting_key=slot.end_key,
            entity_registry_enabled_default=False,
        ),
    )
)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    entry_data = hass.data[DOMAIN][entry.entry_id]
    api: HanchuESSApi = entry_data["api"]
    config_coordinator: HanchuConfigCoordinator = entry_data["config_coordinator"]
    serial = entry_data.get("serial") or entry.data.get(CONF_SERIAL)

    async_add_entities(
        HanchuSlotTimeEntity(api, config_coordinator, serial, description)
        for description in TIME_DESCRIPTIONS
    )


class HanchuSlotTimeEntity(CoordinatorEntity[HanchuConfigCoordinator], TimeEntity):
    entity_description: HanchuTimeDescription

    def __init__(
        self,
        api: HanchuESSApi,
        coordinator: HanchuConfigCoordinator,
        serial: str,
        description: HanchuTimeDescription,
    ) -> None:
        super().__init__(coordinator)
        self._api = api
        self._serial = serial
        self.entity_description = description
        self._attr_has_entity_name = True
        self._attr_name = description.name
        self._attr_unique_id = f"{DOMAIN}_{serial}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=f"Hanchu ESS {serial}",
            manufacturer="Hanchu",
            model="ESS Inverter",
        )

    @property
    def native_value(self) -> time | None:
        seconds = coerce_seconds((self.coordinator.data or {}).get(self.entity_description.setting_key))
        if seconds is None:
            return None
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return time(hour=hours, minute=minutes, second=seconds)

    async def async_set_value(self, value: time) -> None:
        seconds = (value.hour * 3600) + (value.minute * 60) + value.second
        seconds = max(0, min(SECONDS_PER_DAY - 1, seconds))
        await self._api.set_device_setting(self.entity_description.setting_key, seconds)
        await self.coordinator.async_request_refresh()

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None
