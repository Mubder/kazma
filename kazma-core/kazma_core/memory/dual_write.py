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

from kazma_core.memory.episode_text import embed_text
from kazma_core.memory.limits import episode_text
from kazma_core.memory.remember_request import is_remember_request
from kazma_core.spaces.scope import space_named_or_bound

logger = logging.getLogger(__name__)

__all__ = [
    "EpisodeWriteFailed",
    "episode_row",
    "mirror_episode",
    "write_episode",
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

def _episode_id(session_id: str, turn: int, content: str, tenant_id: str | None = None) -> str:
    """Stable episode PK: the id a turn of *tenant_id* is written under.

    The owner's space keeps the id it always had (chat, turn number, text).
    Every other space's ids carry the space (Spaces S1-09, the owner's
    decision 17: the space is salted into new ids and ``id`` stays the
    table's key). The id held no tenant, so the same turn written for a
    second space was ignored by the insert, reported as written, and found
    by recall in the first space only.
    """
    return episode_ids(session_id, turn, content, tenant_id)[0]


def episode_ids(session_id: str, turn: int, content: str, tenant_id: str | None = None) -> tuple[str, str]:
    """Both ids a turn may be stored under in *tenant_id*: today's first,
    then the other form.

    ``plain`` is the id from before Spaces; ``spaced`` carries the tenant.
    The owner's space writes ``plain``; every other tenant writes ``spaced``,
    and may hold ``plain`` rows an older build wrote (an older multi-user
    tenant). Code that looks a turn up by id -- the writer, recovery -- asks
    for both, always together with the row's tenant.
    """
    from kazma_core.spaces.scope import OWNER_SPACE_ID

    tenant = str(tenant_id or OWNER_SPACE_ID)
    body = f"{session_id}|{turn}|{content[:512]}"
    plain = "e_" + hashlib.sha256(body.encode("utf-8")).hexdigest()[:24]
    spaced = "e_" + hashlib.sha256(f"{tenant}\x1f{body}".encode("utf-8")).hexdigest()[:24]
    return (plain, spaced) if tenant == OWNER_SPACE_ID else (spaced, plain)


def _id_in_tenant(conn: Any, ids: tuple[str, ...], tenant_id: str) -> tuple[str | None, bool]:
    """``(id, held)`` for a turn about to be written for *tenant_id*: the id
    it is already stored under (``held`` True), else the first of *ids* no
    other tenant holds (``held`` False), else ``(None, False)``. Called
    inside the writer's transaction.

    An empty tenant is the owner's, on both sides of the comparison: a
    writer that passed ``None`` or ``""`` never matched its own stored row,
    and each re-write added a copy under the other id.
    """
    from kazma_core.spaces.scope import OWNER_SPACE_ID

    tenant = str(tenant_id or OWNER_SPACE_ID)
    free: str | None = None
    for candidate in ids:
        row = conn.execute("SELECT tenant_id FROM episodes WHERE id = ?", (candidate,)).fetchone()
        if row is None:
            free = free or candidate
        elif str(row[0] or OWNER_SPACE_ID) == tenant:
            return candidate, True
    return free, False


#: Phrases that mark a turn the user explicitly asked Kazma to keep.
#: Sources whose turns are written after the fact, so they are not the live
#: session's short-term buffer and stay episodic.
_AFTER_THE_FACT_SOURCES = frozenset(
    {"turn_reconcile", "benchmark", "golden_eval", "history_import", "chat_move"}
)


def episode_row(
    *,
    session_id: str,
    turn_number: int,
    user_text: str = "",
    assistant_text: str = "",
    summary_text: str = "",
    tenant_id: str | None = None,
    tier: str = "episodic",
    importance: int = 1,
    source: str = "dual_write_mirror",
    created_at: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The episode row the writer stores for one turn.

    Its id, tier, importance and metadata, and ``embed_text`` -- the text the
    meaning vector is computed from. Everything that writes an episode (this
    mirror, the golden eval, the retrieval benchmark) builds the row here, so
    none of them can drift from what live chat turns get. *metadata* adds to
    the row's (the chat an agent's note was written in, for example).
    """
    tenant_id = space_named_or_bound(tenant_id)
    content = (user_text or assistant_text or summary_text or "").strip()
    meta: dict[str, Any] = {**(metadata or {}), "source": source}
    effective_tier = tier
    effective_importance = int(importance)
    if is_remember_request(user_text):  # English or Arabic (W4)
        # Explicit "remember" turns go straight to the recall tier so meaning
        # search finds them immediately (Phase A).
        effective_tier = "recall"
        effective_importance = max(effective_importance, 3)
        meta["promote_reason"] = "explicit_remember"
    elif tier == "episodic" and source not in _AFTER_THE_FACT_SOURCES:
        # A live turn lands in the working buffer (Phase C); the post-turn
        # pass promotes the session's earlier working turns to episodic.
        effective_tier = "working"
        meta["promote_reason"] = "working_buffer"
    return {
        "id": _episode_id(session_id, turn_number, content, tenant_id),
        "ids": episode_ids(session_id, turn_number, content, tenant_id),
        "tenant_id": tenant_id,
        "session_id": session_id,
        "turn_number": int(turn_number),
        # Whole (memory/limits.py): until 2026-11-19 the question and the
        # answer were cut at 4,000 characters and the summary at 2,000.
        "user_text": episode_text(user_text, field="question"),
        "assistant_text": episode_text(assistant_text, field="answer"),
        "summary_text": episode_text(summary_text, field="summary"),
        "tier": effective_tier,
        "importance": effective_importance,
        "created_at": time.time() if created_at is None else float(created_at),
        "meta": meta,
        "embedding_model_version": _embedding_model_version(),
        "embed_text": embed_text(user_text, assistant_text, summary_text),
    }


class EpisodeWriteFailed(RuntimeError):
    """The episode could not be written (the database could not be opened, or
    the insert raised). A turn kept out of memory by the user is not this."""


#: What :meth:`DualWriteMirror.write_episode` did: ``written`` (the row is
#: there, an existing one included), ``refused`` (the user keeps the turn or
#: the chat out of memory), ``failed`` (nothing written; retry).
EpisodeWrite = tuple[str, str | None]


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
        # One initializer: two first writers both found ``_ready`` False, both
        # ran the schema scripts on two connections at once, and the second
        # overwrote the first's connections, leaking one. The connections are
        # published only once both schemas are ready, and a failed setup
        # closes what it opened (audit 2026-10-26 AUD-117).
        with self._lock:
            if self._ready:
                return True
            primary: sqlite3.Connection | None = None
            ops: sqlite3.Connection | None = None
            try:
                from kazma_core.memory.schema_v2 import (
                    ensure_ops_schema,
                    ensure_primary_schema,
                )
                from kazma_core.paths import memory_ops_db, primary_memory_db

                primary = sqlite3.connect(primary_memory_db(), check_same_thread=False)
                primary.row_factory = sqlite3.Row
                ensure_primary_schema(primary)
                ops = sqlite3.connect(memory_ops_db(), check_same_thread=False)
                ops.row_factory = sqlite3.Row
                ensure_ops_schema(ops)
            except Exception:
                logger.warning("[dual_write] schema init failed — mirror inactive", exc_info=True)
                for conn in (primary, ops):
                    if conn is not None:
                        conn.close()
                return False
            self._primary, self._ops = primary, ops
            self._ready = True
            logger.info("[dual_write] V2 schema mirror ready")
            return True

    # ── Public mirror API ──────────────────────────────────────────────

    def mirror_episode(
        self,
        *,
        session_id: str,
        turn_number: int,
        user_text: str = "",
        assistant_text: str = "",
        summary_text: str = "",
        tenant_id: str | None = None,
        tier: str = "episodic",
        importance: int = 1,
        source: str = "dual_write_mirror",
        created_at: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """Mirror a legacy turn/fact into the V2 ``episodes`` table.

        *created_at* is when the turn happened, for a turn written after the
        fact (``turn_reconcile``); it defaults to now. Returns the V2 episode
        id, or None if refused/inactive/failed -- :meth:`write_episode` says
        which.
        """
        tenant_id = space_named_or_bound(tenant_id)
        status, eid = self.write_episode(
            session_id=session_id, turn_number=turn_number, user_text=user_text,
            assistant_text=assistant_text, summary_text=summary_text, tenant_id=tenant_id, tier=tier,
            importance=importance, source=source, created_at=created_at, metadata=metadata,
        )
        return eid if status == "written" else None

    def write_episode(
        self,
        *,
        session_id: str,
        turn_number: int,
        user_text: str = "",
        assistant_text: str = "",
        summary_text: str = "",
        tenant_id: str | None = None,
        tier: str = "episodic",
        importance: int = 1,
        source: str = "dual_write_mirror",
        created_at: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EpisodeWrite:
        """:meth:`mirror_episode`, saying what happened: ``(status, id)``.

        A failed write is ``failed``, never the same None as a refusal (Memory
        audit M12): the post-turn pipeline counted a turn whose episode was
        never written as remembered, and its queue task as done.
        """
        tenant_id = space_named_or_bound(tenant_id)
        if not self._ensure():
            return "failed", None
        from kazma_core.spaces.scope import OWNER_SPACE_ID

        # One spelling of the owner's tenant for the ids, the lookup, the
        # ledger and the insert (an empty one wrote a NULL tenant row).
        tenant_id = str(tenant_id or OWNER_SPACE_ID)
        row = episode_row(
            session_id=session_id,
            turn_number=turn_number,
            user_text=user_text,
            assistant_text=assistant_text,
            summary_text=summary_text,
            tenant_id=tenant_id,
            tier=tier,
            importance=importance,
            source=source,
            created_at=created_at,
            metadata=metadata,
        )
        eid = row["id"]
        now = row["created_at"]
        meta = row["meta"]
        effective_tier = row["tier"]
        effective_importance = row["importance"]
        try:
            # A turn the user took back, or a chat they keep out of memory
            # (plan U1, memory/forget.py): nothing is written -- not the row,
            # and not the embedding, remote vector or mirror below, which run
            # even when the insert is ignored. Every episode write reaches this.
            from kazma_core.memory.forget import chat_keys, refuses_write

            # Every id of the chat (a chat-store read): outside the lock that
            # serializes the local writes.
            keys = chat_keys(session_id)
            # Lock only the local SQLite writes. The remote embed, vector
            # upsert, and state mirror below can each block up to ~60s;
            # holding self._lock across them serialized ALL V2 mirror writes
            # process-wide behind one slow embed call (audit finding).
            # The ledger check and the insert are ONE write transaction: a
            # "don't remember this chat" or a forget committed between the two
            # found nothing to forget, and the turn was written after it
            # (Memory audit M08, 2026-10-13).
            with self._lock, self._primary:
                self._primary.execute("BEGIN IMMEDIATE")
                refused = refuses_write(
                    self._primary,
                    tenant_id=tenant_id,
                    session_id=session_id,
                    turn_number=row["turn_number"],
                    user_text=row["user_text"],
                    keys=keys,
                )
                if refused:
                    logger.debug("[dual_write] turn kept out of memory by the user: %s", eid)
                    return "refused", None
                # The id this turn has in THIS tenant: the one it is already
                # stored under (either form), else one no other tenant holds.
                # The insert below ignores a taken id, and everything after it
                # (the vector, the mirror) names the id: writing under another
                # tenant's would change that tenant's row and report success.
                found, _held = _id_in_tenant(self._primary, row["ids"], tenant_id)
                if found is None:
                    logger.warning(
                        "[dual_write] turn %s of %s not written: both of its ids are held by other tenants",
                        row["turn_number"], session_id)
                    return "failed", None
                eid = row["id"] = found
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
                        row["turn_number"],
                        row["user_text"],
                        row["assistant_text"],
                        row["summary_text"],
                        effective_tier,
                        effective_importance,
                        now,
                        json.dumps(meta, ensure_ascii=False),
                        row["embedding_model_version"],
                    ),
                )

            # The episode's vector: embedded ONCE, outside the lock, then one
            # guarded write under it (still no vector, not forgotten) and the
            # remote index (Memory audit M15 -- it was embedded twice, and the
            # second write ran on this shared connection without the lock or
            # the tier guard, so a forget in between got its vector back).
            from kazma_core.memory.vector_write import encode_vector, index_remotely, write_local_vector

            vec = encode_vector(row["embed_text"])
            if vec:
                with self._lock, self._primary:
                    write_local_vector(self._primary, "episodes", eid, vec, still_sql="tier != 'forgotten'")

                def _still_held() -> bool:
                    with self._lock:
                        return self._primary.execute(
                            "SELECT 1 FROM episodes WHERE id = ? AND tier = 'forgotten'", (eid,)
                        ).fetchone() is None

                index_remotely(eid, vec, tenant_id=tenant_id,
                               meta={"kind": "episode", "tier": effective_tier, "session_id": session_id},
                               still_held=_still_held)

            # Its passages (the memory plan's W5): vectors of what its own
            # vector misses, made outside the lock, written under it while
            # the memory still holds their text. A failure leaves them to
            # the repair sweep.
            from kazma_core.memory import passages

            if passages.in_use():
                try:
                    passages.write_passages(self._primary, eid, lock=self._lock)
                except sqlite3.Error:
                    logger.warning("[dual_write] passages not written for %s; the repair sweep "
                                   "writes them", eid, exc_info=True)

            # Optional multi-replica state dual-mirror (Postgres) — outside lock.
            # The row as it is now, never this call's arguments: a forget that
            # ran meanwhile reaches the mirror as the tombstone it is (M08).
            try:
                from kazma_core.memory.state_backend import episode_mirror_row, mirror_episode_to_state

                with self._lock:
                    current = episode_mirror_row(self._primary, eid)
                if current is not None:
                    mirror_episode_to_state(current)
            except Exception:
                logger.debug("[dual_write] state mirror failed", exc_info=True)
            return "written", eid
        except Exception:
            logger.warning("[dual_write] episode write failed for %s", eid, exc_info=True)
            return "failed", None

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


def _get_mirror() -> DualWriteMirror:
    """Return the process-wide dual-write mirror singleton."""
    global _mirror
    if _mirror is not None:
        return _mirror
    with _mirror_lock:
        if _mirror is None:
            _mirror = DualWriteMirror()
        return _mirror


def _reset_mirror() -> None:
    """Close + drop the singleton (tests)."""
    global _mirror
    with _mirror_lock:
        if _mirror is not None:
            _mirror.close()
        _mirror = None


# ── Convenience wrappers (drop-in for legacy call sites) ──────────────────


def mirror_episode(*, session_id: str, turn_number: int, **kwargs: Any) -> str | None:
    """Module-level convenience: mirror a turn into V2 episodes."""
    return _get_mirror().mirror_episode(
        session_id=session_id, turn_number=turn_number, **kwargs
    )


def write_episode(*, session_id: str, turn_number: int, **kwargs: Any) -> EpisodeWrite:
    """Module-level :meth:`DualWriteMirror.write_episode`: ``(status, id)``."""
    return _get_mirror().write_episode(session_id=session_id, turn_number=turn_number, **kwargs)
