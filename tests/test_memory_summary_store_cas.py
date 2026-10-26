"""A weekly summary is never stored from a forgotten turn, or over a forgotten
summary (Memory audit M05, MEM-L01).

``summarize_period`` loaded a week's turns, awaited the model -- one call per
topic, up to 24 a week -- and stored what came back without looking again: a
turn the user forgot during the call was written into an ACTIVE summary,
recalled under "## Weekly Summaries". ``rebuild_summary`` stored with
``UPDATE ... SET status = 'active' WHERE id = ?``: a summary the user forgot
while its rebuild awaited the model came back, text and all.

The store is a compare-and-set now (``topic_summaries._store``). Here: the
audit's two witnesses inverted (the interleaving injected through the fake
model call, as the witnesses did); a turn forgotten during a rebuild; the old
store as the negative control; and a static gate -- every statement that sets
a summary active says which status it may replace.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory import dual_write
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    monkeypatch.setattr("kazma_core.memory.task_queue.enqueue_task", lambda *a, **k: "t")
    monkeypatch.setattr("kazma_core.memory.forget.chat_keys", lambda key: [key] if key else [])
    db = sqlite3.connect(str(tmp_path / "memory_state.db"), check_same_thread=False)
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    dual_write._reset_mirror()
    yield SimpleNamespace(db=db)
    dual_write._reset_mirror()
    ops.close()
    db.close()


def _week():
    from kazma_core.memory import topic_summaries as ts

    return ts._week_of(time.time() - 21 * 86400)


def _ship_week(mem, n: int = 5) -> None:
    """One chat of *n* ShipX turns in a finished week: one topic."""
    _key, start, _end = _week()
    for i in range(n):
        mem.db.execute(
            "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
            "tier, created_at, metadata_json) VALUES (?, 'default', 'c1', ?, ?, ?, 'episodic', ?, '{}')",
            (f"ship{i}", i + 1, f"ShipX delivery routing phase {i} status",
             f"ShipX phase {i} is built.", start + 3600 * (i + 1)),
        )
    mem.db.commit()


def _fake_chat(on_call=None):
    async def chat(messages):
        if on_call is not None:
            on_call()
        body = messages[-1]["content"]
        return json.dumps({"title": "ShipX", "summary": "ShipX: " + body.split("\n\n", 1)[1][:600],
                           "skip": False})
    return chat


def _summaries(mem) -> list[sqlite3.Row]:
    return mem.db.execute("SELECT id, status, summary_text FROM memory_summaries").fetchall()


def test_l01_a_turn_forgotten_while_its_week_is_summarized_is_never_written(mem):
    from kazma_core.memory import forget
    from kazma_core.memory import topic_summaries as ts

    _ship_week(mem)
    key, start, end = _week()

    def user_forgets_meanwhile():
        assert forget.forget_episode("ship2", tenant_id="default")["ok"]

    calls = {"n": 0}

    def once():
        calls["n"] += 1
        if calls["n"] == 1:
            user_forgets_meanwhile()

    assert asyncio.run(ts.summarize_period("default", key, start, end, chat=_fake_chat(once)))
    rows = _summaries(mem)
    assert not any("phase 2" in (r["summary_text"] or "") for r in rows)
    assert not any(r["status"] == "active" for r in rows)  # nothing stored from the old text
    # The four turns left are still a topic: waiting to be written again ...
    waiting = [r for r in rows if r["status"] == "rebuild"]
    assert len(waiting) == 1
    sources = {r[0] for r in mem.db.execute(
        "SELECT episode_id FROM memory_summary_sources WHERE summary_id = ?", (waiting[0]["id"],))}
    assert sources == {"ship0", "ship1", "ship3", "ship4"}
    # ... and the rebuild writes them without the forgotten one.
    assert asyncio.run(ts.rebuild_summary(waiting[0]["id"], chat=_fake_chat()))
    status, text = mem.db.execute("SELECT status, summary_text FROM memory_summaries WHERE id = ?",
                                  (waiting[0]["id"],)).fetchone()
    assert status == "active" and "phase 2" not in text and "phase 3" in text


def test_l01_a_forgotten_summary_stays_forgotten_through_its_rebuild(mem):
    from kazma_core.memory import forget
    from kazma_core.memory import topic_summaries as ts

    _ship_week(mem)
    key, start, end = _week()
    assert asyncio.run(ts.summarize_period("default", key, start, end, chat=_fake_chat()))
    sid = mem.db.execute("SELECT id FROM memory_summaries WHERE status = 'active'").fetchone()[0]
    forget.forget_episode("ship4", tenant_id="default")
    assert mem.db.execute("SELECT status FROM memory_summaries WHERE id = ?", (sid,)).fetchone()[0] == "rebuild"

    def user_forgets_the_summary():
        assert ts.forget_summary(sid, tenant_id="default")["ok"]

    assert asyncio.run(ts.rebuild_summary(sid, chat=_fake_chat(user_forgets_the_summary)))
    status, text = mem.db.execute("SELECT status, summary_text FROM memory_summaries WHERE id = ?",
                                  (sid,)).fetchone()
    assert (status, text) == ("forgotten", "")


def test_a_turn_forgotten_during_a_rebuild_is_not_written(mem):
    from kazma_core.memory import forget
    from kazma_core.memory import topic_summaries as ts

    _ship_week(mem, n=6)
    key, start, end = _week()
    assert asyncio.run(ts.summarize_period("default", key, start, end, chat=_fake_chat()))
    sid = mem.db.execute("SELECT id FROM memory_summaries WHERE status = 'active'").fetchone()[0]
    forget.forget_episode("ship5", tenant_id="default")

    def another_forget_meanwhile():
        assert forget.forget_episode("ship1", tenant_id="default")["ok"]

    assert asyncio.run(ts.rebuild_summary(sid, chat=_fake_chat(another_forget_meanwhile)))
    status, text = mem.db.execute("SELECT status, summary_text FROM memory_summaries WHERE id = ?",
                                  (sid,)).fetchone()
    assert (status, text) == ("rebuild", "")  # still waiting; never the text with ship1
    assert asyncio.run(ts.rebuild_summary(sid, chat=_fake_chat()))
    status, text = mem.db.execute("SELECT status, summary_text FROM memory_summaries WHERE id = ?",
                                  (sid,)).fetchone()
    assert status == "active" and "phase 1 " not in text and "phase 5" not in text


def _rebuilt_without_ship4(mem) -> str:
    from kazma_core.memory import forget
    from kazma_core.memory import topic_summaries as ts

    _ship_week(mem)
    key, start, end = _week()
    assert asyncio.run(ts.summarize_period("default", key, start, end, chat=_fake_chat()))
    sid = mem.db.execute("SELECT id FROM memory_summaries WHERE status = 'active'").fetchone()[0]
    assert forget.forget_episode("ship4", tenant_id="default")["ok"]
    assert asyncio.run(ts.rebuild_summary(sid, chat=_fake_chat()))
    assert mem.db.execute("SELECT status FROM memory_summaries WHERE id = ?", (sid,)).fetchone()[0] == "active"
    return sid


def test_a_rebuilt_summary_names_only_the_turns_it_was_written_from(mem):
    """Live 2026-10-14: a rebuild wrote the summary from the turns left and kept
    the forgotten turn's source row, so the ledger check found an active
    summary of a forgotten turn and sent it back after every rebuild."""
    from kazma_core.memory import forget

    sid = _rebuilt_without_ship4(mem)
    sources = {r[0] for r in mem.db.execute(
        "SELECT episode_id FROM memory_summary_sources WHERE summary_id = ?", (sid,))}
    assert sources == {"ship0", "ship1", "ship2", "ship3"}
    assert forget.enforce_ledger(mem.db) == {"memories": 0, "facts": 0, "summaries": 0}
    assert mem.db.execute("SELECT status FROM memory_summaries WHERE id = ?", (sid,)).fetchone()[0] == "active"


def test_a_stale_source_row_sends_the_summary_back(mem):
    """Negative control: the row the old rebuild left makes the ledger check
    refuse the rebuilt summary again."""
    from kazma_core.memory import forget

    sid = _rebuilt_without_ship4(mem)
    mem.db.execute("INSERT INTO memory_summary_sources (summary_id, episode_id) VALUES (?, 'ship4')", (sid,))
    mem.db.commit()
    assert forget.enforce_ledger(mem.db) == {"memories": 0, "facts": 0, "summaries": 1}
    assert mem.db.execute("SELECT status FROM memory_summaries WHERE id = ?", (sid,)).fetchone()[0] == "rebuild"


OLD_STORE_UPDATE = (
    "UPDATE memory_summaries SET title = ?, summary_text = ?, turn_count = ?, chat_count = ?, "
    "first_turn_at = ?, last_turn_at = ?, model = ?, updated_at = ?, embedding = ?, "
    "embedding_model_version = ?, metadata_json = ?, status = 'active', queued_at = NULL "
    "WHERE id = ?"
)


def test_the_old_store_resurrects_the_forgotten_summary(mem, monkeypatch):
    """Negative control: the rebuild's old unguarded write, with the forget
    made while the model wrote."""
    from kazma_core.memory import topic_summaries as ts

    _ship_week(mem)
    key, start, end = _week()
    assert asyncio.run(ts.summarize_period("default", key, start, end, chat=_fake_chat()))
    sid = mem.db.execute("SELECT id FROM memory_summaries WHERE status = 'active'").fetchone()[0]

    def old_store(conn, *, turns, title, summary, model, summary_id=None, **_k):
        conn.execute(OLD_STORE_UPDATE, (title, summary, len(turns), 1, 0, 0, model, time.time(), None, None,
                                        "{}", summary_id))
        conn.commit()
        return summary_id

    from kazma_core.memory import forget

    forget.forget_episode("ship4", tenant_id="default")
    monkeypatch.setattr(ts, "_store", old_store)
    asyncio.run(ts.rebuild_summary(sid, chat=_fake_chat(lambda: ts.forget_summary(sid, tenant_id="default"))))
    assert mem.db.execute("SELECT status FROM memory_summaries WHERE id = ?", (sid,)).fetchone()[0] == "active"


# ── The static gate ───────────────────────────────────────────────────────


def _unguarded_activations(sources: dict[str, str]) -> list[str]:
    """Statements that set a ``memory_summaries`` row active without saying
    which status they may replace."""
    out = []
    for rel, text in sources.items():
        for node in ast.walk(ast.parse(text)):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            sql = " ".join(node.value.split())
            if not re.match(r"(UPDATE memory_summaries |INSERT INTO memory_summaries )", sql):
                continue
            if not re.search(r"status = 'active'", sql):
                continue
            tail = sql.split("WHERE", 1)[1] if "WHERE" in sql else ""
            if not re.search(r"status\s*=\s*'(rebuild|retired)'", tail):
                out.append(f"{rel}: {sql[:90]}")
    return out


def test_every_activation_names_the_status_it_replaces():
    sources = {
        p.relative_to(REPO).as_posix(): p.read_text(encoding="utf-8")
        for p in (REPO / "kazma-core" / "kazma_core" / "memory").glob("*.py")
    }
    assert _unguarded_activations(sources) == []


def test_the_old_activation_is_caught():
    old = f"conn.execute({OLD_STORE_UPDATE!r}, args)\n"
    assert len(_unguarded_activations({"old.py": old})) == 1
