"""Calendar entities: one aggregate 'All' + one per source_calendar."""

from __future__ import annotations

from datetime import datetime
import logging

from dateutil.parser import parse as parse_datetime

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import SkylightCalendarCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coord: SkylightCalendarCoordinator = data["calendar_coordinator"]
    frame_id: str = data["frame_id"]
    frame_name: str = data["frame_name"]

    # Aggregate entity (backward-compatible with pre-refactor unique_id).
    entities: list[CalendarEntity] = [
        SkylightAggregateCalendar(coord, frame_id, frame_name),
    ]

    seen: set[str] = set()

    def _current_source_ids() -> list[dict]:
        d = coord.data or {}
        return d.get("source_calendars", []) or []

    # Initial per-source entities.
    for sc in _current_source_ids():
        sid = sc["id"]
        entities.append(
            SkylightSourceCalendar(coord, frame_id, frame_name, sid, sc.get("name") or sid)
        )
        seen.add(sid)

    async_add_entities(entities)

    @callback
    def _handle_update() -> None:
        new_entities: list[CalendarEntity] = []
        for sc in _current_source_ids():
            sid = sc["id"]
            if sid in seen:
                continue
            new_entities.append(
                SkylightSourceCalendar(
                    coord, frame_id, frame_name, sid, sc.get("name") or sid
                )
            )
            seen.add(sid)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coord.async_add_listener(_handle_update))


def _parse_events(raw_events: dict, source_id: str | None = None) -> list[CalendarEvent]:
    """Parse Skylight event feed → CalendarEvent list, optionally filtered by source calendar."""
    events: list[CalendarEvent] = []
    for ev in raw_events.get("data", []):
        attrs = ev.get("attributes", {})
        if source_id is not None:
            rels = ev.get("relationships", {}) or {}
            sc_rel = rels.get("source_calendar", {}) or {}
            sc_data = sc_rel.get("data") or {}
            ev_source = str(sc_data.get("id") or attrs.get("source_calendar_id") or "")
            if ev_source and ev_source != str(source_id):
                continue
            if not ev_source:
                # No source info — only include in aggregate view.
                continue
        starts = attrs.get("starts_at")
        ends = attrs.get("ends_at")
        if not starts or not ends:
            continue
        try:
            start_dt = parse_datetime(starts)
            end_dt = parse_datetime(ends)
        except (ValueError, TypeError):
            continue
        events.append(
            CalendarEvent(
                start=start_dt,
                end=end_dt,
                summary=attrs.get("summary") or "Skylight Event",
                description=attrs.get("description") or "",
                location=attrs.get("location") or "",
            )
        )
    return events


class _SkylightCalendarBase(
    CoordinatorEntity[SkylightCalendarCoordinator], CalendarEntity
):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SkylightCalendarCoordinator,
        frame_id: str,
        frame_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._frame_id = frame_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, frame_id)},
            name=frame_name,
            manufacturer="Skylight",
            model="Calendar Frame",
        )

    def _source_filter(self) -> str | None:
        return None

    def _all_events(self) -> list[CalendarEvent]:
        raw = (self.coordinator.data or {}).get("events", {}) or {}
        return _parse_events(raw, self._source_filter())

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.now()
        for ev in self._all_events():
            start = ev.start if isinstance(ev.start, datetime) else None
            end = ev.end if isinstance(ev.end, datetime) else None
            if start and end and start <= now <= end:
                return ev
        upcoming = [
            e for e in self._all_events()
            if isinstance(e.start, datetime) and e.start >= now
        ]
        upcoming.sort(key=lambda e: e.start)
        return upcoming[0] if upcoming else None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        result = []
        for ev in self._all_events():
            ev_start = ev.start if isinstance(ev.start, datetime) else None
            ev_end = ev.end if isinstance(ev.end, datetime) else None
            if not ev_start or not ev_end:
                continue
            if ev_end < start_date or ev_start > end_date:
                continue
            result.append(ev)
        return result


class SkylightAggregateCalendar(_SkylightCalendarBase):
    """Merged view of every source calendar (backward-compatible entity)."""

    _attr_name = "Calendar"

    def __init__(self, coordinator, frame_id, frame_name):
        super().__init__(coordinator, frame_id, frame_name)
        self._attr_unique_id = f"skylight_{frame_id}_calendar"


class SkylightSourceCalendar(_SkylightCalendarBase):
    """Per-source-calendar entity (one per connected Google/Skylight calendar)."""

    def __init__(self, coordinator, frame_id, frame_name, source_id: str, source_name: str):
        super().__init__(coordinator, frame_id, frame_name)
        self._source_id = str(source_id)
        self._attr_name = source_name
        self._attr_unique_id = f"skylight_{frame_id}_calendar_source_{source_id}"

    def _source_filter(self) -> str | None:
        return self._source_id
