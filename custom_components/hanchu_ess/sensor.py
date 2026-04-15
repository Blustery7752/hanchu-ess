from __future__ import annotations

from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_SERIAL
from .coordinator import HanchuCoordinator

Transform = Callable[[dict], Any]

# If the API occasionally reports a lower total by ~25kWh, clamp it.
# Set this a bit above your observed dips.
TOTAL_GUARD_KWH = 60.0


def _pct_from_fraction(key: str) -> Transform:
    def fn(data: dict):
        v = data.get(key)
        try:
            return None if v is None else round(float(v) * 100.0, 2)
        except (TypeError, ValueError):
            return None
    return fn


def _float_key(key: str) -> Transform:
    def fn(data: dict):
        v = data.get(key)
        try:
            return None if v is None else float(v)
        except (TypeError, ValueError):
            return None
    return fn


def _battery_power_signed() -> Transform:
    """Keep API sign: negative = discharging, positive = charging."""
    def fn(data: dict):
        v = data.get("batP")
        try:
            return None if v is None else float(v)
        except (TypeError, ValueError):
            return None
    return fn


def _pos_part(key: str) -> Transform:
    """Return positive part of a numeric value (>=0), else 0."""
    def fn(data: dict):
        v = data.get(key)
        try:
            if v is None:
                return None
            f = float(v)
            return f if f > 0 else 0.0
        except (TypeError, ValueError):
            return None
    return fn


def _neg_part_abs(key: str) -> Transform:
    """Return absolute value of negative part (<=0), else 0."""
    def fn(data: dict):
        v = data.get(key)
        try:
            if v is None:
                return None
            f = float(v)
            return -f if f < 0 else 0.0
        except (TypeError, ValueError):
            return None
    return fn


SENSOR_SPECS = [
    # Power flows
    {"name": "PV Power", "unit": "W", "device_class": SensorDeviceClass.POWER,
     "state_class": SensorStateClass.MEASUREMENT, "value": _float_key("pvTtPwr")},
    {"name": "Load Power", "unit": "W", "device_class": SensorDeviceClass.POWER,
     "state_class": SensorStateClass.MEASUREMENT, "value": _float_key("loadPwr")},
    {"name": "Battery Power", "unit": "W", "device_class": SensorDeviceClass.POWER,
     "state_class": SensorStateClass.MEASUREMENT, "value": _battery_power_signed()},
    {"name": "Grid Meter Power", "unit": "W", "device_class": SensorDeviceClass.POWER,
     "state_class": SensorStateClass.MEASUREMENT, "value": _float_key("meterPPwr")},

    # Battery
    {"name": "Battery SoC", "unit": "%", "device_class": SensorDeviceClass.BATTERY,
     "state_class": SensorStateClass.MEASUREMENT, "value": _pct_from_fraction("batSoc")},
    {"name": "Battery Voltage", "unit": "V", "device_class": SensorDeviceClass.VOLTAGE,
     "state_class": SensorStateClass.MEASUREMENT, "value": _float_key("batV")},
    {"name": "Battery Current", "unit": "A", "device_class": SensorDeviceClass.CURRENT,
     "state_class": SensorStateClass.MEASUREMENT, "value": _float_key("batI")},

    # Grid / AC
    {"name": "Grid Frequency", "unit": "Hz", "device_class": SensorDeviceClass.FREQUENCY,
     "state_class": SensorStateClass.MEASUREMENT, "value": _float_key("gridFreq")},
    {"name": "Line Voltage L1", "unit": "V", "device_class": SensorDeviceClass.VOLTAGE,
     "state_class": SensorStateClass.MEASUREMENT, "value": _float_key("vL1")},

    # Inverter temperatures
    {"name": "Inverter Temp Heatsink", "unit": "°C", "device_class": SensorDeviceClass.TEMPERATURE,
     "state_class": SensorStateClass.MEASUREMENT, "value": _float_key("tHs")},
    {"name": "Inverter Temp Upper", "unit": "°C", "device_class": SensorDeviceClass.TEMPERATURE,
     "state_class": SensorStateClass.MEASUREMENT, "value": _float_key("tU")},
    {"name": "Inverter Temp Boost", "unit": "°C", "device_class": SensorDeviceClass.TEMPERATURE,
     "state_class": SensorStateClass.MEASUREMENT, "value": _float_key("tBoost")},

    # Energy (kWh)
    {
        "name": "PV Energy Today",
        "unit": "kWh",
        "device_class": SensorDeviceClass.ENERGY,
        "state_class": SensorStateClass.TOTAL,  # resets at midnight per API
        "value": _float_key("pvDge"),
    },

    # ---- Energy totals for Energy dashboard (kWh) ----
    {
        "name": "Grid Import Energy Total",
        "unit": "kWh",
        "device_class": SensorDeviceClass.ENERGY,
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "value": _float_key("gridTtEe"),
    },
    {
        "name": "Grid Export Energy Total",
        "unit": "kWh",
        "device_class": SensorDeviceClass.ENERGY,
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "value": _float_key("gridTtFe"),
    },
    {
        "name": "Load Energy Total",
        "unit": "kWh",
        "device_class": SensorDeviceClass.ENERGY,
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "value": _float_key("loadTtEe"),
    },
    {
        "name": "PV Energy Total",
        "unit": "kWh",
        "device_class": SensorDeviceClass.ENERGY,
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "value": _float_key("pvTge"),
    },

    # ---- Battery directional power (W) ----
    {
        "name": "Battery Charge Power",
        "unit": "W",
        "device_class": SensorDeviceClass.POWER,
        "state_class": SensorStateClass.MEASUREMENT,
        "value": _pos_part("batP"),
    },
    {
        "name": "Battery Discharge Power",
        "unit": "W",
        "device_class": SensorDeviceClass.POWER,
        "state_class": SensorStateClass.MEASUREMENT,
        "value": _neg_part_abs("batP"),
    },
]


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities):
    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: HanchuCoordinator = entry_data["coordinator"]
    serial = entry_data.get("serial") or entry.data.get(CONF_SERIAL)

    entities: list[SensorEntity] = []
    current = coordinator.data or {}
    for spec in SENSOR_SPECS:
        try:
            if spec["value"](current) is not None:
                entities.append(HanchuTransformedSensor(coordinator, serial, spec))
        except Exception:
            continue

    entities.append(HanchuDiagnosticsSensor(coordinator, serial))
    async_add_entities(entities)


class BaseHanchuSensor(CoordinatorEntity[HanchuCoordinator], SensorEntity):
    def __init__(self, coordinator: HanchuCoordinator, serial: str, name_suffix: str) -> None:
        super().__init__(coordinator)
        self._attr_has_entity_name = True
        self._attr_name = name_suffix
        self._serial = serial
        self._attr_unique_id = f"{DOMAIN}_{serial}_{name_suffix.lower().replace(' ', '_')}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=f"Hanchu ESS {serial}",
            manufacturer="Hanchu",
            model="ESS Inverter",
        )


class HanchuTransformedSensor(BaseHanchuSensor, RestoreEntity):
    def __init__(self, coordinator: HanchuCoordinator, serial: str, spec: dict[str, Any]) -> None:
        super().__init__(coordinator, serial, spec["name"])
        self._spec = spec
        self._attr_native_unit_of_measurement = spec.get("unit")
        self._attr_device_class = spec.get("device_class")
        self._attr_state_class = spec.get("state_class")

        # For TOTAL_INCREASING monotonic guard
        self._last_good: float | None = None

        # We'll store the computed value here and let HA read it.
        self._attr_native_value: Any = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        # Restore last state so we don't "forget" the monotonic baseline on restart
        last = await self.async_get_last_state()
        if last and last.state not in (None, "unknown", "unavailable"):
            try:
                restored = float(last.state)
                self._attr_native_value = restored
                if self._attr_state_class == SensorStateClass.TOTAL_INCREASING:
                    self._last_good = restored
            except ValueError:
                pass

        # Also compute an initial value from current coordinator data if available
        self._handle_coordinator_update()

    def _handle_coordinator_update(self) -> None:
        # Compute raw value from coordinator payload
        try:
            raw = self._spec["value"](self.coordinator.data or {})
        except Exception:
            raw = None

        if raw is None:
            self._attr_native_value = None
            self.async_write_ha_state()
            return

        # Apply monotonic guard only for TOTAL_INCREASING sensors
        if self._attr_state_class == SensorStateClass.TOTAL_INCREASING:
            try:
                raw_f = float(raw)
            except (TypeError, ValueError):
                self._attr_native_value = None
                self.async_write_ha_state()
                return

            if self._last_good is None:
                self._last_good = raw_f
            else:
                # Decrease detected
                if raw_f + 1e-9 < self._last_good:
                    drop = self._last_good - raw_f
                    # Ignore the glitchy "small-ish" drops
                    if drop <= TOTAL_GUARD_KWH:
                        raw_f = self._last_good
                    else:
                        # Big drop: treat as a real reset and accept it
                        self._last_good = raw_f
                else:
                    self._last_good = raw_f

            self._attr_native_value = self._last_good
        else:
            # Non-total sensors: just pass through
            self._attr_native_value = raw

        self.async_write_ha_state()

    @property
    def native_value(self):
        return self._attr_native_value

    @property
    def available(self) -> bool:
        return super().available and self._attr_native_value is not None


class HanchuDiagnosticsSensor(BaseHanchuSensor):
    def __init__(self, coordinator: HanchuCoordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "Diagnostics")

    @property
    def native_value(self):
        return 1

    @property
    def extra_state_attributes(self):
        return self.coordinator.data or {}
