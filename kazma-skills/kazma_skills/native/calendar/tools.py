"""Calendar Native Skill — list/create/update/delete events + find free slots.

Backends are selected by the router (``router.py``): Google Calendar and
Microsoft Outlook when an OAuth token is configured, else a local sandbox.
All tools return human-readable ``str`` results.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


def _default_window(time_min: str = "", time_max: str = "") -> tuple[str, str]:
    """Default to the next 7 days when bounds are omitted."""
    now = datetime.now(timezone.utc)
    lo = time_min or now.isoformat()
    hi = time_max or (now + timedelta(days=7)).isoformat()
    return lo, hi


def _fmt(events: list) -> str:
    if not events:
        return "No events found."
    lines = []
    for e in events:
        loc = f" @ {e.get('location')}" if e.get("location") else ""
        lines.append(
            f"• {e.get('summary', '(no title)')}\n"
            f"  {e.get('start')} → {e.get('end')}{loc}\n"
            f"  id: {e.get('id')} ({e.get('provider', '?')})"
        )
    return "\n".join(lines)


async def list_events(
    time_min: str = "",
    time_max: str = "",
    max_results: int = 25,
    provider: str = "auto",
) -> str:
    """List upcoming calendar events (ISO 8601 bounds; defaults to next 7 days)."""
    from kazma_skills.native.calendar.router import get_backend

    lo, hi = _default_window(time_min, time_max)
    try:
        backend = get_backend(provider)
        events = await backend.list_events(lo, hi, max_results)
    except Exception as exc:  # noqa: BLE001
        return f"Error: could not list events — {type(exc).__name__}: {exc}"
    return f"Calendar: {backend.name}\n{_fmt(events)}"


async def create_event(
    summary: str,
    start: str,
    end: str,
    location: str = "",
    description: str = "",
    provider: str = "auto",
) -> str:
    """Create a calendar event (start/end in ISO 8601)."""
    if not summary or not summary.strip():
        return "Error: event summary is required."
    if not start or not end:
        return "Error: start and end (ISO 8601) are required."
    from kazma_skills.native.calendar.router import get_backend

    try:
        backend = get_backend(provider)
        ev = await backend.create_event(
            summary.strip(), start, end, location, description
        )
    except Exception as exc:  # noqa: BLE001
        return f"Error: could not create event — {type(exc).__name__}: {exc}"
    return f"Event created ({backend.name}):\n{json.dumps(ev, indent=2, default=str)}"


async def update_event(
    event_id: str,
    fields: dict,
    provider: str = "auto",
) -> str:
    """Update an event by id. ``fields`` keys: summary, start, end, location, description."""
    if not event_id or not event_id.strip():
        return "Error: event_id is required."
    from kazma_skills.native.calendar.router import get_backend

    try:
        backend = get_backend(provider)
        ev = await backend.update_event(event_id.strip(), fields or {})
    except Exception as exc:  # noqa: BLE001
        return f"Error: could not update event — {type(exc).__name__}: {exc}"
    return f"Event updated ({backend.name}):\n{json.dumps(ev, indent=2, default=str)}"


async def delete_event(event_id: str, provider: str = "auto") -> str:
    """Delete a calendar event by id."""
    if not event_id or not event_id.strip():
        return "Error: event_id is required."
    from kazma_skills.native.calendar.router import get_backend

    try:
        backend = get_backend(provider)
        ok = await backend.delete_event(event_id.strip())
    except Exception as exc:  # noqa: BLE001
        return f"Error: could not delete event — {type(exc).__name__}: {exc}"
    return f"Event {event_id} deleted ({backend.name})." if ok else f"Event {event_id} not found."


async def find_free_slots(
    date: str,
    duration_minutes: int = 30,
    provider: str = "auto",
) -> str:
    """Find free slots of ``duration_minutes`` on ``date`` (YYYY-MM-DD or ISO)."""
    if not date or not date.strip():
        return "Error: date (YYYY-MM-DD) is required."
    from kazma_skills.native.calendar.router import get_backend

    try:
        backend = get_backend(provider)
        slots = await backend.find_free_slots(date.strip(), duration_minutes)
    except Exception as exc:  # noqa: BLE001
        return f"Error: could not find free slots — {type(exc).__name__}: {exc}"
    if not slots:
        return f"No {duration_minutes}-minute free slots found on {date} ({backend.name})."
    lines = [f"Free slots on {date} ({backend.name}, {duration_minutes} min):"]
    for s in slots:
        lines.append(f"  {s['start']} → {s['end']}")
    return "\n".join(lines)
