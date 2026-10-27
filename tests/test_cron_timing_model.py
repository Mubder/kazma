"""One time model for every scheduling path (Scheduled audit SCH-E03, E05, E09, S06, S09).

* SCH-E03 -- a time without an offset means the operator's zone on EVERY
  path: chat (the commitment gate's rewrite), the Web page, edit_scheduled
  and X booking read one string as one instant. The gate read it as UTC and
  rewrote the booking to that instant.
* SCH-E05 -- a recurring request keeps its recurrence and its clock time
  through the gate, English and Arabic; it was denied, or rewritten to one
  fire a day later at the current time of day.
* SCH-E09 / S06 -- one typed validator: RFC 3339 with Z, future only, finite,
  one clock-change policy, a typed recurrence; errors name the zone and are
  validation errors (never 500 or 404).
* SCH-S09 -- every timing example the model or the operator is shown is
  accepted by the validator of the path it is shown on.
"""

from __future__ import annotations

import ast
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from kazma_core.cron.timing import (
    TimingError,
    next_occurrence,
    parse_recurrence,
    resolve_timing,
)

ROOT = Path(__file__).resolve().parents[1]
ZONES = ["UTC", "Asia/Kuwait", "America/New_York", "Europe/London"]


def _zone(name: str):
    try:
        return UTC if name == "UTC" else ZoneInfo(name)
    except Exception:  # noqa: BLE001
        pytest.skip("tz database unavailable")


@pytest.fixture
def ops_db(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "ops.db"))


def _set_zone(monkeypatch, name: str) -> None:
    import kazma_core.cron.scheduler as sched

    monkeypatch.setattr(sched, "resolve_cron_timezone_name", lambda: (name, "env"))


# ── SCH-E03: one string, one instant, every path ─────────────────────────


@pytest.mark.parametrize("zone_name", ZONES)
def test_one_string_is_one_instant_on_every_path(zone_name, monkeypatch, ops_db):
    """Chat (the gate's rewrite), the Web page and edit_scheduled (the
    scheduler's validator) and X booking agree, in every zone."""
    zone = _zone(zone_name)
    _set_zone(monkeypatch, zone_name)
    from kazma_core.cron.scheduler import resolve_cron_zone
    from kazma_core.safety.commitment import authorize_effect
    from kazma_core.x_api.booking import _parse_when

    naive = (datetime.now(UTC) + timedelta(days=3)).strftime("%Y-%m-%dT09:00:00")
    want = datetime.fromisoformat(naive).replace(tzinfo=zone)
    d = authorize_effect(
        "schedule_task", {"timing": naive, "prompt": "x"},
        user_text="remind me at 9 to call the dentist",
        request_at=datetime.now(UTC), memory_beliefs=[], thread_id="t-e03", turn_id="turn1",
    )
    assert d.decision == "allow"
    # The booking the gate's (rewritten) arguments reach: the scheduler's
    # zone and the one timing model.
    booked_zone, booked_name = resolve_cron_zone()
    via_chat = resolve_timing(d.rewritten_args["timing"] if d.rewritten_args else naive,
                              zone=booked_zone, zone_name=booked_name).first
    via_web = resolve_timing(naive, zone=zone, zone_name=zone_name).first
    via_x = datetime.fromtimestamp(_parse_when(naive), tz=UTC)
    assert via_chat == want and via_web == want and via_x == want
    if d.rewritten_args:
        # The rewrite names its offset: an aware instant, never re-zoned.
        assert datetime.fromisoformat(d.rewritten_args["timing"]).tzinfo is not None


def test_negative_control_the_old_gate_read_naive_as_utc(monkeypatch):
    """The old parse_absolute_timing (no zone) gives a different instant than
    the scheduler for the same string in Kuwait -- the bug the check above
    catches."""
    zone = _zone("Asia/Kuwait")
    from kazma_core.safety.commitment.relative_time import parse_absolute_timing

    naive = "2026-12-01T09:00:00"
    assert parse_absolute_timing(naive) != datetime(2026, 12, 1, 9, tzinfo=zone)  # old: UTC
    assert parse_absolute_timing(naive, zone=zone) == datetime(2026, 12, 1, 9, tzinfo=zone)


# ── SCH-E05: recurrence is kept, English and Arabic ──────────────────────

_RECURRING_CORPUS = [
    ("remind me every day at 9am to take my meds", "daily at 9am"),
    ("remind me every day at 9am starting tomorrow to take my meds", "daily at 9am"),
    ("ذكرني كل يوم الساعة 9 الصبح أشرب الدواء", "daily at 9am"),
    ("كل يوم الساعة 9:30 مساءً ذكرني أقفل الباب", "daily at 9:30pm"),
    ("remind me every weekday at 8:30 to stand up", "weekdays at 08:30"),
    ("ذكرني يومياً الساعة 7 الصبح", "daily at 7am"),
    ("every monday and wednesday at 21:00 remind me about the gym", "every mon,wed at 21:00"),
    ("remind me daily at 6pm until the end of the year", "daily at 6pm until 2026-12-31"),
]


@pytest.mark.parametrize(("user_text", "timing"), _RECURRING_CORPUS)
def test_recurring_requests_keep_their_recurrence(user_text, timing, ops_db, monkeypatch):
    _set_zone(monkeypatch, "Asia/Kuwait")
    zone = _zone("Asia/Kuwait")
    from kazma_core.cron.scheduler import resolve_cron_zone
    from kazma_core.safety.commitment import authorize_effect

    request_at = datetime(2026, 10, 13, 15, 17, tzinfo=UTC)
    d = authorize_effect("schedule_task", {"timing": timing, "prompt": "x"}, user_text=user_text,
                         request_at=request_at, memory_beliefs=[], thread_id="t-e05", turn_id="turn1")
    assert d.decision == "allow", d.reason
    assert d.rewritten_args is None  # kept as written: never turned into one fire
    # Its first occurrence is the scheduler's.
    rec = parse_recurrence(timing)
    first = next_occurrence(rec, zone=zone, after=request_at)
    booked_zone, booked_name = resolve_cron_zone()
    assert first == resolve_timing(timing, zone=booked_zone, zone_name=booked_name, now=request_at,
                                   allow_past=True).first
    assert first.astimezone(zone).hour == rec.hour


@pytest.mark.parametrize("user_text", [t for t, _ in _RECURRING_CORPUS[:3]])
def test_a_recurring_request_booked_as_one_fire_is_refused(user_text, ops_db, monkeypatch):
    """The person asked for something that repeats; a one-off booking is
    refused with the forms to use -- never allowed and rewritten."""
    _set_zone(monkeypatch, "UTC")
    from kazma_core.safety.commitment import authorize_effect

    d = authorize_effect("schedule_task", {"timing": "1d", "prompt": "x"}, user_text=user_text,
                         request_at=datetime(2026, 10, 13, 15, 17, tzinfo=UTC), memory_beliefs=[],
                         thread_id="t-e05c", turn_id="turn1")
    assert d.decision == "deny" and "daily at 9am" in d.reason


def test_a_one_off_with_a_clock_time_fires_at_that_time(ops_db, monkeypatch):
    """'tomorrow at 9am' fires at 09:00 tomorrow in the operator's zone, not
    24 hours after the request."""
    _set_zone(monkeypatch, "Asia/Kuwait")
    zone = _zone("Asia/Kuwait")
    from kazma_core.safety.commitment import authorize_effect

    request_at = datetime(2026, 10, 13, 15, 17, tzinfo=UTC)
    d = authorize_effect("schedule_task", {"timing": "", "prompt": "x"},
                         user_text="remind me tomorrow at 9am to call the bank",
                         request_at=request_at, memory_beliefs=[], thread_id="t-clock", turn_id="turn1")
    assert d.decision == "allow", d.reason
    fire = datetime.fromisoformat(d.rewritten_args["timing"])
    assert fire.astimezone(zone).hour == 9 and fire.astimezone(zone).date() == (
        request_at.astimezone(zone) + timedelta(days=1)).date()


def test_negative_control_the_old_relative_rewrite():
    """Before the clock-time rule the same phrase resolved to request_at + 1
    day (zone None keeps the old behaviour of resolve_remind)."""
    from kazma_core.safety.commitment.relative_time import resolve_remind

    request_at = datetime(2026, 10, 13, 15, 17, tzinfo=UTC)
    res = resolve_remind("remind me tomorrow at 9am", request_at=request_at, memory_beliefs=[])
    assert res.fire_at == request_at + timedelta(days=1)


# ── SCH-E09 / S06: one typed validator, both paths ──────────────────────

#: (timing, accepted by cron?, accepted by X?)
_TABLE = [
    ("30m", True, True),
    ("2h", True, True),
    ("3d", True, True),
    ("2027-12-01T09:00:00Z", True, True),
    ("2027-12-01T09:00:00+03:00", True, True),
    ("2027-12-01T09:00", True, True),
    ("2027-12-01 09:00", True, True),
    ("daily at 9:30am", True, False),
    ("RRULE:FREQ=WEEKLY;BYDAY=MO,FR;BYHOUR=9;BYMINUTE=15;COUNT=3", True, False),
    ("0m", False, False),
    ("2020-01-01T09:00:00", False, False),
    ("99999999999h", False, False),
    ("next tuesday", False, False),
    ("daily at 9", False, False),
    ("x" * 101, False, False),
]


@pytest.mark.parametrize(("timing", "cron_ok", "x_ok"), _TABLE)
def test_one_validator_for_cron_and_x(timing, cron_ok, x_ok, monkeypatch):
    _set_zone(monkeypatch, "UTC")
    from kazma_core.x_api.booking import _parse_when

    try:
        resolve_timing(timing, zone=UTC, zone_name="UTC")
        cron = True
    except TimingError:
        cron = False
    try:
        _parse_when(timing)
        x = True
    except ValueError:
        x = False
    assert (cron, x) == (cron_ok, x_ok)


def test_errors_name_the_zone():
    with pytest.raises(TimingError, match="Asia/Kuwait"):
        resolve_timing("2020-01-01T09:00", zone=_zone("Asia/Kuwait"), zone_name="Asia/Kuwait")


@pytest.mark.parametrize(("civil", "problem"), [("2027-03-14T02:30:00", "does not exist"),
                                                 ("2026-11-01T01:30:00", "occurs twice")])
def test_one_clock_change_policy(civil, problem, monkeypatch):
    """A one-off local time the clock skips or repeats is refused on both
    paths (cron accepted it silently)."""
    _set_zone(monkeypatch, "America/New_York")
    zone = _zone("America/New_York")
    from kazma_core.x_api.booking import _parse_when

    with pytest.raises(TimingError, match=problem):
        resolve_timing(civil, zone=zone, zone_name="America/New_York",
                       now=datetime(2026, 10, 1, tzinfo=UTC))
    with pytest.raises(ValueError, match=problem):
        _parse_when(civil)


@pytest.mark.parametrize(("zone_name", "start"), [("America/New_York", datetime(2026, 2, 20, tzinfo=UTC)),
                                                  ("Europe/London", datetime(2026, 3, 10, tzinfo=UTC))])
@pytest.mark.parametrize("clock", ["daily at 9am", "daily at 01:30", "daily at 02:30"])
def test_a_daily_series_across_two_clock_changes(zone_name, start, clock):
    """A fake clock walks a daily series across spring-forward and fall-back:
    each local day exactly once, at the intended wall time (a time the clock
    skips fires at the same wall time shifted by the jump)."""
    zone = _zone(zone_name)
    rec = parse_recurrence(clock)
    seen: list[datetime] = []
    after = start
    while after < start + timedelta(days=270):  # spring and autumn changes
        nxt = next_occurrence(rec, zone=zone, after=after)
        assert nxt is not None and nxt > after
        seen.append(nxt)
        after = nxt
    days = [d.astimezone(zone).date() for d in seen]
    assert len(days) == len(set(days))  # never twice a day (the fold)
    assert all((b - a).days == 1 for a, b in zip(days, days[1:], strict=False))  # never a day skipped
    for fire in seen:
        local = fire.astimezone(zone)
        assert (local.hour, local.minute) in {(rec.hour, rec.minute), ((rec.hour + 1) % 24, rec.minute)}


def test_count_and_until_end_a_series():
    rec = parse_recurrence("daily at 9am count 3")
    start = datetime(2026, 10, 14, 9, tzinfo=UTC)
    fires = [start]
    while True:
        nxt = next_occurrence(rec, zone=UTC, after=fires[-1], series_start=start)
        if nxt is None:
            break
        fires.append(nxt)
    assert len(fires) == 3
    rec = parse_recurrence("daily at 9am until 2026-10-16")
    assert next_occurrence(rec, zone=UTC, after=datetime(2026, 10, 16, 10, tzinfo=UTC)) is None


def test_negative_control_the_old_parser():
    """What the old parse_timing did with these: Z refused (it lowercased the
    input first), minutes refused, 0m and the past accepted."""
    def old(timing: str):
        timing = timing.strip().lower()
        if re.match(r"^(\d+)(m|h)$", timing):
            return "relative"
        if re.match(r"^daily at (\d{1,2})(am|pm)$", timing):
            return "daily"
        try:
            datetime.fromisoformat(timing)
            return "iso"
        except ValueError:
            return None

    assert old("2026-12-01T09:00:00Z") is None and old("daily at 9:30am") is None
    assert old("0m") == "relative" and old("2020-01-01T09:00:00") == "iso"


# ── SCH-S09: every example shown is accepted where it is shown ───────────

def _examples(text: str) -> list[str]:
    return [m for m in re.findall(r"'([^'\n]{1,40})'", text) if m != "Z"]


def test_every_timing_example_is_accepted_by_its_validator(monkeypatch):
    _set_zone(monkeypatch, "UTC")
    from kazma_core.x_api.booking import _parse_when
    from kazma_skills.native.task_scheduler_cron import tools as cron_tools
    from kazma_skills.native.x_publisher import tools as x_tools

    from kazma_ui.i18n.catalog.scheduled import TRANSLATIONS

    def cron_ok(example: str) -> bool:
        try:
            resolve_timing(example, zone=UTC, zone_name="UTC", now=datetime(2026, 1, 1, tzinfo=UTC))
            return True
        except TimingError:
            return False

    cron_doc = cron_tools.schedule_task.__doc__.split("prompt:")[0]
    for ex in _examples(cron_doc):
        if ex.startswith(("until", "count")):
            continue
        assert cron_ok(ex), f"schedule_task advertises {ex!r}, which the scheduler refuses"
    x_doc = x_tools.x_schedule_post.__doc__.split("reply_to_id")[0]
    assert "daily at" not in x_doc
    for ex in _examples(x_doc):
        if ex == "Z":
            continue
        try:
            _parse_when(ex.replace("2026-12-01", "2027-12-01"))
        except ValueError as exc:
            pytest.fail(f"x_schedule_post advertises {ex!r}, which booking refuses: {exc}")
    for key in ("scheduled.x_timing_hint", "scheduled.x_timing_ph"):
        assert "daily" not in TRANSLATIONS[key]["en"] and "weekdays" not in TRANSLATIONS[key]["en"]
    for ex in ["5m", "1h", "daily at 9am", "weekdays at 08:30", "every mon,wed at 21:00", "2026-12-01T09:00"]:
        assert ex in TRANSLATIONS["scheduled.timing_hint"]["en"] + TRANSLATIONS["scheduled.timing_ph"]["en"]
        assert cron_ok(ex)
    manifest = (ROOT / "kazma-skills/kazma_skills/native/x_publisher/skill_manifest.yaml").read_text(encoding="utf-8")
    assert "daily at 9am" not in manifest


def test_the_x_dialog_uses_the_x_hint():
    html = (ROOT / "kazma-ui/kazma_ui/templates/scheduled.html").read_text(encoding="utf-8")
    assert "{{ t('scheduled.timing_hint') }} {{ t('scheduled.x_fire_note') }}" not in html
    assert "t('scheduled.x_timing_hint')" in html


def test_negative_control_the_example_gate_sees_the_old_docstring(monkeypatch):
    _set_zone(monkeypatch, "UTC")
    from kazma_core.x_api.booking import _parse_when

    old = "when: When to post: '5m', '1h', 'daily at 9am', or an ISO timestamp."
    refused = []
    for ex in _examples(old):
        try:
            _parse_when(ex)
        except ValueError:
            refused.append(ex)
    assert refused == ["daily at 9am"]


def test_the_tool_signature_names_the_kinds():
    tree = ast.parse((ROOT / "kazma-skills/kazma_skills/native/task_scheduler_cron/tools.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "schedule_task")
    assert [a.arg for a in fn.args.args] == ["timing", "prompt", "kind", "tools", "catchup_minutes"]
