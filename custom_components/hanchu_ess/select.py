from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import DEVICE_SETTING_WORK_MODE, HanchuESSApi
from .const import CONF_SERIAL, DOMAIN
from .coordinator import HanchuConfigCoordinator


@dataclass(frozen=True, kw_only=True)
class HanchuSelectDescription(SelectEntityDescription):
    key: str


WORK_MODE_DESCRIPTION = HanchuSelectDescription(
    key=DEVICE_SETTING_WORK_MODE,
    name="Work mode",
    options=[],
)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    entry_data = hass.data[DOMAIN][entry.entry_id]
    api: HanchuESSApi = entry_data["api"]
    config_coordinator: HanchuConfigCoordinator = entry_data["config_coordinator"]
    serial = entry_data.get("serial") or entry.data.get(CONF_SERIAL)
    work_mode_options = entry_data.get("work_mode_options", [])

    async_add_entities(
        [HanchuWorkModeSelect(api, config_coordinator, serial, WORK_MODE_DESCRIPTION, work_mode_options)]
    )


class HanchuWorkModeSelect(CoordinatorEntity[HanchuConfigCoordinator], SelectEntity):
    entity_description: HanchuSelectDescription

    def __init__(
        self,
        api: HanchuESSApi,
        coordinator: HanchuConfigCoordinator,
        serial: str,
        description: HanchuSelectDescription,
        work_mode_options: list[dict[str, str]],
    ) -> None:
        super().__init__(coordinator)
        self._api = api
        self._serial = serial
        self.entity_description = description
        self._code_to_label = {
            str(option["value"]): str(option["name"])
            for option in work_mode_options
            if isinstance(option, dict) and "value" in option and "name" in option
        }
        self._label_to_code = {label: code for code, label in self._code_to_label.items()}
        self._attr_has_entity_name = True
        self._attr_name = description.name
        self._attr_unique_id = f"{DOMAIN}_{serial}_{description.key.lower()}"
        self._attr_options = list(self._label_to_code)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=f"Hanchu ESS {serial}",
            manufacturer="Hanchu",
            model="ESS Inverter",
        )

    @property
    def current_option(self) -> str | None:
        value = (self.coordinator.data or {}).get(self.entity_description.key)
        if value is None:
            return None
        return self._code_to_label.get(str(value))

    async def async_select_option(self, option: str) -> None:
        code = self._label_to_code[option]
        await self._api.set_device_setting(self.entity_description.key, int(code))
        await self.coordinator.async_request_refresh()

    @property
    def available(self) -> bool:
        return super().available and bool(self._attr_options) and self.current_option is not None
