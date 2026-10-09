"""Bounded structural answer signals; no prompts, answers or tool arguments.

These are review signals, never accuracy grades. Graph terminal observations
include repaired drafts; usage commands, cancellations and paused gates are
outside this ledger. SQLite operations run off the server event loop.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)
RETENTION_DAYS = 30
MAX_ROWS = 10000
SIGNALS = (
    "empty_draft", "empty_answer", "argument_recheck", "plan_block",
    "paragraph_miss", "paragraph_repaired", "turn_failed",
)


def _terminal_text(messages: list[dict[str, Any]]) -> str:
    # Never reach back through a tool result to a previous assistant answer.
    if not messages:
        return ""
    message = messages[-1]
    if message.get("role") not in ("assistant", "ai") or message.get("tool_calls"):
        return ""
    return message.get("content") if isinstance(message.get("content"), str) else ""


def _plan_block(text: str) -> bool:
    """Only a real plan-labelled fence, not inline/quoted/nested examples."""
    fence: tuple[str, int] | None = None
    for line in text.splitlines():
        match = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if not match:
            continue
        run, suffix = match.groups()
        if fence is not None:
            if run[0] == fence[0] and len(run) >= fence[1] and not suffix.strip():
                fence = None
        else:
            if suffix.strip().lower() == "plan":
                return True
            fence = (run[0], len(run))
    return False


def observe(
    before: list[dict[str, Any]], after: list[dict[str, Any]], state: dict[str, Any],
) -> dict[str, bool]:
    """Share the formatter's narrow EN/AR predicate; do not invent intent."""
    from kazma_core.agent.answer_format import _one_paragraph, _protected, _requests_one_paragraph
    from kazma_core.agent.turn_input import extract_latest_user_text

    draft, final = _terminal_text(before), _terminal_text(after)
    failed = bool(state.get("turn_failed"))
    requested = not failed and _requests_one_paragraph(extract_latest_user_text(after))
    # Code/lists/JSON/quotes deliberately opt out of the layout safeguard.
    applicable = requested and bool(final.strip()) and not _protected(final)
    return {
        "empty_draft": bool(before and before[-1].get("role") in ("assistant", "ai")
                            and not before[-1].get("tool_calls") and not draft.strip() and not failed),
        "empty_answer": not final.strip() and not failed,
        "argument_recheck": bool(state.get("tool_argument_rechecks")),
        "plan_block": _plan_block(draft) or _plan_block(final),
        "paragraph_miss": bool(applicable and not _one_paragraph(final)),
        "paragraph_repaired": bool(applicable and draft != final and _one_paragraph(final)),
        "turn_failed": failed,
    }


def _connect() -> sqlite3.Connection:
    from kazma_core.config_store import apply_sqlite_pragmas
    from kazma_core.paths import data_dir

    path = data_dir() / "answer_quality.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=1)
    try:
        apply_sqlite_pragmas(conn)
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS answer_quality ("
            "turn_key TEXT PRIMARY KEY, ts TEXT NOT NULL, thread_id TEXT NOT NULL, turn_id TEXT NOT NULL, "
            + ", ".join(f"{signal} INTEGER NOT NULL" for signal in SIGNALS) + ");"
            "CREATE INDEX IF NOT EXISTS answer_quality_ts ON answer_quality(ts);"
        )
    except (sqlite3.Error, OSError, RuntimeError, ValueError, TypeError):
        conn.close()
        raise
    return conn


def record(
    before: list[dict[str, Any]], after: list[dict[str, Any]], state: dict[str, Any],
) -> None:
    """Idempotent per turn, best effort. Never log exception data or content."""
    try:
        from kazma_core.agent.turn_input import extract_latest_user_text
        from kazma_core.memory.consolidator import user_turn_index
        from kazma_core.observability.correlation import current_turn_id

        thread = str(state.get("thread_id") or "")
        if not thread:
            return
        # The correlation ID survives approval resumes. Fallback mirrors the
        # finished-turn identity without persisting the question or its hash.
        correlation = current_turn_id()
        turn = correlation or (
            f"{user_turn_index(after)}\x00{extract_latest_user_text(after)[:512]}"
        )
        key = hashlib.sha256(f"{thread}\x00{turn}".encode("utf-8", "replace")).hexdigest()
        flags = observe(before, after, state)
        now = datetime.now(UTC)
        with closing(_connect()) as conn, conn:
            conn.execute(
                "INSERT OR IGNORE INTO answer_quality VALUES ("
                + ",".join("?" for _ in range(4 + len(SIGNALS))) + ")",
                (key, now.isoformat(), thread, correlation or key[:16], *(int(flags[s]) for s in SIGNALS)),
            )
            conn.execute("DELETE FROM answer_quality WHERE ts < ?", ((now - timedelta(days=RETENTION_DAYS)).isoformat(),))
            conn.execute("DELETE FROM answer_quality WHERE turn_key IN "
                         "(SELECT turn_key FROM answer_quality ORDER BY ts DESC, turn_key DESC LIMIT -1 OFFSET ?)", (MAX_ROWS,))
    except (sqlite3.Error, OSError, ValueError, TypeError, AttributeError, KeyError, RuntimeError, ImportError):
        logger.warning("[AnswerQuality] Observation unavailable; reply delivery continues")


def snapshot() -> dict[str, Any]:
    """Retained 30-day window, bounded to 10,000 turns; latest 25 signals."""
    try:
        with closing(_connect()) as conn:
            conn.row_factory = sqlite3.Row
            cutoff = (datetime.now(UTC) - timedelta(days=RETENTION_DAYS)).isoformat()
            sums = ", ".join(f"COALESCE(SUM({s}),0) AS {s}" for s in SIGNALS)
            totals = dict(conn.execute(f"SELECT COUNT(*) AS turns, {sums} FROM answer_quality WHERE ts >= ?", (cutoff,)).fetchone())
            conditions = " OR ".join(f"{s}=1" for s in SIGNALS)
            rows = conn.execute(
                f"SELECT * FROM answer_quality WHERE ts >= ? AND ({conditions}) ORDER BY ts DESC, turn_key DESC LIMIT 25",
                (cutoff,),
            ).fetchall()
            return {"available": True, "retention_days": RETENTION_DAYS,
                    "max_turns": MAX_ROWS, "totals": totals,
                    "recent": [{"thread_id": r["thread_id"], "turn_id": r["turn_id"], "ts": r["ts"],
                                "signals": [s for s in SIGNALS if r[s]]} for r in rows]}
    except (sqlite3.Error, OSError, ValueError, TypeError, RuntimeError):
        return {"available": False, "retention_days": RETENTION_DAYS,
                "max_turns": MAX_ROWS, "totals": None, "recent": []}
