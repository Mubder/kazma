"""Embedding rebuild + version-accounting helpers (in-process, UI-driven).

The Web UI Embedder settings page offers a one-click "Rebuild embeddings"
action. This module is the in-process counterpart of ``scripts/reembed.py``
with two important differences:

* **Incremental**: only rows whose ``embedding_model_version`` differs from
  the currently configured model are re-encoded (after a fresh model switch
  that is every row; after a small drift it is only the stray rows — e.g.
  episodes written while a stale server was running).
* **Status-driven**: progress is persisted to ConfigStore key
  ``embedding.rebuild_status`` so the UI can poll it. The rebuild runs on a
  background thread via ``asyncio.to_thread`` — never block the event loop.

Row accounting (:func:`embedding_version_counts`) reads the primary memory
DB read-only and groups episodes/beliefs by model version — the numbers the
UI shows in the "vector-space composition" card.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import struct
import time
from datetime import UTC, datetime
from typing import Any, Callable

logger = logging.getLogger(__name__)

__all__ = [
    "REBUILD_STATUS_KEY",
    "embedding_version_counts",
    "get_rebuild_status",
    "rebuild_embeddings",
    "repair_unsearchable_vectors",
    "reset_rebuild_status",
    "run_vector_repair_pass",
    "vector_repair_counts",
]

REBUILD_STATUS_KEY = "embedding.rebuild_status"

ProgressCallback = Callable[[int, int], None]  # (done, total)

_IDLE_STATUS = {
    "state": "idle",
    "model": "",
    "total": 0,
    "done": 0,
    "started_at": None,
    "finished_at": None,
    "error": None,
}

_BATCH_SIZE = 16  # status updates / commits every N rows


def _config_store():
    """Return the ConfigStore singleton without constructing it."""
    try:
        import kazma_core.config_store as _cs_mod

        return getattr(_cs_mod, "_config_store", None)
    except Exception:
        return None


def _set_status(status: dict[str, Any]) -> None:
    try:
        store = _config_store()
        if store is not None:
            store.set(REBUILD_STATUS_KEY, status, category="embedding")
    except Exception:
        logger.debug("[reembed] status write failed", exc_info=True)


def get_rebuild_status() -> dict[str, Any]:
    """Return the persisted rebuild status (never raises)."""
    try:
        store = _config_store()
        if store is not None:
            val = store.get(REBUILD_STATUS_KEY)
            if isinstance(val, dict):
                return {**_IDLE_STATUS, **val}
    except Exception:
        pass
    return dict(_IDLE_STATUS)


def reset_rebuild_status() -> None:
    """Clear the persisted rebuild status (e.g. after a model change)."""
    try:
        store = _config_store()
        if store is not None:
            store.delete(REBUILD_STATUS_KEY)
    except Exception:
        pass


def embedding_version_counts() -> dict[str, dict[str, int]]:
    """Count episodes/beliefs per ``embedding_model_version`` (read-only).

    Returns ``{"episodes": {"BAAI/bge-m3": 435, ...}, "beliefs": {...}}``.
    Rows with a NULL version are grouped under ``"(none)"``. Never raises.
    """
    out: dict[str, dict[str, int]] = {"episodes": {}, "beliefs": {}}
    try:
        from kazma_core.paths import primary_memory_db

        db = primary_memory_db()
        if not db or not os.path.isfile(db):
            return out
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            for table in ("episodes", "beliefs"):
                rows = con.execute(
                    "SELECT COALESCE(embedding_model_version, '(none)') AS v, count(*) "
                    f"FROM {table} GROUP BY v"
                ).fetchall()
                out[table] = {str(r[0]): int(r[1]) for r in rows}
        finally:
            con.close()
    except Exception:
        logger.debug("[reembed] version counts failed", exc_info=True)
    return out


def _encode_text(emb: Any, text: str) -> bytes | None:
    """Encode *text* → float32 BLOB, or None on failure/empty input."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        vec = emb.encode(text)
        if not vec:
            return None
        return struct.pack(f"<{len(vec)}f", *vec)
    except Exception:
        logger.debug("[reembed] encode failed for %r", text[:80], exc_info=True)
        return None


def _upsert_remote_vector(
    conn: sqlite3.Connection,
    emb: Any,
    item_id: str,
    text: str,
    *,
    tenant_id: str,
    meta: dict[str, Any],
) -> None:
    """Best-effort pgvector/Qdrant upsert so rebuild fills the scale index."""
    try:
        vec = emb.encode(text)
        if not vec:
            return
        from kazma_core.memory.backends import get_vector_backend

        get_vector_backend(conn).upsert(
            item_id, list(vec), tenant_id=tenant_id, meta=meta
        )
    except Exception:
        logger.debug(
            "[reembed] remote vector upsert failed for %s", item_id, exc_info=True
        )


def _backup(db_path: str) -> None:
    """Native sqlite backup to ``<db>.pre_reembed`` (once per run)."""
    backup_path = db_path + ".pre_reembed"
    if os.path.exists(backup_path):
        return
    try:
        sconn = sqlite3.connect(db_path)
        dconn = sqlite3.connect(backup_path)
        try:
            sconn.backup(dconn)
        finally:
            dconn.close()
            sconn.close()
        logger.info("[reembed] backup → %s", backup_path)
    except Exception:
        logger.warning("[reembed] backup failed (continuing)", exc_info=True)


def rebuild_embeddings(
    progress: ProgressCallback | None = None,
    *,
    model_name: str | None = None,
) -> dict[str, Any]:
    """Re-embed every row whose model version ≠ the configured model.

    Runs synchronously — call via ``asyncio.to_thread`` from the server.
    Returns a summary dict. *progress* is invoked as ``(done, total)``.

    Safe to re-run: rows already stamped with the current model are skipped.
    """
    from kazma_core.memory.embedder import get_embedder, get_embedding_model_name
    from kazma_core.paths import data_dir, primary_memory_db

    target_model = model_name or get_embedding_model_name()
    db_path = primary_memory_db()
    if not db_path or not os.path.isfile(db_path):
        raise FileNotFoundError(f"memory_state.db not found at {db_path}")

    _backup(db_path)

    started = datetime.now(UTC).isoformat()
    summary: dict[str, Any] = {"episodes": 0, "beliefs": 0, "skipped_rows": 0}

    conn = sqlite3.connect(db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")

        # Only invalidate rows that are NOT already in the target vector
        # space. NULL-version rows are treated as stale (legacy/unknown).
        conn.execute(
            "UPDATE episodes SET embedding = NULL "
            "WHERE embedding_model_version IS NULL OR embedding_model_version != ?",
            (target_model,),
        )
        conn.execute(
            "UPDATE beliefs SET embedding = NULL "
            "WHERE embedding_model_version IS NULL OR embedding_model_version != ?",
            (target_model,),
        )
        conn.commit()

        logger.info("[reembed] loading embedder (model=%s) …", target_model)
        t0 = time.monotonic()
        emb = get_embedder()
        if emb is None:
            raise RuntimeError("Embedder failed to initialize — check server logs.")
        logger.info("[reembed] embedder ready in %.1fs", time.monotonic() - t0)

        # ── Episodes ────────────────────────────────────────────────────
        rows = conn.execute(
            "SELECT id, tenant_id, summary_text, user_text, assistant_text, tier "
            "FROM episodes WHERE embedding IS NULL"
        ).fetchall()
        total = len(rows)
        for i, row in enumerate(rows, 1):
            text = (row["summary_text"] or row["user_text"] or row["assistant_text"] or "").strip()
            if text:
                blob = _encode_text(emb, text)
                if blob:
                    conn.execute(
                        "UPDATE episodes SET embedding = ?, embedding_model_version = ? WHERE id = ?",
                        (blob, target_model, row["id"]),
                    )
                _upsert_remote_vector(
                    conn,
                    emb,
                    str(row["id"]),
                    text,
                    tenant_id=str(row["tenant_id"] or "default"),
                    meta={"kind": "episode", "tier": row["tier"] or "episodic"},
                )
            if i % _BATCH_SIZE == 0:
                conn.commit()
                if progress:
                    progress(i, total)
        conn.commit()
        summary["episodes"] = total
        if progress:
            progress(total, total)

        # ── Beliefs ─────────────────────────────────────────────────────
        rows = conn.execute(
            "SELECT id, tenant_id, subject, predicate, object FROM beliefs WHERE embedding IS NULL"
        ).fetchall()
        total = len(rows)
        for i, row in enumerate(rows, 1):
            text = f"{row['subject']} {row['predicate']} {row['object']}"
            blob = _encode_text(emb, text)
            if blob:
                conn.execute(
                    "UPDATE beliefs SET embedding = ?, embedding_model_version = ? WHERE id = ?",
                    (blob, target_model, row["id"]),
                )
            _upsert_remote_vector(
                conn,
                emb,
                str(row["id"]),
                text,
                tenant_id=str(row["tenant_id"] or "default"),
                meta={"kind": "belief", "tier": "semantic"},
            )
            if i % _BATCH_SIZE == 0:
                conn.commit()
                if progress:
                    progress(i, total)
        conn.commit()
        summary["beliefs"] = total
        if progress:
            progress(total, total)

        # ── Chroma derived store (L3/L4 legacy index) ───────────────────
        vec_dir = os.path.join(data_dir(), "vector_memory")
        if os.path.isdir(vec_dir):
            try:
                shutil.rmtree(vec_dir)
                logger.info("[reembed] removed Chroma store %s", vec_dir)
            except Exception:
                logger.warning("[reembed] could not remove Chroma store", exc_info=True)
    finally:
        conn.close()

    summary["model"] = target_model
    summary["started_at"] = started
    summary["finished_at"] = datetime.now(UTC).isoformat()
    return summary


# ── Continuous repair (the 15-minute maintenance cadence) ──────────────────

#: The text each writer embeds -- ``dual_write`` for episodes (the summary, else
#: the question, else the answer) and ``belief_mutation`` for beliefs. A repair
#: must use the same text, or its vector would sit apart from a fresh one.
_EPISODE_TEXT_SQL = (
    "COALESCE(NULLIF(TRIM(summary_text), ''), NULLIF(TRIM(user_text), ''), "
    "NULLIF(TRIM(assistant_text), ''))"
)

#: Vector size each embedding model produced when probed in this process, so
#: a pass with nothing to repair never loads the model or pays for a call.
_MODEL_DIMS: dict[str, int] = {}
_BELIEF_TEXT_SQL = "TRIM(subject || ' ' || predicate || ' ' || object)"


def _unsearchable_sql(kind: str, model: str) -> str:
    """``FROM ... WHERE`` for the rows meaning search cannot compare.

    Parameters, in order: tenant, vector byte length, then *model* when set.
    """
    stale = "embedding IS NULL OR length(embedding) != ?"
    if model:
        stale += (
            " OR (embedding_model_version IS NOT NULL AND embedding_model_version != ''"
            " AND embedding_model_version != ?)"
        )
    if kind == "episodes":
        return f"episodes WHERE tenant_id = ? AND ({stale}) AND {_EPISODE_TEXT_SQL} IS NOT NULL"
    return (
        "beliefs WHERE tenant_id = ? AND valid_until IS NULL AND invalidated_at IS NULL"
        f" AND ({stale}) AND {_BELIEF_TEXT_SQL} != ''"
    )


def _tenants(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT tenant_id FROM episodes UNION SELECT tenant_id FROM beliefs"
    ).fetchall()
    return sorted({str(r[0] or "default") for r in rows})


def _embed(emb: Any, text: str) -> list[float] | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        vec = emb.encode(text)
    except Exception:  # noqa: BLE001 -- one row that will not encode is skipped
        logger.debug("[reembed] encode failed for %r", text[:80], exc_info=True)
        return None
    return [float(x) for x in vec] if vec is not None and len(vec) else None


def _unsearchable_counts(
    conn: sqlite3.Connection, *, dim: int, model: str, tenant_id: str | None = None
) -> dict[str, int]:
    """Memories meaning search cannot compare right now, per kind."""
    out = {"episodes": 0, "beliefs": 0}
    for tenant in [tenant_id] if tenant_id else _tenants(conn):
        for kind in out:
            params: list[Any] = [tenant, int(dim) * 4] + ([model] if model else [])
            out[kind] += int(
                conn.execute(
                    f"SELECT count(*) FROM {_unsearchable_sql(kind, model)}", params
                ).fetchone()[0]
            )
    return out


def repair_unsearchable_vectors(
    conn: sqlite3.Connection,
    *,
    tenant_id: str | None = None,
    time_budget_s: float = 20.0,
    batch: int = 32,
    embedder: Any = None,
    model: str | None = None,
) -> dict[str, int]:
    """Re-encode, in place, every memory meaning search cannot compare.

    Unsearchable: no vector, a vector of another size, or one stamped with
    another embedding model -- episodes of every tier and every current
    belief. Until 2026-09-26 only missing vectors were repaired, 100 a day,
    and a model switch left every older memory in the old vector space until
    someone clicked Rebuild. The old vector stays until its replacement is
    written, so a switch never leaves a memory with nothing. Newest first,
    bounded by *time_budget_s* per call -- the next pass continues. A remote
    index gets each new vector too. Returns ``{"episodes", "beliefs",
    "remaining"}`` (``remaining`` is -1 when no embedder is available).
    """
    from kazma_core.memory.embedder import (
        get_embedder,
        get_embedding_dim,
        get_embedding_model_name,
    )

    model = get_embedding_model_name() if model is None else model
    if embedder is None:
        # Nothing to repair: no model load, no paid embedding call. Until the
        # model has been probed the size is the configured one; a wrong
        # configured size only makes this say "maybe", and the probe settles it.
        known = _MODEL_DIMS.get(model) or int(get_embedding_dim() or 0)
        if known and not any(
            _unsearchable_counts(conn, dim=known, model=model, tenant_id=tenant_id).values()
        ):
            return {"episodes": 0, "beliefs": 0, "remaining": 0}
    emb = embedder if embedder is not None else get_embedder()
    if emb is None:
        return {"episodes": 0, "beliefs": 0, "remaining": -1}
    dim = _MODEL_DIMS.get(model) if embedder is None else None
    if not dim:
        probe = _embed(emb, "dimension probe")
        if not probe:
            return {"episodes": 0, "beliefs": 0, "remaining": -1}
        dim = len(probe)  # what the model produces, not what the config claims
        if embedder is None:
            _MODEL_DIMS[model] = dim
    tenants = [tenant_id] if tenant_id else _tenants(conn)
    remote = _remote_index(conn)
    deadline = time.monotonic() + max(0.0, float(time_budget_s))
    done = {"episodes": 0, "beliefs": 0}
    skipped: set[str] = set()  # text that would not encode, this pass only
    for kind in ("episodes", "beliefs"):
        text_sql = _EPISODE_TEXT_SQL if kind == "episodes" else _BELIEF_TEXT_SQL
        extra = ", tier, session_id" if kind == "episodes" else ", '', ''"
        order = "created_at" if kind == "episodes" else "ingested_at"
        for tenant in tenants:
            while time.monotonic() < deadline:
                params: list[Any] = [tenant, dim * 4] + ([model] if model else [])
                exclude = ""
                if skipped:
                    exclude = f" AND id NOT IN ({','.join('?' for _ in skipped)})"
                    params.extend(sorted(skipped))
                rows = conn.execute(
                    f"SELECT id, {text_sql}{extra} FROM {_unsearchable_sql(kind, model)}"
                    f"{exclude} ORDER BY {order} DESC LIMIT ?",
                    [*params, int(batch)],
                ).fetchall()
                if not rows:
                    break
                for rid, text, tier, session_id in rows:
                    vec = _embed(emb, text)
                    if not vec or len(vec) != dim:
                        skipped.add(str(rid))
                        continue
                    conn.execute(
                        f"UPDATE {kind} SET embedding = ?, embedding_model_version = ? WHERE id = ?",
                        (struct.pack(f"<{dim}f", *vec), model, rid),
                    )
                    done[kind] += 1
                    if remote is not None:
                        _upsert_remote(remote, kind, str(rid), vec, tenant, tier, session_id)
                conn.commit()
    remaining = sum(_unsearchable_counts(conn, dim=dim, model=model).values())
    if done["episodes"] or done["beliefs"]:
        logger.info(
            "[memory] re-encoded %d episode and %d belief vector(s); %d still unsearchable",
            done["episodes"], done["beliefs"], remaining,
        )
    return {**done, "remaining": remaining}


def vector_repair_counts(conn: sqlite3.Connection) -> dict[str, Any]:
    """How many memories meaning search can compare now, and how many wait
    for the repair pass, per kind (every tenant). No model load."""
    from kazma_core.memory.embedder import get_embedding_dim, get_embedding_model_name

    model = get_embedding_model_name()
    dim = _MODEL_DIMS.get(model) or int(get_embedding_dim() or 0)
    pending = _unsearchable_counts(conn, dim=dim, model=model)
    totals = {
        "episodes": int(
            conn.execute(
                f"SELECT count(*) FROM episodes WHERE {_EPISODE_TEXT_SQL} IS NOT NULL"
            ).fetchone()[0]
        ),
        "beliefs": int(
            conn.execute(
                "SELECT count(*) FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL"
                f" AND {_BELIEF_TEXT_SQL} != ''"
            ).fetchone()[0]
        ),
    }
    out: dict[str, Any] = {"model": model, "dim": dim}
    for kind, total in totals.items():
        out[kind] = {"total": total, "searchable": max(0, total - pending[kind]), "pending": pending[kind]}
    return out


def run_vector_repair_pass(*, time_budget_s: float = 20.0) -> dict[str, int]:
    """One repair pass over the primary memory database (15-minute cadence).

    Returns :func:`repair_unsearchable_vectors`'s counts, or zeros when there
    is no memory database yet.
    """
    from kazma_core.config_store import apply_sqlite_pragmas
    from kazma_core.paths import primary_memory_db

    zero = {"episodes": 0, "beliefs": 0, "remaining": 0}
    db = primary_memory_db()
    if not db or not os.path.isfile(db):
        return zero
    conn = sqlite3.connect(db, timeout=15)
    try:
        apply_sqlite_pragmas(conn, busy_timeout=15000)
        return repair_unsearchable_vectors(conn, time_budget_s=time_budget_s)
    except sqlite3.OperationalError:  # the schema is not there yet
        logger.debug("[reembed] memory database not ready for repair", exc_info=True)
        return zero
    finally:
        conn.close()


def _remote_index(conn: sqlite3.Connection) -> Any:
    """The configured remote vector index, or None for the local-only setup."""
    from kazma_core.memory.backends import LocalSqliteVectorBackend, get_vector_backend

    try:
        backend = get_vector_backend(conn)
    except RuntimeError:  # remote down with failover=raise: the local repair still runs
        logger.debug("[reembed] vector backend unavailable for repair", exc_info=True)
        return None
    return None if isinstance(backend, LocalSqliteVectorBackend) else backend


def _upsert_remote(
    backend: Any, kind: str, rid: str, vec: list[float], tenant: str, tier: str, session_id: str
) -> None:
    meta = (
        {"kind": "episode", "tier": tier or "episodic", "session_id": session_id}
        if kind == "episodes"
        else {"kind": "belief", "tier": "semantic"}
    )
    # A backend reports a failed write by returning False; the local row is the truth.
    if not backend.upsert(rid, vec, tenant_id=tenant, meta=meta):
        logger.debug("[reembed] remote index did not take %s", rid)
