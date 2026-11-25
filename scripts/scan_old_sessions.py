#!/usr/bin/env python3
"""Retroactive session scanner — extract V2 beliefs from old chat sessions.

Scans every stored chat (the store in use: Postgres or ``chat_sessions.db``)
and Time Travel's saved steps, and runs the V2 belief extraction
pipeline (heuristic + optional LLM) on each user/assistant pair. This recovers
important facts (reminders, preferences, identity, project info) from
conversations that happened before the V2 memory system was deployed or before
the episode recall bug was fixed.

A chat is scanned in its own space (Spaces), under the person who asked:
a shared space files a fact under its speaker, and a first-person fact whose
speaker is not known is not filed. It used to file every space's chats under
one ``--tenant-id``, the owner's when none was named.

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

    # Scan one space only (default: every space)
    python scripts/scan_old_sessions.py --tenant-id sp1234

    # Scan only sessions newer than a date
    python scripts/scan_old_sessions.py --since "2026-07-01"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
from collections import deque
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def resolve_snapshots_db() -> str:
    """Find the snapshots.db path."""
    try:
        from kazma_core.paths import data_dir

        return str(data_dir() / "snapshots.db")
    except Exception:
        return "kazma-data/snapshots.db"


def resolve_memory_dbs() -> tuple[str, str]:
    """Return (primary_memory_db, memory_ops_db) paths."""
    try:
        from kazma_core.paths import memory_ops_db, primary_memory_db

        return primary_memory_db(), memory_ops_db()
    except Exception:
        return "kazma-data/memory_state.db", "kazma-data/memory_ops.db"


def load_snapshots_sessions(
    db_path: str, *, max_sessions: int | None = None, since: str | None = None,
    tenant_id: str | None = None,
) -> list[dict[str, Any]]:
    """Load latest snapshot state for each thread from snapshots.db, with the
    space the thread's record names (a thread with none is from before
    Spaces: its state's, else the owner's)."""
    from kazma_core.spaces.scope import space_of_record
    from kazma_core.spaces.threads import record_of

    if not Path(db_path).exists():
        logger.info("Snapshots DB not found: %s", db_path)
        return []

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='snapshots'"
        ).fetchall()
        if not tables:
            logger.info("No 'snapshots' table found in %s", db_path)
            return []

        query = """
            SELECT s1.thread_id, s1.state_json, s1.timestamp
            FROM snapshots s1
            WHERE s1.timestamp = (
                SELECT MAX(s2.timestamp) FROM snapshots s2 WHERE s2.thread_id = s1.thread_id
            )
        """
        params: list[Any] = []
        if since:
            query += " AND s1.timestamp >= ?"
            params.append(since)

        query += " ORDER BY s1.timestamp DESC"
        rows = conn.execute(query, params).fetchall()
        sessions = []
        for r in rows:
            try:
                st = json.loads(r["state_json"] or "{}")
                messages = st.get("messages", [])
            except (json.JSONDecodeError, TypeError, AttributeError):
                st, messages = {}, []
            record = record_of(r["thread_id"])
            space = record.space_id if record is not None else space_of_record(st.get("tenant_id"))
            if tenant_id not in (None, space):
                continue
            if max_sessions and len(sessions) >= max_sessions:
                break

            sessions.append({
                "tenant_id": space,
                "session_id": r["thread_id"],
                "thread_id": r["thread_id"],
                "messages": messages,
                "created_at": r["timestamp"],
                "updated_at": r["timestamp"],
                "title": f"Snapshot Thread {r['thread_id'][:8]}",
            })
        return sessions
    finally:
        conn.close()


def load_sessions(
    db_path: str | None = None, *, max_sessions: int | None = None, since: str | None = None,
    tenant_id: str | None = None,
) -> list[dict[str, Any]]:
    """The chats of the store in use (Postgres, or ``chat_sessions.db``; a save
    the store refused counts), each with its space; *db_path* reads that
    SQLite file instead. Newest first."""
    from kazma_core.memory.chat_history import sessions_changed_since

    sessions: deque[dict[str, Any]] = deque(maxlen=max_sessions or None)  # the newest: they come oldest first
    after = (since or "", "")
    while page := sessions_changed_since(after, limit=200, sqlite_path=Path(db_path) if db_path else None):
        after = (page[-1]["updated_at"], page[-1]["session_id"])
        sessions.extend(
            {**row, "thread_id": row["thread_id"] or row["session_id"], "title": ""}
            for row in page
            if tenant_id in (None, row["tenant_id"])
        )
    return list(reversed(sessions))


def extract_turn_pairs(messages: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Extract (user_text, assistant_text) pairs from a message list."""
    return [(user, answer) for user, answer, _author in extract_turns(messages)]


def extract_turns(messages: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """Extract (user_text, assistant_text, author) from a message list: the
    account that asked, as the chat store recorded it (Spaces S1-08), or "".

    Groups consecutive user→assistant turns. Skips slash commands and
    system/tool messages.
    """
    turns: list[tuple[str, str, str]] = []
    user_text = author = ""
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
            author = str(msg.get("author") or "")
        elif role == "assistant" and user_text:
            turns.append((user_text, content, author))
            user_text = author = ""
    return turns


def turns_said_here(session: dict[str, Any], copied: dict[str, float]) -> list[tuple[int, str, str, str]]:
    """``(turn number, user text, assistant text, author)`` of the session's
    turns. A chat copied here from another space (Spaces S2-12; *copied*:
    ``threads.copied_threads``) gives only the turns asked since, as turn
    reconcile does: the rest was said there."""
    from kazma_core.memory.chat_history import said_since

    every = extract_turns(session["messages"])
    since = copied.get(str(session.get("thread_id") or session["session_id"]))
    turns = every if since is None else extract_turns(said_since(session["messages"], since))
    return [(number, *turn) for number, turn in enumerate(turns, start=1 + len(every) - len(turns))]


def run_heuristic_extraction(
    primary_conn: sqlite3.Connection,
    ops_conn: sqlite3.Connection,
    user_text: str,
    assistant_text: str,
    *,
    session_id: str,
    turn: int,
    tenant_id: str,
    said_by: str | None = None,
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
            said_by=said_by,
            extraction_method="retroactive_scan",
        )
        return stats.get("applied", 0)
    except Exception as e:
        logger.debug("Heuristic extraction failed for %s turn %d: %s", session_id, turn, e)
        return 0


async def run_llm_extraction(
    primary_conn: sqlite3.Connection,
    ops_conn: sqlite3.Connection,
    user_text: str,
    assistant_text: str,
    *,
    session_id: str,
    turn: int,
    tenant_id: str,
    said_by: str | None = None,
    ignore_filler: bool = False,
) -> int:
    """Run LLM belief extraction on one turn. Returns count of beliefs extracted."""
    try:
        from kazma_core.memory.belief_extractor import extract_and_apply_beliefs

        stats = await extract_and_apply_beliefs(
            primary_conn,
            ops_conn,
            user_text,
            assistant_text,
            session_id=session_id,
            turn=turn,
            tenant_id=tenant_id,
            said_by=said_by,
            extraction_method="retroactive_scan",
            use_llm=True,
            ignore_filler=ignore_filler,
        )
        return stats.get("applied", 0)
    except Exception as e:
        logger.warning("LLM extraction failed for %s turn %d: %s", session_id, turn, e)
        return 0


def _binding(space: str, author: str) -> Any:
    """What a turn's extraction runs as: its own space, for its own person,
    by the one rule the live extraction has (``task_queue``). ``dropped`` or
    ``wait`` when it does not run: the person left the space, is disabled, or
    is past their daily limit."""
    from kazma_core.memory.task_queue import TaskBinding, _persons_binding
    from kazma_core.spaces.scope import spaces_enforced

    if not spaces_enforced():
        return TaskBinding(space=space)
    return _persons_binding("micro_consolidation", space, author)


async def scan_session(
    primary_conn: sqlite3.Connection,
    ops_conn: sqlite3.Connection,
    session: dict[str, Any],
    *,
    use_llm: bool = False,
    ignore_filler: bool = False,
) -> dict[str, int]:
    """Scan one session, in its own space. Returns stats dict."""
    from kazma_core.memory.forget import chat_keys, refuses_write
    from kazma_core.memory.task_queue import _task_space
    from kazma_core.spaces.scope import space_of_record
    from kazma_core.spaces.threads import copied_threads

    space = space_of_record(session.get("tenant_id"))
    turns = turns_said_here(session, copied_threads())
    if not turns:
        return {"turns": 0, "beliefs": 0}

    total_beliefs = 0
    keys: list[str] | None = None
    for turn_idx, user_text, assistant_text, author in turns:
        binding = _binding(space, author)
        if binding.dropped or binding.wait:
            logger.info("  a turn of %s is left out: %s", session["session_id"][:12], binding.dropped or binding.wait)
            continue
        with _task_space(binding):
            keys = keys if keys is not None else chat_keys(session["session_id"])
            # A turn the user forgot, or a chat they keep out of memory, gives
            # no facts -- the snapshots and transcripts still hold the words
            # (Memory audit M08; Time Travel TT-05).
            if refuses_write(primary_conn, tenant_id=space, session_id=session["session_id"],
                             turn_number=turn_idx, user_text=user_text, keys=keys):
                continue
            # Skip already-scanned turns that actually produced beliefs
            # (idempotency), in this space: the same id elsewhere is another's.
            existing = primary_conn.execute(
                "SELECT COUNT(*) FROM beliefs WHERE tenant_id=? AND source_session=? AND source_turn=? "
                "AND extraction_method='retroactive_scan'",
                (space, session["session_id"], turn_idx),
            ).fetchone()[0]
            if existing > 0:
                continue
            if use_llm:
                total_beliefs += await run_llm_extraction(
                    primary_conn, ops_conn, user_text, assistant_text,
                    session_id=session["session_id"], turn=turn_idx, tenant_id=space,
                    said_by=author or None, ignore_filler=ignore_filler,
                )
            else:
                total_beliefs += run_heuristic_extraction(
                    primary_conn, ops_conn, user_text, assistant_text,
                    session_id=session["session_id"], turn=turn_idx, tenant_id=space,
                    said_by=author or None,
                )

    return {"turns": len(turns), "beliefs": total_beliefs}


async def main_async() -> None:
    parser = argparse.ArgumentParser(description="Retroactive V2 belief scanner")
    parser.add_argument("--dry-run", action="store_true", help="Report sessions without extracting")
    parser.add_argument("--use-llm", action="store_true", help="Run LLM deep-pass (slower)")
    parser.add_argument("--max-sessions", type=int, default=None, help="Limit to N sessions")
    parser.add_argument("--since", type=str, default=None, help="Only scan sessions updated after this date (ISO format)")
    parser.add_argument("--tenant-id", type=str, default=None, help="Scan this space only (default: every space)")
    parser.add_argument("--ignore-filler", action="store_true", help="Do not skip short/filler turns during extraction")
    parser.add_argument(
        "--source",
        choices=["all", "chat_sessions", "snapshots"],
        default="all",
        help="Source database to scan (default: all)",
    )
    args = parser.parse_args()

    # Whether Spaces is on decides whose a fact is, so it is read first; a
    # settings store that cannot say stops the scan (unknown is never "off").
    from kazma_core.spaces import identity

    identity.enforcement_enabled()
    identity.prime_enforcement()

    primary_db, ops_db = resolve_memory_dbs()
    logger.info("Memory DB: %s", primary_db)

    if args.use_llm:
        try:
            from kazma_core.model_registry import get_model_registry

            client = get_model_registry().get_client()
            if client:
                model_name = getattr(client, "model", None) or getattr(client, "model_id", "unknown")
                provider_name = getattr(client, "provider_name", None) or type(client).__name__
                logger.info("LLM extraction mode active: model=%s provider=%s", model_name, provider_name)
            else:
                logger.warning(
                    "⚠️ --use-llm requested, but get_model_registry().get_client() returned None! "
                    "Ensure LLM provider credentials (e.g. OPENAI_API_KEY, ANTHROPIC_API_KEY, or settings) are set."
                )
        except Exception as exc:
            logger.warning("⚠️ Error checking ModelRegistry LLM client: %s", exc)

    sessions: list[dict[str, Any]] = []

    if args.source in ("all", "chat_sessions"):
        cs_sessions = load_sessions(max_sessions=args.max_sessions, since=args.since, tenant_id=args.tenant_id)
        logger.info("Loaded %d sessions from the chat store", len(cs_sessions))
        sessions.extend(cs_sessions)

    if args.source in ("all", "snapshots"):
        snapshots_db = resolve_snapshots_db()
        logger.info("Checking Snapshots DB: %s", snapshots_db)
        if Path(snapshots_db).exists():
            sn_sessions = load_snapshots_sessions(
                snapshots_db, max_sessions=args.max_sessions, since=args.since, tenant_id=args.tenant_id)
            logger.info("  Loaded %d threads from snapshots.db", len(sn_sessions))
            sessions.extend(sn_sessions)

    logger.info("Total sessions/threads found to scan: %d", len(sessions))

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
            stats = await scan_session(
                primary_conn, ops_conn, s,
                use_llm=args.use_llm,
                ignore_filler=args.ignore_filler,
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


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    # An entry point loads the install's .env itself (since 2026-09-22
    # importing kazma_core does not): without it this ran against the
    # stale SQLite settings and no vault key.
    from kazma_core.env_files import load_env_files

    load_env_files()
    main()
