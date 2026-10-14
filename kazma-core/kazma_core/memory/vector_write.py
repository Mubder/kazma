"""One vector per memory row, computed once, written outside every write lock.

Memory audits M14 and M15 (2026-10-13): an episode was embedded twice -- a
guarded write under the writer's lock, then a second encode and
``LocalSqliteVectorBackend.upsert`` on the same shared connection, without the
lock and without the tier guard, so a forget landing in between got its vector
back. A fact was embedded inside the mutation's ``BEGIN IMMEDIATE`` under the
process-wide mutation lock (an embedder call can take two minutes, and every
belief write in the process waited), and the local upsert committed the
caller's transaction.

The rules here:

* :func:`encode_vector` is called before any transaction or lock;
* :func:`write_local_vector` is one guarded ``UPDATE`` -- the row must still
  hold what was embedded -- that commits only a transaction it began;
* :func:`index_remotely` writes the remote index only (never the local
  store: the guarded write is that), and takes the vector back out when the
  row stopped holding it meanwhile.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Sequence
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["encode_vector", "index_remotely", "write_local_vector"]


def encode_vector(text: str) -> list[float] | None:
    """The shared embedder's vector for *text*, or None (no embedder, an
    empty text, a failed call). Call it with no transaction open."""
    body = (text or "").strip()
    if not body:
        return None
    from kazma_core.memory.embedder import get_embedder

    emb = get_embedder()
    if emb is None:
        return None
    try:
        vec = emb.encode(body)
    except Exception:  # noqa: BLE001 - a failed embedding leaves the row for vector repair
        logger.debug("[vector_write] embedding failed", exc_info=True)
        return None
    return [float(x) for x in vec] if vec else None


def write_local_vector(
    conn: sqlite3.Connection,
    table: str,
    row_id: str,
    vec: Sequence[float],
    *,
    still_sql: str,
    still_params: Sequence[Any] = (),
) -> bool:
    """Store *vec* on the row, once: only while it has no vector and still
    matches *still_sql* (it holds what was embedded). Commits only a
    transaction this call began -- never the caller's."""
    if table not in ("episodes", "beliefs") or not row_id or not vec:
        return False
    from kazma_core.memory.embedder import get_embedding_model_name, serialize_f32_embedding

    blob = serialize_f32_embedding(list(vec))
    if not blob:
        return False
    began = not conn.in_transaction
    cur = conn.execute(
        f"UPDATE {table} SET embedding = ?, embedding_model_version = ? "
        f"WHERE id = ? AND embedding IS NULL AND {still_sql}",
        (blob, get_embedding_model_name() or "", row_id, *still_params),
    )
    if began and conn.in_transaction:
        conn.commit()
    return bool(cur.rowcount)


def index_remotely(
    row_id: str,
    vec: Sequence[float],
    *,
    tenant_id: str,
    meta: dict[str, Any],
    still_held: Callable[[], bool],
) -> bool:
    """Upsert *vec* into the remote vector index, when one is configured; the
    vector is taken back out if the row no longer holds it afterwards (a
    forget landed while this ran). The local store is never written here."""
    from kazma_core.memory.backends import get_remote_vector_backend

    remote = get_remote_vector_backend()
    if remote is None or not vec or not getattr(remote, "available", False):
        return False
    try:
        remote.upsert(row_id, list(vec), tenant_id=tenant_id, meta=meta)
        if not still_held():
            remote.delete(row_id, tenant_id=tenant_id)
            return False
        return True
    except Exception:  # noqa: BLE001 - the remote index is best effort; vector repair re-indexes
        logger.debug("[vector_write] remote vector upsert failed for %s", row_id, exc_info=True)
        return False
