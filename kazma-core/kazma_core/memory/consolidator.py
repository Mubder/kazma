"""V2 post-turn memory dispatch.

This module used to host the legacy V1 consolidator pipeline (heuristic +
LLM fact/triple extraction written into the old V1 storage layer and the
SQLite property graph). The V1 storage modules it depended on (the legacy
memory adapter, the knowledge graph, and the old auto-store / async
adapter modules) have been removed, and V2 is now the always-on write
path, so the legacy functions were deleted.

What remains is the V2 post-turn entry point and its helpers:

- :func:`schedule_post_turn_memory` — the post-turn entry point (called by
  graph_builder). Spawns a dedicated OS thread that runs the V2 mirror +
  sync heuristic belief extraction, and enqueues a deferred
  ``micro_consolidation`` task for the LLM deep-pass.
- :func:`extract_turn_texts` — V2-shared helper that pulls the last
  user/assistant pair from a message list.
- :func:`_mirror_turn_to_v2` / :func:`_v2_extract_sync` — V2 helpers.
- :func:`reset_turn_counter`, :func:`_bump_turn`, :func:`_cons_block`,
  :func:`_min_chars` — config/turn-counter helpers.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

__all__ = [
    "reset_turn_counter",
    "schedule_post_turn_memory",
    "extract_turn_texts",
]

logger = logging.getLogger(__name__)

# Process-local turn counter for every_n_turns cost control
_turn_lock = threading.Lock()
_turn_counter = 0


def reset_turn_counter() -> None:
    """Test helper: reset every_n_turns counter."""
    global _turn_counter
    with _turn_lock:
        _turn_counter = 0


def _bump_turn() -> int:
    global _turn_counter
    with _turn_lock:
        _turn_counter += 1
        return _turn_counter


def _cons_block(cfg: dict[str, Any]) -> dict[str, Any]:
    block = cfg.get("consolidation")
    return dict(block) if isinstance(block, dict) else {}


def _min_chars(cfg: dict[str, Any]) -> int:
    block = _cons_block(cfg)
    try:
        return max(12, int(block.get("min_user_chars", cfg.get("consolidation_min_chars", 24))))
    except (TypeError, ValueError):
        return 24


def extract_turn_texts(messages: list[dict[str, Any]]) -> tuple[str, str]:
    """Return (last_user, last_assistant) content from the message list.

    Lifted here from the deleted ``auto_store.py`` so the V2 post-turn path
    (``_mirror_turn_to_v2`` / ``_v2_extract_sync``) doesn't depend on a V1
    module. Pure stdlib — no transitive V1 dependencies.
    """
    user = ""
    assistant = ""
    for m in reversed(messages or []):
        role = m.get("role")
        content = m.get("content")
        if not isinstance(content, str):
            # Multimodal: take first text part if present.
            if isinstance(content, list):
                parts = [
                    p.get("text", "")
                    for p in content
                    if isinstance(p, dict) and p.get("type") in (None, "text")
                ]
                content = " ".join(p for p in parts if p)
            else:
                content = ""
        content = str(content or "").strip()
        if not content:
            continue
        if role == "assistant" and not assistant:
            assistant = content
        elif role == "user" and not user:
            user = content
        if user and assistant:
            break
    return user, assistant


def schedule_post_turn_memory(
    messages: list[dict[str, Any]],
    *,
    session_id: str | None = None,
    turn: int | None = None,
) -> None:
    """Run the V2 post-turn memory pipeline in a dedicated thread.

    Two things happen for each finalized turn:

    1. The turn is mirrored into the V2 schema via the dual-write bridge
       (:func:`_mirror_turn_to_v2`) so recall can find it.
    2. Sync heuristic belief extraction runs (:func:`_v2_extract_sync`)
       to catch name/location/preference/favorite patterns instantly.

    A ``micro_consolidation`` task is also enqueued so the LLM deep-pass
    can run later on the worker's own event loop (where the httpx client
    is valid). Provenance (``session_id`` / ``turn``) is nullable —
    populated when available so V2 beliefs/episodes carry source traces.

    Args:
        messages: The finalized conversation messages for this turn.
        session_id: The active conversation/session identifier (e.g. the
            LangGraph thread_id). None when unavailable — the V2 mirror
            writes NULL provenance, never fails.
        turn: The turn number within the session. None when unavailable.
    """
    import asyncio

    # Confirm we're inside a running loop — the legacy path and the V2
    # thread both assume one exists. We don't actually use the loop here
    # anymore (V2 runs in a plain OS thread), but we preserve the
    # no-loop-early-return contract callers rely on.
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return

    def _run_v2_sync() -> None:
        """Synchronous V2 mirror + heuristic belief extraction (runs in a thread).

        Deliberately SYNC and httpx-free: this thread never touches the
        loop-bound httpx client, so it cannot raise
        ``RuntimeError: ... bound to a different event loop`` (the bug
        that poisoned the LLM connection pool and caused V2 amnesia).

        Two-stage extraction:
          1. HERE (sync, thread): heuristic extraction + mutate_belief.
             Catches name/location/preference/favorite patterns instantly.
          2. DEFERRED (queue, worker loop): a ``micro_consolidation`` task
             runs the LLM extraction on the worker's own event loop where
             the httpx client is valid, so complex/nuanced beliefs that the
             heuristic misses still get extracted — just not synchronously.
        """
        # ── V2 dual-write mirror ─────────────────────────────────────
        try:
            _mirror_turn_to_v2(messages, session_id=session_id, turn=turn)
        except Exception:
            logger.debug("[post_turn] V2 mirror failed", exc_info=True)
        # ── Stage 1: sync heuristic extraction ───────────────────────
        try:
            _v2_extract_sync(messages, session_id=session_id, turn=turn)
        except Exception:
            logger.debug("[post_turn] V2 heuristic extraction failed", exc_info=True)
        # ── Stage 2: enqueue LLM deep-pass on the worker loop ─────────
        # The micro_consolidation handler runs on the durable worker's
        # event loop (where httpx is valid), so it can safely call the LLM.
        # We point it at the episode the mirror just wrote.
        try:
            from kazma_core.memory.dual_write import _episode_id

            user_text, _ = extract_turn_texts(messages)
            if user_text and not user_text.strip().startswith("/"):
                eid = _episode_id(
                    session_id or "unknown",
                    int(turn) if turn is not None else 0,
                    user_text,
                )
                from kazma_core.memory.task_queue import enqueue_task

                enqueue_task(
                    "micro_consolidation",
                    {"episode_id": eid},
                )
        except Exception:
            logger.debug("[post_turn] could not enqueue micro_consolidation", exc_info=True)

    # V2 path runs in a DEDICATED OS thread (not the loop's executor) so
    # blocking sync calls cannot starve or gate V2 writes. A plain Thread
    # is fully decoupled from the asyncio loop and runs even if the loop
    # freezes.
    import threading

    try:
        t = threading.Thread(target=_run_v2_sync, daemon=True, name="kazma-v2-extract")
        t.start()
    except Exception:
        logger.debug("[post_turn] could not start V2 thread", exc_info=True)


def _mirror_turn_to_v2(
    messages: list[dict[str, Any]],
    *,
    session_id: str | None,
    turn: int | None,
) -> None:
    """Best-effort mirror of the just-finished turn into the V2 schema.

    Extracts the last user/assistant pair and writes a V2 episode. Belief
    extraction proper happens in the Phase 3 consolidator; here we only
    capture the raw turn so recall can find it once ``use_new_stack`` flips.
    """
    from kazma_core.memory.dual_write import mirror_episode

    user_text, assistant_text = extract_turn_texts(messages)
    if not (user_text or assistant_text):
        return
    # Skip slash commands and trivial turns
    u = (user_text or "").strip()
    if not u or u.startswith("/"):
        return
    mirror_episode(
        session_id=session_id or "unknown",
        turn_number=int(turn) if turn is not None else 0,
        user_text=user_text,
        assistant_text=assistant_text,
    )


def _v2_extract_sync(
    messages: list[dict[str, Any]],
    *,
    session_id: str | None,
    turn: int | None,
) -> None:
    """SYNC heuristic belief extraction (runs in the V2 thread).

    Uses :func:`extract_and_apply_beliefs_sync` which is heuristic-only —
    NO LLM call, NO httpx client, so it is safe to run from a worker
    thread without risking the ``bound to a different event loop``
    RuntimeError that poisoned the connection pool.

    The LLM deep-pass is deferred to the ``micro_consolidation`` queue
    task (enqueued by the caller), which runs on the worker's own event
    loop where the httpx client is valid.
    """
    import sqlite3

    from kazma_core.memory.config import read_memory_cfg
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema
    from kazma_core.paths import memory_ops_db, primary_memory_db

    from kazma_core.memory.belief_extractor import extract_and_apply_beliefs_sync

    user_text, assistant_text = extract_turn_texts(messages)
    if not user_text or user_text.strip().startswith("/"):
        return
    cfg = read_memory_cfg()
    primary_conn = None
    ops_conn = None
    try:
        # isolation_level=None = autocommit mode, so Python's sqlite3 does
        # NOT open implicit transactions (which would hold a stale WAL
        # snapshot and miss concurrent supersede commits from other threads).
        # mutate_belief controls transactions explicitly via BEGIN IMMEDIATE.
        primary_conn = sqlite3.connect(
            primary_memory_db(), check_same_thread=False, isolation_level=None
        )
        primary_conn.row_factory = sqlite3.Row
        ensure_primary_schema(primary_conn)
        ops_conn = sqlite3.connect(
            memory_ops_db(), check_same_thread=False, isolation_level=None
        )
        ensure_ops_schema(ops_conn)
        stats = extract_and_apply_beliefs_sync(
            primary_conn,
            ops_conn,
            user_text,
            assistant_text,
            session_id=session_id,
            turn=turn,
            cfg=cfg,
        )
        if stats.get("applied"):
            logger.info(
                "[post_turn] V2 beliefs extracted (heuristic): source=%s applied=%d rejected=%d filler=%s",
                stats.get("source"),
                stats.get("applied", 0),
                stats.get("rejected", 0),
                stats.get("skipped_filler", False),
            )
    finally:
        for conn in (primary_conn, ops_conn):
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
