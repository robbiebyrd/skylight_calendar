"""The Skylight Calendar integration."""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SkylightAPI, SkylightAPIError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_DEVICE_FINGERPRINT,
    CONF_FRAME_ID,
    CONF_FRAME_NAME,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    PLATFORM_CALENDAR,
    PLATFORM_IMAGE,
    PLATFORM_NUMBER,
    PLATFORM_SENSOR,
    PLATFORM_SWITCH,
    PLATFORM_TODO,
)
from .coordinator import (
    SkylightCalendarCoordinator,
    SkylightFrameCoordinator,
    SkylightListsCoordinator,
    SkylightPhotosCoordinator,
    SkylightSensorCoordinator,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    PLATFORM_CALENDAR,
    PLATFORM_TODO,
    PLATFORM_SENSOR,
    PLATFORM_IMAGE,
    PLATFORM_SWITCH,
    PLATFORM_NUMBER,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Skylight frame from a config entry."""
    session = async_get_clientsession(hass)

    access_token = entry.data.get(CONF_ACCESS_TOKEN)
    refresh_token = entry.data.get(CONF_REFRESH_TOKEN)
    device_fp = entry.data.get(CONF_DEVICE_FINGERPRINT, "")
    frame_id = entry.data.get(CONF_FRAME_ID)
    frame_name = entry.data.get(CONF_FRAME_NAME) or f"Skylight Frame {frame_id}"

    if not access_token or not refresh_token or not frame_id:
        _LOGGER.error(
            "Skylight config entry incomplete (missing tokens or frame_id) — "
            "please remove and re-add the integration"
        )
        return False

    async def _persist_tokens(new_access: str, new_refresh: str, new_fp: str | None) -> None:
        new_data = {
            **entry.data,
            CONF_ACCESS_TOKEN: new_access,
            CONF_REFRESH_TOKEN: new_refresh,
        }
        if new_fp:
            new_data[CONF_DEVICE_FINGERPRINT] = new_fp
        hass.config_entries.async_update_entry(entry, data=new_data)

    api = SkylightAPI(
        session=session,
        access_token=access_token,
        refresh_token=refresh_token,
        device_fingerprint=device_fp,
        token_update_cb=_persist_tokens,
    )

    calendar_coord = SkylightCalendarCoordinator(hass, api, frame_id)
    lists_coord = SkylightListsCoordinator(hass, api, frame_id)
    sensor_coord = SkylightSensorCoordinator(hass, api, frame_id)
    frame_coord = SkylightFrameCoordinator(hass, api, frame_id)
    photos_coord = SkylightPhotosCoordinator(hass, api, frame_id)

    await calendar_coord.async_config_entry_first_refresh()
    await lists_coord.async_config_entry_first_refresh()
    await sensor_coord.async_config_entry_first_refresh()
    await frame_coord.async_config_entry_first_refresh()
    # Photos are optional — don't fail entry setup if the feed 500s.
    try:
        await photos_coord.async_config_entry_first_refresh()
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Initial photos refresh failed — continuing without photos")

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": api,
        "frame_id": frame_id,
        "frame_name": frame_name,
        "calendar_coordinator": calendar_coord,
        "lists_coordinator": lists_coord,
        "sensor_coordinator": sensor_coord,
        "frame_coordinator": frame_coord,
        "photos_coordinator": photos_coord,
    }

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, frame_id)},
        manufacturer="Skylight",
        name=frame_name,
        model="Calendar Frame",
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_services(hass)
    return True


SERVICE_UPLOAD_MEDIA = "upload_media"

_ALLOWED_EXTS = {
    # images
    "jpg", "jpeg", "png", "gif", "heic", "heif", "webp",
    # videos (Skylight app accepts short clips)
    "mp4", "mov", "m4v",
}


UPLOAD_MEDIA_SCHEMA = vol.Schema(
    {
        vol.Required("file_path"): vol.All(str, vol.Length(min=1)),
        vol.Optional("caption", default=""): str,
        vol.Optional("frame_id"): vol.All(str, vol.Length(min=1)),
    }
)


def _async_register_services(hass: HomeAssistant) -> None:
    """Register domain-level services once. Idempotent."""
    if hass.services.has_service(DOMAIN, SERVICE_UPLOAD_MEDIA):
        return

    async def _handle_upload_media(call: ServiceCall) -> None:
        file_path = call.data["file_path"]
        caption = call.data.get("caption", "") or ""
        target_frame = call.data.get("frame_id")

        entries = hass.data.get(DOMAIN, {})
        if not entries:
            raise HomeAssistantError("No Skylight config entries loaded.")

        # Pick a target entry
        if target_frame:
            entry_data = next(
                (v for v in entries.values() if str(v.get("frame_id")) == str(target_frame)),
                None,
            )
            if entry_data is None:
                raise HomeAssistantError(
                    f"No Skylight frame with id={target_frame} configured."
                )
        elif len(entries) == 1:
            entry_data = next(iter(entries.values()))
        else:
            raise HomeAssistantError(
                "Multiple Skylight frames configured — pass frame_id to disambiguate."
            )

        api: SkylightAPI = entry_data["api"]
        frame_id: str = entry_data["frame_id"]

        # Validate + read the file. Path check is defensive — HA doesn't restrict
        # local paths but we surface an actionable error early.
        path = Path(file_path).expanduser()
        if not path.is_file():
            raise HomeAssistantError(f"File not found: {path}")

        # Enforce allowlist_external_dirs so users can't sneak arbitrary reads.
        if not hass.config.is_allowed_path(str(path)):
            raise HomeAssistantError(
                f"{path} is not under an allowlist_external_dirs entry. "
                "Add its parent to `homeassistant.allowlist_external_dirs` in "
                "configuration.yaml, or drop the file into /config."
            )

        ext = path.suffix.lstrip(".").lower() or "jpg"
        if ext not in _ALLOWED_EXTS:
            raise HomeAssistantError(
                f"Extension .{ext} not supported. Allowed: "
                + ", ".join(sorted(_ALLOWED_EXTS))
            )
        content_type = mimetypes.guess_type(str(path))[0] or f"image/{ext}"

        # Read off the event loop.
        file_data = await hass.async_add_executor_job(path.read_bytes)

        _LOGGER.info(
            "Skylight upload_media: uploading %s (%d bytes, %s) to frame %s",
            path.name, len(file_data), content_type, frame_id,
        )
        try:
            result = await api.upload_media(
                frame_ids=[frame_id],
                file_data=file_data,
                ext=ext,
                content_type=content_type,
                caption=caption,
            )
        except SkylightAPIError as err:
            raise HomeAssistantError(f"Skylight upload failed: {err}") from err

        msg_ids = (result or {}).get("data", {}).get("message_ids", [])
        _LOGGER.info(
            "Skylight upload_media: success, message_ids=%s (frame %s)",
            msg_ids, frame_id,
        )

        # Kick the photos coordinator to refresh quickly so image.<frame>_latest_photo
        # updates within seconds instead of waiting for the next scheduled poll.
        photos_coord: SkylightPhotosCoordinator | None = entry_data.get(
            "photos_coordinator"
        )
        if photos_coord is not None:
            await photos_coord.async_request_refresh()

    hass.services.async_register(
        DOMAIN, SERVICE_UPLOAD_MEDIA, _handle_upload_media, schema=UPLOAD_MEDIA_SCHEMA
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        # Remove the shared service when the last entry unloads.
        if not hass.data.get(DOMAIN) and hass.services.has_service(
            DOMAIN, SERVICE_UPLOAD_MEDIA
        ):
            hass.services.async_remove(DOMAIN, SERVICE_UPLOAD_MEDIA)
    return unload_ok
