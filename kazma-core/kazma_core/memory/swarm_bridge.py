"""V2-native write paths for the swarm / self-improvement / compaction memory.

Replaces the three V1 adapter entry points that were NOT covered by the
dual-write bridge (worker result storage, SoulEvolution logging, compaction
summary persistence). Each function writes directly to the V2 stores
(``memory_state.db`` episodes/beliefs) using the existing primitives
(``mirror_episode`` / ``mutate_belief`` / ``schema_v2``) — no new tables.

All functions are best-effort and never raise (mirrors the V1 try/except
contract at every call site). They take only the data the V1 callers passed
and map it onto the V2 schema:

  - ``store_swarm_result``  → episode (source="swarm_result") + belief
  - ``log_evolution_v2``    → episode (source="soul_evolution")
  - ``store_compaction_summary`` → episode (source="compaction_summary")

This module is the migration target for the three V1 write paths documented
in the V1→V2 migration plan. The read counterparts use ``recall.search``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "store_swarm_result",
    "log_evolution_v2",
    "store_compaction_summary",
]


def _open_primary() -> sqlite3.Connection | None:
    """Open a short-lived V2 primary DB connection, or None on failure."""
    try:
        from kazma_core.memory.schema_v2 import ensure_primary_schema
        from kazma_core.paths import primary_memory_db

        conn = sqlite3.connect(primary_memory_db(), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        ensure_primary_schema(conn)
        return conn
    except Exception:
        logger.debug("[swarm_bridge] could not open primary DB", exc_info=True)
        return None


def _insert_episode(    conn: sqlite3.Connection,
    *,
    session_id: str,
    turn_number: int,
    user_text: str,
    summary_text: str,
    source: str,
    importance: int,
    metadata: dict[str, Any],
    eid: str | None = None,
    tenant_id: str = "default",
) -> str:
    """Insert one V2 episode row. Caller owns the connection."""
    now = time.time()
    content = (user_text or summary_text or "").strip()
    if eid is None:
        # Stable id keyed on source + content + timestamp-second so repeated
        # writes for the same payload dedup (INSERT OR IGNORE) but distinct
        # events still land.
        import hashlib

        eid = "ep_" + hashlib.sha256(
            f"{source}:{session_id}:{turn_number}:{content[:200]}".encode("utf-8", "ignore")
        ).hexdigest()[:24]
    # The `source` param is the authoritative V2 categorization — it must
    # win over any caller-supplied metadata["source"] (e.g. the legacy
    # "swarm_worker" source) so V2 read filters by source stay reliable.
    meta = {**metadata, "source": source}
    conn.execute(
        """
        INSERT OR IGNORE INTO episodes (
            id, tenant_id, session_id, turn_number,
            user_text, assistant_text, summary_text,
            tier, structural_importance, created_at, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'episodic', ?, ?, ?)
        """,
        (
            eid, tenant_id, session_id, turn_number,
            user_text[:8000] if user_text else None,
            None,
            summary_text[:8000] if summary_text else None,
            importance, now, json.dumps(meta, ensure_ascii=False, default=str),
        ),
    )
    # Compute + store the episode embedding so dense vector recall can
    # find swarm results / evolution entries / compaction summaries.
    # Best-effort: a missing embedder leaves embedding NULL (FTS5 still works).
    try:
        from kazma_core.memory.embedder import encode_text_to_blob

        ep_text = (summary_text or user_text or "").strip()
        if ep_text:
            emb_blob = encode_text_to_blob(ep_text)
            if emb_blob is not None:
                conn.execute(
                    "UPDATE episodes SET embedding=? WHERE id=? AND embedding IS NULL",
                    (emb_blob, eid),
                )
    except Exception:
        logger.debug("[swarm_bridge] episode embedding failed for %s", eid, exc_info=True)
    conn.commit()
    return eid


def store_swarm_result(
    worker: str,
    task_id: str,
    snippet: str,
    metadata: dict[str, Any] | None = None,
    tenant_id: str = "default",
) -> str | None:
    """Persist a successful swarm worker result as a V2 episode.

    Replaces ``worker_dispatch._index_worker_l4_memory``. The ``snippet``
    is the same ``"Task: …\\nResult: …"`` blob the V1 path indexed. The
    full text + worker metadata + embedding live in the episode, which is
    what ``recall.search`` reads — no ``worker → produced`` belief is
    written (the belief had zero readers and polluted the Beliefs UI with
    every worker completion, including throwaway/test tasks).

    Returns the V2 episode id, or None on failure. Never raises.
    """
    if not snippet or len(snippet.strip()) < 12:
        return None
    try:
        meta = dict(metadata or {})
        meta.setdefault("worker", worker)
        meta.setdefault("source", "swarm_worker")
        meta.setdefault("task_id", task_id)
        meta.setdefault("type", "swarm_result")

        conn = _open_primary()
        if conn is None:
            return None
        try:
            eid = _insert_episode(
                conn,
                session_id=f"swarm:{worker}",
                turn_number=int(time.time()) % 10_000_000,
                user_text=snippet,
                summary_text="",
                source="swarm_result",
                importance=3,
                metadata=meta,
                tenant_id=tenant_id,
            )
            # No worker→produced belief is written here: nothing reads it
            # (recall goes through episodes) and it flooded the Beliefs UI
            # with one belief per worker completion. The episode above is
            # the authoritative record.
            logger.debug("[swarm_bridge] stored swarm_result for %s (%s)", worker, eid)
            return eid
        finally:
            conn.close()
    except Exception:
        logger.debug("[swarm_bridge] store_swarm_result failed", exc_info=True)
        return None


def log_evolution_v2(
    worker_name: str,
    task_id: str,
    delta: str,
    summary: str,
    timestamp: str = "",
    tenant_id: str = "default",
) -> str | None:
    """Persist a SoulEvolution entry as a V2 episode (source="soul_evolution").

    Replaces ``adapter.log_evolution``. The V1 ``get_evolution_history``
    read path is dead code (zero callers) — the live read is the loose
    ``recall.search(f"{worker} evolution learning")`` in phonebook, which
    matches this episode via the ``source`` metadata.

    Returns the V2 episode id, or None on failure. Never raises.
    """
    try:
        if not timestamp:
            from datetime import datetime, UTC

            timestamp = datetime.now(UTC).isoformat()
        meta = {
            "worker": worker_name,
            "task_id": task_id,
            "timestamp": timestamp,
            "delta": delta[:500],
            "summary": summary[:300],
        }
        text = (
            f"[SoulEvolution] worker={worker_name} task={task_id} "
            f"summary={summary[:200]} delta={delta[:200]}"
        )
        conn = _open_primary()
        if conn is None:
            return None
        try:
            return _insert_episode(
                conn,
                session_id=f"evolution:{worker_name}",
                turn_number=int(time.time()) % 10_000_000,
                user_text=text,
                summary_text="",
                source="soul_evolution",
                importance=2,
                metadata=meta,
                tenant_id=tenant_id,
            )
        finally:
            conn.close()
    except Exception:
        logger.debug("[swarm_bridge] log_evolution_v2 failed", exc_info=True)
        return None


def store_compaction_summary(
    summary: str,
    metadata: dict[str, Any] | None = None,
    tenant_id: str = "default",
) -> str | None:
    """Persist a compaction summary as a V2 episode (source="compaction_summary").

    Replaces ``compaction.memory_store.store(summary, metadata={"type":
    "compaction_summary", ...})``. The ``is_override_delta`` injection guard
    stays at the call site (it must run BEFORE this function is invoked).

    Returns the V2 episode id, or None on failure. Never raises.
    """
    if not summary or not summary.strip():
        return None
    try:
        meta = dict(metadata or {})
        meta.setdefault("type", "compaction_summary")
        meta.setdefault("source", "compaction")
        meta.setdefault("ts", time.time())
        conn = _open_primary()
        if conn is None:
            return None
        try:
            return _insert_episode(
                conn,
                session_id="compaction",
                turn_number=int(time.time()) % 10_000_000,
                user_text="",
                summary_text=summary,
                source="compaction_summary",
                importance=2,
                metadata=meta,
                tenant_id=tenant_id,
            )
        finally:
            conn.close()
    except Exception:
        logger.debug("[swarm_bridge] store_compaction_summary failed", exc_info=True)
        return None
