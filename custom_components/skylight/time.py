"""Time platform: the frame's sleep/wake schedule."""

from __future__ import annotations

from datetime import time as dt_time
import logging

from homeassistant.components.time import TimeEntity, TimeEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import SkylightDeviceEntity

_LOGGER = logging.getLogger(__name__)


TIMES: tuple[TimeEntityDescription, ...] = (
    TimeEntityDescription(
        key="sleeps_at",
        name="Sleeps at",
        icon="mdi:weather-night",
        entity_category=EntityCategory.CONFIG,
    ),
    TimeEntityDescription(
        key="wakes_at",
        name="Wakes at",
        icon="mdi:weather-sunny",
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
        SkylightDeviceTime(
            data["frame_coordinator"],
            data["api"],
            data["frame_id"],
            data["frame_name"],
            description,
        )
        for description in TIMES
    )


class SkylightDeviceTime(SkylightDeviceEntity, TimeEntity):
    """A wall-clock setting on the device, sent as ``HH:MM``."""

    def __init__(
        self, coordinator, api, frame_id, frame_name, description
    ) -> None:
        super().__init__(coordinator, api, frame_id, frame_name, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> dt_time | None:
        raw = self._raw_value
        if not isinstance(raw, str):
            return None
        try:
            return dt_time.fromisoformat(raw)
        except ValueError:
            _LOGGER.debug("Unparseable %s from Skylight: %r", self._key, raw)
            return None

    async def async_set_value(self, value: dt_time) -> None:
        # The frame reports "22:00" / "07:30", so match that precision rather
        # than sending the seconds HA's picker supplies.
        await self._async_patch(value.strftime("%H:%M"))
