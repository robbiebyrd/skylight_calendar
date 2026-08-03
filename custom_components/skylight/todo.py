"""Todo platform: HA Todo entities per Skylight list AND per family member's chore queue."""

from __future__ import annotations

import logging
from datetime import date, datetime

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import SkylightAPI
from .const import DOMAIN
from .coordinator import SkylightListsCoordinator, SkylightSensorCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    lists_coord: SkylightListsCoordinator = data["lists_coordinator"]
    sensor_coord: SkylightSensorCoordinator = data["sensor_coordinator"]
    api: SkylightAPI = data["api"]
    frame_id: str = data["frame_id"]
    frame_name: str = data["frame_name"]

    entities: list[TodoListEntity] = []

    # 1) One entity per Skylight list.
    for list_id, list_data in (lists_coord.data or {}).items():
        entities.append(
            SkylightTodoList(
                lists_coord, api, frame_id, frame_name, list_id, list_data["name"]
            )
        )

    # 2) One entity per family member: their chore queue (completeable from HA).
    for cat_id, label in _profile_categories(sensor_coord.data or {}):
        entities.append(
            SkylightMemberChoreTodo(
                sensor_coord, api, frame_id, frame_name, cat_id, label
            )
        )

    async_add_entities(entities)

    known_lists: set[str] = {
        e.list_id for e in entities if isinstance(e, SkylightTodoList)
    }

    @callback
    def _handle_lists_update() -> None:
        current = set((lists_coord.data or {}).keys())
        new_ids = current - known_lists
        if not new_ids:
            return
        new_entities = []
        for lid in new_ids:
            ldata = lists_coord.data[lid]
            new_entities.append(
                SkylightTodoList(
                    lists_coord, api, frame_id, frame_name, lid, ldata["name"]
                )
            )
            known_lists.add(lid)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(lists_coord.async_add_listener(_handle_lists_update))


def _profile_categories(data: dict) -> list[tuple[str, str]]:
    cats = (data.get("categories") or {}).get("data", [])
    out: list[tuple[str, str]] = []
    for c in cats:
        attrs = c.get("attributes", {})
        if not attrs.get("linked_to_profile"):
            continue
        label = attrs.get("label", "")
        if "@" in label:
            continue
        out.append((str(c.get("id")), label))
    return out


class SkylightTodoList(CoordinatorEntity[SkylightListsCoordinator], TodoListEntity):
    _attr_has_entity_name = True
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
    )

    def __init__(
        self,
        coordinator: SkylightListsCoordinator,
        api: SkylightAPI,
        frame_id: str,
        frame_name: str,
        list_id: str,
        list_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._api = api
        self._frame_id = frame_id
        self.list_id = list_id
        self._attr_name = list_name
        self._attr_unique_id = f"skylight_{frame_id}_list_{list_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, frame_id)},
            name=frame_name,
            manufacturer="Skylight",
            model="Calendar Frame",
        )

    @property
    def todo_items(self) -> list[TodoItem] | None:
        list_data = (self.coordinator.data or {}).get(self.list_id)
        if list_data is None:
            return None
        items: list[TodoItem] = []
        for it in list_data["items"]:
            status = (
                TodoItemStatus.COMPLETED
                if it["status"] == "completed"
                else TodoItemStatus.NEEDS_ACTION
            )
            items.append(TodoItem(summary=it["name"], uid=it["id"], status=status))
        return items

    async def async_create_todo_item(self, item: TodoItem) -> None:
        await self._api.add_list_item(self._frame_id, self.list_id, item.summary or "")
        await self.coordinator.async_request_refresh()

    async def async_update_todo_item(self, item: TodoItem) -> None:
        attrs: dict = {}
        if item.summary is not None:
            attrs["name"] = item.summary
        if item.status is not None:
            attrs["status"] = (
                "completed"
                if item.status == TodoItemStatus.COMPLETED
                else "pending"
            )
        if attrs and item.uid:
            await self._api.update_list_item(
                self._frame_id, self.list_id, item.uid, attrs
            )
            await self.coordinator.async_request_refresh()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        for uid in uids:
            await self._api.delete_list_item(self._frame_id, self.list_id, uid)
        await self.coordinator.async_request_refresh()


class SkylightMemberChoreTodo(
    CoordinatorEntity[SkylightSensorCoordinator], TodoListEntity
):
    """A single family member's chore queue as a HA Todo entity.

    Read-only for create/delete (Skylight chore CRUD is nontrivial from the app UX),
    but supports UPDATE so you can mark a chore complete from HA and it round-trips
    to the frame + rewards ledger.
    """

    _attr_has_entity_name = True
    _attr_supported_features = TodoListEntityFeature.UPDATE_TODO_ITEM

    def __init__(
        self,
        coordinator: SkylightSensorCoordinator,
        api: SkylightAPI,
        frame_id: str,
        frame_name: str,
        category_id: str,
        label: str,
    ) -> None:
        super().__init__(coordinator)
        self._api = api
        self._frame_id = frame_id
        self._category_id = str(category_id)
        self._attr_name = f"{label} chores"
        self._attr_unique_id = f"skylight_{frame_id}_chore_todo_{category_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, frame_id)},
            name=frame_name,
            manufacturer="Skylight",
            model="Calendar Frame",
        )

    def _member_chores(self) -> list[dict]:
        raw = (self.coordinator.data or {}).get("chores")
        if raw is None:
            return []
        entries = raw if isinstance(raw, list) else raw.get("data", []) or []
        out = []
        for c in entries:
            rel = (c.get("relationships", {}) or {}).get("category", {}) or {}
            data = rel.get("data") or {}
            assignee = str(data.get("id") or c.get("attributes", {}).get("category_id") or "")
            if assignee == self._category_id:
                out.append(c)
        return out

    @property
    def todo_items(self) -> list[TodoItem] | None:
        result: list[TodoItem] = []
        for c in self._member_chores():
            attrs = c.get("attributes", {}) if isinstance(c, dict) else {}
            summary = attrs.get("summary") or attrs.get("name") or "Chore"
            status = (
                TodoItemStatus.COMPLETED
                if attrs.get("status") == "completed"
                else TodoItemStatus.NEEDS_ACTION
            )
            uid = str(c.get("id"))
            due_raw = (
                attrs.get("start")
                or attrs.get("date")
                or attrs.get("due_date")
                or ""
            )
            due: date | datetime | None = None
            if isinstance(due_raw, str) and len(due_raw) >= 10:
                try:
                    due = date.fromisoformat(due_raw[:10])
                except ValueError:
                    due = None
            result.append(
                TodoItem(summary=summary, uid=uid, status=status, due=due)
            )
        return result

    async def async_update_todo_item(self, item: TodoItem) -> None:
        if not item.uid or item.status is None:
            return
        status = (
            "completed"
            if item.status == TodoItemStatus.COMPLETED
            else "pending"
        )
        await self._api.update_chore_status(self._frame_id, item.uid, status)
        await self.coordinator.async_request_refresh()
