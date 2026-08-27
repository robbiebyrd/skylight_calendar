"""Switch platform: boolean Skylight device settings."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import SkylightDeviceEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class SkylightSwitchDescription(SwitchEntityDescription):
    """A boolean device attribute. ``key`` is the wire name."""

    unique_suffix: str | None = None


SWITCHES: tuple[SkylightSwitchDescription, ...] = (
    # No entity_category: this one predates the others and is a day-to-day
    # control rather than configuration. Categorising it now would move it on
    # every existing user's device page.
    SkylightSwitchDescription(
        key="sleep_mode_on",
        unique_suffix="sleep_mode",
        name="Sleep mode",
        icon="mdi:sleep",
    ),
    SkylightSwitchDescription(
        key="nightlight",
        name="Night light",
        icon="mdi:weather-night",
        entity_category=EntityCategory.CONFIG,
    ),
    SkylightSwitchDescription(
        key="show_caption",
        name="Show captions",
        icon="mdi:closed-caption-outline",
        entity_category=EntityCategory.CONFIG,
    ),
    SkylightSwitchDescription(
        key="show_heart",
        name="Show heart",
        icon="mdi:heart-outline",
        entity_category=EntityCategory.CONFIG,
    ),
    SkylightSwitchDescription(
        key="blur_effect",
        name="Blur effect",
        icon="mdi:blur",
        entity_category=EntityCategory.CONFIG,
    ),
    SkylightSwitchDescription(
        key="start_sound",
        name="Start sound",
        icon="mdi:volume-high",
        entity_category=EntityCategory.CONFIG,
    ),
    SkylightSwitchDescription(
        key="side_by_side",
        name="Side by side",
        icon="mdi:view-split-vertical",
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
        SkylightDeviceSwitch(
            data["frame_coordinator"],
            data["api"],
            data["frame_id"],
            data["frame_name"],
            description,
        )
        for description in SWITCHES
    )


class SkylightDeviceSwitch(SkylightDeviceEntity, SwitchEntity):
    entity_description: SkylightSwitchDescription

    def __init__(
        self, coordinator, api, frame_id, frame_name, description
    ) -> None:
        super().__init__(
            coordinator,
            api,
            frame_id,
            frame_name,
            description.key,
            description.unique_suffix,
        )
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        raw = self._raw_value
        return None if raw is None else bool(raw)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_patch(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_patch(False)
