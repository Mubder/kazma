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
    assert out == {"months": 1, "projects": 1}
    [month] = _rollups(mem, "month")
    [project] = _rollups(mem, "project")
    assert month["status"] == project["status"] == "rebuild" and month["period_key"] == "2026-06"
    assert _parts(mem, month["id"]) == ["other", "tr0", "tr1", "tr2", "tr3"]
    assert _parts(mem, project["id"]) == ["tr0", "tr1", "tr2", "tr3"]  # the battery week is another topic
    assert _sources(mem, project["id"]) == {e for eids in turns.values() for e in eids}
    assert _plan(mem) == {"months": 0, "projects": 0}  # nothing changed: nothing planned again


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
