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
    "reset_rebuild_status",
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
