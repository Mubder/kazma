"""One record per turn of what recall showed, and the quality signals read
from them (the memory framework audit's F14, part H).

Nothing recorded, per turn, what memory put in front of the model, whether
recall came back empty or could only search by keyword, how long it took, or
whether the answer used any of it; the only memory metric was the count of
operator actions. Now:

* **The record** (``memory_recall_records`` in ``memory_ops.db``, written by
  the supervisor right after the turn's recall, off the loop:
  :func:`record_recall`): the ids shown (turns, facts, summaries), never
  their text; empty; the ``degraded`` code (``RecallResult.degraded``);
  failed; the milliseconds recall took; the space and the asker. One per
  turn (``turn_key``: the thread and the turn's correlation id, as the
  answer-quality ledger keys it), kept :data:`RETENTION_DAYS` days and at
  most :data:`MAX_ROWS` rows.
* **Used** (:func:`judge_used`, at the turn's terminal answer,
  ``graph_respond``): the answer uses what was shown when it holds a shown
  fact's value, word for word after folding, or three content words of a
  shown turn or summary that the question did not already hold. A review
  signal, not a grade: an answer that rephrases a memory is missed.
* **Corrected** (read by :func:`signals`): a fact shown in a turn that the
  user replaced, or that was removed or forgotten, within
  :data:`CORRECTION_WINDOW_S` after it.
* **The week's growth and contradictions** (:func:`signals`): memories and
  facts added, and facts whose value was replaced.

The daily digest prints one line from :func:`signals`; memory health holds
them for the space (``health.build_v2_health``); the three incidents built
on the records and on the deep passes are ``memory/quality_watch.py``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from contextlib import closing
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "CORRECTION_WINDOW_S",
    "MAX_ROWS",
    "RETENTION_DAYS",
    "judge_used",
    "record_recall",
    "signals",
    "turn_key",
]

RETENTION_DAYS = 30
MAX_ROWS = 50_000
CORRECTION_WINDOW_S = 2 * 86400.0
#: Content words of a shown turn or summary the answer must hold (beyond the
#: question's own) for the turn to count as having used it.
_TURN_WORDS = 3
#: Of a shown memory, the text read to judge "used" (the start of a long one).
_TEXT_CHARS = 4000


def turn_key(thread_id: Any, messages: list[Any]) -> str | None:
    """The turn's key: its thread and its correlation id (the transports bind
    one per turn, and it survives an approval's resume), else the turn's
    number and question. None outside a thread. The answer-quality ledger's
    key (``observability/answer_quality.py``)."""
    thread = str(thread_id or "")
    if not thread:
        return None
    from kazma_core.agent.turn_input import extract_latest_user_text
    from kazma_core.memory.consolidator import user_turn_index
    from kazma_core.observability.correlation import current_turn_id

    turn = current_turn_id() or f"{user_turn_index(messages)}\x00{extract_latest_user_text(messages)}"
    return hashlib.sha256(f"{thread}\x00{turn}".encode("utf-8", "replace")).hexdigest()


def record_recall(
    key: str | None,
    *,
    tenant_id: str,
    asker: str = "",
    result: Any = None,
    latency_ms: float = 0.0,
    error: bool = False,
    now: float | None = None,
) -> None:
    """Keep what the turn's recall showed (*result*, a ``RecallResult``; None
    with *error* when recall raised). Blocking (SQLite); never raises."""
    if not key or not tenant_id:
        return
    now = time.time() if now is None else float(now)
    shown: dict[str, list[str]] = {}
    degraded, failed = "", bool(error)
    if result is not None:
        shown = {kind: [str(h.id) for h in getattr(result, kind, []) or []]
                 for kind in ("episodes", "beliefs", "summaries")}
        degraded = str(getattr(result, "degraded", "") or "")
        failed = failed or bool(getattr(result, "failed", False))
    elif error:
        degraded = "recall_failed:error"
    count = sum(len(v) for v in shown.values())
    from kazma_core.memory.quality_watch import ops_conn, recall_seen

    try:
        with closing(ops_conn()) as conn:  # type: ignore[arg-type]
            conn.execute(
                "INSERT OR IGNORE INTO memory_recall_records (turn_key, tenant_id, asker, at, shown_json, "
                "shown_count, empty, degraded, failed, latency_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (key, str(tenant_id), str(asker or ""), now, json.dumps(shown), count, int(count == 0),
                 degraded[:120], int(failed), int(max(0.0, float(latency_ms)))),
            )
            conn.execute("DELETE FROM memory_recall_records WHERE at < ?", (now - RETENTION_DAYS * 86400.0,))
            conn.execute(
                "DELETE FROM memory_recall_records WHERE turn_key IN (SELECT turn_key FROM "
                "memory_recall_records ORDER BY at DESC LIMIT -1 OFFSET ?)", (MAX_ROWS,))
            conn.commit()
            recall_seen(conn, now=now)
    except (sqlite3.Error, OSError):
        logger.warning("[recall_records] the turn's recall could not be recorded", exc_info=True)


# ── used ───────────────────────────────────────────────────────────────────


def _primary_ro() -> sqlite3.Connection | None:
    from pathlib import Path

    from kazma_core.paths import primary_memory_db

    path = Path(primary_memory_db())
    if not path.is_file():
        return None
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _shown_texts(conn: sqlite3.Connection, tenant: str, shown: dict[str, list[str]]) -> list[tuple[str, str]]:
    """``(kind, text)`` of each shown memory that is still there: its tenant's
    rows only, a forgotten one empty."""
    out: list[tuple[str, str]] = []
    for kind, table, columns in (
        ("fact", "beliefs", "object"),
        ("turn", "episodes", "user_text, assistant_text, summary_text"),
        ("summary", "memory_summaries", "title, summary_text"),
    ):
        ids = [str(i) for i in shown.get({"fact": "beliefs", "turn": "episodes",
                                          "summary": "summaries"}[kind], [])][:50]
        if not ids:
            continue
        marks = ",".join("?" for _ in ids)
        for row in conn.execute(f"SELECT {columns} FROM {table} WHERE id IN ({marks}) "  # nosec B608 - fixed tables and columns; ids bound
                                "AND tenant_id = ?", (*ids, tenant)):
            text = " ".join(str(v or "")[:_TEXT_CHARS] for v in tuple(row))
            if text.strip():
                out.append((kind, text))
    return out


def _uses(kind: str, text: str, answer: set[str], question: set[str]) -> bool:
    from kazma_core.memory.query_terms import content_terms

    terms = set(content_terms(text, digits=True))
    if kind == "fact":
        return bool(terms) and terms <= answer
    return len((terms & answer) - question) >= _TURN_WORDS


def judge_used(key: str | None, *, answer: str, question: str = "") -> bool | None:
    """Whether the turn's *answer* used what its recall showed (module
    docstring); written on the record. None when nothing was shown, the
    record is not there, or the memories cannot be read. Blocking; never
    raises."""
    if not key:
        return None
    from kazma_core.memory.quality_watch import ops_conn
    from kazma_core.memory.query_terms import content_terms

    try:
        conn = ops_conn(create=False)
        if conn is None:
            return None
        with closing(conn):
            row = conn.execute("SELECT tenant_id, shown_json, shown_count FROM memory_recall_records "
                               "WHERE turn_key = ?", (key,)).fetchone()
            if row is None or not int(row["shown_count"] or 0):
                return None
            shown = json.loads(row["shown_json"] or "{}")
            primary = _primary_ro()
            if primary is None:
                return None
            with closing(primary):
                texts = _shown_texts(primary, str(row["tenant_id"]), shown)
            answer_terms = set(content_terms(answer or "", digits=True))
            question_terms = set(content_terms(question or "", digits=True))
            used = any(_uses(kind, text, answer_terms, question_terms) for kind, text in texts)
            conn.execute("UPDATE memory_recall_records SET used = ? WHERE turn_key = ?", (int(used), key))
            conn.commit()
            return used
    except (sqlite3.Error, OSError, ValueError, TypeError):
        logger.warning("[recall_records] whether the answer used memory could not be judged", exc_info=True)
        return None


# ── signals ────────────────────────────────────────────────────────────────


def _percentile(values: list[int], share: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * share))] if ordered else 0


def _corrected(primary: sqlite3.Connection, rows: list[Any], tenant: str | None) -> int:
    """How many of *rows* showed a fact the user replaced, or that was removed
    or forgotten, within :data:`CORRECTION_WINDOW_S` after the turn."""
    from kazma_core.memory.provenance import USER_SOURCES

    shown_at: dict[str, list[float]] = {}
    for row in rows:
        for bid in (json.loads(row["shown_json"] or "{}").get("beliefs") or []):
            shown_at.setdefault(str(bid), []).append(float(row["at"]))
    if not shown_at:
        return 0
    ids = list(shown_at)
    closed: dict[str, float] = {}
    users = set(USER_SOURCES)
    for start in range(0, len(ids), 500):
        part = ids[start:start + 500]
        marks = ",".join("?" for _ in part)
        # Replaced: by the user's own words (a correction, from when they
        # said it) or by anything else (a model's reading: not one).
        replaced: set[str] = set()
        for s in primary.execute(
                f"SELECT supersedes_id, valid_from, extraction_method FROM beliefs WHERE supersedes_id IN ({marks}) "  # nosec B608 - placeholders only
                "AND (? IS NULL OR tenant_id = ?)", (*part, tenant, tenant)):
            replaced.add(str(s["supersedes_id"]))
            if str(s["extraction_method"] or "") in users:
                closed.setdefault(str(s["supersedes_id"]), float(s["valid_from"] or 0.0))
        # Removed or forgotten: closed with nothing in its place.
        for b in primary.execute(
                f"SELECT id, valid_until, invalidated_at FROM beliefs WHERE id IN ({marks}) "  # nosec B608 - placeholders only
                "AND (? IS NULL OR tenant_id = ?) AND (valid_until IS NOT NULL OR invalidated_at IS NOT NULL)",
                (*part, tenant, tenant)):
            if str(b["id"]) not in replaced:
                when = b["valid_until"] if b["valid_until"] is not None else b["invalidated_at"]
                closed[str(b["id"])] = float(when)
    turns = set()
    for row in rows:
        for bid in (json.loads(row["shown_json"] or "{}").get("beliefs") or []):
            when = closed.get(str(bid))
            if when is not None and float(row["at"]) < when <= float(row["at"]) + CORRECTION_WINDOW_S:
                turns.add(row["turn_key"])
    return len(turns)


def _growth(primary: sqlite3.Connection, tenant: str | None, since: float, until: float) -> dict[str, int]:
    def one(sql: str, *args: Any) -> int:
        return int(primary.execute(sql, (*args, tenant, tenant)).fetchone()[0] or 0)

    return {
        "memories_added": one("SELECT COUNT(*) FROM episodes WHERE created_at >= ? AND created_at < ? "
                              "AND tier != 'forgotten' AND (? IS NULL OR tenant_id = ?)", since, until),
        "facts_added": one("SELECT COUNT(*) FROM beliefs WHERE ingested_at >= ? AND ingested_at < ? "
                           "AND object != '' AND (? IS NULL OR tenant_id = ?)", since, until),
        "facts_replaced": one("SELECT COUNT(*) FROM beliefs b WHERE b.valid_until >= ? AND b.valid_until < ? "
                              "AND EXISTS (SELECT 1 FROM beliefs n WHERE n.supersedes_id = b.id) "
                              "AND (? IS NULL OR b.tenant_id = ?)", since, until),
    }


def signals(*, since: float, until: float | None = None, tenant_id: str | None = None) -> dict[str, Any]:
    """The quality signals of the turns between *since* and *until*, for one
    space (*tenant_id*) or the whole install (None: counts only, never a
    space's words). ``available`` False when the records cannot be read.
    Blocking."""
    until = time.time() if until is None else float(until)
    out: dict[str, Any] = {"available": False, "since": since, "until": until}
    from kazma_core.memory.quality_watch import ops_conn

    try:
        conn = ops_conn(create=False)
        if conn is None:
            out.update({"available": True, "turns": 0, "empty": 0, "degraded": 0, "failed": 0, "shown": 0,
                        "judged": 0, "used": 0, "latency_p50_ms": 0, "latency_p95_ms": 0,
                        "deep_passes_ok": 0, "deep_passes_failed": 0, "corrected": 0,
                        "growth": {"memories_added": 0, "facts_added": 0, "facts_replaced": 0}})
            return out
        with closing(conn):
            rows = conn.execute(
                "SELECT turn_key, at, shown_json, shown_count, empty, degraded, failed, latency_ms, used "
                "FROM memory_recall_records WHERE at >= ? AND at < ? AND (? IS NULL OR tenant_id = ?)",
                (since, until, tenant_id, tenant_id)).fetchall()
            deep = dict(conn.execute(
                "SELECT ok, COUNT(*) FROM memory_deep_passes WHERE at >= ? AND at < ? "
                "AND (? IS NULL OR tenant_id = ?) GROUP BY ok", (since, until, tenant_id, tenant_id)).fetchall())
    except (sqlite3.Error, OSError):
        logger.warning("[recall_records] the recall records could not be read", exc_info=True)
        return out
    latencies = [int(r["latency_ms"] or 0) for r in rows]
    judged = [r for r in rows if r["used"] is not None]
    out.update({
        "available": True,
        "turns": len(rows),
        "empty": sum(1 for r in rows if r["empty"]),
        "degraded": sum(1 for r in rows if r["degraded"] and not r["failed"]),
        "failed": sum(1 for r in rows if r["failed"]),
        "shown": sum(1 for r in rows if int(r["shown_count"] or 0)),
        "judged": len(judged),
        "used": sum(1 for r in judged if r["used"]),
        "latency_p50_ms": _percentile(latencies, 0.5),
        "latency_p95_ms": _percentile(latencies, 0.95),
        "deep_passes_ok": int(deep.get(1, 0)),
        "deep_passes_failed": int(deep.get(0, 0)),
        "corrected": 0,
        "growth": {"memories_added": 0, "facts_added": 0, "facts_replaced": 0},
    })
    try:
        primary = _primary_ro()
        if primary is not None:
            with closing(primary):
                out["corrected"] = _corrected(primary, rows, tenant_id)
                out["growth"] = _growth(primary, tenant_id, since, until)
    except (sqlite3.Error, OSError, ValueError):
        logger.warning("[recall_records] the memory store could not be read for the signals", exc_info=True)
    return out
