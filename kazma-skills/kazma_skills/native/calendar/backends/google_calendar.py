"""Google Calendar backend via the Calendar REST API (v3).

Uses an OAuth2 access token resolved from the ``GOOGLE_CALENDAR_TOKEN``
env var or the secret vault (key ``calendar.google.token``). When no token
is available, the router falls back to the sandbox backend.
"""

from __future__ import annotations

from typing import Any

_API = "https://www.googleapis.com/calendar/v3"


class GoogleCalendarBackend:
    """Google Calendar REST backend."""

    name = "google"

    def __init__(self, access_token: str) -> None:
        self._token = access_token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def list_events(
        self, time_min: str, time_max: str, max_results: int = 25
    ) -> list[dict[str, Any]]:
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.get(
                f"{_API}/calendars/primary/events",
                headers=self._headers(),
                params={
                    "timeMin": time_min,
                    "timeMax": time_max,
                    "maxResults": max_results,
                    "singleEvents": "true",
                    "orderBy": "startTime",
                },
            )
            r.raise_for_status()
            items = r.json().get("items", [])
            return [self._norm(e) for e in items]

    async def create_event(
        self,
        summary: str,
        start: str,
        end: str,
        location: str = "",
        description: str = "",
    ) -> dict[str, Any]:
        import httpx

        body = {
            "summary": summary,
            "location": location,
            "description": description,
            "start": {"dateTime": start},
            "end": {"dateTime": end},
        }
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(
                f"{_API}/calendars/primary/events",
                headers=self._headers(),
                json=body,
            )
            r.raise_for_status()
            return self._norm(r.json())

    async def update_event(
        self, event_id: str, fields: dict[str, Any]
    ) -> dict[str, Any]:
        import httpx

        body: dict[str, Any] = {}
        for k in ("summary", "location", "description"):
            if k in fields:
                body[k] = fields[k]
        if "start" in fields:
            body["start"] = {"dateTime": fields["start"]}
        if "end" in fields:
            body["end"] = {"dateTime": fields["end"]}
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.patch(
                f"{_API}/calendars/primary/events/{event_id}",
                headers=self._headers(),
                json=body,
            )
            r.raise_for_status()
            return self._norm(r.json())

    async def delete_event(self, event_id: str) -> bool:
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.delete(
                f"{_API}/calendars/primary/events/{event_id}",
                headers=self._headers(),
            )
            return r.status_code in (204, 200)

    async def find_free_slots(
        self, date: str, duration_minutes: int = 30
    ) -> list[dict[str, str]]:
        # Google's freeBusy API requires a timeMin/timeMax; fall back to a
        # client-side computation from listed events to keep this simple.
        from datetime import datetime, timedelta, timezone

        from kazma_skills.native.calendar.backends.sandbox import _parse_iso

        day = _parse_iso(date + "T09:00:00+00:00" if len(date) == 10 else date)
        day_end = day + timedelta(hours=8)
        events = await self.list_events(day.isoformat(), day_end.isoformat(), max_results=50)
        busy = []
        for e in events:
            s = _parse_iso(e["start"]); en = _parse_iso(e["end"])
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
