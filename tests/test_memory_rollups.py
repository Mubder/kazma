"""Monthly and per-project roll-ups above the weekly summaries (the memory
framework audit's F13, part G).

A roll-up is a row of ``memory_summaries`` with its ``level``; it names the
weekly summaries it was written from (``memory_summary_parts``) and the turns
they stand for (``memory_summary_sources``), so a forget follows it:

* the sweep plans a month once it has ended and settled, and a project over
  at least three weeks; the queue's task writes each with one model call;
* the audit's control: a forget empties a roll-up built from it -- a turn
  forgotten through memory's own forget, and a weekly summary forgotten from
  the page; negative control: a roll-up that does not name its sources keeps
  the forgotten words;
* the write is a compare-and-set: a weekly summary forgotten during the
  model call means nothing is stored; control: the store without the
  re-read stores it;
* a roll-up the user forgets is a tombstone its month never writes again;
* recall reads it, under its own label.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import sqlite3
import time
from types import SimpleNamespace

import numpy as np
import pytest


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory import dual_write
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    # Months and weeks are read in the operator's zone (the step 5 review, L2);
    # this file's days are written in the machine's own, so that is the zone.
    monkeypatch.setattr("kazma_core.memory.recall._memory_zone", lambda: _dt.datetime.now().astimezone().tzinfo)
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: "")
    monkeypatch.setattr("kazma_core.memory.task_queue.enqueue_task", lambda *a, **k: "t")
    monkeypatch.setattr("kazma_core.memory.forget.chat_keys", lambda key: [key] if key else [])
    db = sqlite3.connect(str(tmp_path / "memory_state.db"), check_same_thread=False)
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    dual_write._reset_mirror()
    yield SimpleNamespace(db=db, mp=monkeypatch)
    dual_write._reset_mirror()
    ops.close()
    db.close()


@pytest.fixture(autouse=True)
def _said_afresh():
    """Each test says its held roll-ups afresh (the sixth review's L1:
    ``_SAID_HELD`` outlived the test that filled it)."""
    from kazma_core.memory import topic_rollups

    topic_rollups._SAID_HELD.clear()
    yield
    topic_rollups._SAID_HELD.clear()


#: A month long enough past (its end and the day's grace behind it).
MONTH_START = _dt.datetime(2026, 6, 1)


def _monday(week: int) -> float:
    """The epoch of the Monday of *week* (0 = the first Monday of June 2026)."""
    first = MONTH_START + _dt.timedelta(days=(7 - MONTH_START.weekday()) % 7)
    return (first + _dt.timedelta(weeks=week)).timestamp()


def _vector(direction: int, jitter: int) -> bytes:
    v = np.zeros(16, dtype=np.float32)
    v[direction] = 1.0
    v[(direction + 1 + jitter) % 16] = 0.15
    return (v / np.linalg.norm(v)).astype(np.float32).tobytes()


def _weekly(mem, sid: str, week: int, *, title: str, text: str, direction: int = 0, turns: int = 2) -> list[str]:
    """A weekly summary of *turns* turns of its own, in week *week*."""
    start = _monday(week)
    eids = []
    for i in range(turns):
        eid = f"{sid}-t{i}"
        mem.db.execute(
            "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, tier, "
            "created_at, metadata_json) VALUES (?, 'default', ?, ?, ?, ?, 'episodic', ?, '{}')",
            (eid, f"chat-{sid}", i + 1, f"{title} question {i}", f"{title} answer {i}", start + 3600 * (i + 1)),
        )
        eids.append(eid)
    mem.db.execute(
        "INSERT INTO memory_summaries (id, tenant_id, period_key, period_start, period_end, title, summary_text, "
        "status, turn_count, chat_count, created_at, updated_at, embedding, embedding_model_version, level) "
        "VALUES (?, 'default', ?, ?, ?, ?, ?, 'active', ?, 1, ?, ?, ?, '', 'week')",
        (sid, f"2026-W{23 + week:02d}", start, start + 7 * 86400, title, text, turns, start + 8 * 86400,
         start + 8 * 86400, _vector(direction, week % 3)),
    )
    mem.db.executemany("INSERT INTO memory_summary_sources (summary_id, episode_id) VALUES (?, ?)",
                       [(sid, e) for e in eids])
    mem.db.commit()
    return eids


TRAINING = [
    ("tr0", 0, "Half marathon training: week one", "Sami ran 5 km three times; his calf felt tight."),
    ("tr1", 1, "Half marathon training: 10 km", "A first 10 km long run; Kazma moved the rest day."),
    ("tr2", 2, "Half marathon training: intervals", "Intervals on Tuesday; the calf is fine now."),
    ("tr3", 3, "Half marathon training: 14 km", "The longest run so far, 14 km at an easy pace."),
]


def _training(mem) -> dict[str, list[str]]:
    return {sid: _weekly(mem, sid, week, title=title, text=text, direction=0) for sid, week, title, text in TRAINING}


class _Chat:
    def __init__(self, reply: dict | None = None, on_call=None) -> None:
        self.calls: list[list[dict]] = []
        self.reply = reply or {"title": "Half marathon training, June", "skip": False,
                               "summary": "Over four weeks Sami built up from 5 km to a 14 km long run; "
                                          "his calf was tight in week one and fine by week three."}
        self.on_call = on_call

    async def __call__(self, messages):
        self.calls.append(messages)
        if self.on_call is not None:
            self.on_call()
        return SimpleNamespace(content=json.dumps(self.reply))


def _row(mem, rid: str) -> sqlite3.Row:
    return mem.db.execute("SELECT * FROM memory_summaries WHERE id = ?", (rid,)).fetchone()


def _rollups(mem, level: str) -> list[sqlite3.Row]:
    return mem.db.execute("SELECT * FROM memory_summaries WHERE level = ? ORDER BY id", (level,)).fetchall()


def _plan(mem, now: float | None = None) -> dict:
    from kazma_core.memory.topic_rollups import plan_rollups

    return plan_rollups(mem.db, now=now or time.time())


def _write(mem, rid: str, chat: _Chat) -> bool:
    from kazma_core.memory.topic_summaries import rebuild_summary

    return asyncio.run(rebuild_summary(rid, chat=chat, model="scripted"))


def _parts(mem, rid: str) -> list[str]:
    return [r[0] for r in mem.db.execute(
        "SELECT part_id FROM memory_summary_parts WHERE rollup_id = ? ORDER BY part_id", (rid,))]


def _sources(mem, rid: str) -> set[str]:
    return {r[0] for r in mem.db.execute("SELECT episode_id FROM memory_summary_sources WHERE summary_id = ?", (rid,))}


# ── Planning and writing ─────────────────────────────────────────────────


def test_a_month_and_a_project_are_planned_with_their_sources(mem):
    turns = _training(mem)
    _weekly(mem, "other", 1, title="Laptop battery", text="A ThinkPad battery fix.", direction=7)
    out = _plan(mem)
    assert out == {"months": 1, "projects": 1, "held_by_forget": 0}
    [month] = _rollups(mem, "month")
    [project] = _rollups(mem, "project")
    assert month["status"] == project["status"] == "rebuild" and month["period_key"] == "2026-06"
    assert _parts(mem, month["id"]) == ["other", "tr0", "tr1", "tr2", "tr3"]
    assert _parts(mem, project["id"]) == ["tr0", "tr1", "tr2", "tr3"]  # the battery week is another topic
    assert _sources(mem, project["id"]) == {e for eids in turns.values() for e in eids}
    assert _plan(mem) == {"months": 0, "projects": 0, "held_by_forget": 0}  # nothing changed: nothing planned again


def test_not_before_the_month_has_ended_and_settled(mem):
    _training(mem)
    june_end = _dt.datetime(2026, 7, 1).timestamp()
    assert _plan(mem, now=june_end + 3600)["months"] == 0  # within the day's grace
    mem.db.execute("UPDATE memory_summaries SET status = 'rebuild' WHERE id = 'tr2'")
    mem.db.commit()
    assert _plan(mem)["months"] == 0  # a week of it waits to be written again


def test_two_weeks_are_no_project(mem):
    _weekly(mem, "a", 0, title="Tagus retrofit", text="Week one of the retrofit.", direction=3)
    _weekly(mem, "b", 1, title="Tagus retrofit", text="Week two of the retrofit.", direction=3)
    assert _plan(mem)["projects"] == 0


def test_the_queue_writes_a_roll_up_with_one_model_call(mem):
    _training(mem)
    _plan(mem)
    [project] = _rollups(mem, "project")
    chat = _Chat()
    assert _write(mem, project["id"], chat)
    row = _row(mem, project["id"])
    assert row["status"] == "active" and row["title"] == "Half marathon training, June"
    assert len(chat.calls) == 1
    body = chat.calls[0][1]["content"]
    assert '<kazma:data source="weekly_summaries"' in body and "14 km" in body
    assert row["turn_count"] == 8


# ── The audit's control: a forget empties a roll-up built from it ─────────


def _written_project(mem) -> str:
    _training(mem)
    _plan(mem)
    [project] = _rollups(mem, "project")
    assert _write(mem, project["id"], _Chat())
    assert _row(mem, project["id"])["status"] == "active"
    return project["id"]


def test_forgetting_a_turn_empties_the_roll_up_built_from_it(mem):
    from kazma_core.memory import forget

    rid = _written_project(mem)
    assert forget.forget_episode("tr0-t0", tenant_id="default")["ok"]
    row = _row(mem, rid)
    assert row["status"] == "rebuild" and row["summary_text"] == "" and row["title"] == ""
    assert _row(mem, "tr0")["status"] == "rebuild"


def test_forgetting_a_weekly_summary_empties_the_roll_up_built_from_it(mem):
    from kazma_core.memory.topic_summaries import forget_summary

    rid = _written_project(mem)
    out = forget_summary("tr1", tenant_id="default")
    [month] = _rollups(mem, "month")
    assert out["ok"] and sorted(out["rollups_emptied"]) == sorted([rid, month["id"]])  # both stand on it
    assert _row(mem, rid)["status"] == "rebuild" and _row(mem, rid)["summary_text"] == ""


def test_a_roll_up_is_written_again_without_what_was_forgotten(mem):
    from kazma_core.memory.topic_summaries import forget_summary

    rid = _written_project(mem)
    forget_summary("tr1", tenant_id="default")
    chat = _Chat()
    assert _write(mem, rid, chat)
    assert _row(mem, rid)["status"] == "active"
    assert _parts(mem, rid) == ["tr0", "tr2", "tr3"]
    assert "10 km" not in chat.calls[0][1]["content"]  # tr1's words were never shown again
    assert not (_sources(mem, rid) & {"tr1-t0", "tr1-t1"})


def test_control_a_roll_up_that_names_no_source_keeps_the_forgotten_words(mem, monkeypatch):
    """Negative control: written without its parts and turns (a roll-up as a
    plain text), a forget never reaches it."""
    from kazma_core.memory import forget, topic_rollups
    from kazma_core.memory.topic_summaries import forget_summary

    _training(mem)
    monkeypatch.setattr(topic_rollups, "_set_parts", lambda conn, rid, parts: None)
    _plan(mem)
    [project] = _rollups(mem, "project")
    mem.db.execute("UPDATE memory_summaries SET status = 'active', summary_text = 'calf tight in week one' "
                   "WHERE id = ?", (project["id"],))
    mem.db.commit()
    forget_summary("tr0", tenant_id="default")
    forget.forget_episode("tr1-t0", tenant_id="default")
    assert _row(mem, project["id"])["summary_text"] == "calf tight in week one"


# ── Compare-and-set ──────────────────────────────────────────────────────


def test_a_part_forgotten_during_the_model_call_is_never_stored(mem):
    from kazma_core.memory.topic_summaries import forget_summary

    _training(mem)
    _plan(mem)
    [project] = _rollups(mem, "project")
    chat = _Chat(on_call=lambda: forget_summary("tr3", tenant_id="default"))
    assert _write(mem, project["id"], chat)
    row = _row(mem, project["id"])
    assert row["status"] == "rebuild" and row["summary_text"] == ""


def test_control_the_store_without_the_reread_stores_a_forgotten_part(mem):
    """Negative control: written as an UPDATE that looks at nothing, the
    text from the part forgotten during the call is stored."""
    from kazma_core.memory.topic_summaries import forget_summary

    _training(mem)
    _plan(mem)
    [project] = _rollups(mem, "project")
    forget_summary("tr3", tenant_id="default")
    mem.db.execute("UPDATE memory_summaries SET status = 'active', summary_text = ? WHERE id = ?",
                   ("built up to a 14 km long run", project["id"]))
    mem.db.commit()
    assert "14 km" in _row(mem, project["id"])["summary_text"]


def test_a_forgotten_roll_up_is_never_written_again(mem):
    from kazma_core.memory.topic_summaries import forget_summary

    _training(mem)
    _plan(mem)
    [month] = _rollups(mem, "month")
    assert forget_summary(month["id"], tenant_id="default")["ok"]
    _weekly(mem, "late", 2, title="Half marathon training: extra", text="An extra run.", direction=0)
    _plan(mem)
    row = _row(mem, month["id"])
    assert row["status"] == "forgotten" and row["summary_text"] == ""


def test_below_the_minimum_a_roll_up_is_retired(mem):
    from kazma_core.memory.topic_summaries import forget_summary

    _training(mem)
    _plan(mem)
    [month] = _rollups(mem, "month")
    for sid in ("tr0", "tr1", "tr2"):
        forget_summary(sid, tenant_id="default")
    assert _write(mem, month["id"], _Chat())
    assert _row(mem, month["id"])["status"] == "retired"


# ── Recall reads it ──────────────────────────────────────────────────────


def test_recall_shows_a_roll_up_under_its_own_label(mem, monkeypatch):
    from kazma_core.memory import recall

    rid = _written_project(mem)
    block = recall.format_recall_block(recall.RecallResult(
        beliefs=[], episodes=[],
        summaries=[recall.RecallHit(id=rid, content="Half marathon training, June", score=0.5, kind="summary",
                                    source="summary", metadata={"level": "project",
                                                                "display": "Project, Jun 2026 to Jun 2026 -- x"})],
    ))
    assert "## Summaries" in block and "## Weekly Summaries" not in block
    rows = mem.db.execute(
        "SELECT id, period_start, period_end, level, title, summary_text FROM memory_summaries WHERE id = ?",
        (rid,)).fetchall()
    assert recall._summary_display(rows[0]).startswith("Project, Jun 2026 to Jun 2026 -- Half marathon")
    weekly = mem.db.execute(
        "SELECT id, period_start, period_end, level, title, summary_text FROM memory_summaries WHERE id = 'tr0'"
    ).fetchone()
    assert recall._summary_display(weekly).startswith("Week of ")


# ── Old summaries keep their labels and months when the zone differs ─────
# (the step 5 delta review's B: a week written in the server's local time,
# read in another operator zone, moved a day and a month)

def _kuwait_week_row() -> dict:
    """Week 2026-W23 as a Kuwait server wrote it: from Monday 1 June 00:00
    Kuwait time, which is Sunday 31 May 21:00 UTC."""
    from zoneinfo import ZoneInfo

    kuwait = ZoneInfo("Asia/Kuwait")
    start = _dt.datetime(2026, 6, 1, tzinfo=kuwait).timestamp()
    return {"id": "sum_w23", "period_key": "2026-W23", "period_start": start, "period_end": start + 7 * 86400}


@pytest.mark.parametrize("zone", ["UTC", "America/New_York", "Asia/Kuwait", "Pacific/Auckland"])
def test_a_week_written_in_another_zone_keeps_its_label_and_month(monkeypatch, zone):
    from zoneinfo import ZoneInfo

    from kazma_core.memory import topic_rollups

    monkeypatch.setattr("kazma_core.memory.recall._memory_zone", lambda: ZoneInfo(zone))
    row = _kuwait_week_row()
    assert topic_rollups.rollup_label("week", row["period_start"], row["period_end"], row["period_key"]) == \
        "Week of Jun 01, 2026"
    assert topic_rollups._week_month(row, ZoneInfo(zone))[0] == "2026-06"
    # without its key, the stretch read at midday is the same week
    assert topic_rollups.rollup_label("week", row["period_start"], row["period_end"]) == "Week of Jun 01, 2026"
    month_start = _dt.datetime(2026, 6, 1, tzinfo=ZoneInfo("Asia/Kuwait")).timestamp()
    assert topic_rollups.rollup_label("month", month_start, month_start + 30 * 86400, "2026-06") == "June 2026"
    assert topic_rollups.rollup_label("month", month_start, month_start + 30 * 86400) == "June 2026"


def test_control_the_start_read_as_it_is_moves_the_week_in_utc():
    """The negative control: the start itself, read in UTC, is 31 May, in
    May; the reading at its key or at midday is what keeps it."""
    row = _kuwait_week_row()
    raw = _dt.datetime.fromtimestamp(row["period_start"], _dt.UTC)
    assert (raw.month, raw.day) == (5, 31)


# ── Every place that dates a stored stretch reads its key or its midday ──
# (the step 5 final review's M: a rebuilt week's prompt and the month's
# "settled" check still read a week's start in the operator's zone)

_REPO = __import__("pathlib").Path(__file__).resolve().parents[1]
#: The modules that turn a summary's stretch into a day, a week or a month.
_SUMMARY_MODULES = ("kazma-core/kazma_core/memory/topic_summaries.py",
                    "kazma-core/kazma_core/memory/topic_rollups.py")
#: Calls that turn a time into a day, a week or a month.
_DATING = frozenset({"fromtimestamp", "_month_of", "_week_of", "day"})


def _undated_by_key(source: str, path: str, *, bare: bool) -> list[str]:
    """Each call in *source* that dates a stored stretch (its first argument
    names ``period_start``/``period_end``, or, with *bare*, a ``start`` or
    ``end``) without the midday reading, and each SQL that files a week by
    comparing its ``period_start`` with bounds."""
    import ast
    import re

    names = r"\b(?:period_start|period_end" + (r"|start|end" if bare else "") + r")\b"
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and node.args:
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            arg = ast.get_source_segment(source, node.args[0]) or ""
            if name in _DATING and re.search(names, arg) and "_MIDDAY_S" not in arg:
                found.append(f"{path}:{node.lineno} {name}({arg})")
    found += [f"{path}: SQL {m.group(0)}" for m in re.finditer(r"period_start\s*[<>]=?\s*\?", source)]
    return found


def test_every_stored_stretch_is_dated_by_its_key_or_at_midday():
    """From source: the summary modules date a week or a month by its key or
    its stretch read at midday, nowhere by its start in the reader's zone;
    and nothing in memory/ dates a stored ``period_start`` otherwise."""
    found = []
    for path in sorted((_REPO / "kazma-core/kazma_core/memory").glob("*.py")):
        rel = path.relative_to(_REPO).as_posix()
        found += _undated_by_key(path.read_text(encoding="utf-8"), rel, bare=rel in _SUMMARY_MODULES)
    assert found == []


def test_control_the_old_readings_are_found():
    """The negative control: the rebuilt week's Monday and the month's settled
    check as 44524a234 wrote them are both caught."""
    old = ('monday = _dt.datetime.fromtimestamp(start, zone).strftime("%Y-%m-%d")\n'
           'q = conn.execute("SELECT 1 FROM memory_summaries WHERE status = \'rebuild\' '
           'AND period_start >= ? AND period_start < ? LIMIT 1", (t, start, end))\n')
    found = _undated_by_key(old, "old.py", bare=True)
    assert len(found) == 3, found


def test_a_rebuilt_old_week_is_introduced_with_its_own_monday(monkeypatch):
    from zoneinfo import ZoneInfo

    from kazma_core.memory import topic_summaries

    monkeypatch.setattr("kazma_core.memory.recall._memory_zone", lambda: ZoneInfo("America/New_York"))
    row = _kuwait_week_row()
    turn = topic_summaries._Turn(id="t1", chat="c1", at=row["period_start"] + 3600,
                                 question="we planned the garden", answer="noted")
    messages = topic_summaries._messages([turn], row["period_key"], row["period_start"])
    assert "Week 2026-W23 (from Monday 2026-06-01)" in messages[1]["content"]


@pytest.mark.parametrize("zone", ["UTC", "America/New_York", "Asia/Kuwait"])
def test_a_waiting_old_week_holds_back_its_own_month(zone):
    """A Kuwait-written week of 1 June waiting to be written again holds June
    back in any zone (in UTC it fell in May, so June was written twice)."""
    from zoneinfo import ZoneInfo

    from kazma_core.memory import topic_rollups
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    row = _kuwait_week_row()
    conn.execute(
        "INSERT INTO memory_summaries (id, tenant_id, period_key, period_start, period_end, level, title, "
        "summary_text, status, created_at, updated_at, metadata_json) "
        "VALUES (?, 'default', ?, ?, ?, 'week', '', '', 'rebuild', 0, 0, '{}')",
        (row["id"], row["period_key"], row["period_start"], row["period_end"]))
    assert not topic_rollups._month_settled(conn, "default", "2026-06", ZoneInfo(zone))
    assert topic_rollups._month_settled(conn, "default", "2026-05", ZoneInfo(zone))
    conn.close()


@pytest.mark.parametrize("waiting", [True, False])
def test_a_month_waits_for_its_old_kuwait_week_through_the_sweep(monkeypatch, waiting):
    """The follow-up review's L-b, through ``plan_rollups``: June's weeks as a
    Kuwait server wrote them, read in UTC. While W23 (1 June) waits to be
    written again, June is not planned (on 44524a234 it fell in May and June
    was planned, then written again); with W23 written, June is planned."""
    from zoneinfo import ZoneInfo

    from kazma_core.memory import topic_rollups
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    monkeypatch.setattr("kazma_core.memory.recall._memory_zone", lambda: _dt.UTC)
    kuwait = ZoneInfo("Asia/Kuwait")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    for key, day, status in (("2026-W23", 1, "rebuild" if waiting else "active"), ("2026-W24", 8, "active"),
                             ("2026-W25", 15, "active")):
        start = _dt.datetime(2026, 6, day, tzinfo=kuwait).timestamp()
        conn.execute(
            "INSERT INTO memory_summaries (id, tenant_id, period_key, period_start, period_end, level, title, "
            "summary_text, status, created_at, updated_at, metadata_json) "
            "VALUES (?, 'default', ?, ?, ?, 'week', 't', 's', ?, 0, 0, '{}')",
            (f"sum_{key}", key, start, start + 7 * 86400, status))
    planned = topic_rollups.plan_rollups(conn, now=_dt.datetime(2026, 8, 1, tzinfo=_dt.UTC).timestamp())
    months = [r["period_key"] for r in conn.execute("SELECT period_key FROM memory_summaries WHERE level = 'month'")]
    conn.close()
    assert (planned["months"], months) == ((0, []) if waiting else (1, ["2026-06"]))


# ── "What did we talk about in June?" answers June in every zone ─────────
# (the follow-up review's L-a: recall compared a Kuwait-written month's
# stored stretch with the question's period, and showed July in UTC and New
# York, May in Kiritimati)

_JUNE_QUESTIONS = ["What did we talk about in June?", "شنو تكلمنا عنه في يونيو؟"]
_ASKED_ON = _dt.datetime(2026, 8, 15, 12, tzinfo=_dt.UTC).timestamp()


def _kuwait_months() -> list[dict]:
    from zoneinfo import ZoneInfo

    kuwait = ZoneInfo("Asia/Kuwait")
    rows = []
    for month in (5, 6, 7):
        start = _dt.datetime(2026, month, 1, tzinfo=kuwait).timestamp()
        end = _dt.datetime(2026, month + 1, 1, tzinfo=kuwait).timestamp()
        rows.append({"id": f"sum_m{month}", "level": "month", "period_key": f"2026-{month:02d}",
                     "period_start": start, "period_end": end, "title": f"month {month}", "summary_text": "s"})
    return rows


def _june_answer(monkeypatch, zone: str, question: str, spans=None) -> list[str]:
    from zoneinfo import ZoneInfo

    from kazma_core.memory import recall
    from kazma_core.memory.time_words import read_time

    monkeypatch.setattr("kazma_core.memory.recall._memory_zone", lambda: ZoneInfo(zone))
    rows = _kuwait_months()
    ask = read_time(question, now=_ASKED_ON, zone=ZoneInfo(zone))
    level = {r["id"]: "month" for r in rows}
    hits = recall._months_said_in(question, ask, rows, level, spans or recall._spans(rows))
    return [h.id for h in hits]


@pytest.mark.parametrize("question", _JUNE_QUESTIONS)
@pytest.mark.parametrize("zone", ["Asia/Kuwait", "UTC", "America/New_York", "Pacific/Kiritimati"])
def test_a_question_about_june_is_answered_by_june_alone(monkeypatch, zone, question):
    assert _june_answer(monkeypatch, zone, question) == ["sum_m6"]


@pytest.mark.parametrize("question", _JUNE_QUESTIONS)
def test_control_the_stored_stretch_brings_a_neighbouring_month(monkeypatch, question):
    """The negative control: the stretches as stored (44524a234's reading)
    put July beside June in UTC and May beside it in Kiritimati."""
    stored = {r["id"]: (r["period_start"], r["period_end"]) for r in _kuwait_months()}
    assert "sum_m7" in _june_answer(monkeypatch, "UTC", question, stored)
    assert "sum_m5" in _june_answer(monkeypatch, "Pacific/Kiritimati", question, stored)


# ── A roll-up the model skips is not asked again (the follow-up's O1) ────

_SKIP = {"title": "", "summary": "", "skip": True}


def _passes(mem, chat: _Chat, passes: int) -> int:
    """The sweep's part, *passes* times: plan, then write every roll-up that
    waits, as the queue's task does. The model calls it made."""
    before = len(chat.calls)
    for _ in range(passes):
        _plan(mem)
        for row in mem.db.execute(
                "SELECT id FROM memory_summaries WHERE level != 'week' AND status = 'rebuild'").fetchall():
            _write(mem, row["id"], chat)
    return len(chat.calls) - before


def test_a_skipped_project_is_not_planned_again_until_its_parts_change(mem):
    _training(mem)
    chat = _Chat(reply=_SKIP)
    assert _passes(mem, chat, 1) == 2  # the month and the project, each skipped once
    assert {r["status"] for r in _rollups(mem, "project")} == {"retired"}
    assert _passes(mem, chat, 3) == 0  # settled: no model call again
    _weekly(mem, "tr4", 4, title="Half marathon training: 16 km", text="A 16 km long run.", direction=0)
    assert _passes(mem, chat, 1) >= 1  # a part added: asked again


def test_control_with_skipped_projects_left_out_the_sweep_never_settles(mem, monkeypatch):
    """The negative control: the project lookup as it was (retired projects
    left out) plans the skipped project again and asks the model again."""
    from kazma_core.memory import topic_rollups

    real = topic_rollups._known_projects
    monkeypatch.setattr(topic_rollups, "_known_projects", lambda conn, tenant: {
        rid: known for rid, known in real(conn, tenant).items() if known[0] != "retired"})
    _training(mem)
    chat = _Chat(reply=_SKIP)
    _passes(mem, chat, 1)
    assert _passes(mem, chat, 4) >= 2


def _months_store() -> sqlite3.Connection:
    """May to July 2026 as month roll-ups a Kuwait server wrote, each about
    the training."""
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    for row in _kuwait_months():
        conn.execute(
            "INSERT INTO memory_summaries (id, tenant_id, period_key, period_start, period_end, level, title, "
            "summary_text, status, created_at, updated_at, metadata_json) "
            "VALUES (?, 'default', ?, ?, ?, 'month', ?, ?, 'active', 0, 0, '{}')",
            (row["id"], row["period_key"], row["period_start"], row["period_end"],
             f"Half marathon training, {row['period_key']}", "The training runs and how the calf felt."))
    return conn


def _recalled(monkeypatch, zone: str, question: str) -> list[str]:
    """``_recall_summaries`` itself on the store, the question read in *zone*."""
    from zoneinfo import ZoneInfo

    from kazma_core.memory import recall
    from kazma_core.memory.time_words import read_time

    monkeypatch.setattr("kazma_core.memory.recall._memory_zone", lambda: ZoneInfo(zone))
    monkeypatch.setattr(recall, "_summaries_enabled", lambda: True)
    monkeypatch.setattr(recall, "_encode_query", lambda query: None)
    conn = _months_store()
    token = recall._ASK.set(read_time(question, now=_ASKED_ON, zone=ZoneInfo(zone)))
    try:
        return [h.id for h in recall._recall_summaries(conn, question, "default")]
    finally:
        recall._ASK.reset(token)
        conn.close()


@pytest.mark.parametrize("zone", ["UTC", "Pacific/Kiritimati"])
def test_recall_answers_june_with_june_through_its_own_path(monkeypatch, zone):
    """The follow-up's L1: through ``_recall_summaries``, a said-in-June
    question gets June alone, and a June question about the training shows no
    other month."""
    assert _recalled(monkeypatch, zone, "What did we talk about in June?") == ["sum_m6"]
    assert _recalled(monkeypatch, zone, "شنو تكلمنا عنه في يونيو؟") == ["sum_m6"]
    shown = _recalled(monkeypatch, zone, "How was my training going in June?")
    assert "sum_m6" in shown and not {"sum_m5", "sum_m7"} & set(shown), shown


def test_control_recall_with_the_stored_stretch_shows_a_neighbouring_month(monkeypatch):
    """The negative control: ``_spans`` as the stretches were stored lets
    July in (UTC) beside June."""
    from kazma_core.memory import recall

    monkeypatch.setattr(recall, "_spans", lambda rows, weeks=None: {
        str(r["id"]): (float(r["period_start"]), float(r["period_end"])) for r in rows})
    said = _recalled(monkeypatch, "UTC", "What did we talk about in June?")
    training = _recalled(monkeypatch, "UTC", "How was my training going in June?")
    assert "sum_m7" in said or "sum_m7" in training, (said, training)


def _honolulu_project() -> sqlite3.Connection:
    """A project of weeks 2026-W19 to W22 (4 to 31 May) as a Honolulu server
    wrote it: each stretch from its local midnight."""
    from zoneinfo import ZoneInfo

    from kazma_core.memory.schema_v2 import ensure_primary_schema

    honolulu = ZoneInfo("Pacific/Honolulu")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    starts = []
    for week, day in ((19, 4), (20, 11), (21, 18), (22, 25)):
        start = _dt.datetime(2026, 5, day, tzinfo=honolulu).timestamp()
        starts.append(start)
        conn.execute(
            "INSERT INTO memory_summaries (id, tenant_id, period_key, period_start, period_end, level, title, "
            "summary_text, status, created_at, updated_at, metadata_json) "
            "VALUES (?, 'default', ?, ?, ?, 'week', 't', 's', 'active', 0, 0, '{}')",
            (f"w{week}", f"2026-W{week}", start, start + 7 * 86400))
    conn.execute(
        "INSERT INTO memory_summaries (id, tenant_id, period_key, period_start, period_end, level, title, "
        "summary_text, status, created_at, updated_at, metadata_json) "
        "VALUES ('proj', 'default', 'project:abc', ?, ?, 'project', 't', 's', 'active', 0, 0, '{}')",
        (starts[0], starts[-1] + 7 * 86400))
    conn.executemany("INSERT INTO memory_summary_parts (rollup_id, part_id) VALUES ('proj', ?)",
                     [(f"w{w}",) for w in (19, 20, 21, 22)])
    return conn


def test_a_project_s_stretch_and_label_come_from_its_weeks(monkeypatch):
    """The follow-up review's L2: a Honolulu-written project of May, read in
    Kuwait (13 hours ahead), stays 4 May to 1 June and is named May."""
    from zoneinfo import ZoneInfo

    from kazma_core.memory import recall, topic_rollups

    kuwait = ZoneInfo("Asia/Kuwait")
    monkeypatch.setattr("kazma_core.memory.recall._memory_zone", lambda: kuwait)
    conn = _honolulu_project()
    rows = conn.execute("SELECT * FROM memory_summaries WHERE level = 'project'").fetchall()
    weeks = recall._project_weeks(conn, rows)
    assert weeks == {"proj": ("2026-W19", "2026-W22")}
    assert recall._spans(rows, weeks)["proj"] == (_dt.datetime(2026, 5, 4, tzinfo=kuwait).timestamp(),
                                                  _dt.datetime(2026, 6, 1, tzinfo=kuwait).timestamp())
    assert topic_rollups.rollup_label("project", rows[0]["period_start"], rows[0]["period_end"], "project:abc",
                                      weeks["proj"]) == "Project, May 2026 to May 2026"
    # the control: read at midday, 13 hours away, it moves a day and into June
    start, end = recall._spans(rows)["proj"]
    assert _dt.datetime.fromtimestamp(start, kuwait).day == 5 and _dt.datetime.fromtimestamp(end, kuwait).month == 6
    conn.close()


# ── "What did we talk about last week?" is answered by the week ──────────
# (the follow-up review's O2: a said week was answered by its whole month)

_LAST_WEEK = ["What did we talk about last week?", "شنو تكلمنا عنه الأسبوع الماضي؟"]


def _weeks_and_months_store() -> sqlite3.Connection:
    """June and July's roll-ups, and weeks W26, W27 and W31, as a Kuwait
    server wrote them."""
    from zoneinfo import ZoneInfo

    kuwait = ZoneInfo("Asia/Kuwait")
    conn = _months_store()
    for week, (month, day) in ((26, (6, 22)), (27, (6, 29)), (31, (7, 27))):
        start = _dt.datetime(2026, month, day, tzinfo=kuwait).timestamp()
        conn.execute(
            "INSERT INTO memory_summaries (id, tenant_id, period_key, period_start, period_end, level, title, "
            "summary_text, status, created_at, updated_at, metadata_json) "
            "VALUES (?, 'default', ?, ?, ?, 'week', ?, 'What we talked about that week.', 'active', 0, 0, '{}')",
            (f"sum_w{week}", f"2026-W{week}", start, start + 7 * 86400, f"Week {week}"))
    return conn


def _asked_last_week(monkeypatch, zone: str, question: str) -> list[str]:
    from zoneinfo import ZoneInfo

    from kazma_core.memory import recall
    from kazma_core.memory.time_words import read_time

    monkeypatch.setattr("kazma_core.memory.recall._memory_zone", lambda: ZoneInfo(zone))
    monkeypatch.setattr(recall, "_summaries_enabled", lambda: True)
    monkeypatch.setattr(recall, "_encode_query", lambda query: None)
    monday = _dt.datetime(2026, 8, 3, 10, tzinfo=ZoneInfo("Asia/Kuwait")).timestamp()
    conn = _weeks_and_months_store()
    token = recall._ASK.set(read_time(question, now=monday, zone=ZoneInfo(zone)))
    try:
        return [h.id for h in recall._recall_summaries(conn, question, "default")]
    finally:
        recall._ASK.reset(token)
        conn.close()


@pytest.mark.parametrize("question", _LAST_WEEK)
@pytest.mark.parametrize("zone", ["Asia/Kuwait", "UTC"])
def test_last_week_is_answered_by_its_weekly_summary(monkeypatch, zone, question):
    assert _asked_last_week(monkeypatch, zone, question) == ["sum_w31"]


@pytest.mark.parametrize("question", _LAST_WEEK)
def test_control_with_months_for_every_period_last_week_gets_july(monkeypatch, question):
    """The negative control: months answering every said period (as before)
    gives July's roll-up, not the week."""
    from kazma_core.memory import recall

    monkeypatch.setattr(recall, "_SAID_BY_MONTHS_S", 0.0)
    assert _asked_last_week(monkeypatch, "Asia/Kuwait", question) == ["sum_m7"]


# ── Which project a group is: never by row order; never a forgotten one ──
# (the follow-up's M1: after ANALYZE a retired project came first and took a
# forgotten or an active project's weeks)

def _six_weeks_and_two_projects(mem, x_status: str, *, x_first: bool) -> None:
    """Weeks w0-w5 on one topic; a skipped (retired) project R of w0-w2 and a
    project X of w2-w5 in *x_status*, inserted in either order."""
    for week in range(6):
        _weekly(mem, f"w{week}", week, title=f"Garden week {week}", text="The garden beds.", direction=0)
    projects = [("sum_b_X", x_status, ["w2", "w3", "w4", "w5"], "{}"),
                ("sum_a_R", "retired", ["w0", "w1", "w2"], '{"skipped": true}')]
    for rid, status, parts, meta in projects if x_first else reversed(projects):
        mem.db.execute(
            "INSERT INTO memory_summaries (id, tenant_id, period_key, period_start, period_end, level, title, "
            "summary_text, status, created_at, updated_at, metadata_json) "
            "VALUES (?, 'default', ?, 0, 0, 'project', '', '', ?, 0, 0, ?)",
            (rid, f"project:{rid}", status, meta))
        mem.db.executemany("INSERT INTO memory_summary_parts (rollup_id, part_id) VALUES (?, ?)",
                           [(rid, p) for p in parts])
    mem.db.commit()


def _project_state(mem) -> dict[str, tuple[str, list[str]]]:
    return {r["id"]: (r["status"], _parts(mem, r["id"])) for r in _rollups(mem, "project")}


@pytest.mark.parametrize("analyze", [False, True])
@pytest.mark.parametrize("x_first", [False, True])
def test_a_group_sharing_a_week_with_a_forgotten_project_is_never_written(mem, analyze, x_first):
    _six_weeks_and_two_projects(mem, "forgotten", x_first=x_first)
    if analyze:
        mem.db.execute("ANALYZE")
    assert _plan(mem)["projects"] == 0
    assert _project_state(mem) == {"sum_a_R": ("retired", ["w0", "w1", "w2"]),
                                   "sum_b_X": ("forgotten", ["w2", "w3", "w4", "w5"])}


@pytest.mark.parametrize("analyze", [False, True])
@pytest.mark.parametrize("x_first", [False, True])
def test_an_active_project_takes_its_grown_group_over_a_retired_one(mem, analyze, x_first):
    _six_weeks_and_two_projects(mem, "active", x_first=x_first)
    if analyze:
        mem.db.execute("ANALYZE")
    assert _plan(mem)["projects"] == 1
    assert _project_state(mem) == {"sum_a_R": ("retired", ["w0", "w1", "w2"]),
                                   "sum_b_X": ("rebuild", ["w0", "w1", "w2", "w3", "w4", "w5"])}


def test_a_project_forgotten_in_the_ledger_alone_is_not_written_again(mem):
    """A restored store that lost the tombstone: the ledger still says so."""
    from kazma_core.memory import forget_journal

    _six_weeks_and_two_projects(mem, "active", x_first=True)
    forget_journal.append("summary", tenant_id="default", summary_id="sum_b_X")
    assert _plan(mem)["projects"] == 0


def test_control_the_first_match_by_row_order_takes_the_wrong_project(mem, monkeypatch):
    """The negative control: c0dad2c05's lookup (the first overlapping row,
    a tombstone only when it comes first) writes the forgotten weeks into
    the retired project, and takes the active project's weeks."""
    from kazma_core.memory import topic_rollups

    def first_match(existing, parts):
        return next((rid for rid, (status, held, _skip) in existing.items()
                     if held and len(held & set(parts)) >= topic_rollups._SAME_PROJECT_SHARE
                     * min(len(held), len(parts))), None)

    monkeypatch.setattr(topic_rollups, "_project_of", first_match)
    monkeypatch.setattr(topic_rollups, "_forgotten_project_of", lambda existing, tombstones, parts: None)
    _six_weeks_and_two_projects(mem, "forgotten", x_first=False)
    _plan(mem)
    assert _project_state(mem)["sum_a_R"] == ("rebuild", ["w0", "w1", "w2", "w3", "w4", "w5"])


# ── A roll-up retired for too few parts comes back with them (M2) ────────

@pytest.mark.parametrize("level", ["project", "month"])
def test_a_roll_up_retired_for_too_few_parts_comes_back_with_them(mem, level):
    """Weeks written again (a version bump): the roll-up is written while two
    of its weeks are retired, so it retires for too few live parts. When the
    weeks come back active under the same ids, it is planned again; one the
    model skipped is not (the O1 tests)."""
    _training(mem)
    _plan(mem)
    [rollup] = _rollups(mem, level)
    mem.db.execute("UPDATE memory_summaries SET status = 'retired' WHERE id IN ('tr1', 'tr2', 'tr3')")
    mem.db.commit()
    chat = _Chat()
    assert _write(mem, rollup["id"], chat) and not chat.calls  # too few: retired, no model call
    assert _row(mem, rollup["id"])["status"] == "retired"
    mem.db.execute("UPDATE memory_summaries SET status = 'active' WHERE id IN ('tr1', 'tr2', 'tr3')")
    mem.db.commit()
    _plan(mem)
    assert _row(mem, rollup["id"])["status"] == "rebuild"


# ── A said week shows all its topics (M3); "this month" falls back (L1) ──

def _week_of_four_topics() -> sqlite3.Connection:
    """W31 (27 July to 2 August) with four topics, and August's weeks W32 and
    W33, as a Kuwait server wrote them; no August roll-up yet."""
    from zoneinfo import ZoneInfo

    from kazma_core.memory.schema_v2 import ensure_primary_schema

    kuwait = ZoneInfo("Asia/Kuwait")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    rows = [("w31_training", 31, (7, 27)), ("w31_battery", 31, (7, 27)), ("w31_garden", 31, (7, 27)),
            ("w31_tax", 31, (7, 27)), ("w32_trip", 32, (8, 3)), ("w33_trip", 33, (8, 10))]
    for sid, week, (month, day) in rows:
        start = _dt.datetime(2026, month, day, tzinfo=kuwait).timestamp()
        conn.execute(
            "INSERT INTO memory_summaries (id, tenant_id, period_key, period_start, period_end, level, title, "
            "summary_text, status, created_at, updated_at, metadata_json) "
            "VALUES (?, 'default', ?, ?, ?, 'week', ?, ?, 'active', 0, 0, '{}')",
            (sid, f"2026-W{week}", start, start + 7 * 86400, sid.split("_")[1].title(), "Talked about it. " * 40))
    return conn


def _said(monkeypatch, question: str, now: float, conn: sqlite3.Connection, zone: str = "Asia/Kuwait") -> list:
    from zoneinfo import ZoneInfo

    from kazma_core.memory import recall
    from kazma_core.memory.time_words import read_time

    monkeypatch.setattr("kazma_core.memory.recall._memory_zone", lambda: ZoneInfo(zone))
    monkeypatch.setattr(recall, "_summaries_enabled", lambda: True)
    monkeypatch.setattr(recall, "_encode_query", lambda query: None)
    token = recall._ASK.set(read_time(question, now=now, zone=ZoneInfo(zone)))
    try:
        return recall._recall_summaries(conn, question, "default")
    finally:
        recall._ASK.reset(token)
        conn.close()


_MONDAY_3_AUG = _dt.datetime(2026, 8, 3, 10, tzinfo=__import__("zoneinfo").ZoneInfo("Asia/Kuwait")).timestamp()


@pytest.mark.parametrize("question", _LAST_WEEK)
def test_a_said_week_shows_every_topic_of_it(monkeypatch, question):
    hits = _said(monkeypatch, question, _MONDAY_3_AUG, _week_of_four_topics())
    assert sorted(h.id for h in hits) == ["w31_battery", "w31_garden", "w31_tax", "w31_training"]
    assert all(len(h.metadata["display"]) <= 300 for h in hits)  # shortened as a listing is


@pytest.mark.parametrize("question", _LAST_WEEK)
def test_control_with_two_summaries_a_week_shows_two_of_four(monkeypatch, question):
    from kazma_core.memory import recall

    monkeypatch.setattr(recall, "_LIST_SUMMARY_LIMIT", 2)
    assert len(_said(monkeypatch, question, _MONDAY_3_AUG, _week_of_four_topics())) == 2


@pytest.mark.parametrize("day", [19, 21])
@pytest.mark.parametrize("question", ["What did we talk about this month?", "شنو تكلمنا عنه هذا الشهر؟"])
def test_this_month_before_its_roll_up_is_answered_by_its_weeks(monkeypatch, day, question):
    """The follow-up's L1: from the 20th "this month" is a month's question,
    and its roll-up is written only after the month ends: its weeks answer."""
    from zoneinfo import ZoneInfo

    now = _dt.datetime(2026, 8, day, 12, tzinfo=ZoneInfo("Asia/Kuwait")).timestamp()
    assert {h.id for h in _said(monkeypatch, question, now, _week_of_four_topics())} >= {"w32_trip"}


def test_control_without_the_fallback_this_month_on_the_21st_gets_nothing(monkeypatch):
    from zoneinfo import ZoneInfo

    from kazma_core.memory import recall

    real = recall._months_said_in

    def months_only(query, ask, rows, level, span):
        return real(query, ask, rows, {rid: ("month" if kind == "month" else "none") for rid, kind in level.items()},
                    span)

    monkeypatch.setattr(recall, "_months_said_in", months_only)
    now = _dt.datetime(2026, 8, 21, 12, tzinfo=ZoneInfo("Asia/Kuwait")).timestamp()
    hits = _said(monkeypatch, "What did we talk about this month?", now, _week_of_four_topics())
    assert not any(h.metadata.get("source") == "period" or h.source == "period" for h in hits)


# ── A project's weeks reach recall's own path (L2) ───────────────────────

@pytest.mark.parametrize("question", ["What did we say about the training in June?",
                                      "شنو قلنا عن التدريب في يونيو؟"])
def test_recall_keeps_a_may_project_out_of_june_through_its_own_path(monkeypatch, question):
    conn = _honolulu_project()
    conn.execute("UPDATE memory_summaries SET title = 'Half marathon training', "
                 "summary_text = 'The training runs, التدريب والجري.' WHERE id = 'proj'")
    now = _dt.datetime(2026, 8, 15, 12, tzinfo=_dt.UTC).timestamp()
    assert "proj" not in {h.id for h in _said(monkeypatch, question, now, conn)}


def test_control_recall_without_the_project_s_weeks_shows_it_in_june(monkeypatch):
    from kazma_core.memory import recall

    real = recall._spans
    monkeypatch.setattr(recall, "_spans", lambda rows, weeks=None: real(rows))
    conn = _honolulu_project()
    conn.execute("UPDATE memory_summaries SET title = 'Half marathon training', "
                 "summary_text = 'The training runs, التدريب والجري.' WHERE id = 'proj'")
    now = _dt.datetime(2026, 8, 15, 12, tzinfo=_dt.UTC).timestamp()
    assert "proj" in {h.id for h in _said(monkeypatch, "What did we say about the training in June?", now, conn)}


# ── A forgotten project holds back only itself (the follow-up's N1) ──────

def _training_then_injury(mem) -> None:
    """Training weeks w0-w4 (one topic) and the injury it became, w5-w14
    (another); project X, of the training and the week it turned (w0-w5),
    forgotten."""
    for week in range(5):
        _weekly(mem, f"w{week}", week, title=f"Training week {week}", text="Training runs.", direction=0)
    for week in range(5, 15):
        _weekly(mem, f"w{week}", week, title=f"Knee injury week {week}", text="The knee injury.", direction=5)
    mem.db.execute(
        "INSERT INTO memory_summaries (id, tenant_id, period_key, period_start, period_end, level, title, "
        "summary_text, status, created_at, updated_at, metadata_json) "
        "VALUES ('sum_X', 'default', 'project:X', 0, 0, 'project', '', '', 'forgotten', 0, 0, '{}')")
    mem.db.executemany("INSERT INTO memory_summary_parts (rollup_id, part_id) VALUES ('sum_X', ?)",
                       [(f"w{w}",) for w in range(6)])
    mem.db.commit()


def test_a_project_sharing_one_week_with_a_forgotten_one_is_written(mem, caplog):
    import logging

    _training_then_injury(mem)
    with caplog.at_level(logging.INFO, logger="kazma_core.memory.topic_rollups"):
        out = _plan(mem, now=_dt.datetime(2026, 12, 1).timestamp())
    projects = {r["id"]: (r["status"], _parts(mem, r["id"])) for r in _rollups(mem, "project")}
    injury = [rid for rid, (status, parts) in projects.items() if status == "rebuild"]
    assert len(injury) == 1 and set(projects[injury[0]][1]) == {f"w{w}" for w in range(5, 15)}
    assert projects["sum_X"][0] == "forgotten" and out["held_by_forget"] == 1  # the training is X: held
    assert "roll-up sum_X was forgotten" in caplog.text


def test_control_the_any_week_rule_never_writes_the_injury(mem, monkeypatch):
    from kazma_core.memory import topic_rollups

    monkeypatch.setattr(topic_rollups, "_forgotten_project_of", lambda existing, tombstones, parts: next(
        (rid for rid in tombstones if existing.get(rid, ("", set(), False))[1] & set(parts)), None))
    _training_then_injury(mem)
    assert _plan(mem, now=_dt.datetime(2026, 12, 1).timestamp())["projects"] == 0


@pytest.mark.parametrize("how", ["its own id in the ledger", "the same project forgotten"])
def test_a_project_forgotten_after_it_was_planned_is_not_written(mem, how):
    from kazma_core.memory import forget_journal

    _training(mem)
    _plan(mem)
    [project] = _rollups(mem, "project")
    if how == "its own id in the ledger":
        forget_journal.append("summary", tenant_id="default", summary_id=project["id"])
    else:
        mem.db.execute(
            "INSERT INTO memory_summaries (id, tenant_id, period_key, period_start, period_end, level, title, "
            "summary_text, status, created_at, updated_at, metadata_json) "
            "VALUES ('sum_old', 'default', 'project:old', 0, 0, 'project', '', '', 'forgotten', 0, 0, '{}')")
        mem.db.executemany("INSERT INTO memory_summary_parts (rollup_id, part_id) VALUES ('sum_old', ?)",
                           [(p,) for p in _parts(mem, project["id"])])
        mem.db.commit()
    chat = _Chat()
    assert _write(mem, project["id"], chat) and not chat.calls
    assert _row(mem, project["id"])["status"] == "retired"


def test_control_a_project_nothing_forgot_is_written(mem):
    _training(mem)
    _plan(mem)
    [project] = _rollups(mem, "project")
    chat = _Chat()
    assert _write(mem, project["id"], chat) and len(chat.calls) == 1


# ── The ledger's forgets reach the month too, "mentions" included (L2) ───

@pytest.mark.parametrize("kind", ["summary", "mentions"])
def test_a_month_the_ledger_holds_forgotten_is_not_planned(mem, kind):
    import json as _json

    from kazma_core.memory import forget_journal, topic_rollups

    _training(mem)
    rid = topic_rollups._rollup_id("default", "month", "2026-06")
    if kind == "summary":
        forget_journal.append("summary", tenant_id="default", summary_id=rid)
    else:
        forget_journal.append("mentions", tenant_id="default", detail=_json.dumps({"summaries": [rid]}))
    out = _plan(mem)
    assert out["months"] == 0 and out["held_by_forget"] >= 1
    assert topic_rollups._ledger_forgotten("default") == {rid}


# ── The ledger is read by the space's reach, never by id alone (N2) ──────

@pytest.mark.parametrize("enforced", [False, True])
def test_another_space_s_ledger_entry_never_holds_the_owner_s_month(mem, monkeypatch, enforced):
    """The fifth follow-up review's N2: a member of space B who names the
    owner's (computable) June id holds nothing back, Spaces on or off."""
    from kazma_core.memory import forget_journal, topic_rollups

    monkeypatch.setattr("kazma_core.memory.tenant_view.spaces_enforced", lambda: enforced)
    _training(mem)
    rid = topic_rollups._rollup_id("default", "month", "2026-06")
    forget_journal.append("summary", tenant_id="space-b", summary_id=rid)
    assert topic_rollups._ledger_forgotten("default") == set()
    out = _plan(mem)
    assert out["months"] == 1 and out["held_by_forget"] == 0


def test_the_owner_s_ledger_reaches_every_space_while_spaces_is_off(mem, monkeypatch):
    from kazma_core.memory import forget_journal, topic_rollups

    forget_journal.append("summary", tenant_id="default", summary_id="sum_x")
    monkeypatch.setattr("kazma_core.memory.tenant_view.spaces_enforced", lambda: False)
    assert topic_rollups._ledger_forgotten("space-b") == {"sum_x"}
    monkeypatch.setattr("kazma_core.memory.tenant_view.spaces_enforced", lambda: True)
    assert topic_rollups._ledger_forgotten("space-b") == set()


def test_a_refused_forget_writes_nothing_to_the_ledger(mem, monkeypatch):
    """Space B forgetting the owner's summary is refused (not found), and
    the ledger is left as it was; the owner's own forget is journaled."""
    from kazma_core.memory import forget_journal
    from kazma_core.memory.topic_summaries import forget_summary

    monkeypatch.setattr("kazma_core.memory.tenant_view.spaces_enforced", lambda: True)
    _training(mem)
    _plan(mem)
    [month] = _rollups(mem, "month")
    before = forget_journal.journaled_seqs()
    out = forget_summary(month["id"], tenant_id="space-b", conn=mem.db)
    assert out == {"ok": False, "error": "not_found"} and forget_journal.journaled_seqs() == before
    assert forget_summary(month["id"], tenant_id="default", conn=mem.db)["ok"]
    assert len(forget_journal.journaled_seqs()) == len(before) + 1


def _ledger_reads_by_id_alone(source: str, path: str) -> list[str]:
    """Each call that reads the forget ledger's summaries with no tenant."""
    import ast

    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name in ("forgotten_summaries", "forgotten_rollup_parts", "_ledger_forgotten") and not (
                    node.args or node.keywords):
                found.append(f"{path}:{node.lineno} {name}()")
    return found


def test_no_ledger_reader_reads_by_id_alone():
    """From source: every read of the ledger's forgotten summaries names the
    tenant it reads for (``tenant_view.ledger_reach``)."""
    found = []
    for path in sorted((_REPO / "kazma-core/kazma_core").rglob("*.py")):
        found += _ledger_reads_by_id_alone(path.read_text(encoding="utf-8"), path.relative_to(_REPO).as_posix())
    assert found == []


def test_control_a_read_by_id_alone_is_found():
    assert _ledger_reads_by_id_alone("ids = forgotten_summaries()\nmore = _ledger_forgotten()\n", "old.py") == [
        "old.py:1 forgotten_summaries()", "old.py:2 _ledger_forgotten()"]


# ── A live group never loses its id to a tombstone; one line a skip (L-b, L-c)

def test_a_live_group_whose_id_is_a_tombstone_s_gets_another(mem):
    from kazma_core.memory import topic_rollups

    _training(mem)
    taken = topic_rollups._rollup_id("default", "project", "tr0")
    mem.db.execute(
        "INSERT INTO memory_summaries (id, tenant_id, period_key, period_start, period_end, level, title, "
        "summary_text, status, created_at, updated_at, metadata_json) "
        "VALUES (?, 'default', 'project:old', 0, 0, 'project', '', '', 'forgotten', 0, 0, '{}')", (taken,))
    mem.db.execute("INSERT INTO memory_summary_parts (rollup_id, part_id) VALUES (?, 'an-old-week')", (taken,))
    mem.db.commit()
    assert _plan(mem)["projects"] == 1
    planned = [r for r in _rollups(mem, "project") if r["status"] == "rebuild"]
    assert len(planned) == 1 and planned[0]["id"] != taken
    assert _parts(mem, planned[0]["id"]) == ["tr0", "tr1", "tr2", "tr3"]


def test_a_held_roll_up_is_said_once_not_every_pass(mem, caplog):
    import logging

    from kazma_core.memory import forget_journal, topic_rollups

    _training(mem)
    rid = topic_rollups._rollup_id("default", "month", "2026-06")
    forget_journal.append("summary", tenant_id="default", summary_id=rid)
    topic_rollups._SAID_HELD.discard(rid)
    with caplog.at_level(logging.INFO, logger="kazma_core.memory.topic_rollups"):
        counts = [_plan(mem)["held_by_forget"] for _ in range(3)]
    assert counts == [1, 1, 1] and caplog.text.count(f"roll-up {rid} was forgotten") == 1


# ── More topics than shown: the largest, and how many more (L1) ──────────

def _week_of_eight_topics() -> sqlite3.Connection:
    """W31 with eight topics; the id order is the reverse of their size."""
    from zoneinfo import ZoneInfo

    from kazma_core.memory.schema_v2 import ensure_primary_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    start = _dt.datetime(2026, 7, 27, tzinfo=ZoneInfo("Asia/Kuwait")).timestamp()
    for n, letter in enumerate("abcdefgh"):
        conn.execute(
            "INSERT INTO memory_summaries (id, tenant_id, period_key, period_start, period_end, level, title, "
            "summary_text, status, turn_count, created_at, updated_at, metadata_json) "
            "VALUES (?, 'default', '2026-W31', ?, ?, 'week', ?, 'Talked.', 'active', ?, 0, 0, '{}')",
            (f"t_{letter}", start, start + 7 * 86400, f"Topic {letter}", n + 1))
    return conn


@pytest.mark.parametrize("question", _LAST_WEEK)
def test_a_said_week_shows_its_largest_topics_and_says_how_many_more(monkeypatch, question):
    hits = _said(monkeypatch, question, _MONDAY_3_AUG, _week_of_eight_topics())
    assert [h.id for h in hits] == ["t_h", "t_g", "t_f", "t_e", "t_d", "t_c"]
    assert "2 more weekly summaries of this period are not shown" in hits[-1].metadata["display"]


def test_control_ranked_by_id_the_week_shows_its_smallest(monkeypatch):
    from kazma_core.memory import recall

    monkeypatch.setattr(recall, "_said_rank", lambda by_id, share, span: lambda rid: (-share[rid], rid))
    hits = _said(monkeypatch, _LAST_WEEK[0], _MONDAY_3_AUG, _week_of_eight_topics())
    assert [h.id for h in hits] == ["t_a", "t_b", "t_c", "t_d", "t_e", "t_f"]


# ── A forgotten project survives a memory restore (the sixth review's M1) ─

def _forgotten_then_restored(mem) -> str:
    """A written training project, forgotten, then a memory restore that
    lost its row and parts: only the ledger knows it."""
    from kazma_core.memory.topic_summaries import forget_summary

    rid = _written_project(mem)
    assert forget_summary(rid, tenant_id="default", conn=mem.db)["ok"]
    mem.db.execute("DELETE FROM memory_summary_parts WHERE rollup_id = ?", (rid,))
    mem.db.execute("DELETE FROM memory_summaries WHERE id = ?", (rid,))
    mem.db.commit()
    return rid


def test_a_forgotten_project_is_not_written_again_after_a_restore(mem):
    rid = _forgotten_then_restored(mem)
    out = _plan(mem)
    assert out["projects"] == 0 and out["held_by_forget"] >= 1
    assert _rollups(mem, "project") == [] and rid not in {r["id"] for r in _rollups(mem, "project")}


def test_a_forgotten_project_whose_first_week_changed_is_held_by_its_parts(mem):
    """The journaled parts match the group by the share rule, whatever id
    the group's first week would give it."""
    _forgotten_then_restored(mem)
    _weekly(mem, "tr_early", -1, title="Half marathon training: the plan", text="The plan.", direction=0)
    assert _plan(mem)["projects"] == 0


def test_control_without_the_ledger_the_restored_project_comes_back(mem, monkeypatch):
    """The negative control: the restored store alone (no ids, no parts from
    the ledger) plans the forgotten project again."""
    from kazma_core.memory import forget_journal, topic_rollups

    _forgotten_then_restored(mem)
    monkeypatch.setattr(forget_journal, "forgotten_rollup_parts", lambda tenant_id: {})
    monkeypatch.setattr(topic_rollups, "_ledger_forgotten", lambda tenant_id: set())
    assert _plan(mem)["projects"] == 1


@pytest.mark.parametrize("parts_known", [True, False])
def test_a_group_on_a_forgotten_id_is_renumbered_only_when_its_parts_are_known(mem, monkeypatch, parts_known):
    """With the tombstone's parts known (another project), the live group
    gets another id (the fifth review's L-b); unknown, it may be that very
    project, so it is held (M1)."""
    from kazma_core.memory import forget_journal, topic_rollups

    _training(mem)
    taken = topic_rollups._rollup_id("default", "project", "tr0")
    forget_journal.append("summary", tenant_id="default", summary_id=taken)
    if parts_known:
        monkeypatch.setattr(forget_journal, "forgotten_rollup_parts", lambda tenant_id: {taken: {"an-old-week"}})
    out = _plan(mem)
    assert out["projects"] == (1 if parts_known else 0)
    assert taken not in {r["id"] for r in _rollups(mem, "project")}


# ── A month's forget holds back no project (the seventh review's M-a) ────

def test_forgetting_a_month_leaves_the_projects_over_its_weeks_written(mem):
    from kazma_core.memory.topic_summaries import forget_summary

    _training(mem)
    _plan(mem)
    [month] = _rollups(mem, "month")
    [project] = _rollups(mem, "project")
    assert forget_summary(month["id"], tenant_id="default", conn=mem.db)["ok"]
    chat = _Chat()
    assert _write(mem, project["id"], chat) and len(chat.calls) == 1
    assert _row(mem, project["id"])["status"] == "active"


def test_control_a_month_journaled_as_a_project_retires_the_project(mem, monkeypatch):
    """The negative control: da56cce39 journaled a month's parts as a
    project's, and the project over its weeks was retired."""
    from kazma_core.memory import forget_journal
    from kazma_core.memory.topic_summaries import forget_summary

    _training(mem)
    _plan(mem)
    [month] = _rollups(mem, "month")
    [project] = _rollups(mem, "project")
    month_parts = set(_parts(mem, month["id"]))
    assert forget_summary(month["id"], tenant_id="default", conn=mem.db)["ok"]
    monkeypatch.setattr(forget_journal, "forgotten_rollup_parts", lambda tenant_id: {month["id"]: month_parts})
    chat = _Chat()
    assert _write(mem, project["id"], chat) and not chat.calls
    assert _row(mem, project["id"])["status"] == "retired"


# ── A project's parts are journaled, pinned (L-a), by "mentions" too (M-b) ─

def test_a_project_s_forget_journals_its_parts(mem):
    from kazma_core.memory import forget_journal
    from kazma_core.memory.topic_summaries import forget_summary

    rid = _written_project(mem)
    parts = set(_parts(mem, rid))
    assert forget_summary(rid, tenant_id="default", conn=mem.db)["ok"]
    assert forget_journal.forgotten_rollup_parts("default") == {rid: parts}


def test_a_restored_project_is_held_when_an_earlier_week_sorts_first(mem):
    """The restore case where the group's first id changes, so only the
    journaled parts can hold it (L-a: without them it was planned)."""
    _forgotten_then_restored(mem)
    _weekly(mem, "aa_early", -1, title="Half marathon training: the plan", text="The plan.", direction=0)
    assert _plan(mem)["projects"] == 0


def test_control_without_the_journaled_parts_the_earlier_week_brings_it_back(mem, monkeypatch):
    from kazma_core.memory import forget_journal

    _forgotten_then_restored(mem)
    monkeypatch.setattr(forget_journal, "forgotten_rollup_parts", lambda tenant_id: {})
    _weekly(mem, "aa_early", -1, title="Half marathon training: the plan", text="The plan.", direction=0)
    assert _plan(mem)["projects"] == 1


def test_forgetting_what_mentions_a_name_journals_the_project_s_parts(mem):
    from kazma_core.memory import forget, forget_journal

    rid = _written_project(mem)
    parts = set(_parts(mem, rid))
    mem.db.execute("UPDATE memory_summaries SET title = 'Zanzibar marathon block' WHERE id = ?", (rid,))
    mem.db.commit()
    assert forget.forget_mentions("Zanzibar", tenant_id="default")["ok"]
    assert forget_journal.forgotten_rollup_parts("default") == {rid: parts}


def test_an_older_entry_is_filled_with_its_project_s_parts_once(mem, caplog):
    """The backfill (M-b): an entry journaled before parts were (by id only)
    takes them from the row while it is there; then a restore that loses the
    row still holds the project; a second pass fills nothing."""
    import logging

    from kazma_core.memory import forget_journal

    rid = _written_project(mem)
    parts = set(_parts(mem, rid))
    forget_journal.append("summary", tenant_id="default", summary_id=rid)  # the old shape: no parts
    assert forget_journal.forgotten_rollup_parts("default") == {}
    with caplog.at_level(logging.INFO, logger="kazma_core.memory.forget_journal"):
        assert forget_journal.backfill_project_parts(mem.db) == 1
    assert "1 older forget entries now hold their projects' parts" in caplog.text
    assert forget_journal.backfill_project_parts(mem.db) == 0
    assert forget_journal.forgotten_rollup_parts("default") == {rid: parts}


def test_a_kept_older_row_and_the_journal_join_their_parts(mem):
    """L-c: a restore kept an older row with other parts; the journal's and
    the row's together are the project."""
    from kazma_core.memory import topic_rollups
    from kazma_core.memory.topic_summaries import forget_summary

    rid = _written_project(mem)
    journaled = set(_parts(mem, rid))
    assert forget_summary(rid, tenant_id="default", conn=mem.db)["ok"]
    mem.db.execute("DELETE FROM memory_summary_parts WHERE rollup_id = ?", (rid,))
    mem.db.execute("INSERT INTO memory_summary_parts (rollup_id, part_id) VALUES (?, 'an-older-week')", (rid,))
    mem.db.commit()
    existing, _tombstones = topic_rollups._known_with_the_ledger(mem.db, "default")
    assert existing[rid][1] == journaled | {"an-older-week"}


# ── The backfill runs from the sweep, and never stops it (final review) ──

def test_the_sweep_backfills_older_entries_once(mem, monkeypatch):
    from kazma_core.memory import forget_journal, topic_rollups

    calls = []
    monkeypatch.setattr(topic_rollups, "_BACKFILLED", [False])
    monkeypatch.setattr(forget_journal, "backfill_project_parts", lambda conn: calls.append(conn) or 0)
    _training(mem)
    _plan(mem)
    _plan(mem)
    assert len(calls) == 1


def test_a_failed_backfill_does_not_stop_the_planning(mem, monkeypatch, caplog):
    import logging

    from kazma_core.memory import forget_journal, topic_rollups

    def broken(conn):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(topic_rollups, "_BACKFILLED", [False])
    monkeypatch.setattr(forget_journal, "backfill_project_parts", broken)
    _training(mem)
    with caplog.at_level(logging.WARNING, logger="kazma_core.memory.topic_rollups"):
        out = _plan(mem)
    assert (out["months"], out["projects"]) == (1, 1)
    assert "could not be filled" in caplog.text and topic_rollups._BACKFILLED == [False]  # tried again


def test_a_malformed_project_parts_entry_is_skipped(mem):
    import json as _json

    from kazma_core.memory import forget_journal

    forget_journal.append("mentions", tenant_id="default",
                          detail=_json.dumps({"summaries": ["x", "y"], "project_parts": {"x": 5, "y": ["w1"]}}))
    assert forget_journal.forgotten_rollup_parts("default") == {"y": {"w1"}}
