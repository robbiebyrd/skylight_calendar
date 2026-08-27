"""Binary sensor platform: read-only frame state."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import SkylightDeviceEntity

_LOGGER = logging.getLogger(__name__)


BINARY_SENSORS: tuple[BinarySensorEntityDescription, ...] = (
    BinarySensorEntityDescription(
        key="currently_sleeping",
        name="Screen asleep",
        icon="mdi:sleep",
    ),
    BinarySensorEntityDescription(
        key="activated",
        name="Activated",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        SkylightDeviceBinarySensor(
            data["frame_coordinator"],
            data["api"],
            data["frame_id"],
            data["frame_name"],
            description,
        )
        for description in BINARY_SENSORS
    )


class SkylightDeviceBinarySensor(SkylightDeviceEntity, BinarySensorEntity):
    """Device state the frame reports but doesn't accept writes for.

    ``currently_sleeping`` is distinct from the ``sleep_mode_on`` switch: the
    switch is whether the schedule is armed, this is whether the screen is off
    right now.
    """

    def __init__(
        self, coordinator, api, frame_id, frame_name, description
    ) -> None:
        super().__init__(coordinator, api, frame_id, frame_name, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        raw = self._raw_value
        return None if raw is None else bool(raw)
