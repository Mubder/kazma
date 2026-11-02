"""Calendar backend protocol, and the one free-slot finder every backend uses."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date as _date
from datetime import datetime, time, timedelta, tzinfo
from typing import Any, Protocol, runtime_checkable

#: The working day the free-slot search looks in, in the operator's zone.
WORKDAY_START = time(9, 0)
WORKDAY_HOURS = 8
#: Free slots a search answers at most.
MAX_SLOTS = 10


@runtime_checkable
class CalendarBackend(Protocol):
    """Structural interface for calendar backends."""

    name: str

    async def list_events(
        self, time_min: str, time_max: str, max_results: int = 25
    ) -> list[dict[str, Any]]:
        ...

    async def create_event(
        self,
        summary: str,
        start: str,
        end: str,
        location: str = "",
        description: str = "",
    ) -> dict[str, Any]:
        ...

    async def update_event(
        self, event_id: str, fields: dict[str, Any]
    ) -> dict[str, Any]:
        ...

    async def delete_event(self, event_id: str) -> bool:
        ...

    async def find_free_slots(
        self, date: str, duration_minutes: int = 30
    ) -> list[dict[str, str]]:
        ...


def operator_zone() -> tzinfo:
    """The zone the operator lives in (``cron.timezone``, else ``KAZMA_TZ``,
    else UTC): the scheduler's, so "free at 9" means the same everywhere.
    Blocking (a settings read): async callers use a thread."""
    from kazma_core.cron.scheduler import get_cron_timezone

    return get_cron_timezone()


def _event_time(value: str, zone: tzinfo) -> datetime | None:
    """An event's start or end as an aware instant: an ISO time with its
    offset, a naive one in UTC (the providers answer UTC when no zone is
    named), a date alone (an all-day event) at midnight in *zone*."""
    text = (value or "").strip()
    if not text:
        return None
    if len(text) == 10:
        try:
            return datetime.combine(_date.fromisoformat(text), time(0, 0), tzinfo=zone)
        except ValueError:
            return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:  # Graph's seven-digit fraction: "2026-11-03T09:00:00.0000000"
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00")[:26])
        except ValueError:
            return None
    if parsed.tzinfo is None:
        from datetime import timezone

        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def workday(day: str, zone: tzinfo) -> tuple[datetime, datetime]:
    """The working day *day* names (``YYYY-MM-DD``, or an ISO time whose date
    in *zone* is the day), from ``WORKDAY_START`` for ``WORKDAY_HOURS`` in
    *zone*."""
    text = (day or "").strip()
    if len(text) == 10:
        on = _date.fromisoformat(text)
    else:
        moment = _event_time(text, zone)
        if moment is None:
            raise ValueError(f"not a date: {day!r}")
        on = moment.astimezone(zone).date()
    start = datetime.combine(on, WORKDAY_START, tzinfo=zone)
    return start, start + timedelta(hours=WORKDAY_HOURS)


def free_slots(
    events: Iterable[dict[str, Any]], *, day: str, duration_minutes: int, zone: tzinfo,
) -> list[dict[str, str]]:
    """The free slots of *duration_minutes* in *day*'s working day, around
    every event that overlaps it -- one that began on an earlier day, an
    all-day event, an overnight one -- in the operator's *zone*.

    Each backend had its own loop: a working day of 09:00 UTC (12:00 in
    Kuwait) and only the events that STARTED that day, so an event from the
    2nd to the 5th left the 3rd "free" (2026-11 functional audit, FA-112).
    Backends supply the events; this decides.
    """
    window_start, window_end = workday(day, zone)
    busy: list[tuple[datetime, datetime]] = []
    for event in events:
        start = _event_time(str(event.get("start") or ""), zone)
        end = _event_time(str(event.get("end") or ""), zone)
        if start is None:
            continue
        if end is None or end <= start:
            end = start + timedelta(minutes=1)
        if start < window_end and end > window_start:
            busy.append((max(start, window_start), min(end, window_end)))
    busy.sort()
    delta = timedelta(minutes=max(1, int(duration_minutes)))
    slots: list[dict[str, str]] = []
    cursor = window_start
    for start, end in busy:
        while cursor + delta <= start and len(slots) < MAX_SLOTS:
            slots.append({"start": cursor.isoformat(), "end": (cursor + delta).isoformat()})
            cursor += delta
        cursor = max(cursor, end)
    while cursor + delta <= window_end and len(slots) < MAX_SLOTS:
        slots.append({"start": cursor.isoformat(), "end": (cursor + delta).isoformat()})
        cursor += delta
    return slots
