"""V2 post-turn memory dispatch.

This module used to host the legacy V1 consolidator pipeline (heuristic +
LLM fact/triple extraction written into the old V1 storage layer and the
SQLite property graph). The V1 storage modules it depended on (the legacy
memory adapter, the knowledge graph, and the old auto-store / async
adapter modules) have been removed, and V2 is now the always-on write
path, so the legacy functions were deleted.

What remains is the V2 post-turn entry point and its helpers:

- :func:`remember_turn` — THE hand-over of a finished turn to long-term
  memory, called once per turn by ``kazma_ui.turn_runtime.close_turn`` (the
  closer every transport runs). It calls :func:`_schedule_post_turn_memory`,
  which spawns a dedicated OS thread for the V2 mirror + sync heuristic
  belief extraction and enqueues a deferred ``micro_consolidation`` task.
- :func:`extract_turn_texts` — the last question and ITS answer.
- :func:`user_turn_index` — which turn of the conversation that is.
- :func:`_mirror_turn_to_v2` / :func:`_v2_extract_sync` — V2 helpers.
- :func:`reset_turn_counter`, :func:`_bump_turn`, :func:`_cons_block`,
  :func:`_min_chars` — config/turn-counter helpers.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from typing import Any

from kazma_core.config_store import apply_sqlite_pragmas

__all__ = [
    "reset_turn_counter",
    "remember_turn",
    "clear_working_memory",
    "extract_turn_texts",
    "get_post_turn_metrics",
    "message_text",
    "user_turn_index",
]

logger = logging.getLogger(__name__)

# Process-local turn counter for every_n_turns cost control
_turn_lock = threading.Lock()
_turn_counter = 0

# Bounded concurrency for V2 post-turn extraction. Previously every finalized
# turn spawned a fresh OS thread with no cap, so sustained multi-user load
# spawned hundreds of concurrent daemon threads — each opening 2 fresh SQLite
# connections — causing a thread/connection storm that hit "database is
# locked" and silently dropped beliefs (audit finding).
_V2_EXTRACT_CONCURRENCY = 4
_v2_extract_sem = threading.Semaphore(_V2_EXTRACT_CONCURRENCY)

# Phase A observability — process-local counters for Dashboard / health
_metrics_lock = threading.Lock()
_post_turn_metrics: dict[str, Any] = {
    "ok": 0,
    "mirror_fail": 0,
    "extract_fail": 0,
    "enqueue_fail": 0,
    "thread_fail": 0,
    "last_ok_at": None,
    "last_error": None,
    "last_error_at": None,
}


def get_post_turn_metrics() -> dict[str, Any]:
    """Snapshot of post-turn pipeline counters (for health/dashboard)."""
    with _metrics_lock:
        return dict(_post_turn_metrics)


def _metric_ok() -> None:
    import time

    with _metrics_lock:
        _post_turn_metrics["ok"] += 1
        _post_turn_metrics["last_ok_at"] = time.time()


def _metric_fail(kind: str, exc: BaseException | None = None) -> None:
    import time

    with _metrics_lock:
        key = f"{kind}_fail" if not kind.endswith("_fail") else kind
        if key in _post_turn_metrics:
            _post_turn_metrics[key] = int(_post_turn_metrics.get(key) or 0) + 1
        if exc is not None:
            _post_turn_metrics["last_error"] = f"{type(exc).__name__}: {exc}"[:400]
            _post_turn_metrics["last_error_at"] = time.time()


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


def _message(m: Any) -> dict[str, Any]:
    if isinstance(m, dict):
        return m
    from kazma_core.memory.chat_history import normalize_message

    return normalize_message(m)


def message_text(content: Any) -> str:
    """A message's text, stripped: the string, or a multimodal message's text
    parts joined."""
    if not isinstance(content, str):
        if isinstance(content, list):
            parts = [
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") in (None, "text")
            ]
            content = " ".join(p for p in parts if p)
        else:
            content = ""
    return str(content or "").strip()


def extract_turn_texts(messages: list[dict[str, Any]]) -> tuple[str, str]:
    """The last question in *messages* and its answer: the last non-empty
    assistant message AFTER that question (empty when the turn produced none).

    Until 2026-09-26 the scan went on past the question, so a turn that
    produced no answer was stored with the previous turn's answer. Rows made
    that way are still reproduced by ``memory.rehydrate`` (its own copy of
    the old rule). Messages may be dicts or LangChain messages.
    """
    user = ""
    assistant = ""
    for m in reversed(messages or []):
        m = _message(m)
        role = m.get("role")
        content = message_text(m.get("content"))
        if not content:
            continue
        if role == "user":
            user = content
            break
        if role == "assistant" and not assistant:
            assistant = content
    return user, assistant


def user_turn_index(messages: list[Any]) -> int:
    """Which turn of the conversation *messages* ends on: its user messages,
    counted. ``respond_node`` stamps it on ``_post_turn_memory`` and
    :func:`remember_turn` recomputes it from the closing state -- one helper,
    so the two always count alike. As the episode's ``turn_number`` it keeps
    a question asked twice in one thread two memories (the iteration count
    used before made the second one's id collide with the first)."""
    return sum(1 for m in messages or [] if _message(m).get("role") == "user")


#: Turns already handed to memory in this process: close_turn runs after every
#: settlement of a turn (complete, disconnect, a late resume), and a turn is
#: remembered once.
_remembered: OrderedDict[tuple[str, int, str], None] = OrderedDict()
_remembered_lock = threading.Lock()
_REMEMBERED_MAX = 4096


def remember_turn(values: dict[str, Any] | None, *, thread_id: str = "") -> bool:
    """Hand a finished turn to long-term memory. True when it was scheduled.

    *values* is the graph's terminal state (``snapshot.values``); the caller
    has checked the run is finished, not paused. ``respond_node`` marks the
    turn it finalized with ``_post_turn_memory``; a record whose turn index
    is not the state's current one belongs to an earlier turn (this one
    ended some other way) and is ignored. Never raises.

    Called by ``kazma_ui.turn_runtime.close_turn`` alone. Until 2026-09-26
    the gateway handler was the only caller, so no web chat turn reached
    memory from 2026-08-08 (877 turns); tests/test_memory_every_turn.py holds
    the single call site.
    """
    values = values if isinstance(values, dict) else {}
    post = values.get("_post_turn_memory")
    if not isinstance(post, dict):
        return False
    messages = list(values.get("messages") or [])
    turn = user_turn_index(messages)
    try:
        if int(post.get("turn") or 0) != turn:
            return False
    except (TypeError, ValueError):
        return False
    session = str(post.get("session_id") or thread_id or "")
    question, _answer = extract_turn_texts(messages)
    key = (session, turn, question[:512])
    with _remembered_lock:
        if key in _remembered:
            return False
        _remembered[key] = None
        while len(_remembered) > _REMEMBERED_MAX:
            _remembered.popitem(last=False)
    _schedule_post_turn_memory(
        messages,
        session_id=session or None,
        turn=turn,
        tenant_id=str(post.get("tenant_id") or "default"),
    )
    return True


def _schedule_post_turn_memory(
    messages: list[dict[str, Any]],
    *,
    session_id: str | None = None,
    turn: int | None = None,
    tenant_id: str = "default",
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
        # ── Phase C: promote prior working → episodic for this session ─
        try:
            _promote_working_to_episodic(session_id=session_id, tenant_id=tenant_id)
        except Exception:
            logger.debug("[post_turn] working→episodic promote failed", exc_info=True)
        # ── V2 dual-write mirror ─────────────────────────────────────
        try:
            _mirror_turn_to_v2(messages, session_id=session_id, turn=turn, tenant_id=tenant_id)
        except Exception as exc:
            logger.warning("[post_turn] V2 mirror failed: %s", exc, exc_info=True)
            _metric_fail("mirror", exc)
        # ── Stage 1: sync heuristic extraction ───────────────────────
        try:
            _v2_extract_sync(messages, session_id=session_id, turn=turn, tenant_id=tenant_id)
        except Exception as exc:
            logger.warning("[post_turn] V2 heuristic extraction failed: %s", exc, exc_info=True)
            _metric_fail("extract", exc)
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
                    # episode_id only: the handler reads tenant_id from the
                    # episode ROW (authoritative). A payload tenant_id was
                    # dead data — the handler never consulted it (audit finding).
                    {"episode_id": eid},
                )
        except Exception as exc:
            logger.warning(
                "[post_turn] could not enqueue micro_consolidation: %s",
                exc,
                exc_info=True,
            )
            _metric_fail("enqueue", exc)
        else:
            _metric_ok()

    # V2 path runs in a DEDICATED OS thread (not the loop's executor) so
    # blocking sync calls cannot starve or gate V2 writes. A plain Thread
    # is fully decoupled from the asyncio loop and runs even if the loop
    # freezes. Concurrency is bounded by _v2_extract_sem (audit: previously
    # unbounded → thread/connection storm).
    def _run_v2_bounded() -> None:
        try:
            _run_v2_sync()
        finally:
            _v2_extract_sem.release()

    # Best-effort: if the pool is full, skip this turn's consolidation rather
    # than pile up threads or block the turn. The next turn picks up recent
    # context.
    if not _v2_extract_sem.acquire(blocking=False):
        logger.debug("[post_turn] V2 extract pool full — skipping this turn")
        return
    try:
        t = threading.Thread(target=_run_v2_bounded, daemon=True, name="kazma-v2-extract")
        t.start()
    except Exception as exc:
        _v2_extract_sem.release()  # thread never started — free the slot
        logger.warning("[post_turn] could not start V2 thread: %s", exc, exc_info=True)
        _metric_fail("thread", exc)


def _promote_working_to_episodic(
    *,
    session_id: str | None,
    tenant_id: str = "default",
) -> int:
    """Promote existing working-tier episodes for a session → episodic.

    Called before writing the new turn as ``working`` so the active thread
    keeps a short buffer of the latest turn only. Returns rows updated.
    """
    if not session_id:
        return 0
    import sqlite3

    from kazma_core.memory.schema_v2 import ensure_primary_schema
    from kazma_core.paths import primary_memory_db

    conn = sqlite3.connect(primary_memory_db(), check_same_thread=False)
    apply_sqlite_pragmas(conn)
    try:
        ensure_primary_schema(conn)
        cur = conn.execute(
            """
            UPDATE episodes SET tier = 'episodic'
            WHERE session_id = ? AND tenant_id = ? AND tier = 'working'
            """,
            (session_id, tenant_id),
        )
        conn.commit()
        return int(cur.rowcount or 0)
    finally:
        conn.close()


def clear_working_memory(
    session_id: str,
    *,
    tenant_id: str = "default",
) -> int:
    """Drop working-tier episodes for a session (e.g. on ``/new``).

    Episodic/recall tiers are retained. Returns deleted row count.
    """
    if not session_id:
        return 0
    import sqlite3

    from kazma_core.memory.schema_v2 import ensure_primary_schema
    from kazma_core.paths import primary_memory_db

    try:
        conn = sqlite3.connect(primary_memory_db(), check_same_thread=False)
        apply_sqlite_pragmas(conn)
        try:
            ensure_primary_schema(conn)
            cur = conn.execute(
                """
                DELETE FROM episodes
                WHERE session_id = ? AND tenant_id = ? AND tier = 'working'
                """,
                (session_id, tenant_id),
            )
            conn.commit()
            return int(cur.rowcount or 0)
        finally:
            conn.close()
    except Exception:
        logger.debug("[post_turn] clear_working_memory failed", exc_info=True)
        return 0


def _mirror_turn_to_v2(
    messages: list[dict[str, Any]],
    *,
    session_id: str | None,
    turn: int | None,
    tenant_id: str = "default",
) -> None:
    """Best-effort mirror of the just-finished turn into the V2 schema.

    Extracts the last user/assistant pair and writes a V2 episode (working
    tier by default; explicit-remember → recall).
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
        tenant_id=tenant_id,
    )


def _v2_extract_sync(
    messages: list[dict[str, Any]],
    *,
    session_id: str | None,
    turn: int | None,
    tenant_id: str = "default",
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
        apply_sqlite_pragmas(primary_conn)
        ensure_primary_schema(primary_conn)
        ops_conn = sqlite3.connect(
            memory_ops_db(), check_same_thread=False, isolation_level=None
        )
        apply_sqlite_pragmas(ops_conn)
        ensure_ops_schema(ops_conn)
        stats = extract_and_apply_beliefs_sync(
            primary_conn,
            ops_conn,
            user_text,
            assistant_text,
            session_id=session_id,
            turn=turn,
            tenant_id=tenant_id,
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
