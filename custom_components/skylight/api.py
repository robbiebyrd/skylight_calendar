"""Async Skylight API client with OAuth2 Bearer + refresh_token cascade."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
import json as _json
import logging
from typing import Any

import aiohttp
from yarl import URL

from .const import API_VERSION, BASE_URL, CLIENT_ID, OAUTH_URL, USER_AGENT

_LOGGER = logging.getLogger(__name__)

TokenUpdateCallback = Callable[[str, str, str | None], Awaitable[None]]


def _sigv4_sign(
    method: str,
    url: str,
    headers: dict,
    body: bytes,
    access_key: str,
    secret_key: str,
    session_token: str,
    region: str = "us-east-1",
    service: str = "s3",
) -> dict:
    """AWS SigV4 sign an S3 upload PUT.

    Skylight's S3 bucket policy requires signing amz-sdk-invocation-id,
    amz-sdk-request, if-none-match, and x-amz-user-agent in addition to the
    standard SigV4 headers — omitting any results in 403 AccessDenied. This
    mirrors the exact header set sent by aws-sdk-js/3.928.0 in the browser.
    """
    import hashlib
    import hmac
    import uuid as _uuid
    from datetime import datetime, timezone
    from urllib.parse import quote, urlparse

    parsed = urlparse(url)
    host = parsed.netloc
    path = parsed.path or "/"
    query = parsed.query

    now = datetime.now(tz=timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body).hexdigest()
    invocation_id = str(_uuid.uuid4())

    to_sign = {
        "amz-sdk-invocation-id": invocation_id,
        "amz-sdk-request": "attempt=1; max=3",
        "content-type": headers.get("Content-Type", "application/octet-stream"),
        "host": host,
        "if-none-match": "*",
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "x-amz-security-token": session_token,
        "x-amz-user-agent": (
            "aws-sdk-js/3.928.0 ua/2.1 os/Windows lang/js "
            "md/browser#Firefox_unknown api/s3#3.928.0 m/a,b,E,e"
        ),
    }
    canonical_headers = "".join(f"{k}:{v}\n" for k, v in sorted(to_sign.items()))
    signed_headers_str = ";".join(sorted(to_sign.keys()))

    canonical_qs = (
        "&".join(
            f"{quote(k, safe='')}={quote(v, safe='')}"
            for k, v in sorted(p.split("=", 1) for p in query.split("&") if p)
        )
        if query
        else ""
    )

    canonical_request = "\n".join(
        [method, path, canonical_qs, canonical_headers, signed_headers_str, payload_hash]
    )
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )

    def _hmac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    signing_key = _hmac(
        _hmac(
            _hmac(_hmac(f"AWS4{secret_key}".encode(), date_stamp), region),
            service,
        ),
        "aws4_request",
    )
    signature = hmac.new(
        signing_key, string_to_sign.encode(), hashlib.sha256
    ).hexdigest()

    return {
        **headers,
        "Authorization": (
            f"AWS4-HMAC-SHA256 Credential={access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers_str}, Signature={signature}"
        ),
        "amz-sdk-invocation-id": invocation_id,
        "amz-sdk-request": "attempt=1; max=3",
        "if-none-match": "*",
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "x-amz-security-token": session_token,
        "x-amz-user-agent": to_sign["x-amz-user-agent"],
    }


class SkylightAuthError(Exception):
    """Raised when authentication fails and cannot be recovered."""


class SkylightAPIError(Exception):
    """Raised on non-401 HTTP failures."""


class _Unset:
    """Sentinel distinguishing "field omitted" from "field explicitly cleared"."""


UNSET = _Unset()


def _compact(**fields: Any) -> dict:
    """Drop ``None`` fields — used where the API rejects explicit nulls."""
    return {k: v for k, v in fields.items() if v is not None}


def _jsonapi_doc(
    resource_type: str,
    attributes: Mapping[str, Any],
    *,
    relationships: Mapping[str, Any] | None = None,
) -> dict:
    """Wrap attributes in a JSON:API request document.

    UNVERIFIED. Every remaining caller of this helper (create/update list,
    create task_box item, create/update reward, update chore) came from the
    skylight-mcp reference, which has no fixtures for any of them. Chore
    *creation* was ported from that same reference and turned out to be plain
    flat JSON against a different route entirely — so treat this envelope as
    suspect until a request capture confirms it per-endpoint.

    Confirmed-flat so far: chore create, calendar events, recipes, meal
    sittings, list items, device PATCH.
    """
    data: dict[str, Any] = {"type": resource_type, "attributes": dict(attributes)}
    if relationships:
        data["relationships"] = dict(relationships)
    return {"data": data}


def _to_many(resource_type: str, resource_ids: list[str]) -> dict:
    """JSON:API to-many relationship."""
    return {"data": [{"type": resource_type, "id": str(r)} for r in resource_ids]}


class SkylightAPI:
    """Async Skylight API client."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        access_token: str,
        refresh_token: str,
        device_fingerprint: str | None = None,
        token_update_cb: TokenUpdateCallback | None = None,
    ) -> None:
        self._session = session
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._device_fingerprint = device_fingerprint or ""
        self._token_update_cb = token_update_cb

    @property
    def access_token(self) -> str:
        return self._access_token

    @property
    def refresh_token(self) -> str:
        return self._refresh_token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
        _retry: bool = True,
    ) -> Any:
        url = URL(f"{BASE_URL}{path}")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._access_token}",
            "User-Agent": USER_AGENT,
            "Skylight-Api-Version": API_VERSION,
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        clean_params = None
        if params:
            clean_params = {k: v for k, v in params.items() if v is not None}

        async with self._session.request(
            method,
            url,
            headers=headers,
            params=clean_params,
            json=json_body,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            if resp.status == 401 and _retry:
                _LOGGER.debug("Skylight 401 on %s — refreshing token", path)
                await self._refresh_access_token()
                return await self._request(
                    method, path, params=params, json_body=json_body, _retry=False
                )
            if resp.status == 401:
                raise SkylightAuthError("Skylight auth failed after refresh")
            if resp.status >= 400:
                text = await resp.text()
                # Echo the payload back on validation failures. Skylight's 4xx
                # bodies name the offending field but never what we sent, and
                # that's the only thing separating a wrong key name from a wrong
                # envelope — without it every fix is a guess.
                _LOGGER.debug(
                    "Skylight %s %s rejected (%s): sent=%s got=%s",
                    method,
                    path,
                    resp.status,
                    _json.dumps(json_body) if json_body is not None else "<no body>",
                    text[:500],
                )
                detail = ""
                if resp.status == 422 and json_body is not None:
                    detail = f" — sent {_json.dumps(json_body)}"
                raise SkylightAPIError(
                    f"{method} {path} → {resp.status}: {text[:200]}{detail}"
                )
            text = await resp.text()
            if not text:
                return {}
            return _json.loads(text)

    async def _refresh_access_token(self) -> None:
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "client_id": CLIENT_ID,
            "scope": "everything",
            "skylight_api_client_device_fingerprint": self._device_fingerprint,
            "skylight_api_client_device_platform": "web",
            "skylight_api_client_device_name": "home-assistant",
            "skylight_api_client_device_os_version": "10",
            "skylight_api_client_device_app_version": "unknown",
            "skylight_api_client_device_hardware": "3",
            "source": "web",
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        async with self._session.post(
            OAUTH_URL,
            data=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            body = await resp.text()
            if resp.status != 200:
                raise SkylightAuthError(
                    f"OAuth refresh failed ({resp.status}): {body[:200]}"
                )
            data = _json.loads(body)

        new_access = data.get("access_token")
        new_refresh = data.get("refresh_token", self._refresh_token)
        if not new_access:
            raise SkylightAuthError(f"OAuth refresh: no access_token in response: {data}")

        self._access_token = new_access
        self._refresh_token = new_refresh
        _LOGGER.debug("Skylight tokens refreshed")

        if self._token_update_cb is not None:
            try:
                await self._token_update_cb(
                    new_access, new_refresh, self._device_fingerprint
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Token persistence callback failed")

    # ── Endpoints ───────────────────────────────────────────────────────

    async def get_frames(self) -> list[dict]:
        """Frames on the account, as ``{id, name}`` for the config flow.

        ``attributes.name`` is a generated slug ("byrd-malone-7772"), so prefer
        ``household_name`` ("Byrd & Malone") — that's what the app displays and
        what a user will recognise in the frame picker.
        """
        data = await self._request("GET", "/api/frames")
        out = []
        for item in data.get("data", []):
            fid = item.get("id")
            attrs = item.get("attributes", {}) or {}
            name = attrs.get("household_name") or attrs.get("name")
            if fid:
                out.append({"id": str(fid), "name": name or f"Skylight Frame {fid}"})
        return out

    async def get_frame(self, frame_id: str) -> dict:
        return await self._request("GET", f"/api/frames/{frame_id}")

    async def get_devices(self, frame_id: str) -> list[dict]:
        """List devices attached to a frame.

        Frame-level settings (brightness, slideshow_speed, sleep_mode_on, etc.)
        actually live on the device, not the frame — `/api/frames/{fid}` embeds
        the device attributes for convenience, but the writable resource is
        `/api/frames/{fid}/devices/{did}`. Returns each device as a dict with
        {id, attributes}.
        """
        resp = await self._request("GET", f"/api/frames/{frame_id}/devices")
        return [
            {"id": str(d.get("id")), "attributes": d.get("attributes", {}) or {}}
            for d in resp.get("data", [])
            if d.get("id") is not None
        ]

    async def patch_device(
        self, frame_id: str, device_id: str, attributes: dict
    ) -> dict:
        """PATCH device settings (brightness 0-255, slideshow_speed, sleep_mode_on, …).

        Verified request shape: plain-JSON body, keys sent flat (no JSON:API
        envelope, no ``device`` wrapper). Returns the fresh device resource so
        callers can update local state without a re-fetch.
        """
        return await self._request(
            "PATCH",
            f"/api/frames/{frame_id}/devices/{device_id}",
            json_body=attributes,
        )

    async def get_calendar_events(
        self, frame_id: str, date_min: str, date_max: str, timezone: str = "UTC"
    ) -> dict:
        return await self._request(
            "GET",
            f"/api/frames/{frame_id}/calendar_events",
            params={"date_min": date_min, "date_max": date_max, "timezone": timezone},
        )

    async def create_calendar_event(
        self,
        frame_id: str,
        summary: str,
        starts_at: str,
        ends_at: str,
        *,
        all_day: bool = False,
        description: str | None = None,
        location: str | None = None,
        category_ids: list[str] | None = None,
        calendar_account_id: str | None = None,
        calendar_id: str | None = None,
        rrule: list[str] | None = None,
        timezone: str = "UTC",
        kind: str = "standard",
    ) -> dict:
        """Create a calendar event (plain-JSON body, no JSON:API envelope).

        ``calendar_account_id`` / ``calendar_id`` target a specific connected
        source calendar; omit both to land the event on the frame's own Skylight
        calendar. ``category_ids`` assigns the event to family members.
        """
        body: dict[str, Any] = {
            "summary": summary,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "all_day": all_day,
            "timezone": timezone,
            "kind": kind,
            **_compact(
                description=description,
                location=location,
                category_ids=category_ids,
                calendar_account_id=calendar_account_id,
                calendar_id=calendar_id,
                rrule=rrule,
            ),
        }
        return await self._request(
            "POST", f"/api/frames/{frame_id}/calendar_events", json_body=body
        )

    async def update_calendar_event(
        self, frame_id: str, event_id: str, attributes: dict
    ) -> dict:
        """Partial update of a calendar event (plain-JSON PUT body).

        Only the keys present in ``attributes`` change. Use wire names:
        ``summary``, ``starts_at``, ``ends_at``, ``all_day``, ``description``,
        ``location``, ``category_ids``, ``rrule``, ``timezone``.
        """
        return await self._request(
            "PUT",
            f"/api/frames/{frame_id}/calendar_events/{event_id}",
            json_body=attributes,
        )

    async def delete_calendar_event(self, frame_id: str, event_id: str) -> None:
        await self._request(
            "DELETE", f"/api/frames/{frame_id}/calendar_events/{event_id}"
        )

    async def get_source_calendars(self, frame_id: str) -> dict:
        return await self._request("GET", f"/api/frames/{frame_id}/source_calendars")

    async def get_categories(self, frame_id: str, include_profiles: bool = True) -> dict:
        return await self._request(
            "GET",
            f"/api/frames/{frame_id}/categories",
            params={"include_profiles": "true" if include_profiles else None},
        )

    async def get_lists(self, frame_id: str) -> dict:
        return await self._request("GET", f"/api/frames/{frame_id}/lists")

    async def create_list(
        self, frame_id: str, label: str, kind: str = "to_do", color: str | None = None
    ) -> dict:
        """Create a list (JSON:API POST). ``kind`` is ``shopping`` or ``to_do``."""
        doc = _jsonapi_doc("list", {"label": label, "kind": kind, "color": color})
        return await self._request(
            "POST", f"/api/frames/{frame_id}/lists", json_body=doc
        )

    async def update_list(self, frame_id: str, list_id: str, attributes: dict) -> dict:
        """Partial update of a list (JSON:API PUT) — ``label``, ``kind``, ``color``."""
        return await self._request(
            "PUT",
            f"/api/frames/{frame_id}/lists/{list_id}",
            json_body=_jsonapi_doc("list", attributes),
        )

    async def delete_list(self, frame_id: str, list_id: str) -> None:
        await self._request("DELETE", f"/api/frames/{frame_id}/lists/{list_id}")

    async def get_list_items(self, frame_id: str, list_id: str) -> dict:
        return await self._request(
            "GET",
            f"/api/frames/{frame_id}/lists/{list_id}",
            params={"include": "list_items"},
        )

    async def add_list_item(self, frame_id: str, list_id: str, label: str) -> dict:
        return await self._request(
            "POST",
            f"/api/frames/{frame_id}/lists/{list_id}/list_items",
            json_body={"label": label},
        )

    async def update_list_item(
        self, frame_id: str, list_id: str, item_id: str, attrs: dict
    ) -> dict:
        return await self._request(
            "PUT",
            f"/api/frames/{frame_id}/lists/{list_id}/list_items/{item_id}",
            json_body=attrs,
        )

    async def delete_list_item(self, frame_id: str, list_id: str, item_id: str) -> None:
        await self._request(
            "DELETE", f"/api/frames/{frame_id}/lists/{list_id}/list_items/{item_id}"
        )

    async def get_chores(
        self,
        frame_id: str,
        after: str,
        before: str,
        *,
        filter_linked_to_profile: bool = False,
    ) -> dict:
        """Chores in a date range. Set ``filter_linked_to_profile`` to drop chores
        that aren't assigned to a real family member profile.

        ``include_up_for_grabs`` mirrors the web app, which always asks for
        unclaimed chores; without it they're missing from the feed entirely.
        """
        return await self._request(
            "GET",
            f"/api/frames/{frame_id}/chores",
            params={
                "after": after,
                "before": before,
                "include_late": "true",
                "include_up_for_grabs": "true",
                "filter": "linked_to_profile" if filter_linked_to_profile else None,
            },
        )

    async def create_chores(
        self,
        frame_id: str,
        summary: str,
        start: str,
        category_ids: list[str],
        *,
        description: str | None = None,
        start_time: str | None = None,
        routine: bool = False,
        up_for_grabs: bool = False,
        recurrence_set: str | None = None,
        recurring_until: str | None = None,
        renewal_interval: int | None = None,
        renewal_unit: str | None = None,
    ) -> dict:
        """Create a chore for each of ``category_ids``.

        Skylight has no singular chore-create route: the only one is
        ``chores/create_multiple``, which fans out one chore per assigned family
        member. ``POST /chores`` exists but answers ``422 Category is required``
        no matter how the category is passed.

        Wire shape captured from the Skylight web app. It is a flat body — *not*
        the JSON:API envelope the rest of the chore routes use — and every key is
        sent, nulls included. Don't add unobserved keys here: ``status``,
        ``reward_points`` and ``emoji_icon`` are deliberately absent because the
        app never sends them on create, and this payload is known-good as-is.
        """
        body = {
            "start": start,
            "up_for_grabs": up_for_grabs,
            "routine": routine,
            "start_time": start_time,
            "recurrence_set": recurrence_set,
            "renewal_interval": renewal_interval,
            "renewal_unit": renewal_unit,
            "recurring_until": recurring_until,
            "summary": summary,
            "description": description,
            "category_ids": [str(c) for c in category_ids],
        }
        return await self._request(
            "POST", f"/api/frames/{frame_id}/chores/create_multiple", json_body=body
        )

    async def update_chore(
        self,
        frame_id: str,
        chore_id: str,
        attributes: dict,
        *,
        category_id: str | None | _Unset = UNSET,
    ) -> dict:
        """Partial update of a chore (JSON:API PUT).

        Only the keys present in ``attributes`` change. Pass ``category_id=None``
        to unassign the chore; omit it to leave the assignment untouched.

        UNVERIFIED envelope. This is the pre-existing shape that
        :meth:`update_chore_status` has always used, kept as-is rather than
        churned on a guess — but :meth:`create_chores` turned out to need a flat
        body on a different route, so this one likely does too. Needs a capture
        of the web app toggling a chore complete to settle it.
        """
        if not isinstance(category_id, _Unset):
            attributes = {**attributes, "category_id": category_id}
        return await self._request(
            "PUT",
            f"/api/frames/{frame_id}/chores/{chore_id}",
            json_body=_jsonapi_doc("chore", attributes),
        )

    async def update_chore_status(
        self, frame_id: str, chore_id: str, status: str
    ) -> dict:
        """Move a chore between ``pending`` and ``completed``."""
        return await self.update_chore(frame_id, chore_id, {"status": status})

    async def complete_chore(self, frame_id: str, chore_id: str) -> dict:
        """Mark a chore complete."""
        return await self.update_chore_status(frame_id, chore_id, "completed")

    async def delete_chore(self, frame_id: str, chore_id: str) -> None:
        await self._request("DELETE", f"/api/frames/{frame_id}/chores/{chore_id}")

    async def get_meals(self, frame_id: str, date_min: str, date_max: str) -> dict:
        return await self._request(
            "GET",
            f"/api/frames/{frame_id}/meals/sittings",
            params={
                "date_min": date_min,
                "date_max": date_max,
                "include": "meal_category,meal_recipe",
            },
        )

    async def get_meal_categories(self, frame_id: str) -> dict:
        """Meal slots for the frame (Breakfast, Lunch, Dinner, Snack)."""
        return await self._request("GET", f"/api/frames/{frame_id}/meals/categories")

    async def create_meal_sitting(
        self,
        frame_id: str,
        date: str,
        meal_category_id: str,
        recipe_id: str | None = None,
    ) -> dict:
        """Schedule a meal into a slot on a date (plain-JSON body).

        Omit ``recipe_id`` to block out the slot without picking a recipe.
        """
        body = {
            "date": date,
            "meal_category_id": meal_category_id,
            **_compact(meal_recipe_id=recipe_id),
        }
        return await self._request(
            "POST", f"/api/frames/{frame_id}/meals/sittings", json_body=body
        )

    async def get_reward_points(self, frame_id: str) -> dict:
        return await self._request("GET", f"/api/frames/{frame_id}/reward_points")

    async def get_rewards(
        self,
        frame_id: str,
        redeemed_at_min: str | None = None,
        redeemed_at_max: str | None = None,
    ) -> dict:
        """Redeemable rewards, one record per family member per reward.

        The window bounds also pull in already-redeemed rewards; the web app
        passes a rolling 30 days of both. Each reward carries a *to-one*
        ``category`` relationship — a reward belongs to exactly one member, and
        Skylight duplicates it across members rather than sharing one record.
        """
        return await self._request(
            "GET",
            f"/api/frames/{frame_id}/rewards",
            params={
                "redeemed_at_min": redeemed_at_min,
                "redeemed_at_max": redeemed_at_max,
            },
        )

    async def create_reward(
        self,
        frame_id: str,
        name: str,
        point_value: int,
        *,
        description: str | None = None,
        emoji_icon: str | None = None,
        category_ids: list[str] | None = None,
        respawn_on_redemption: bool = False,
    ) -> dict:
        """Create a redeemable reward (JSON:API POST).

        ``category_ids`` restricts the reward to specific family members; omit it
        to offer it to the whole household. ``respawn_on_redemption`` keeps the
        reward available after it's claimed.

        KNOWN-WRONG, pending a request capture. A GET shows each reward with a
        *to-one* ``category`` relationship, so this ``categories`` to-many block
        is wrong on both the key and the cardinality. By analogy with
        :meth:`create_chores` the real route probably takes a flat body with
        ``category_ids`` and fans out one reward per member — which is exactly
        the duplication a GET shows — but that's inference, not observation, and
        guessing it twice already cost a round trip on chores.
        """
        doc = _jsonapi_doc(
            "reward",
            {
                "name": name,
                "point_value": point_value,
                "description": description,
                "emoji_icon": emoji_icon,
                "respawn_on_redemption": respawn_on_redemption,
            },
            relationships=(
                {"categories": _to_many("category", category_ids)}
                if category_ids
                else None
            ),
        )
        return await self._request(
            "POST", f"/api/frames/{frame_id}/rewards", json_body=doc
        )

    async def update_reward(
        self,
        frame_id: str,
        reward_id: str,
        attributes: dict,
        *,
        category_ids: list[str] | None = None,
    ) -> dict:
        """Partial update of a reward (JSON:API PATCH).

        Passing ``category_ids`` replaces the whole member set.
        """
        doc = _jsonapi_doc(
            "reward",
            attributes,
            relationships=(
                {"categories": _to_many("category", category_ids)}
                if category_ids is not None
                else None
            ),
        )
        return await self._request(
            "PATCH", f"/api/frames/{frame_id}/rewards/{reward_id}", json_body=doc
        )

    async def delete_reward(self, frame_id: str, reward_id: str) -> None:
        await self._request("DELETE", f"/api/frames/{frame_id}/rewards/{reward_id}")

    async def redeem_reward(
        self, frame_id: str, reward_id: str, category_id: str | None = None
    ) -> dict:
        """Spend points on a reward. ``category_id`` is the redeeming member."""
        return await self._request(
            "POST",
            f"/api/frames/{frame_id}/rewards/{reward_id}/redeem",
            json_body=_compact(category_id=category_id),
        )

    async def unredeem_reward(self, frame_id: str, reward_id: str) -> dict:
        """Cancel a redemption and refund the points."""
        return await self._request(
            "POST",
            f"/api/frames/{frame_id}/rewards/{reward_id}/unredeem",
            json_body={},
        )

    async def get_task_box(self, frame_id: str) -> dict:
        """Reusable chore-template items (the frame's 'Task Box').

        Returns a flat list of task_box_item records — the pool the frame
        pulls from when adding an ad-hoc chore from its touchscreen.
        """
        return await self._request("GET", f"/api/frames/{frame_id}/task_box/items")

    async def create_task_box_item(
        self,
        frame_id: str,
        summary: str,
        *,
        emoji_icon: str | None = None,
        routine: bool = False,
        reward_points: int | None = None,
    ) -> dict:
        """Add an unscheduled item to the frame's Task Box (JSON:API POST).

        Task box items carry no date — the frame assigns them to a day later.
        """
        doc = _jsonapi_doc(
            "task_box_item",
            {
                "summary": summary,
                "emoji_icon": emoji_icon,
                "routine": routine,
                "reward_points": reward_points,
            },
        )
        return await self._request(
            "POST", f"/api/frames/{frame_id}/task_box/items", json_body=doc
        )

    async def get_recipes(self, frame_id: str, include: str = "meal_category") -> dict:
        return await self._request(
            "GET",
            f"/api/frames/{frame_id}/meals/recipes",
            params={"include": include},
        )

    async def get_recipe(self, frame_id: str, recipe_id: str) -> dict:
        return await self._request(
            "GET", f"/api/frames/{frame_id}/meals/recipes/{recipe_id}"
        )

    async def create_recipe(
        self,
        frame_id: str,
        summary: str,
        *,
        description: str | None = None,
        meal_category_id: str | None = None,
    ) -> dict:
        """Create a recipe (plain-JSON body, no JSON:API envelope)."""
        body = {
            "summary": summary,
            "description": description,
            **_compact(meal_category_id=meal_category_id),
        }
        return await self._request(
            "POST", f"/api/frames/{frame_id}/meals/recipes", json_body=body
        )

    async def update_recipe(
        self, frame_id: str, recipe_id: str, attributes: dict
    ) -> dict:
        """Partial update of a recipe (plain-JSON PATCH) — ``summary``,
        ``description``, ``meal_category_id``."""
        return await self._request(
            "PATCH",
            f"/api/frames/{frame_id}/meals/recipes/{recipe_id}",
            json_body=attributes,
        )

    async def delete_recipe(self, frame_id: str, recipe_id: str) -> None:
        await self._request(
            "DELETE", f"/api/frames/{frame_id}/meals/recipes/{recipe_id}"
        )

    async def add_recipe_to_grocery_list(self, frame_id: str, recipe_id: str) -> dict:
        """Push a recipe's ingredients onto the frame's default grocery list."""
        return await self._request(
            "POST",
            f"/api/frames/{frame_id}/meals/recipes/{recipe_id}/add_to_grocery_list",
            json_body={},
        )

    async def get_albums(self, frame_id: str) -> dict:
        """Photo albums configured on the frame."""
        return await self._request("GET", f"/api/frames/{frame_id}/albums")

    async def get_avatars(self) -> dict:
        """Account-wide avatar options (used on family member profiles)."""
        return await self._request("GET", "/api/avatars")

    async def get_colors(self) -> dict:
        """Account-wide colour palette (used on categories and lists)."""
        return await self._request("GET", "/api/colors")

    async def get_cloud_upload_credentials(self) -> dict:
        """Fetch short-lived S3 credentials for uploading media."""
        return await self._request("GET", "/api/messages/cloud_upload_credentials")

    async def notify_media_upload(
        self,
        frame_ids: list[str],
        bucket: str,
        key: str,
        etag: str,
        ext: str,
        caption: str = "",
    ) -> dict:
        """Register a completed S3 upload with Skylight → creates message_status records."""
        body = {
            "file_upload": {"bucket": bucket, "etag": f'"{etag}"', "key": key},
            "frame_ids": [str(f) for f in frame_ids],
            "caption": caption,
            "ext": ext,
        }
        return await self._request("POST", "/api/messages/uploads", json_body=body)

    async def upload_media(
        self,
        frame_ids: list[str],
        file_data: bytes,
        ext: str,
        content_type: str,
        caption: str = "",
    ) -> dict:
        """End-to-end upload: cloud creds → SigV4 PUT to S3 → notify Skylight.

        Returns the notify_media_upload response containing ``data.message_ids``.
        """
        import uuid as _uuid

        creds_resp = await self.get_cloud_upload_credentials()
        creds = creds_resp["data"]["credentials"]
        bucket = creds_resp["data"]["bucket"]
        region = creds_resp["data"]["region"]
        prefix = creds_resp["data"]["key_prefix"].rstrip("/")
        key = f"{prefix}/{_uuid.uuid4()}.{ext}"
        s3_url = f"https://{bucket}.s3.{region}.amazonaws.com/{key}?x-id=PutObject"

        put_headers = _sigv4_sign(
            "PUT",
            s3_url,
            headers={"Content-Type": content_type},
            body=file_data,
            access_key=creds["access_key_id"],
            secret_key=creds["secret_access_key"],
            session_token=creds["session_token"],
            region=region,
        )
        put_headers["Content-Length"] = str(len(file_data))

        async with self._session.put(
            s3_url, data=file_data, headers=put_headers
        ) as resp:
            if resp.status not in (200, 204):
                text = await resp.text()
                raise SkylightAPIError(
                    f"S3 upload failed {resp.status}: {text[:200]}"
                )
            etag = (resp.headers.get("ETag") or "").strip('"')

        return await self.notify_media_upload(
            frame_ids=frame_ids,
            bucket=bucket,
            key=key,
            etag=etag,
            ext=ext,
            caption=caption,
        )

    async def get_messages(self, frame_id: str, page_token: str = "__START__") -> dict:
        """Photo/message feed."""
        return await self._request(
            "GET",
            f"/api/frames/{frame_id}/messages",
            params={"page_token": page_token},
        )


async def exchange_refresh_token(
    session: aiohttp.ClientSession,
    refresh_token: str,
    device_fingerprint: str = "",
) -> dict:
    """One-shot refresh exchange used by the config flow to verify tokens."""
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": CLIENT_ID,
        "scope": "everything",
        "skylight_api_client_device_fingerprint": device_fingerprint,
        "skylight_api_client_device_platform": "web",
        "skylight_api_client_device_name": "home-assistant",
        "skylight_api_client_device_os_version": "10",
        "skylight_api_client_device_app_version": "unknown",
        "skylight_api_client_device_hardware": "3",
        "source": "web",
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    async with session.post(
        OAUTH_URL,
        data=payload,
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=15),
    ) as resp:
        text = await resp.text()
        if resp.status != 200:
            raise SkylightAuthError(f"Refresh exchange failed ({resp.status}): {text[:200]}")
        data = _json.loads(text)
    if not data.get("access_token"):
        raise SkylightAuthError(f"Refresh exchange: no access_token: {data}")
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", refresh_token),
    }


async def exchange_authorization_code(
    session: aiohttp.ClientSession,
    code: str,
    code_verifier: str,
    device_fingerprint: str = "",
) -> dict:
    """OAuth2 authorization_code + PKCE exchange used by the config flow.

    The Skylight OAuth server only allows one registered redirect URI
    (``https://ourskylight.com/welcome``), so we always pass that back — the
    server never actually redirects there during the HA flow; the user copies
    the ``?code=...`` value out of their browser's address bar.
    """
    from .const import OAUTH_REDIRECT_URI

    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": CLIENT_ID,
        "redirect_uri": OAUTH_REDIRECT_URI,
        "code_verifier": code_verifier,
        "scope": "everything",
        "source": "js-mobile",
        "skylight_api_client_device_fingerprint": device_fingerprint,
        "skylight_api_client_device_platform": "web",
        "skylight_api_client_device_name": "home-assistant",
        "skylight_api_client_device_os_version": "unknown",
        "skylight_api_client_device_app_version": "unknown",
        "skylight_api_client_device_hardware": "3",
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    async with session.post(
        OAUTH_URL,
        data=payload,
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=15),
    ) as resp:
        text = await resp.text()
        if resp.status != 200:
            raise SkylightAuthError(
                f"Authorization code exchange failed ({resp.status}): {text[:200]}"
            )
        data = _json.loads(text)
    if not data.get("access_token") or not data.get("refresh_token"):
        raise SkylightAuthError(f"Code exchange: missing tokens: {data}")
    return {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
    }
