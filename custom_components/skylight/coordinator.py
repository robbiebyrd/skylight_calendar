"""DataUpdateCoordinators for Skylight resources."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import SkylightAPI, SkylightAPIError, SkylightAuthError
from .const import (
    CALENDAR_SCAN_INTERVAL,
    DOMAIN,
    FRAME_SCAN_INTERVAL,
    LISTS_SCAN_INTERVAL,
    PHOTOS_SCAN_INTERVAL,
    SENSOR_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class SkylightCalendarCoordinator(DataUpdateCoordinator):
    """Fetch calendar events + source_calendars for splitting into per-calendar entities."""

    def __init__(self, hass: HomeAssistant, api: SkylightAPI, frame_id: str) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} calendar {frame_id}",
            update_interval=timedelta(seconds=CALENDAR_SCAN_INTERVAL),
        )
        self.api = api
        self.frame_id = frame_id

    async def _async_update_data(self) -> dict:
        today = dt_util.now().date()
        date_min = (today - timedelta(days=14)).isoformat()
        date_max = (today + timedelta(days=60)).isoformat()
        tz = getattr(self.hass.config, "time_zone", "UTC") or "UTC"
        try:
            events = await self.api.get_calendar_events(
                self.frame_id, date_min, date_max, timezone=tz
            )
        except SkylightAuthError as err:
            raise UpdateFailed(f"Auth failed: {err}") from err
        except SkylightAPIError as err:
            raise UpdateFailed(str(err)) from err

        # Best-effort source_calendars fetch (used to split into per-calendar entities).
        source_calendars: list[dict] = []
        try:
            sc_resp = await self.api.get_source_calendars(self.frame_id)
            for entry in sc_resp.get("data", []):
                a = entry.get("attributes", {})
                source_calendars.append(
                    {
                        "id": str(entry.get("id")),
                        "name": a.get("label") or a.get("name") or a.get("source_id") or a.get("email") or f"Calendar {entry.get('id')}",
                        "email": a.get("source_id") or a.get("email"),
                        "editable": a.get("editable"),
                        "role": a.get("role"),
                        "kind": a.get("kind"),
                    }
                )
        except (SkylightAuthError, SkylightAPIError) as err:
            _LOGGER.debug("source_calendars fetch failed: %s", err)

        return {"events": events, "source_calendars": source_calendars}


class SkylightListsCoordinator(DataUpdateCoordinator):
    """Fetch every list + its items."""

    def __init__(self, hass: HomeAssistant, api: SkylightAPI, frame_id: str) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} lists {frame_id}",
            update_interval=timedelta(seconds=LISTS_SCAN_INTERVAL),
        )
        self.api = api
        self.frame_id = frame_id

    async def _async_update_data(self) -> dict:
        try:
            lists_resp = await self.api.get_lists(self.frame_id)
        except SkylightAuthError as err:
            raise UpdateFailed(f"Auth failed: {err}") from err
        except SkylightAPIError as err:
            raise UpdateFailed(str(err)) from err

        out: dict[str, dict] = {}
        for entry in lists_resp.get("data", []):
            lid = str(entry.get("id"))
            attrs = entry.get("attributes", {})
            try:
                detail = await self.api.get_list_items(self.frame_id, lid)
            except (SkylightAuthError, SkylightAPIError) as err:
                _LOGGER.warning("Failed to fetch items for list %s: %s", lid, err)
                continue
            items = []
            for inc in detail.get("included", []) or []:
                if inc.get("type") != "list_item":
                    continue
                i_attrs = inc.get("attributes", {})
                items.append(
                    {
                        "id": str(inc.get("id")),
                        "name": i_attrs.get("label", ""),
                        "status": i_attrs.get("status", "pending"),
                        "position": i_attrs.get("position", 0) or 0,
                    }
                )
            items.sort(key=lambda x: x["position"])
            out[lid] = {
                "id": lid,
                "name": attrs.get("label", f"List {lid}"),
                "color": attrs.get("color"),
                "kind": attrs.get("kind"),
                "items": items,
            }
        return out


class SkylightSensorCoordinator(DataUpdateCoordinator):
    """Aggregate chores + meals + rewards + categories for sensors and per-member todos."""

    def __init__(self, hass: HomeAssistant, api: SkylightAPI, frame_id: str) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} sensors {frame_id}",
            update_interval=timedelta(seconds=SENSOR_SCAN_INTERVAL),
        )
        self.api = api
        self.frame_id = frame_id

    async def _async_update_data(self) -> dict:
        today = dt_util.now().date()
        week_end = (today + timedelta(days=7)).isoformat()
        result: dict = {
            "chores": None,
            "meals": None,
            "meals_included": [],
            "reward_points": None,
            "categories": None,
            "task_box": None,
        }
        try:
            result["chores"] = await self.api.get_chores(
                self.frame_id, today.isoformat(), week_end
            )
        except (SkylightAuthError, SkylightAPIError) as err:
            _LOGGER.debug("chores fetch failed: %s", err)
        try:
            meals = await self.api.get_meals(
                self.frame_id, today.isoformat(), week_end
            )
            result["meals"] = meals
            # Preserve JSON:API `included` payload for recipe / category label lookup.
            if isinstance(meals, dict):
                result["meals_included"] = meals.get("included", []) or []
        except (SkylightAuthError, SkylightAPIError) as err:
            _LOGGER.debug("meals fetch failed: %s", err)
        try:
            result["reward_points"] = await self.api.get_reward_points(self.frame_id)
        except (SkylightAuthError, SkylightAPIError) as err:
            _LOGGER.debug("reward_points fetch failed: %s", err)
        try:
            result["categories"] = await self.api.get_categories(self.frame_id)
        except (SkylightAuthError, SkylightAPIError) as err:
            _LOGGER.debug("categories fetch failed: %s", err)
        try:
            result["task_box"] = await self.api.get_task_box(self.frame_id)
        except (SkylightAuthError, SkylightAPIError) as err:
            _LOGGER.debug("task_box fetch failed: %s", err)
        return result


class SkylightFrameCoordinator(DataUpdateCoordinator):
    """Fetch device settings (brightness, sleep_mode_on, slideshow_speed, name...).

    Despite the name, this coordinator reads and writes the *device* resource
    (``/api/frames/{fid}/devices/{did}``), which is where brightness /
    slideshow_speed / sleep_mode_on actually live. The `/api/frames/{fid}`
    endpoint embeds these attributes on the frame for convenience but treats
    them as read-only — writes there return 404 "Record not found".

    Data shape: ``{"device_id": "5669988", "attributes": {...}}``
    """

    def __init__(self, hass: HomeAssistant, api: SkylightAPI, frame_id: str) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} frame {frame_id}",
            update_interval=timedelta(seconds=FRAME_SCAN_INTERVAL),
        )
        self.api = api
        self.frame_id = frame_id
        self._device_id: str | None = None

    @property
    def device_id(self) -> str | None:
        """Cached device_id — becomes available after the first successful refresh."""
        return self._device_id

    async def _async_update_data(self) -> dict:
        try:
            devices = await self.api.get_devices(self.frame_id)
        except SkylightAuthError as err:
            raise UpdateFailed(f"Auth failed: {err}") from err
        except SkylightAPIError as err:
            raise UpdateFailed(str(err)) from err

        if not devices:
            raise UpdateFailed(
                f"Skylight frame {self.frame_id} has no device attached — "
                "cannot control settings"
            )

        # A frame is a 1:1 device pairing in production; if that ever changes we
        # take the first (primary) device deterministically.
        primary = devices[0]
        self._device_id = primary["id"]
        return {"device_id": primary["id"], "attributes": primary["attributes"]}


class SkylightPhotosCoordinator(DataUpdateCoordinator):
    """Fetch latest frame photos (messages feed)."""

    def __init__(self, hass: HomeAssistant, api: SkylightAPI, frame_id: str) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} photos {frame_id}",
            update_interval=timedelta(seconds=PHOTOS_SCAN_INTERVAL),
        )
        self.api = api
        self.frame_id = frame_id

    async def _async_update_data(self) -> list[dict]:
        try:
            resp = await self.api.get_messages(self.frame_id)
        except SkylightAuthError as err:
            raise UpdateFailed(f"Auth failed: {err}") from err
        except SkylightAPIError as err:
            # Photos are non-critical — don't kill the whole entry over this.
            _LOGGER.debug("photos fetch failed: %s", err)
            return []
        return resp.get("data", []) if isinstance(resp, dict) else []
