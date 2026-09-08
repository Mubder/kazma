"""Google Calendar backend via the Calendar REST API (v3).

Tokens come from :mod:`kazma_skills.native.calendar.credentials` (vault +
env). A 401 triggers one refresh of ``calendar.google.refresh_token``
(or the Gmail grant when that grant includes Calendar scope).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_API = "https://www.googleapis.com/calendar/v3"
_refresh_lock = asyncio.Lock()


class GoogleCalendarBackend:
    """Google Calendar REST backend."""

    name = "google"

    def __init__(self, access_token: str, refresh_token: str = "") -> None:
        self._token = access_token
        self._refresh = refresh_token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def _ensure_token(self) -> None:
        if self._token and self._token != "pending_refresh":
            return
        await self._do_refresh()

    async def _do_refresh(self) -> bool:
        refresh = self._refresh
        if not refresh:
            from kazma_skills.native.calendar.credentials import google_refresh_token

            refresh = google_refresh_token()
        if not refresh:
            return False
        async with _refresh_lock:
            try:
                from kazma_skills.native.calendar.oauth_google import (
                    refresh_google_calendar_access_token,
                )

                access, new_refresh = await refresh_google_calendar_access_token(refresh)
                self._token = access
                self._refresh = new_refresh
                return True
            except Exception as exc:
                logger.warning("[calendar.google] token refresh failed: %s", exc)
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
            if r.status_code == 401:
                if await self._do_refresh():
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
            f"{_API}/calendars/primary/events",
            params={
                "timeMin": time_min,
                "timeMax": time_max,
                "maxResults": max_results,
                "singleEvents": "true",
                "orderBy": "startTime",
            },
        )
        items = (data or {}).get("items", [])
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
            "summary": summary,
            "location": location,
            "description": description,
            "start": {"dateTime": start, "timeZone": "UTC"},
            "end": {"dateTime": end, "timeZone": "UTC"},
        }
        return self._norm(
            await self._request("POST", f"{_API}/calendars/primary/events", json=body)
        )

    async def update_event(
        self, event_id: str, fields: dict[str, Any]
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        for k in ("summary", "location", "description"):
            if k in fields:
                body[k] = fields[k]
        if "start" in fields:
            body["start"] = {"dateTime": fields["start"], "timeZone": "UTC"}
        if "end" in fields:
            body["end"] = {"dateTime": fields["end"], "timeZone": "UTC"}
        return self._norm(
            await self._request(
                "PATCH", f"{_API}/calendars/primary/events/{event_id}", json=body
            )
        )

    async def delete_event(self, event_id: str) -> bool:
        import httpx

        await self._ensure_token()
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.delete(
                f"{_API}/calendars/primary/events/{event_id}",
                headers=self._headers(),
            )
            if r.status_code == 401 and await self._do_refresh():
                r = await c.delete(
                    f"{_API}/calendars/primary/events/{event_id}",
                    headers=self._headers(),
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
                slots.append({"start": cursor.isoformat(), "end": (cursor + delta).isoformat()})
            cursor = max(cursor, en)
        while cursor + delta <= day_end:
            slots.append({"start": cursor.isoformat(), "end": (cursor + delta).isoformat()})
            cursor += delta
        return slots[:10]

    @staticmethod
    def _norm(e: dict[str, Any]) -> dict[str, Any]:
        start = (e.get("start") or {}).get("dateTime") or (e.get("start") or {}).get("date", "")
        end = (e.get("end") or {}).get("dateTime") or (e.get("end") or {}).get("date", "")
        return {
            "id": e.get("id", ""),
            "summary": e.get("summary", "(no title)"),
            "start": start,
            "end": end,
            "location": e.get("location", ""),
            "description": e.get("description", ""),
            "provider": "google",
        }
