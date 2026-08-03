"""Number platform: brightness + slideshow speed."""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import SkylightAPI
from .const import DOMAIN
from .coordinator import SkylightFrameCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            SkylightBrightness(
                data["frame_coordinator"], data["api"], data["frame_id"], data["frame_name"]
            ),
            SkylightSlideshowSpeed(
                data["frame_coordinator"], data["api"], data["frame_id"], data["frame_name"]
            ),
        ]
    )


class _SkylightFrameNumber(
    CoordinatorEntity[SkylightFrameCoordinator], NumberEntity
):
    _attr_has_entity_name = True
    _attr_mode = NumberMode.SLIDER
    _attr_field: str = ""

    def __init__(
        self,
        coordinator: SkylightFrameCoordinator,
        api: SkylightAPI,
        frame_id: str,
        frame_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._api = api
        self._frame_id = frame_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, frame_id)},
            name=frame_name,
            manufacturer="Skylight",
            model="Calendar Frame",
        )

    @property
    def _current_device_id(self) -> str | None:
        """Prefer coordinator.data (freshest); fall back to the cached attr."""
        data = self.coordinator.data or {}
        return data.get("device_id") or self.coordinator.device_id

    @property
    def native_value(self) -> float | None:
        attributes = (self.coordinator.data or {}).get("attributes") or {}
        raw = attributes.get(self._attr_field)
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    async def _async_patch(self, attributes: dict) -> None:
        device_id = self._current_device_id
        if not device_id:
            raise RuntimeError(
                "Skylight device_id not yet available — first coordinator refresh "
                "hasn't completed"
            )
        await self._api.patch_device(self._frame_id, device_id, attributes)
        await self.coordinator.async_request_refresh()


class SkylightBrightness(_SkylightFrameNumber):
    _attr_name = "Brightness"
    _attr_icon = "mdi:brightness-6"
    _attr_native_min_value = 0
    _attr_native_max_value = 255
    _attr_native_step = 1
    _attr_field = "brightness"

    def __init__(self, coordinator, api, frame_id, frame_name):
        super().__init__(coordinator, api, frame_id, frame_name)
        self._attr_unique_id = f"skylight_{frame_id}_brightness"

    async def async_set_native_value(self, value: float) -> None:
        await self._async_patch({"brightness": int(value)})


class SkylightSlideshowSpeed(_SkylightFrameNumber):
    _attr_name = "Slideshow speed"
    _attr_icon = "mdi:timer-outline"
    _attr_native_min_value = 0
    _attr_native_max_value = 240
    _attr_native_step = 1
    _attr_field = "slideshow_speed"
    _attr_native_unit_of_measurement = "s"

    def __init__(self, coordinator, api, frame_id, frame_name):
        super().__init__(coordinator, api, frame_id, frame_name)
        self._attr_unique_id = f"skylight_{frame_id}_slideshow_speed"

    async def async_set_native_value(self, value: float) -> None:
        await self._async_patch({"slideshow_speed": int(value)})
