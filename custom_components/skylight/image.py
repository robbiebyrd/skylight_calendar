"""Image entity: latest photo pushed to the frame."""

from __future__ import annotations

import logging

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import SkylightPhotosCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coord: SkylightPhotosCoordinator = data["photos_coordinator"]
    async_add_entities(
        [SkylightLatestPhoto(hass, coord, data["frame_id"], data["frame_name"])]
    )


class SkylightLatestPhoto(
    CoordinatorEntity[SkylightPhotosCoordinator], ImageEntity
):
    _attr_has_entity_name = True
    _attr_name = "Latest photo"
    _attr_icon = "mdi:image"
    _attr_content_type = "image/jpeg"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: SkylightPhotosCoordinator,
        frame_id: str,
        frame_name: str,
    ) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, hass)
        self._frame_id = frame_id
        self._attr_unique_id = f"skylight_{frame_id}_latest_photo"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, frame_id)},
            name=frame_name,
            manufacturer="Skylight",
            model="Calendar Frame",
        )
        self._current_url: str | None = None
        self._sync_url()

    def _latest(self) -> dict | None:
        items = self.coordinator.data or []
        if not items:
            return None
        # Skylight returns newest first; fall back to created_at sort if not.
        return items[0]

    def _sync_url(self) -> None:
        item = self._latest()
        if not item:
            self._attr_image_url = None
            self._current_url = None
            return
        attrs = item.get("attributes", item) or {}
        # Skylight uses `asset_url` (full-size CloudFront URL) as the canonical
        # image location. `thumbnail_url` is the sized preview. Fallbacks kept
        # for defensive parity with older payload shapes.
        url = (
            attrs.get("asset_url")
            or attrs.get("url")
            or attrs.get("image_url")
            or attrs.get("thumbnail_url")
        )
        if url and url != self._current_url:
            self._attr_image_last_updated = dt_util.utcnow()
            self._current_url = url
        self._attr_image_url = url

    @property
    def extra_state_attributes(self) -> dict:
        item = self._latest()
        if not item:
            return {}
        attrs = item.get("attributes", item) or {}
        return {
            "id": item.get("id"),
            "caption": attrs.get("caption"),
            "created_at": attrs.get("created_at"),
            "status": attrs.get("status"),
            "asset_type": attrs.get("asset_type"),
            "thumbnail_url": attrs.get("thumbnail_url"),
            "sender_id": attrs.get("sender_id"),
        }

    def _handle_coordinator_update(self) -> None:
        self._sync_url()
        super()._handle_coordinator_update()
