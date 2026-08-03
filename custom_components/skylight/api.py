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
                raise SkylightAPIError(f"{method} {path} → {resp.status}: {text[:200]}")
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
        data = await self._request("GET", "/api/frames")
        out = []
        for item in data.get("data", []):
            fid = item.get("id")
            name = item.get("attributes", {}).get("name")
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

    async def get_chores(self, frame_id: str, after: str, before: str) -> dict:
        return await self._request(
            "GET",
            f"/api/frames/{frame_id}/chores",
            params={"after": after, "before": before, "include_late": "true"},
        )

    async def complete_chore(self, frame_id: str, chore_id: str) -> dict:
        """Mark a chore complete (JSON:API PUT)."""
        body = {
            "data": {
                "type": "chore",
                "id": chore_id,
                "attributes": {"status": "completed"},
            }
        }
        return await self._request(
            "PUT", f"/api/frames/{frame_id}/chores/{chore_id}", json_body=body
        )

    async def update_chore_status(
        self, frame_id: str, chore_id: str, status: str
    ) -> dict:
        body = {
            "data": {"type": "chore", "id": chore_id, "attributes": {"status": status}}
        }
        return await self._request(
            "PUT", f"/api/frames/{frame_id}/chores/{chore_id}", json_body=body
        )

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

    async def get_reward_points(self, frame_id: str) -> dict:
        return await self._request("GET", f"/api/frames/{frame_id}/reward_points")

    async def get_rewards(self, frame_id: str) -> dict:
        return await self._request("GET", f"/api/frames/{frame_id}/rewards")

    async def get_task_box(self, frame_id: str) -> dict:
        """Reusable chore-template items (the frame's 'Task Box').

        Returns a flat list of task_box_item records — the pool the frame
        pulls from when adding an ad-hoc chore from its touchscreen.
        """
        return await self._request("GET", f"/api/frames/{frame_id}/task_box/items")

    async def get_recipe(self, frame_id: str, recipe_id: str) -> dict:
        return await self._request(
            "GET", f"/api/frames/{frame_id}/meals/recipes/{recipe_id}"
        )

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
