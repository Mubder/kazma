"""In-memory sandbox calendar — always available, no account required.

Stores events in a process-lifetime list. Useful for testing and as the
fallback when no calendar account is connected.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from kazma_skills.native.calendar.backends.base import CalendarBackend  # noqa: F401


def _parse_iso(s: str) -> datetime:
    # Accept trailing Z and naive datetimes.
    s = (s or "").replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class SandboxBackend:
    """Process-local in-memory calendar."""

    name = "sandbox"

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []

    async def list_events(
        self, time_min: str, time_max: str, max_results: int = 25
    ) -> list[dict[str, Any]]:
        lo, hi = _parse_iso(time_min), _parse_iso(time_max)
        out = [
            e for e in self._events
            if not (e["_end"] < lo or e["_start"] > hi)
        ]
        out.sort(key=lambda e: e["_start"])
        return out[:max_results]

    async def create_event(
        self,
        summary: str,
        start: str,
        end: str,
        location: str = "",
        description: str = "",
    ) -> dict[str, Any]:
        ev = {
            "id": uuid.uuid4().hex[:12],
            "summary": summary,
            "start": start,
            "end": end,
            "location": location,
            "description": description,
            "_start": _parse_iso(start),
            "_end": _parse_iso(end),
            "provider": "sandbox",
        }
        self._events.append(ev)
        return {k: v for k, v in ev.items() if not k.startswith("_")}

    async def update_event(
        self, event_id: str, fields: dict[str, Any]
    ) -> dict[str, Any]:
        for e in self._events:
            if e["id"] == event_id:
                for k, v in fields.items():
                    if k in ("summary", "start", "end", "location", "description"):
                        e[k] = v
                        if k == "start":
                            e["_start"] = _parse_iso(v)
                        elif k == "end":
                            e["_end"] = _parse_iso(v)
                return {k: v for k, v in e.items() if not k.startswith("_")}
        return {"error": f"Event {event_id} not found"}

    async def delete_event(self, event_id: str) -> bool:
        before = len(self._events)
        self._events = [e for e in self._events if e["id"] != event_id]
        return len(self._events) < before

    async def find_free_slots(
        self, date: str, duration_minutes: int = 30
    ) -> list[dict[str, str]]:
        import asyncio

        from kazma_skills.native.calendar.backends.base import free_slots, operator_zone

        zone = await asyncio.to_thread(operator_zone)
        events = [{"start": e["_start"].isoformat(), "end": e["_end"].isoformat()} for e in self._events]
        return free_slots(events, day=date, duration_minutes=duration_minutes, zone=zone)
