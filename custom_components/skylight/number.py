"""Number platform: numeric Skylight device settings."""

from __future__ import annotations

import logging

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import SkylightDeviceEntity

_LOGGER = logging.getLogger(__name__)


NUMBERS: tuple[NumberEntityDescription, ...] = (
    NumberEntityDescription(
        key="brightness",
        name="Brightness",
        icon="mdi:brightness-6",
        native_min_value=0,
        native_max_value=255,
        native_step=1,
    ),
    NumberEntityDescription(
        key="slideshow_speed",
        name="Slideshow speed",
        icon="mdi:timer-outline",
        native_min_value=0,
        native_max_value=240,
        native_step=1,
        native_unit_of_measurement="s",
    ),
    # The 0-100 ranges below are inferred, not observed: a real frame reports
    # nightlight_brightness=65 and sleep_sound_volume=70, which rules out the
    # 0-255 scale `brightness` uses but doesn't prove the ceiling. If the frame
    # rejects a high value, this is the first place to look.
    NumberEntityDescription(
        key="nightlight_brightness",
        name="Night light brightness",
        icon="mdi:brightness-4",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement="%",
        entity_category=EntityCategory.CONFIG,
    ),
    NumberEntityDescription(
        key="sleep_sound_volume",
        name="Sleep sound volume",
        icon="mdi:volume-medium",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement="%",
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        SkylightDeviceNumber(
            data["frame_coordinator"],
            data["api"],
            data["frame_id"],
            data["frame_name"],
            description,
        )
        for description in NUMBERS
    )


class SkylightDeviceNumber(SkylightDeviceEntity, NumberEntity):
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self, coordinator, api, frame_id, frame_name, description
    ) -> None:
        super().__init__(coordinator, api, frame_id, frame_name, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        raw = self._raw_value
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        await self._async_patch(int(value))
