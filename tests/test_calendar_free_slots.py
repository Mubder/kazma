"""The free-slot search answers "free" only over free time (FA-112).

The 2026-11 functional audit: every calendar backend had its own loop with a
working day of 09:00 UTC (12:00 in Kuwait) and only the events that STARTED
that day. An event from the 2nd to the 5th left the 3rd with ten free slots;
an all-day event and an overnight one left 09:00Z free.

One finder (``backends.base.free_slots``) in the operator's zone, fed by
each backend's own events: the sandbox's store, Google's and Outlook's event
shapes (``_norm`` output) from a scripted listing.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from kazma_skills.native.calendar.backends import base
from kazma_skills.native.calendar.backends.google_calendar import GoogleCalendarBackend
from kazma_skills.native.calendar.backends.outlook_calendar import OutlookCalendarBackend
from kazma_skills.native.calendar.backends.sandbox import SandboxBackend

DAY = "2026-11-03"


@pytest.fixture(autouse=True)
def kuwait(monkeypatch):
    monkeypatch.setenv("KAZMA_TZ", "Asia/Kuwait")
    from kazma_core.config_store import get_config_store

    get_config_store().delete("cron.timezone")


def _sandbox(events: list[tuple[str, str]]):
    backend = SandboxBackend()
    backend._events = []
    for i, (start, end) in enumerate(events):
        asyncio.run(backend.create_event(f"e{i}", start, end))
    return backend


def _listed(cls, events: list[dict]):
    backend = cls.__new__(cls)

    async def list_events(time_min, time_max, max_results=25):
        return events

    backend.list_events = list_events
    backend.name = cls.__name__
    return backend


def _slots(backend) -> list[dict]:
    return asyncio.run(backend.find_free_slots(DAY, 30))


MULTI_DAY = ("2026-11-02T08:00:00+03:00", "2026-11-05T18:00:00+03:00")


@pytest.mark.parametrize("backend", [
    lambda: _sandbox([MULTI_DAY]),
    lambda: _listed(GoogleCalendarBackend, [{"start": MULTI_DAY[0], "end": MULTI_DAY[1]}]),
    lambda: _listed(OutlookCalendarBackend, [{"start": "2026-11-02T05:00:00.0000000",
                                              "end": "2026-11-05T15:00:00.0000000"}]),
], ids=["sandbox", "google", "outlook"])
def test_an_event_spanning_the_day_leaves_no_slot(backend):
    assert _slots(backend()) == []


@pytest.mark.parametrize("backend", [
    lambda: _listed(GoogleCalendarBackend, [{"start": "2026-11-03", "end": "2026-11-04"}]),
    lambda: _listed(OutlookCalendarBackend, [{"start": "2026-11-02T21:00:00.0000000",
                                              "end": "2026-11-03T21:00:00.0000000"}]),
], ids=["google", "outlook"])
def test_an_all_day_event_leaves_no_slot(backend):
    assert _slots(backend()) == []


@pytest.mark.parametrize("backend", [
    lambda: _sandbox([("2026-11-02T22:00:00+03:00", "2026-11-03T10:00:00+03:00")]),
    lambda: _listed(GoogleCalendarBackend, [{"start": "2026-11-02T22:00:00+03:00",
                                             "end": "2026-11-03T10:00:00+03:00"}]),
], ids=["sandbox", "google"])
def test_the_day_is_the_operators_and_an_overnight_event_holds_its_morning(backend):
    slots = _slots(backend())
    first = datetime.fromisoformat(slots[0]["start"])
    assert first == datetime(2026, 11, 3, 10, 0, tzinfo=timezone(timedelta(hours=3)))
    last = datetime.fromisoformat(slots[-1]["end"])
    assert last <= datetime(2026, 11, 3, 17, 0, tzinfo=timezone(timedelta(hours=3)))


def test_an_empty_day_starts_at_nine_in_the_operators_zone():
    slots = _slots(_sandbox([]))
    assert slots[0]["start"] == "2026-11-03T09:00:00+03:00"
    assert len(slots) == base.MAX_SLOTS


def _old_sandbox_loop(events, date, duration_minutes=30):
    """The sandbox loop before 2026-11, for the negative control."""
    day = datetime.fromisoformat(date + "T09:00:00+00:00")
    day_end = day + timedelta(hours=8)
    busy = sorted([e for e in events if day.date() == e[0].date()])
    slots, cursor, delta = [], day, timedelta(minutes=duration_minutes)
    for start, end in busy:
        if cursor + delta <= start:
            slots.append(cursor)
        cursor = max(cursor, end)
    while cursor + delta <= day_end:
        slots.append(cursor)
        cursor += delta
    return slots[:10]


def test_the_old_loop_called_a_booked_day_free():
    """Negative control: the multi-day event is invisible to the old loop,
    and its day starts at 09:00 UTC (12:00 in Kuwait)."""
    event = (datetime.fromisoformat(MULTI_DAY[0]), datetime.fromisoformat(MULTI_DAY[1]))
    old = _old_sandbox_loop([event], DAY)
    assert len(old) == 10 and old[0].hour == 9 and old[0].utcoffset() == timedelta(0)
