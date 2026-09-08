"""Microsoft Outlook calendar backend via MS Graph.

Tokens come from :mod:`kazma_skills.native.calendar.credentials` (vault +
env, including the Microsoft mail grant when it includes Calendars.*).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_GRAPH = "https://graph.microsoft.com/v1.0/me"
_refresh_lock = asyncio.Lock()


class OutlookCalendarBackend:
    """Microsoft Outlook calendar via MS Graph."""

    name = "outlook"

    def __init__(
        self,
        access_token: str,
        refresh_token: str = "",
        client_id: str = "",
        client_secret: str = "",
        tenant_id: str = "common",
    ) -> None:
        self._token = access_token
        self._refresh = refresh_token
        self._client_id = client_id
        self._client_secret = client_secret
        self._tenant = tenant_id or "common"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def _ensure_token(self) -> None:
        if self._token and self._token != "pending_refresh":
            return
        await self._do_refresh()

    async def _do_refresh(self) -> bool:
        if not self._refresh or not self._client_id:
            return False
        import httpx

        from kazma_skills.native.calendar.credentials import persist_microsoft_tokens
        from kazma_skills.native.email_manager.credentials import vault_store

        async with _refresh_lock:
            token_url = (
                f"https://login.microsoftonline.com/{self._tenant}/oauth2/v2.0/token"
            )
            data = {
                "client_id": self._client_id,
                "grant_type": "refresh_token",
                "refresh_token": self._refresh,
            }
            if self._client_secret:
                data["client_secret"] = self._client_secret
            try:
                async with httpx.AsyncClient(timeout=30.0) as c:
                    r = await c.post(token_url, data=data)
                    payload = r.json() if r.content else {}
                    if r.status_code >= 400:
                        logger.warning(
                            "[calendar.outlook] token refresh failed: %s",
                            payload.get("error_description") or r.status_code,
                        )
                        return False
                    access = payload.get("access_token") or ""
                    if not access:
                        return False
                    new_refresh = payload.get("refresh_token") or self._refresh
                    self._token = access
                    self._refresh = new_refresh
                    persist_microsoft_tokens(
                        access, new_refresh, str(payload.get("scope") or "")
                    )
                    vault_store("email.microsoft.access_token", access, category="email")
                    if new_refresh:
                        vault_store(
                            "email.microsoft.refresh_token",
                            new_refresh,
                            category="email",
                        )
                    return True
            except Exception as exc:
                logger.warning("[calendar.outlook] token refresh failed: %s", exc)
                return False

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        import httpx

        await self._ensure_token()
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.request(
                method, url, headers=self._headers(), params=params, json=json
            )
            if r.status_code == 401 and await self._do_refresh():
                r = await c.request(
                    method, url, headers=self._headers(), params=params, json=json
                )
            r.raise_for_status()
            if r.status_code == 204 or not r.content:
                return None
            return r.json()

    async def list_events(
        self, time_min: str, time_max: str, max_results: int = 25
    ) -> list[dict[str, Any]]:
        data = await self._request(
            "GET",
            f"{_GRAPH}/calendarview",
            params={
                "startDateTime": time_min,
                "endDateTime": time_max,
                "$top": str(max_results),
                "$orderby": "start/dateTime",
            },
        )
        items = (data or {}).get("value", [])
        return [self._norm(e) for e in items]

    async def create_event(
        self,
        summary: str,
        start: str,
        end: str,
        location: str = "",
        description: str = "",
    ) -> dict[str, Any]:
        body = {
            "subject": summary,
            "body": {"contentType": "Text", "content": description},
            "start": {"dateTime": start, "timeZone": "UTC"},
            "end": {"dateTime": end, "timeZone": "UTC"},
            "location": {"displayName": location},
        }
        return self._norm(await self._request("POST", f"{_GRAPH}/events", json=body))

    async def update_event(
        self, event_id: str, fields: dict[str, Any]
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if "summary" in fields:
            body["subject"] = fields["summary"]
        if "description" in fields:
            body["body"] = {"contentType": "Text", "content": fields["description"]}
        if "location" in fields:
            body["location"] = {"displayName": fields["location"]}
        if "start" in fields:
            body["start"] = {"dateTime": fields["start"], "timeZone": "UTC"}
        if "end" in fields:
            body["end"] = {"dateTime": fields["end"], "timeZone": "UTC"}
        return self._norm(
            await self._request("PATCH", f"{_GRAPH}/events/{event_id}", json=body)
        )

    async def delete_event(self, event_id: str) -> bool:
        import httpx

        await self._ensure_token()
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.delete(f"{_GRAPH}/events/{event_id}", headers=self._headers())
            if r.status_code == 401 and await self._do_refresh():
                r = await c.delete(
                    f"{_GRAPH}/events/{event_id}", headers=self._headers()
                )
            return r.status_code in (204, 200)

    async def find_free_slots(
        self, date: str, duration_minutes: int = 30
    ) -> list[dict[str, str]]:
        from datetime import timedelta

        from kazma_skills.native.calendar.backends.sandbox import _parse_iso

        day = _parse_iso(date + "T09:00:00+00:00" if len(date) == 10 else date)
        day_end = day + timedelta(hours=8)
        events = await self.list_events(day.isoformat(), day_end.isoformat(), max_results=50)
        busy = []
        for e in events:
            s = _parse_iso(e["start"])
            en = _parse_iso(e["end"])
            if s.date() == day.date():
                busy.append((s, en))
        busy.sort()
        slots: list[dict[str, str]] = []
        cursor = day
        delta = timedelta(minutes=duration_minutes)
        for s, en in busy:
            if cursor + delta <= s:
                slots.append(
                    {"start": cursor.isoformat(), "end": (cursor + delta).isoformat()}
                )
            cursor = max(cursor, en)
        while cursor + delta <= day_end:
            slots.append(
                {"start": cursor.isoformat(), "end": (cursor + delta).isoformat()}
            )
            cursor += delta
        return slots[:10]

    @staticmethod
    def _norm(e: dict[str, Any]) -> dict[str, Any]:
        start = ((e.get("start") or {}).get("dateTime")) or ""
        end = ((e.get("end") or {}).get("dateTime")) or ""
        loc = ((e.get("location") or {}).get("displayName")) or ""
        desc = ((e.get("body") or {}).get("content")) or ""
        return {
            "id": e.get("id", ""),
            "summary": e.get("subject", "(no title)"),
            "start": start,
            "end": end,
            "location": loc,
            "description": desc,
            "provider": "outlook",
        }
