"""Microsoft Outlook calendar backend via MS Graph.

Uses an OAuth2 access token from ``MS_CALENDAR_TOKEN`` env var or the
secret vault (key ``calendar.microsoft.token``). Falls back to sandbox when
no token is available.
"""

from __future__ import annotations

from typing import Any

_GRAPH = "https://graph.microsoft.com/v1.0/me"


class OutlookCalendarBackend:
    """Microsoft Outlook calendar via MS Graph."""

    name = "outlook"

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
                f"{_GRAPH}/calendarview",
                headers=self._headers(),
                params={
                    "startDateTime": time_min,
                    "endDateTime": time_max,
                    "$top": str(max_results),
                    "$orderby": "start/dateTime",
                },
            )
            r.raise_for_status()
            items = r.json().get("value", [])
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
            "subject": summary,
            "body": {"contentType": "Text", "content": description},
            "start": {"dateTime": start, "timeZone": "UTC"},
            "end": {"dateTime": end, "timeZone": "UTC"},
            "location": {"displayName": location},
        }
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(f"{_GRAPH}/events", headers=self._headers(), json=body)
            r.raise_for_status()
            return self._norm(r.json())

    async def update_event(
        self, event_id: str, fields: dict[str, Any]
    ) -> dict[str, Any]:
        import httpx

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
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.patch(
                f"{_GRAPH}/events/{event_id}", headers=self._headers(), json=body
            )
            r.raise_for_status()
            return self._norm(r.json())

    async def delete_event(self, event_id: str) -> bool:
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.delete(f"{_GRAPH}/events/{event_id}", headers=self._headers())
            return r.status_code in (204, 200)

    async def find_free_slots(
        self, date: str, duration_minutes: int = 30
    ) -> list[dict[str, str]]:
        # Reuse the Google client-side computation for parity/simplicity.
        from datetime import timedelta

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
