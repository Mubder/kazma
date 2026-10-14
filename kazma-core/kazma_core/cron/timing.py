"""One time model for every scheduling path (Scheduled audit SCH-E03, E05, E09).

Cron reminders and agent tasks, the commitment gate that checks a booking
from chat, the Scheduled page and X booking all read a timing string here,
and get the same instant for it:

* **A time without an offset means the operator's zone** (``cron.timezone``,
  else ``KAZMA_TZ``, else UTC). The commitment gate used to read it as UTC and
  rewrite the booking to that instant, so "09:00" booked from chat fired at
  12:00 for an operator in Asia/Kuwait while the same text on the page fired
  at 09:00 (audit M14's bug, back through another door).
* **RFC 3339** is accepted as written, a trailing ``Z`` included: the old
  parser lowercased its input first and refused ``...T09:00:00Z``.
* **One-off times are future and finite**: in the past, ``0m``, or more than
  :data:`MAX_HORIZON` away is refused with a reason (it used to be booked and
  then fired at once, skipped as "done", or answered 500 on an overflow).
* **One clock-change policy.** A one-off local time that does not exist (the
  clock jumps over it) or occurs twice is refused, as X booking always did.
  A recurring time that does not exist on a clock-change day fires at the
  same wall time shifted by the jump (02:30 becomes 03:30); one that occurs
  twice fires once, at its first occurrence.
* **A typed recurrence**: daily or weekly, at an hour and minute, on chosen
  weekdays, with an optional end (``until``) or number of occurrences
  (``count``) and an optional pinned zone. Friendly forms ("daily at 9am",
  "weekdays at 08:30", "every mon,wed at 9:15pm until 2026-12-31") and an
  RRULE subset (``FREQ=DAILY|WEEKLY;BYHOUR;BYMINUTE;BYDAY;UNTIL;COUNT;TZID``)
  read to the same :class:`Recurrence`. The commitment gate keeps it; it never
  turns a recurrence into a one-off again.

Everything here is pure: the zone is passed in. Resolving the operator's zone
reads settings, so callers do that once, off the event loop, at their
boundary (SCH-E10).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from zoneinfo import ZoneInfo

__all__ = [
    "MAX_HORIZON",
    "MAX_TIMING_CHARS",
    "Recurrence",
    "ResolvedTiming",
    "TimingError",
    "looks_recurring",
    "next_occurrence",
    "parse_recurrence",
    "resolve_timing",
]

#: The furthest ahead a one-off may be booked, and how far a recurrence may run.
MAX_HORIZON = timedelta(days=730)
#: A timing string longer than this is refused before any parsing.
MAX_TIMING_CHARS = 100
#: The largest COUNT a recurrence may carry.
MAX_COUNT = 1000

_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_RRULE_DAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")


class TimingError(ValueError):
    """A timing string that cannot be booked, with the reason in words.

    A ``ValueError`` so every existing ``except ValueError`` answers it as a
    validation error (400), never 500 or 404.
    """


@dataclass(frozen=True)
class Recurrence:
    """A daily or weekly series at one local wall time."""

    freq: str  # "daily" | "weekly"
    hour: int
    minute: int
    weekdays: tuple[int, ...] = ()  # 0 = Monday; empty for daily
    until: datetime | None = None  # aware; the last allowed instant
    count: int | None = None
    tzid: str | None = None  # a zone pinned by the rule itself

    def describe(self) -> str:
        """The canonical friendly text (round-trips through :func:`parse_recurrence`)."""
        at = f"{self.hour:02d}:{self.minute:02d}"
        if self.freq == "daily":
            text = f"daily at {at}"
        elif self.weekdays == (0, 1, 2, 3, 4):
            text = f"weekdays at {at}"
        else:
            text = f"weekly on {','.join(_WEEKDAYS[d] for d in self.weekdays)} at {at}"
        if self.until is not None:
            text += f" until {self.until.date().isoformat()}"
        if self.count is not None:
            text += f" count {self.count}"
        if self.tzid:
            text += f" tz {self.tzid}"
        return text


@dataclass(frozen=True)
class ResolvedTiming:
    """What a timing string means, resolved against one zone and one 'now'."""

    first: datetime  # aware; the first (or only) fire
    recurrence: Recurrence | None
    zone: str  # the zone the string was read in
    kind: str  # "relative" | "absolute" | "recurring"

    @property
    def recurring(self) -> bool:
        return self.recurrence is not None


def _zone_named(name: str) -> tzinfo:
    """``ZoneInfo(name)``, UTC for ``""``/``"UTC"``. Raises :class:`TimingError`."""
    name = (name or "").strip()
    if not name or name.upper() == "UTC":
        return UTC
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 — any zoneinfo failure is "no such zone"
        raise TimingError(f"Unknown time zone {name!r} (an IANA name is required, e.g. 'Asia/Kuwait').") from None


# ── recurrence ────────────────────────────────────────────────────────────

_CLOCK_RE = re.compile(
    r"^(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ampm>am|pm|a\.m\.|p\.m\.)?$",
    re.IGNORECASE,
)


def _clock(text: str) -> tuple[int, int]:
    m = _CLOCK_RE.match(text.strip())
    if not m:
        raise TimingError(f"Unreadable time of day {text!r}. Use 9am, 9:30pm or 21:30.")
    hour, minute = int(m.group("h")), int(m.group("m") or 0)
    ampm = (m.group("ampm") or "").replace(".", "").lower()
    if minute > 59:
        raise TimingError(f"Minute out of range in {text!r}.")
    if ampm:
        if not 1 <= hour <= 12:
            raise TimingError(f"Hour must be 1-12 with am/pm in {text!r}.")
        hour = (hour % 12) + (12 if ampm == "pm" else 0)
    elif m.group("m") is None:
        # "daily at 9" is ambiguous (9am or 21:00?); say so instead of guessing.
        raise TimingError(f"Say am or pm, or use 24-hour time, in {text!r} (e.g. 9am or 21:00).")
    if not 0 <= hour <= 23:
        raise TimingError(f"Hour out of range in {text!r}.")
    return hour, minute


def _days(text: str) -> tuple[int, ...]:
    out: set[int] = set()
    for part in re.split(r"[\s,/]+|\band\b", text.strip().lower()):
        part = part.strip()
        if not part:
            continue
        key = part[:3]
        if key not in _WEEKDAYS:
            raise TimingError(f"Unknown weekday {part!r}. Use mon, tue, wed, thu, fri, sat or sun.")
        out.add(_WEEKDAYS.index(key))
    if not out:
        raise TimingError("Name at least one weekday.")
    return tuple(sorted(out))


def _until(text: str) -> datetime:
    raw = text.strip()
    try:
        if re.fullmatch(r"\d{8}(T\d{6}Z?)?", raw):  # RRULE form
            day = date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
        else:
            day = date.fromisoformat(raw[:10])
    except ValueError:
        raise TimingError(f"Unreadable end date {text!r}. Use YYYY-MM-DD.") from None
    # The whole end day is included; the zone is applied when it is compared.
    return datetime.combine(day, time(23, 59, 59), tzinfo=UTC)


def _count(text: str) -> int:
    try:
        n = int(text)
    except ValueError:
        raise TimingError(f"Unreadable count {text!r}.") from None
    if not 1 <= n <= MAX_COUNT:
        raise TimingError(f"A recurrence count must be 1-{MAX_COUNT}.")
    return n


_FRIENDLY_RE = re.compile(
    r"^(?:(?P<daily>daily|every\s+day)"
    r"|(?P<weekdays>weekdays|every\s+weekday)"
    r"|(?:weekly\s+on|every)\s+(?P<days>[a-z ,/]+?))"
    r"\s+at\s+(?P<clock>[0-9:. apm]+?)"
    r"(?:\s+until\s+(?P<until>[0-9-]{10}))?"
    r"(?:\s+(?:count|for)\s+(?P<count>\d+)(?:\s+times)?)?"
    r"(?:\s+tz\s+(?P<tz>[A-Za-z0-9_+/-]+))?$",
    re.IGNORECASE,
)


def parse_recurrence(text: str) -> Recurrence | None:
    """The recurrence *text* names, None when it names none.

    Raises :class:`TimingError` when *text* is recurring but malformed: a
    recurring request is never silently read as something else.
    """
    raw = " ".join((text or "").strip().split())
    if not raw:
        return None
    if raw.upper().startswith(("RRULE:", "FREQ=")):
        return _parse_rrule(raw)
    m = _FRIENDLY_RE.match(raw)
    if m is None:
        if looks_recurring(raw):
            raise TimingError(
                f"Unreadable recurring time {raw!r}. Use 'daily at 9am', 'weekdays at 08:30', "
                "'every mon,wed at 21:00', optionally with 'until YYYY-MM-DD' or 'count N'.")
        return None
    hour, minute = _clock(m.group("clock"))
    if m.group("daily"):
        freq, days = "daily", ()
    elif m.group("weekdays"):
        freq, days = "weekly", (0, 1, 2, 3, 4)
    else:
        freq, days = "weekly", _days(m.group("days"))
        if days == tuple(range(7)):
            freq, days = "daily", ()
    tzid = m.group("tz")
    if tzid:
        _zone_named(tzid)  # refuses an unknown zone now, not at the first fire
    return Recurrence(
        freq=freq, hour=hour, minute=minute, weekdays=days,
        until=_until(m.group("until")) if m.group("until") else None,
        count=_count(m.group("count")) if m.group("count") else None,
        tzid=tzid,
    )


def _parse_rrule(raw: str) -> Recurrence:
    body = raw.split(":", 1)[1] if raw.upper().startswith("RRULE:") else raw
    parts: dict[str, str] = {}
    for item in body.split(";"):
        if not item.strip():
            continue
        if "=" not in item:
            raise TimingError(f"Unreadable RRULE part {item!r}.")
        key, value = item.split("=", 1)
        parts[key.strip().upper()] = value.strip()
    allowed = {"FREQ", "BYHOUR", "BYMINUTE", "BYDAY", "UNTIL", "COUNT", "TZID", "INTERVAL"}
    unknown = sorted(set(parts) - allowed)
    if unknown:
        raise TimingError(f"RRULE parts not supported: {', '.join(unknown)}.")
    if parts.get("INTERVAL", "1") != "1":
        raise TimingError("RRULE INTERVAL other than 1 is not supported.")
    freq = parts.get("FREQ", "").upper()
    if freq not in ("DAILY", "WEEKLY"):
        raise TimingError("RRULE FREQ must be DAILY or WEEKLY.")
    try:
        hour = int(parts.get("BYHOUR", ""))
        minute = int(parts.get("BYMINUTE", "0"))
    except ValueError:
        raise TimingError("RRULE needs a numeric BYHOUR (and BYMINUTE).") from None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise TimingError("RRULE BYHOUR/BYMINUTE out of range.")
    days: tuple[int, ...] = ()
    if parts.get("BYDAY"):
        try:
            days = tuple(sorted({_RRULE_DAYS.index(d.strip().upper()) for d in parts["BYDAY"].split(",")}))
        except ValueError:
            raise TimingError("RRULE BYDAY takes MO,TU,WE,TH,FR,SA,SU.") from None
    if freq == "WEEKLY" and not days:
        raise TimingError("A weekly RRULE needs BYDAY.")
    if freq == "DAILY" and days:
        freq = "WEEKLY"
    if "UNTIL" in parts and "COUNT" in parts:
        raise TimingError("RRULE takes UNTIL or COUNT, not both.")
    tzid = parts.get("TZID") or None
    if tzid:
        _zone_named(tzid)
    rec = Recurrence(
        freq="weekly" if freq == "WEEKLY" else "daily", hour=hour, minute=minute,
        weekdays=days if freq == "WEEKLY" else (),
        until=_until(parts["UNTIL"]) if "UNTIL" in parts else None,
        count=_count(parts["COUNT"]) if "COUNT" in parts else None,
        tzid=tzid,
    )
    if rec.freq == "weekly" and rec.weekdays == tuple(range(7)):
        rec = Recurrence("daily", rec.hour, rec.minute, (), rec.until, rec.count, rec.tzid)
    return rec


_RECURRING_HINT = re.compile(r"^\s*(daily|weekly|weekdays?|hourly|every\b|rrule:|freq=)", re.IGNORECASE)


def looks_recurring(text: str) -> bool:
    """Whether *text* is phrased as a recurrence (well formed or not)."""
    return bool(_RECURRING_HINT.match(text or ""))


def _instant(day: date, hour: int, minute: int, zone: tzinfo) -> datetime:
    """The wall time on *day* in *zone*, by the recurring clock-change policy.

    ``fold=0`` is PEP 495's reading: a time the clock jumps over is read with
    the offset before the jump (so 02:30 on a spring-forward day is 03:30),
    and a time that occurs twice is its first occurrence.
    """
    return datetime.combine(day, time(hour, minute), tzinfo=zone).replace(fold=0)


def _day_matches(rec: Recurrence, day: date) -> bool:
    return rec.freq == "daily" or day.weekday() in rec.weekdays


def _occurrences_through(rec: Recurrence, zone: tzinfo, start: datetime, last: datetime) -> int:
    """How many occurrences of the series lie in ``[start, last]``."""
    n = 0
    day = start.astimezone(zone).date() - timedelta(days=1)
    end = last.astimezone(zone).date() + timedelta(days=1)
    while day <= end:
        if _day_matches(rec, day):
            at = _instant(day, rec.hour, rec.minute, zone)
            if start <= at <= last:
                n += 1
        day += timedelta(days=1)
    return n


def next_occurrence(rec: Recurrence, *, zone: tzinfo, after: datetime,
                    series_start: datetime | None = None) -> datetime | None:
    """The first occurrence strictly after *after*, or None when the series ended.

    *zone* is the operator's zone; a zone the rule pins (``tz``/``TZID``)
    wins. *series_start* anchors ``count``: occurrences before it do not
    count, and none is ever returned before it.
    """
    if rec.tzid:
        zone = _zone_named(rec.tzid)
    if after.tzinfo is None:
        raise ValueError("next_occurrence needs an aware 'after'")
    day = after.astimezone(zone).date() - timedelta(days=1)
    found: datetime | None = None
    for _ in range(10):  # a matching weekday is at most 7 days away
        if _day_matches(rec, day):
            at = _instant(day, rec.hour, rec.minute, zone)
            if at > after and (series_start is None or at >= series_start):
                found = at
                break
        day += timedelta(days=1)
    if found is None:
        return None
    if rec.until is not None:
        until_local = datetime.combine(rec.until.date(), time(23, 59, 59), tzinfo=zone)
        if found > until_local:
            return None
    if rec.count is not None and series_start is not None:
        if _occurrences_through(rec, zone, series_start, found) > rec.count:
            return None
    return found


# ── one-off times ─────────────────────────────────────────────────────────

_RELATIVE_RE = re.compile(r"^(\d+)\s*(m|min|mins|minutes?|h|hr|hrs|hours?|d|days?)$", re.IGNORECASE)


def _local_wall_time_problem(civil: datetime, zone: tzinfo) -> str | None:
    """Why a NAIVE local *civil* time cannot be one instant in *zone*, or None."""
    instants = set()
    for fold in (0, 1):
        aware = civil.replace(tzinfo=zone, fold=fold)
        if aware.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == civil:
            instants.add(aware.astimezone(UTC))
    if not instants:
        return "does not exist because the clock changes"
    if len(instants) > 1:
        return "occurs twice because the clock changes; add an explicit UTC offset to choose one"
    return None


def _zone_label(zone: tzinfo, zone_name: str) -> str:
    return zone_name or str(zone)


def resolve_timing(text: str, *, zone: tzinfo, zone_name: str = "", now: datetime | None = None,
                   allow_past: bool = False) -> ResolvedTiming:
    """Resolve *text* to its first fire and its recurrence, in *zone*.

    Raises :class:`TimingError` (a ``ValueError``) with a reason that names
    the zone. ``allow_past`` is only for reading a stored one-off back (its
    instant is history, not a booking).
    """
    raw = " ".join((text or "").strip().split())
    label = _zone_label(zone, zone_name)
    if not raw:
        raise TimingError("A time is required.")
    if len(raw) > MAX_TIMING_CHARS:
        raise TimingError(f"A time is at most {MAX_TIMING_CHARS} characters.")
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("resolve_timing needs an aware 'now'")

    rec = parse_recurrence(raw)
    if rec is not None:
        if rec.until is not None and rec.until - now > MAX_HORIZON:
            raise TimingError(f"A recurrence may run at most {MAX_HORIZON.days} days.")
        first = next_occurrence(rec, zone=zone, after=now)
        if first is None:
            raise TimingError("This recurrence has no occurrence after now (its end is in the past).")
        return ResolvedTiming(first=first, recurrence=rec,
                              zone=rec.tzid or label, kind="recurring")

    m = _RELATIVE_RE.match(raw)
    if m:
        value, unit = int(m.group(1)), m.group(2).lower()
        if value < 1:
            raise TimingError("A relative time must be at least 1 minute ahead.")
        if unit.startswith("m"):
            limit = MAX_HORIZON.days * 1440
            delta_kw = {"minutes": value}
        elif unit.startswith("h"):
            limit = MAX_HORIZON.days * 24
            delta_kw = {"hours": value}
        else:
            limit = MAX_HORIZON.days
            delta_kw = {"days": value}
        if value > limit:
            raise TimingError(f"A time may be at most {MAX_HORIZON.days} days ahead.")
        return ResolvedTiming(first=now + timedelta(**delta_kw), recurrence=None, zone=label, kind="relative")

    civil = _parse_iso(raw)
    if civil is None:
        raise TimingError(
            f"Unreadable time {raw!r}. Use 30m, 2h, 2026-12-01T09:00 (read in {label}), an "
            "offset such as 2026-12-01T09:00+03:00 or Z, or 'daily at 9am'.")
    if civil.tzinfo is None:
        problem = _local_wall_time_problem(civil, zone)
        if problem:
            raise TimingError(f"{civil.isoformat(timespec='minutes')} in {label} {problem}.")
        first = civil.replace(tzinfo=zone)
    else:
        first = civil
    if not math.isfinite(first.timestamp()):
        raise TimingError("The time is not a finite instant.")
    if not allow_past:
        if first <= now:
            raise TimingError(
                f"{first.astimezone(zone).isoformat(timespec='minutes')} ({label}) is in the past. "
                "Pick a future time.")
        if first - now > MAX_HORIZON:
            raise TimingError(f"A time may be at most {MAX_HORIZON.days} days ahead.")
    return ResolvedTiming(first=first, recurrence=None, zone=label, kind="absolute")


def _parse_iso(raw: str) -> datetime | None:
    """RFC 3339 / ISO 8601 (date, date-time, ``Z`` or an offset), else None.

    Case is kept: lowercasing turned ``Z`` into ``z``, which
    ``fromisoformat`` refuses.
    """
    cand = raw.strip()
    if cand.endswith(("z", "Z")):
        cand = cand[:-1] + "+00:00"
    cand = cand.replace(" ", "T", 1) if re.match(r"^\d{4}-\d{2}-\d{2} \d", cand) else cand
    try:
        return datetime.fromisoformat(cand)
    except (ValueError, OverflowError):
        return None
