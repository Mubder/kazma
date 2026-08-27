"""Transcript recall fallback — search past chat sessions when memory is empty.

Born from the 2026-08-27 "green names" incident: facts living only in old
chat transcripts are invisible to V2 recall, so the supervisor hand-wrote SQL
against chat_sessions.db for 21 iterations. These tests pin the searcher, the
kill-switch, the prompt-fenced block, and the supervisor wiring.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kazma_core.memory.transcript_recall import (
    format_transcript_block,
    search_transcripts,
    transcript_fallback_enabled,
)

_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    tenant_id TEXT,
    session_id TEXT,
    messages TEXT,
    created_at TEXT,
    total_cost REAL,
    total_tokens INTEGER,
    thread_id TEXT,
    updated_at TEXT DEFAULT '',
    title TEXT DEFAULT '',
    archived INTEGER DEFAULT 0,
    pinned INTEGER DEFAULT 0,
    PRIMARY KEY (tenant_id, session_id)
);
"""


@pytest.fixture()
def sessions_db(tmp_path: Path) -> Path:
    db = tmp_path / "chat_sessions.db"
    conn = sqlite3.connect(db)
    conn.executescript(_DDL)
    rows = [
        # The "green names" session the incident was about.
        ("default", "s-green", "thread-green", "HypertFit green names",
         '[{"role":"user","content":"list the green names for HypertFit"},{"role":"assistant","content":"Green (4/4): aiform.ai, aiphysique.ai, aistrength.ai, workoutai.ai — 3/4: burnai, aivital"}]',
         "2026-08-20T10:00:00"),
        # Decoy: matches a term but not the intent.
        ("default", "s-decoy", "thread-decoy", "workout notes",
         '[{"role":"user","content":"general workoutai routine tips"}]',
         "2026-08-25T09:00:00"),
        # Arabic transcript.
        ("default", "s-ar", "thread-ar", "أسماء التطبيق",
         '[{"role":"user","content":"ما هي الأسماء الخضراء المتاحة لكاظمه"},{"role":"assistant","content":"الأسماء الخضراء: كوثرفت، سخرقت"}]',
         "2026-08-26T12:00:00"),
        # Different tenant — must never leak.
        ("other", "s-other", "thread-other", "HypertFit green names",
         '[{"role":"user","content":"green names here"}]',
         "2026-08-27T08:00:00"),
    ]
    for tenant, sid, tid, title, messages, created in rows:
        conn.execute(
            "INSERT INTO sessions (tenant_id, session_id, thread_id, title, messages, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (tenant, sid, tid, title, messages, created),
        )
    conn.commit()
    conn.close()
    return db


def test_finds_green_names_session_and_ranks_title_match_first(sessions_db: Path) -> None:
    hits = search_transcripts(
        "list me every available green name for my HypertFit app",
        db_path=sessions_db,
    )
    assert hits, "expected at least one hit"
    top = hits[0]
    assert top["session_id"] == "s-green"
    assert "HypertFit" in top["matched"] or "green" in top["matched"]
    assert "aiform.ai" in top["snippet"]
    # decoy ranks below the title-matching session
    ids = [h["session_id"] for h in hits]
    if "s-decoy" in ids:
        assert ids.index("s-decoy") > ids.index("s-green")


def test_arabic_terms_match(sessions_db: Path) -> None:
    hits = search_transcripts("الأسماء الخضراء المتاحة", db_path=sessions_db)
    assert hits and hits[0]["session_id"] == "s-ar"


def test_excludes_current_session_and_missing_db(tmp_path: Path, sessions_db: Path) -> None:
    hits = search_transcripts(
        "HypertFit green names", db_path=sessions_db, exclude_session_id="s-green"
    )
    assert all(h["session_id"] != "s-green" for h in hits)
    # Missing store → quiet no-op
    assert search_transcripts("anything", db_path=tmp_path / "nope.db") == []


def test_no_hits_for_unrelated_query(sessions_db: Path) -> None:
    assert search_transcripts("zebra quantum umbrella", db_path=sessions_db) == []


def test_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_TRANSCRIPT_RECALL", "0")
    assert transcript_fallback_enabled() is False
    monkeypatch.setenv("KAZMA_TRANSCRIPT_RECALL", "1")
    assert transcript_fallback_enabled() is True
    monkeypatch.delenv("KAZMA_TRANSCRIPT_RECALL")
    assert transcript_fallback_enabled() is True  # default on


def test_block_is_prompt_fenced() -> None:
    block = format_transcript_block(
        [{"title": "HypertFit green names", "created_at": "2026-08-20",
          "matched": ["HypertFit"], "snippet": "Green (4/4): aiform.ai"}]
    )
    assert "kazma:data" in block and "untrusted" in block
    assert "chat_history" in block
    assert "aiform.ai" in block
    assert format_transcript_block([]) == ""


def test_supervisor_wires_the_fallback() -> None:
    src = (
        Path(__file__).resolve().parents[1]
        / "kazma-core" / "kazma_core" / "agent" / "graph_supervisor.py"
    ).read_text(encoding="utf-8")
    assert "from kazma_core.memory.transcript_recall import" in src
    assert "transcript_fallback_enabled()" in src
    assert "format_transcript_block(_tx_hits)" in src
    # Only in the recall-empty branch, and suppressed turns skip it too.
    assert "elif not _suppress_recall:" in src
