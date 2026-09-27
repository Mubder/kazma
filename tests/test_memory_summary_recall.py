"""Recall reads the weekly topic summaries (plan C2b, ``memory/recall.py``).

A topic worked on for weeks outgrows the five turns recall injects; the
weekly summaries (``memory/topic_summaries.py``) hold its gist. Recall ranks
them on evidence like every other memory -- against the question's background
among the summaries, with thresholds measured on the live model's summaries
and locked by the benchmark (``tests/test_memory_benchmark.py``, dataset v3:
"overview" questions, and no-answer questions that must stay clean).

Held here, each with a negative control: only an ACTIVE summary is recalled
(a forgotten one, one emptied because a turn it came from was forgotten, a
retired one: never); only the caller's tenant's; nothing when summaries are
switched off; the block shows them after the history, counted in the footer;
the Postgres-primary path reads them from the local database.
"""

from __future__ import annotations

import math
import sqlite3
import struct
import time
import zlib
from types import SimpleNamespace

import pytest

from kazma_core.memory import recall as R
from kazma_core.memory.schema_v2 import ensure_primary_schema

DIM = 1024  # buckets for the words: enough that different words rarely share one
MODEL = "fake-bow"


def _vector(text: str) -> list[float]:
    """A bag of words hashed into DIM buckets, unit length: similar words,
    similar vectors -- enough to exercise the ranking without a model."""
    vec = [0.0] * DIM
    for word in text.lower().replace(",", " ").replace(".", " ").replace("?", " ").split():
        vec[zlib.crc32(word.encode()) % DIM] += 1.0  # a bucket, not a secret: a checksum does
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class _Embedder:
    dim = DIM

    def encode(self, text: str) -> list[float]:
        return _vector(text)


TOPICS = [
    ("ShipX delivery platform phases", "ShipX phases one to nine were built and committed; the loyalty "
     "module and the Meta suite are left open for the ShipX platform."),
    *[(f"Assorted questions {i}", f"The user asked about recipes, rice, weather number {i}, trains and "
       f"football scores, and small talk about week {i}.") for i in range(24)],
]


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: _Embedder())
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: MODEL)
    R._QUERY_VECTORS.clear()
    db = sqlite3.connect(str(tmp_path / "memory_state.db"))
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    now = time.time()
    for i, (title, text) in enumerate(TOPICS):
        _summary(db, f"s{i}", title, text, at=now - (i + 2) * 7 * 86400)
    yield SimpleNamespace(db=db, dir=tmp_path)
    R._QUERY_VECTORS.clear()
    db.close()


def _summary(db, sid, title, text, *, at, tenant="default", status="active"):
    vec = _vector(f"{title}\n{text}")
    db.execute(
        "INSERT INTO memory_summaries (id, tenant_id, period_key, period_start, period_end, title, "
        "summary_text, status, turn_count, chat_count, created_at, updated_at, embedding, "
        "embedding_model_version) VALUES (?, ?, 'w', ?, ?, ?, ?, ?, 6, 1, ?, ?, ?, ?)",
        (sid, tenant, at, at + 7 * 86400, title, text, status, at, at,
         struct.pack(f"<{DIM}f", *vec), MODEL),
    )
    db.commit()


def _ids(hits):
    return [h.id for h in hits]


def test_a_question_about_a_topic_brings_its_summary(mem):
    hits = R._recall_summaries(mem.db, "Where are we with the ShipX platform phases?", "default")
    assert _ids(hits)[:1] == ["s0"]
    shown = hits[0].metadata["display"]
    assert shown.startswith("Week of ") and "ShipX delivery platform phases" in shown


def _weekly_topic(mem, weeks=14):
    """One topic summarized every week, as live held the subscription resets
    -- in the same words each week (the fake embedder hashes words, so a week
    number would make one week's vector differ by accident)."""
    now = time.time()
    for i in range(weeks):
        _summary(mem.db, f"reset{i}", "Subscription reset reminders",
                 "Grok and ZCode subscription reset times noted and reminders scheduled.",
                 at=now - (i + 1) * 7 * 86400)


def test_a_topic_summarized_every_week_stays_findable(mem):
    """Live 2026-09-27: with ten reset summaries, "list me all my
    subscription resets" found none -- they were their own background."""
    _weekly_topic(mem)
    hits = R._recall_summaries(mem.db, "List me all my subscription resets", "default")
    assert hits and all(h.id.startswith("reset") for h in hits)


def test_against_its_nearest_neighbours_the_topic_would_hide_itself(mem, monkeypatch):
    """Negative control: the episodes' background (ranks 4-15) is the topic."""
    _weekly_topic(mem)
    monkeypatch.setattr(R, "_summary_background", R._question_background)
    assert R._recall_summaries(mem.db, "List me all my subscription resets", "default") == []


def test_an_unrelated_question_brings_none(mem):
    assert R._recall_summaries(mem.db, "What is the capital of Peru?", "default") == []
    assert R._recall_summaries(mem.db, "hello", "default") == []  # small talk looks nothing up


@pytest.mark.parametrize("status", ["forgotten", "rebuild", "retired"])
def test_only_an_active_summary_is_recalled(mem, status):
    """A forgotten summary, one waiting to be written without a forgotten
    turn, a retired one: never recalled -- whatever text the row holds."""
    mem.db.execute("UPDATE memory_summaries SET status = ? WHERE id = 's0'", (status,))
    mem.db.commit()
    assert "s0" not in _ids(R._recall_summaries(mem.db, "Where are we with the ShipX platform phases?",
                                                "default"))


def test_another_tenants_summary_is_not_recalled(mem):
    _summary(mem.db, "beta_ship", "ShipX delivery platform phases", "ShipX phases for beta.",
             at=time.time() - 86400, tenant="beta")
    ids = _ids(R._recall_summaries(mem.db, "Where are we with the ShipX platform phases?", "default"))
    assert "beta_ship" not in ids and ids[:1] == ["s0"]
    assert _ids(R._recall_summaries(mem.db, "Where are we with the ShipX platform phases?", "beta")) == [
        "beta_ship"]


def test_switched_off_no_summary_is_recalled(mem, monkeypatch):
    monkeypatch.setattr(R, "_summaries_enabled", lambda: False)
    assert R._recall_summaries(mem.db, "Where are we with the ShipX platform phases?", "default") == []


def test_recall_returns_them_and_the_block_shows_them_after_the_history(mem):
    result = R.recall("Where are we with the ShipX platform phases?", conn=mem.db, tenant_id="default",
                      local_only=True)
    assert _ids(result.summaries)[:1] == ["s0"] and not result.empty
    hit = R.RecallHit(id="e1", content="x", score=1.0, metadata={"display": "User: ShipX? / Assistant: Phase 9."})
    block = R.format_recall_block(R.RecallResult(beliefs=[], episodes=[hit], summaries=result.summaries),
                                  explain=False)
    assert block.index("## Relevant History") < block.index("## Weekly Summaries")
    assert "weekly summar" in block.split("## Weekly Summaries", 1)[1]  # counted in the footer
    only = R.format_recall_block(R.RecallResult(beliefs=[], episodes=[], summaries=result.summaries),
                                 explain=False)
    assert "## Weekly Summaries" in only and "Week of " in only


def test_without_summaries_the_block_is_as_before(mem):
    """Negative control for the section: no summaries, no section."""
    hit = R.RecallHit(id="e1", content="x", score=1.0, metadata={"display": "User: q / Assistant: a"})
    block = R.format_recall_block(R.RecallResult(beliefs=[], episodes=[hit]), explain=False)
    assert "## Weekly Summaries" not in block and "## Relevant History" in block


def test_the_postgres_primary_path_reads_them_from_the_local_database(mem, monkeypatch):
    """Summaries are local only: the primary role's recall still has them."""
    from kazma_core.memory import state_backend

    monkeypatch.setattr(state_backend, "is_state_primary", lambda cfg=None: True)
    monkeypatch.setattr(state_backend, "get_state_backend",
                        lambda: SimpleNamespace(name="pg", available=True))
    monkeypatch.setattr(R, "_pg_primary_episodes", lambda *a, **k: [])
    monkeypatch.setattr(R, "_pg_primary_beliefs", lambda *a, **k: [])
    result = R.recall("Where are we with the ShipX platform phases?", tenant_id="default")
    assert _ids(result.summaries)[:1] == ["s0"]


def test_the_turns_memory_panel_names_a_weekly_summary(mem):
    """What the chat's "memory used this turn" panel receives: a turn that
    recalled only a summary is not "no memory" (live 2026-09-27: the panel
    listed facts, turns and KB, and would have said nothing was used)."""
    hits = R._recall_summaries(mem.db, "Where are we with the ShipX platform phases?", "default")
    payload = R.build_memory_explain_payload(
        query="Where are we with ShipX?", result=R.RecallResult(beliefs=[], episodes=[], summaries=hits),
        explain=True,
    )
    assert payload["empty"] is False and payload["summary"]["weekly_summaries"] == len(hits) >= 1
    assert payload["weekly_summaries"][0]["content"].startswith("Week of ")


def test_the_chat_panel_draws_a_row_for_each_weekly_summary():
    """The browser half: chat.js paints the payload's weekly summaries (a
    row each, after the history) and counts them in the panel's line."""
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "kazma-ui" / "kazma_ui" / "static" / "js" / "chat.js").read_text(
        encoding="utf-8")
    panel = js.split("function applyMemoryExplain(data)", 1)[1].split("\n  function ", 1)[0]
    assert "(data.weekly_summaries || []).forEach(function(h) { row('weekly', h); });" in panel
    assert panel.index("row('episode', h)") < panel.index("row('weekly', h)")
    assert "sum.weekly_summaries" in panel and "is-weekly" in panel


def test_a_database_from_before_summaries_recalls_none(tmp_path):
    db = sqlite3.connect(str(tmp_path / "old.db"))
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE episodes (id TEXT)")
    assert R._recall_summaries(db, "Where are we with ShipX?", "default") == []
    db.close()
