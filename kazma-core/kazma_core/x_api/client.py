"""Official X API v2 client (OAuth 1.0a user context).

Only ``POST /2/tweets``, ``DELETE /2/tweets/:id``, and ``GET /2/users/me``.
No scrape, no like/follow/DM, no Bearer posting, no Playwright.
Writes are **not** retried (a retry after a dropped 201 would double-post).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from kazma_core.x_api.config import XCredentials, get_x_config
from kazma_core.x_api.oauth1 import oauth1_authorization_header, sign_request

logger = logging.getLogger(__name__)

__all__ = ["XApiError", "XClient", "user_agent"]

API_HOST = "https://api.x.com"


def user_agent() -> str:
    try:
        from importlib.metadata import version

        ver = version("kazma")
    except Exception:
        ver = "0.10.0"
    return f"Kazma/{ver} (self-hosted; official X API v2)"


class XApiError(Exception):
    def __init__(self, message: str, *, status: int = 0, transient: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.transient = transient


class XClient:
    def __init__(self, credentials: XCredentials | None = None) -> None:
        self._creds = credentials or get_x_config().credentials

    def _headers(self, method: str, url: str) -> dict[str, str]:
        c = self._creds
        oauth = sign_request(
            method=method,
            url=url,
            consumer_key=c.api_key,
            consumer_secret=c.api_key_secret,
            token=c.access_token,
            token_secret=c.access_token_secret,
        )
        return {
            "Authorization": oauth1_authorization_header(oauth),
            "User-Agent": user_agent(),
            "Accept": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        url = f"{API_HOST}{path}"
        headers = self._headers(method, url)
        if json_body is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                resp = await client.request(method, url, headers=headers, json=json_body)
        except httpx.TimeoutException as exc:
            raise XApiError("X API timed out. Did not retry (avoids double-post).", transient=True) from exc
        except httpx.HTTPError as exc:
            raise XApiError(f"X API network error: {type(exc).__name__}. Did not retry.", transient=True) from exc

        if resp.status_code in (200, 201):
            try:
                return resp.json() if resp.content else {}
            except Exception as exc:
                raise XApiError("X API returned a non-JSON success body.") from exc

        detail = ""
        try:
            payload = resp.json()
            err = payload.get("detail") or payload.get("title") or payload.get("errors")
            detail = str(err)[:400] if err else resp.text[:400]
        except Exception:
            detail = (resp.text or "")[:400]

        if resp.status_code == 429:
            retry_after = resp.headers.get("retry-after") or resp.headers.get("x-rate-limit-reset") or ""
            raise XApiError(
                f"X rate limit (HTTP 429). Wait before retrying"
                + (f" (Retry-After {retry_after})" if retry_after else "")
                + ". Kazma did not auto-retry.",
                status=429,
                transient=True,
            )
        if resp.status_code in (401, 403):
            raise XApiError(
                f"X auth/permission error HTTP {resp.status_code}. "
                "Confirm the app is Read + Write and the four OAuth 1.0a user tokens "
                f"(not the Bearer token) are in Settings → X. {detail}",
                status=resp.status_code,
            )
        raise XApiError(
            f"X API HTTP {resp.status_code}: {detail or 'no body'}",
            status=resp.status_code,
        )

    async def verify_credentials(self) -> dict[str, Any]:
        data = await self._request(
            "GET",
            "/2/users/me?user.fields=username,name,id",
        )
        return data.get("data") or data

    async def create_tweet(self, text: str, *, reply_to_id: str = "") -> dict[str, Any]:
        body: dict[str, Any] = {"text": text}
        if reply_to_id:
            body["reply"] = {"in_reply_to_tweet_id": str(reply_to_id).strip()}
        data = await self._request("POST", "/2/tweets", json_body=body)
        tweet = data.get("data") or data
        return tweet

    async def delete_tweet(self, tweet_id: str) -> dict[str, Any]:
        tid = str(tweet_id).strip()
        data = await self._request("DELETE", f"/2/tweets/{tid}")
        return data.get("data") or data
