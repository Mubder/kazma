"""V2 memory write mirror — best-effort mirroring of writes into the cognitive schema.

The legacy 4-layer RRF stack has been **removed** (V1→V2 cutover is
complete). V2 (``memory_state.db``) is now the sole read/write path.
This module mirrors writes from compatibility call sites (compaction,
swarm, self-improvement) into the V2 schema, best-effort and
non-blocking: a write failure is logged but never propagates.

The ``memory.v2.use_new_stack`` flag (now ``True`` by default) formerly
controlled the V1→V2 read cutover; with V1 removed, setting it to
``False`` only disables V2 injection/post-turn consolidation — there
is no legacy fallback to serve reads.

Three legacy write paths are mirrored:

1. **Fact text** (``UnifiedMemoryAdapter.store`` / ``index``) → ``episodes``
   as a turn snapshot with the raw text.
2. **Triples** (``KnowledgeGraph.upsert_triple``) → ``beliefs`` with
   ``predicate_type`` inferred heuristically (functional predicates are a
   small known set; everything else is treated as ``set``-valued so
   nothing is accidentally superseded during the mirror).
3. **Turn snapshots** (``auto_store``) → ``episodes`` with the full
   user/assistant text.

Provenance (``source_session`` / ``source_turn``) is nullable per
resolution #3 — populated when available, NULL otherwise.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "DualWriteMirror",
    "get_mirror",
    "mirror_belief",
    "mirror_episode",
    "reset_mirror",
]


def _embedding_model_version() -> str:
    """Return the currently configured embedding model name.

    Stamped on every new row so ``embedding_model_version`` stays accurate
    even on databases whose column DEFAULT predates an embedder model switch
    (SQLite cannot alter a column default in place).
    """
    try:
        from kazma_core.memory.embedder import get_embedding_model_name

        return get_embedding_model_name()
    except Exception:
        return ""

# Predicates that are single-valued (functional) by nature. Mirrored
# triples using one of these get predicate_type='functional'; everything
# else is 'set' (multi-valued) to avoid accidental supersession during
# the mirror — the V2 consolidator will re-classify when it runs.
_FUNCTIONAL_PREDICATES = frozenset(
    {
        "name_is",
        "lives_in",
        "works_at",
        "active_project",
        "favorite_ide",
        "favorite_editor",
        "favorite_language",
        "located_in",
        "current_role",
        "preferred_name",
    }
)


def _slug(text: str) -> str:
    """Canonicalize an entity label into a stable slug."""
    import re

    raw = (text or "").strip().lower()
    s = re.sub(r"[^a-z0-9_]+", "_", raw)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:80] or "entity"


def _belief_id(tenant_id: str, subject: str, predicate: str, valid_from: float) -> str:
    """Belief PK. Includes a uuid suffix so two mirrors at the same timestamp
    (e.g. rapid programmatic writes that share a ``valid_from`` float-second)
    don't collide on PK and silently drop via INSERT OR IGNORE. The hash prefix
    keeps the id visually traceable to its (tenant, subject, predicate,
    valid_from). Mirrors ``belief_mutation._belief_id`` (audit finding: this
    copy was missing the suffix the other one added to fix exactly this)."""
    import uuid

    h = hashlib.sha256(
        f"{tenant_id}|{subject}|{predicate}|{valid_from}".encode("utf-8")
    ).hexdigest()
    return f"b_{h[:20]}_{uuid.uuid4().hex[:6]}"


def _episode_id(session_id: str, turn: int, content: str) -> str:
    """Stable episode PK."""
    h = hashlib.sha256(
        f"{session_id}|{turn}|{content[:512]}".encode("utf-8")
    ).hexdigest()
    return f"e_{h[:24]}"


def _infer_predicate_type(predicate: str) -> str:
    """Heuristic: functional predicates are single-valued; else set-valued.

    Conservative on purpose — defaults to 'set' so the mirror never
    supersedes an existing belief during the transition. The V2
    consolidator (Phase 3) re-classifies with LLM extraction.
    """
    p = (predicate or "").strip().lower()
    if p in _FUNCTIONAL_PREDICATES:
        return "functional"
    return "set"


class DualWriteMirror:
    """Best-effort mirror of legacy writes into the V2 schema.

    Holds two lazy SQLite connections (primary + ops) protected by a
    lock. All public methods swallow exceptions and log — the legacy
    write path must never be blocked by a V2 mirror failure.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._primary: sqlite3.Connection | None = None
        self._ops: sqlite3.Connection | None = None
        self._ready = False

    def _ensure(self) -> bool:
        """Lazily open + initialize both DBs. Returns False if unavailable."""
        if self._ready:
            return True
        try:
            from kazma_core.memory.schema_v2 import (
                ensure_ops_schema,
                ensure_primary_schema,
            )
            from kazma_core.paths import memory_ops_db, primary_memory_db

            self._primary = sqlite3.connect(primary_memory_db(), check_same_thread=False)
            self._primary.row_factory = sqlite3.Row
            ensure_primary_schema(self._primary)
            self._ops = sqlite3.connect(memory_ops_db(), check_same_thread=False)
            self._ops.row_factory = sqlite3.Row
            ensure_ops_schema(self._ops)
            self._ready = True
            logger.info("[dual_write] V2 schema mirror ready")
            return True
        except Exception:
            logger.debug("[dual_write] schema init failed — mirror inactive", exc_info=True)
            self._ready = False
            return False

    # ── Public mirror API ──────────────────────────────────────────────

    def mirror_belief(
        self,
        subject: str,
        predicate: str,
        obj: str,
        *,
        fact_text: str = "",
        tenant_id: str = "default",
        source_session: str | None = None,
        source_turn: int | None = None,
        extraction_method: str = "llm_inferred",
        confidence: float = 0.5,
        importance: int = 1,
        trust_weight: float = 0.60,
    ) -> str | None:
        """Mirror a legacy triple into the V2 ``beliefs`` table.

        Conservative: always inserts a NEW belief row with
        ``valid_from=now``. It does NOT supersede existing rows during
        the mirror — that's the consolidator's job (Phase 3) once it can
        classify predicate types with LLM confidence. This means the
        mirror may create parallel rows for the same (subject, predicate)
        during the transition; the consolidator reconciles them.

        Returns the V2 belief id, or None if the mirror is inactive /
        the write failed.
        """
        if not self._ensure():
            return None
        now = time.time()
        sub = _slug(subject)
        pred = (predicate or "").strip().lower().replace(" ", "_") or "related"
        ptype = _infer_predicate_type(pred)
        bid = _belief_id(tenant_id, sub, pred, now)
        meta = {"source": "dual_write_mirror", "fact_text": (fact_text or "")[:500]}
        try:
            with self._lock:
                self._primary.execute(
                    """
                    INSERT OR IGNORE INTO beliefs (
                        id, tenant_id, subject, predicate, predicate_type, object,
                        confidence, structural_importance, source_trust_weight,
                        valid_from, ingested_at, source_session, source_turn,
                        extraction_method, metadata_json, embedding_model_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        bid,
                        tenant_id,
                        sub,
                        pred,
                        ptype,
                        (obj or "")[:500],
                        float(confidence),
                        int(importance),
                        float(trust_weight),
                        now,
                        now,
                        source_session,
                        source_turn,
                        extraction_method,
                        json.dumps(meta, ensure_ascii=False),
                        _embedding_model_version(),
                    ),
                )
                self._primary.commit()
            return bid
        except Exception:
            logger.debug("[dual_write] belief mirror failed", exc_info=True)
            return None

    def mirror_episode(
        self,
        *,
        session_id: str,
        turn_number: int,
        user_text: str = "",
        assistant_text: str = "",
        summary_text: str = "",
        tenant_id: str = "default",
        tier: str = "episodic",
        importance: int = 1,
        source: str = "dual_write_mirror",
    ) -> str | None:
        """Mirror a legacy turn/fact into the V2 ``episodes`` table.

        Returns the V2 episode id, or None if inactive/failed.
        """
        if not self._ensure():
            return None
        # Build a content blob for the stable id when texts are empty
        content = (user_text or assistant_text or summary_text or "").strip()
        eid = _episode_id(session_id, turn_number, content)
        now = time.time()
        meta = {"source": source}
        # Explicit "remember" turns go straight to recall tier so dense search
        # finds them immediately (Phase A — avoid episodic-only dense miss).
        # Phase C: default new turns land in working (short-term buffer);
        # post-turn promotes prior working → episodic for the session.
        effective_tier = tier
        effective_importance = int(importance)
        ut_low = (user_text or "").strip().lower()
        if any(
            phrase in ut_low
            for phrase in (
                "remember that",
                "remember my",
                "remember this",
                "don't forget",
                "do not forget",
                "note that",
            )
        ):
            effective_tier = "recall"
            effective_importance = max(effective_importance, 3)
            meta["promote_reason"] = "explicit_remember"
        elif tier == "episodic" and source not in (
            "knowledge_library_promote",
            "kb_promote",
        ):
            # Default post-turn path uses tier=episodic — promote to working
            # buffer so active-thread recall prefers the current session.
            # KB soft-merge promotes stay episodic so they are not session-buffer.
            effective_tier = "working"
            meta["promote_reason"] = "working_buffer"
        try:
            with self._lock:
                self._primary.execute(
                    """
                    INSERT OR IGNORE INTO episodes (
                        id, tenant_id, session_id, turn_number,
                        user_text, assistant_text, summary_text,
                        tier, structural_importance, created_at, metadata_json,
                        embedding_model_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        eid,
                        tenant_id,
                        session_id,
                        int(turn_number),
                        (user_text or "")[:4000],
                        (assistant_text or "")[:4000],
                        (summary_text or "")[:2000],
                        effective_tier,
                        effective_importance,
                        now,
                        json.dumps(meta, ensure_ascii=False),
                        _embedding_model_version(),
                    ),
                )
                # Compute + store the episode embedding so dense vector
                # recall can find it. Also dual-write to VectorBackend
                # (Qdrant/pgvector) when configured (P2-1 remote write path).
                try:
                    from kazma_core.memory.embedder import (
                        encode_text_to_blob,
                        get_embedder,
                    )

                    ep_text = (summary_text or user_text or assistant_text or "").strip()
                    if ep_text:
                        emb_blob = encode_text_to_blob(ep_text)
                        if emb_blob is not None:
                            self._primary.execute(
                                "UPDATE episodes SET embedding=? WHERE id=? AND embedding IS NULL",
                                (emb_blob, eid),
                            )
                        # Remote / hybrid vector upsert (best-effort)
                        try:
                            from kazma_core.memory.backends import get_vector_backend

                            emb = get_embedder()
                            if emb is not None:
                                qvec = emb.encode(ep_text)
                                if qvec:
                                    be = get_vector_backend(self._primary)
                                    be.upsert(
                                        eid,
                                        qvec,
                                        tenant_id=tenant_id,
                                        meta={
                                            "tier": effective_tier,
                                            "session_id": session_id,
                                        },
                                    )
                        except Exception:
                            logger.debug(
                                "[dual_write] vector backend upsert failed for %s",
                                eid,
                                exc_info=True,
                            )
                except Exception:
                    logger.debug("[dual_write] episode embedding failed for %s", eid, exc_info=True)
                self._primary.commit()
                # Optional multi-replica state dual-mirror (Postgres)
                try:
                    from kazma_core.memory.state_backend import mirror_episode_to_state

                    mirror_episode_to_state(
                        {
                            "id": eid,
                            "tenant_id": tenant_id,
                            "session_id": session_id,
                            "turn_number": int(turn_number),
                            "user_text": (user_text or "")[:4000],
                            "assistant_text": (assistant_text or "")[:4000],
                            "summary_text": (summary_text or "")[:2000],
                            "tier": effective_tier,
                            "structural_importance": effective_importance,
                            "created_at": now,
                            "metadata": meta,
                        }
                    )
                except Exception:
                    logger.debug("[dual_write] state mirror failed", exc_info=True)
            return eid
        except Exception:
            logger.debug("[dual_write] episode mirror failed", exc_info=True)
            return None

    def close(self) -> None:
        with self._lock:
            for conn in (self._primary, self._ops):
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
            self._primary = None
            self._ops = None
            self._ready = False


# ── Module-level singleton ────────────────────────────────────────────────

_mirror: DualWriteMirror | None = None
_mirror_lock = threading.Lock()


def get_mirror() -> DualWriteMirror:
    """Return the process-wide dual-write mirror singleton."""
    global _mirror
    if _mirror is not None:
        return _mirror
    with _mirror_lock:
        if _mirror is None:
            _mirror = DualWriteMirror()
        return _mirror


def reset_mirror() -> None:
    """Close + drop the singleton (tests)."""
    global _mirror
    with _mirror_lock:
        if _mirror is not None:
            _mirror.close()
        _mirror = None


# ── Convenience wrappers (drop-in for legacy call sites) ──────────────────


def mirror_belief(
    subject: str,
    predicate: str,
    obj: str,
    **kwargs: Any,
) -> str | None:
    """Module-level convenience: mirror a triple into V2 beliefs."""
    return get_mirror().mirror_belief(subject, predicate, obj, **kwargs)


def mirror_episode(*, session_id: str, turn_number: int, **kwargs: Any) -> str | None:
    """Module-level convenience: mirror a turn into V2 episodes."""
    return get_mirror().mirror_episode(
        session_id=session_id, turn_number=turn_number, **kwargs
    )
