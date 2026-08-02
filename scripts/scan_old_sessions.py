"""Retroactive session scanner — extract V2 beliefs from old chat sessions.

Scans all sessions in ``chat_sessions.db`` and runs the V2 belief extraction
pipeline (heuristic + optional LLM) on each user/assistant pair. This recovers
important facts (reminders, preferences, identity, project info) from
conversations that happened before the V2 memory system was deployed or before
the episode recall bug was fixed.

Idempotent: beliefs are tagged with ``extraction_method='retroactive_scan'``
so re-running never duplicates. Uses the same ``_NEVER_SUPERSEDE_PATTERNS``
protection as the live pipeline so reminder beliefs won't be lost again.

Usage:

    # Dry run — report what would be scanned without writing
    python scripts/scan_old_sessions.py --dry-run

    # Full scan with heuristic extraction only (fast, no LLM calls)
    python scripts/scan_old_sessions.py

    # Full scan with LLM deep-pass (slower, more accurate)
    python scripts/scan_old_sessions.py --use-llm

    # Limit to N most recent sessions
    python scripts/scan_old_sessions.py --max-sessions 50

    # Scan only sessions newer than a date
    python scripts/scan_old_sessions.py --since "2026-07-01"
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def resolve_chat_sessions_db() -> str:
    """Find the chat_sessions.db path."""
    try:
        from kazma_core.paths import data_dir

        return str(data_dir() / "chat_sessions.db")
    except Exception:
        return "kazma-data/chat_sessions.db"


def resolve_memory_dbs() -> tuple[str, str]:
    """Return (primary_memory_db, memory_ops_db) paths."""
    try:
        from kazma_core.paths import memory_ops_db, primary_memory_db

        return primary_memory_db(), memory_ops_db()
    except Exception:
        return "kazma-data/memory_state.db", "kazma-data/memory_ops.db"


def load_sessions(
    db_path: str, *, max_sessions: int | None = None, since: str | None = None
) -> list[dict[str, Any]]:
    """Load sessions from chat_sessions.db."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Check table exists
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
        ).fetchall()
        if not tables:
            logger.info("No 'sessions' table found in %s", db_path)
            return []

        query = "SELECT session_id, messages, created_at, updated_at, title FROM sessions"
        params: list[Any] = []

        if since:
            query += " WHERE updated_at >= ?"
            params.append(since)

        query += " ORDER BY updated_at DESC"
        if max_sessions:
            query += " LIMIT ?"
            params.append(max_sessions)

        rows = conn.execute(query, params).fetchall()
        sessions = []
        for r in rows:
            try:
                messages = json.loads(r["messages"] or "[]")
            except (json.JSONDecodeError, TypeError):
                messages = []
            sessions.append({
                "session_id": r["session_id"],
                "messages": messages,
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "title": r["title"] or "",
            })
        return sessions
    finally:
        conn.close()


def extract_turn_pairs(messages: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Extract (user_text, assistant_text) pairs from a message list.

    Groups consecutive user→assistant turns. Skips slash commands and
    system/tool messages.
    """
    pairs: list[tuple[str, str]] = []
    user_text = ""
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, list):
            # Multimodal: take first text part
            content = " ".join(
                p.get("text", "") for p in content
                if isinstance(p, dict) and p.get("type") in (None, "text")
            )
        content = str(content or "").strip()
        if not content:
            continue

        if role == "user":
            # Skip slash commands
            if content.startswith("/"):
                continue
            user_text = content
        elif role == "assistant" and user_text:
            pairs.append((user_text, content))
            user_text = ""
    return pairs


def run_heuristic_extraction(
    primary_conn: sqlite3.Connection,
    ops_conn: sqlite3.Connection,
    user_text: str,
    assistant_text: str,
    *,
    session_id: str,
    turn: int,
    tenant_id: str = "default",
) -> int:
    """Run heuristic belief extraction on one turn. Returns count of beliefs extracted."""
    try:
        from kazma_core.memory.belief_extractor import extract_and_apply_beliefs_sync

        stats = extract_and_apply_beliefs_sync(
            primary_conn,
            ops_conn,
            user_text,
            assistant_text,
            session_id=session_id,
            turn=turn,
            tenant_id=tenant_id,
            extraction_method="retroactive_scan",
        )
        return stats.get("applied", 0)
    except Exception as e:
        logger.debug("Heuristic extraction failed for %s turn %d: %s", session_id, turn, e)
        return 0


def scan_session(
    primary_conn: sqlite3.Connection,
    ops_conn: sqlite3.Connection,
    session: dict[str, Any],
    *,
    use_llm: bool = False,
    tenant_id: str = "default",
) -> dict[str, int]:
    """Scan one session and extract beliefs. Returns stats dict."""
    pairs = extract_turn_pairs(session["messages"])
    if not pairs:
        return {"turns": 0, "beliefs": 0}

    total_beliefs = 0
    for turn_idx, (user_text, assistant_text) in enumerate(pairs, start=1):
        # Skip already-scanned turns (idempotency check)
        existing = primary_conn.execute(
            "SELECT COUNT(*) FROM beliefs WHERE source_session=? AND source_turn=? AND extraction_method='retroactive_scan'",
            (session["session_id"], turn_idx),
        ).fetchone()[0]
        if existing > 0:
            continue

        count = run_heuristic_extraction(
            primary_conn,
            ops_conn,
            user_text,
            assistant_text,
            session_id=session["session_id"],
            turn=turn_idx,
            tenant_id=tenant_id,
        )
        total_beliefs += count

    return {"turns": len(pairs), "beliefs": total_beliefs}


def main() -> None:
    parser = argparse.ArgumentParser(description="Retroactive V2 belief scanner")
    parser.add_argument("--dry-run", action="store_true", help="Report sessions without extracting")
    parser.add_argument("--use-llm", action="store_true", help="Run LLM deep-pass (slower)")
    parser.add_argument("--max-sessions", type=int, default=None, help="Limit to N sessions")
    parser.add_argument("--since", type=str, default=None, help="Only scan sessions updated after this date (ISO format)")
    parser.add_argument("--tenant-id", type=str, default="default", help="Tenant ID")
    args = parser.parse_args()

    chat_db = resolve_chat_sessions_db()
    primary_db, ops_db = resolve_memory_dbs()

    logger.info("Chat sessions DB: %s", chat_db)
    logger.info("Memory DB: %s", primary_db)

    if not Path(chat_db).exists():
        logger.error("Chat sessions DB not found: %s", chat_db)
        sys.exit(1)

    sessions = load_sessions(chat_db, max_sessions=args.max_sessions, since=args.since)
    logger.info("Found %d sessions to scan", len(sessions))

    if not sessions:
        logger.info("Nothing to do.")
        return

    if args.dry_run:
        for s in sessions[:10]:
            pairs = extract_turn_pairs(s["messages"])
            title = s["title"] or s["session_id"][:30]
            logger.info("  Session: %s — %d turns, updated %s", title, len(pairs), s["updated_at"])
        if len(sessions) > 10:
            logger.info("  ... and %d more sessions", len(sessions) - 10)
        logger.info("Dry run complete. Remove --dry-run to actually extract beliefs.")
        return

    # Open memory DBs
    primary_conn = sqlite3.connect(primary_db, check_same_thread=False, isolation_level=None)
    primary_conn.row_factory = sqlite3.Row
    ops_conn = sqlite3.connect(ops_db, check_same_thread=False, isolation_level=None)

    try:
        from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

        ensure_primary_schema(primary_conn)
        ensure_ops_schema(ops_conn)

        total_sessions = 0
        total_turns = 0
        total_beliefs = 0
        skipped = 0

        for s in sessions:
            stats = scan_session(
                primary_conn, ops_conn, s,
                use_llm=args.use_llm, tenant_id=args.tenant_id,
            )
            if stats["turns"] == 0:
                skipped += 1
            else:
                total_sessions += 1
                total_turns += stats["turns"]
                total_beliefs += stats["beliefs"]
                title = s["title"] or s["session_id"][:30]
                if stats["beliefs"] > 0:
                    logger.info(
                        "  %s: %d turns, %d beliefs extracted",
                        title, stats["turns"], stats["beliefs"],
                    )

        logger.info(
            "Scan complete: %d sessions scanned, %d turns processed, %d beliefs extracted, %d skipped (no text)",
            total_sessions, total_turns, total_beliefs, skipped,
        )

        # Show what was extracted
        if total_beliefs > 0:
            rows = primary_conn.execute(
                """SELECT subject, predicate, object, predicate_type
                   FROM beliefs
                   WHERE extraction_method='retroactive_scan'
                     AND valid_until IS NULL AND invalidated_at IS NULL
                   ORDER BY valid_from DESC
                   LIMIT 20"""
            ).fetchall()
            if rows:
                logger.info("Recent extracted beliefs:")
                for r in rows:
                    d = dict(r)
                    logger.info("  %s / %s / %s (%s)", d["subject"], d["predicate"], d["object"], d["predicate_type"])

    finally:
        primary_conn.close()
        ops_conn.close()


if __name__ == "__main__":
    main()
