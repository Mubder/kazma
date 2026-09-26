"""Every conversation turn reaches long-term memory -- reconciled from the chat store.

The chat store (Postgres ``kazma_chat_sessions`` or SQLite ``chat_sessions.db``)
holds every conversation Kazma has had. Long-term memory holds one episode per
turn, written when the turn closes (``kazma_ui.turn_runtime.close_turn`` ->
``consolidator.remember_turn``). When that write does not happen, the turn is
missing from memory for good: from 2026-08-08 to 2026-09-26 no web chat turn
was written at all (the web never called it; 877 turns on the live install),
and 127 gateway turns were lost to a full extraction pool, a crash between the
reply and the write, or an id collision.

This pass makes memory converge on the chat store. Every 15 minutes it reads
the sessions changed since its cursor and writes an episode for each turn that
has none, stamped with the turn's own time (``dual_write.mirror_episode``, the
one episode writer). A turn younger than :data:`_SETTLE_S` is left to the live
pipeline. It writes EPISODES only: facts are not re-extracted from old turns,
because the functional-supersede rule orders facts by when they are ingested,
so replaying an old "I live in X" would overwrite a newer "I moved to Y". The
episodes are searchable by words and meaning like any other.

A turn is already in memory when an episode of the same conversation has the
same question -- counted, so a question asked twice needs two -- or when an
erased row's stub starts with it (``rehydrate`` owns those).
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from kazma_core.memory.chat_history import STORE_ERRORS

logger = logging.getLogger(__name__)

__all__ = ["STATE_KEY", "run_turn_reconcile_pass"]

#: ConfigStore key: the cursor ``(updated_at, session_id)`` and the last report.
STATE_KEY = "memory.v2.turn_reconcile"

#: A turn younger than this belongs to the live pipeline (close_turn hands it
#: to memory within seconds); reconciling it too would race that write.
_SETTLE_S = 600.0
_PAGE = 100
_TEXT_CAP = 4000


def _epoch(value: Any) -> float | None:
    """Epoch seconds of the chat store's ISO timestamps; None when absent."""
    if not value:
        return None
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when.timestamp()


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def _turns(messages: list[Any]) -> list[tuple[int, str, str, float | None]]:
    """``(turn index, question, answer, when)`` of every turn with a question:
    the answer AFTER it (``consolidator.extract_turn_texts``), the index as
    ``consolidator.user_turn_index`` counts it. Slash commands are skipped,
    as the live pipeline skips them."""
    from kazma_core.memory.chat_history import normalize_message
    from kazma_core.memory.consolidator import extract_turn_texts, message_text

    msgs = [normalize_message(m) for m in messages or []]
    out: list[tuple[int, str, str, float | None]] = []
    index = 0
    last_question = ""
    when: float | None = None
    for end in range(1, len(msgs) + 1):
        msg = msgs[end - 1]
        if msg.get("role") == "user":
            index += 1
            last_question = message_text(msg.get("content"))
            when = _epoch(msg.get("ts"))
        closes = end == len(msgs) or msgs[end].get("role") == "user"
        if not (index and closes and last_question):
            continue  # no question yet, mid-turn, or an attachment-only message
        question, answer = extract_turn_texts(msgs[:end])
        if question and not question.startswith("/"):
            out.append((index, question, answer, when))
    return out


def _in_memory(conn: sqlite3.Connection, tenant: str, keys: list[str]) -> tuple[Counter, list[str]]:
    """The conversation's episode questions (counted) and erased-row stubs."""
    marks = ",".join("?" for _ in keys)
    texts: Counter = Counter()
    stubs: list[str] = []
    for user, summary in conn.execute(
        f"SELECT user_text, summary_text FROM episodes WHERE tenant_id = ? AND session_id IN ({marks})",
        [tenant, *keys],
    ):
        if user is not None:
            texts[str(user).strip()] += 1
        elif summary:
            stubs.append(str(summary))
    return texts, stubs


def _reconcile_session(
    conn: sqlite3.Connection, row: dict[str, Any], settled_before: float
) -> tuple[int, int]:
    """Write the session's missing settled turns. Returns (written, still young)."""
    from kazma_core.memory.dual_write import mirror_episode

    thread = row["thread_id"] or row["session_id"]
    keys = list(dict.fromkeys(k for k in (thread, row["session_id"]) if k))
    texts, stubs = _in_memory(conn, row["tenant_id"], keys)
    fallback = _epoch(row["updated_at"]) or _epoch(row["created_at"])
    written = young = 0
    for index, question, answer, when in _turns(row["messages"]):
        stored = question[:_TEXT_CAP].strip()
        if texts[stored] > 0:
            texts[stored] -= 1
            continue
        head = question[:200].strip(" ")
        stub = next((s for s in stubs if s.startswith(head)), None)
        if stub is not None:
            stubs.remove(stub)
            continue
        at = when if when is not None else fallback
        if at is None:
            at = settled_before - 1.0  # a legacy row with no time at all: settled
        if at >= settled_before:
            young += 1  # the live pipeline's to write; checked again next pass
            continue
        if mirror_episode(
            session_id=thread,
            turn_number=index,
            user_text=question[:_TEXT_CAP],
            assistant_text=answer[:_TEXT_CAP],
            tenant_id=row["tenant_id"],
            tier="episodic",
            source="turn_reconcile",
            created_at=at,
        ):
            written += 1
    return written, young


def _reconcile(
    conn: sqlite3.Connection, after: tuple[str, str], deadline: float, now: float
) -> dict[str, Any]:
    from kazma_core.memory.chat_history import sessions_changed_since

    settled_before = now - _SETTLE_S
    settled_iso = _iso(settled_before)
    report: dict[str, Any] = {"sessions": 0, "turns_written": 0, "turns_waiting": 0,
                              "done": False}
    cursor = scan = after
    frozen = False  # an unsettled session was met: the cursor stays before it
    while time.monotonic() < deadline:
        rows = sessions_changed_since(scan, limit=_PAGE)
        if not rows:
            report["done"] = True
            break
        for row in rows:
            if time.monotonic() >= deadline:
                break
            written, young = _reconcile_session(conn, row, settled_before)
            report["sessions"] += 1
            report["turns_written"] += written
            report["turns_waiting"] += young
            scan = (row["updated_at"], row["session_id"])
            # The cursor passes only a run of settled sessions: one still
            # changing (or with a turn too young) is read again next pass.
            if young or row["updated_at"] >= settled_iso:
                frozen = True
            elif not frozen:
                cursor = scan
    report["after"] = list(cursor)
    return report


def run_turn_reconcile_pass(*, time_budget_s: float = 60.0) -> dict[str, Any]:
    """One reconcile pass (15-minute cadence); the cursor and report are kept
    under :data:`STATE_KEY`. Returns the report (``{}`` without a database)."""
    from kazma_core.config_store import apply_sqlite_pragmas, get_config_store
    from kazma_core.paths import primary_memory_db

    db = primary_memory_db()
    if not db or not os.path.isfile(db):
        return {}
    store = get_config_store()
    state = store.get(STATE_KEY)
    state = state if isinstance(state, dict) else {}
    after = tuple(state.get("after") or ("", ""))
    started = time.monotonic()
    conn = sqlite3.connect(db, timeout=15)
    try:
        apply_sqlite_pragmas(conn, busy_timeout=15000)
        report = _reconcile(conn, after, started + max(0.0, float(time_budget_s)), time.time())
    except STORE_ERRORS:
        logger.warning("[memory] conversation history unreadable; turn reconcile waits", exc_info=True)
        return {"error": "chat store unreadable"}
    finally:
        conn.close()
    report["duration_s"] = round(time.monotonic() - started, 3)
    if report["turns_written"] or list(after) != report["after"]:
        store.set(STATE_KEY, {"after": report["after"], "last": {**report, "at": time.time()}},
                  category="memory")
    if report["turns_written"]:
        logger.info(
            "[memory] %d conversation turns from %d sessions added to memory from the chat "
            "history%s", report["turns_written"], report["sessions"],
            "" if report["done"] else " (more next pass)",
        )
    return report
