from __future__ import annotations

from dataclasses import dataclass


SECONDS_PER_DAY = 24 * 60 * 60
DEFAULT_ENABLED_SLOT_END_SECONDS = 60


@dataclass(frozen=True)
class HanchuSlotDefinition:
    key: str
    name: str
    start_key: str
    end_key: str


SLOT_DEFINITIONS: tuple[HanchuSlotDefinition, ...] = (
    HanchuSlotDefinition(
        key="charge_1",
        name="Charge slot 1",
        start_key="TCT_START_1",
        end_key="TCT_END_1",
    ),
    HanchuSlotDefinition(
        key="charge_2",
        name="Charge slot 2",
        start_key="TCT_START_2",
        end_key="TCT_END_2",
    ),
    HanchuSlotDefinition(
        key="charge_3",
        name="Charge slot 3",
        start_key="TCT_START_3",
        end_key="TCT_END_3",
    ),
    HanchuSlotDefinition(
        key="discharge_1",
        name="Discharge slot 1",
        start_key="TDT_START_1",
        end_key="TDT_END_1",
    ),
    HanchuSlotDefinition(
        key="discharge_2",
        name="Discharge slot 2",
        start_key="TDT_START_2",
        end_key="TDT_END_2",
    ),
    HanchuSlotDefinition(
        key="discharge_3",
        name="Discharge slot 3",
        start_key="TDT_START_3",
        end_key="TDT_END_3",
    ),
)


def coerce_seconds(value: object) -> int | None:
    try:
        if value is None:
            return None
        numeric = int(float(value))
    except (TypeError, ValueError):
        return None
    return max(0, min(SECONDS_PER_DAY - 1, numeric))


def slot_is_enabled(start_value: object, end_value: object) -> bool:
    start = coerce_seconds(start_value)
    end = coerce_seconds(end_value)
    return start is not None and end is not None and not (start == 0 and end == 0)
