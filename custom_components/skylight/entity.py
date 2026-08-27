"""Shared base for entities backed by the Skylight *device* resource.

Brightness, the sleep schedule, the nightlight and friends all live on
``/api/frames/{fid}/devices/{did}`` rather than on the frame, so every one of
these entities reads a single key out of the frame coordinator's ``attributes``
and writes it back with one flat ``patch_device`` call.
"""

from __future__ import annotations

from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import SkylightAPI, SkylightAPIError
from .const import DOMAIN
from .coordinator import SkylightFrameCoordinator


class SkylightDeviceEntity(CoordinatorEntity[SkylightFrameCoordinator]):
    """One writable device setting."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SkylightFrameCoordinator,
        api: SkylightAPI,
        frame_id: str,
        frame_name: str,
        key: str,
        unique_suffix: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._api = api
        self._frame_id = frame_id
        self._key = key
        # unique_suffix exists purely to preserve the unique_ids that shipped
        # before this base class was extracted — sleep_mode_on's entity is
        # registered as "…_sleep_mode". Changing it would orphan the entity and
        # lose the user's history and customisations.
        self._attr_unique_id = f"skylight_{frame_id}_{unique_suffix or key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, frame_id)},
            name=frame_name,
            manufacturer="Skylight",
            model="Calendar Frame",
        )

    @property
    def _device_attributes(self) -> dict:
        return (self.coordinator.data or {}).get("attributes") or {}

    @property
    def _raw_value(self) -> Any:
        return self._device_attributes.get(self._key)

    @property
    def available(self) -> bool:
        """Hide settings this frame doesn't report.

        Skylight's device payload varies by hardware and feature bundle, so a
        key that's simply absent means "not supported here" rather than "off".
        """
        return super().available and self._key in self._device_attributes

    async def _async_patch(self, value: Any) -> None:
        data = self.coordinator.data or {}
        device_id = data.get("device_id") or self.coordinator.device_id
        if not device_id:
            raise HomeAssistantError(
                "Skylight device_id not yet available — the first coordinator "
                "refresh hasn't completed"
            )
        try:
            await self._api.patch_device(self._frame_id, device_id, {self._key: value})
        except SkylightAPIError as err:
            raise HomeAssistantError(
                f"Skylight rejected {self._key}={value!r}: {err}"
            ) from err
        await self.coordinator.async_request_refresh()
