"""V2 Vector Engine — exact similarity over EVERY stored memory.

Both memory kinds are searched here: episodes (conversation memories) and
beliefs (facts). Each search scores every eligible row and returns the best —
there is no candidate cap, no pre-selection by importance or age, and no
sampling. Until 2026-09-26 neither kind worked that way: episodes were
compared within an arbitrary ``LIMIT limit*16`` slice fetched with no ORDER BY
(on the live install the 60 newest of 300 conversation memories were never
searched), and beliefs within the 400 most "important". A memory the search
cannot see is a memory the agent has lost, whatever the database holds.

Capability ladder:

1. **sqlite-vec** (a core dependency) — ``vec_distance_cosine`` evaluated in
   SQL over every eligible row. Exact, read-only, ~1 ms per 1,000 rows of
   1024-dim vectors (20,000 rows: ~21 ms, measured 2026-09-26).
2. **NumPy** — the same exact scan in chunks (bounded memory, merged top-k).
3. **Degraded** — no vector path; recall falls back to FTS5 only.

A row is comparable when its vector has the query's dimension and was made by
the current embedding model (``embedding_model_version``; rows without a
version are legacy rows of the same model). Rows that are not comparable
stay out of the ranking — a different model's vector space gives meaningless
scores — and the repair sweep (:mod:`kazma_core.memory.reembed`) re-encodes
them, so they return within minutes rather than staying lost.

The engine never writes: no temporary tables, no DDL, no commits.
"""

from __future__ import annotations

import heapq
import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["BELIEF_ACTIVE_SQL", "RECALLABLE_TIERS", "VectorEngine"]

#: Every episode tier recall may return. Archived memories are recallable:
#: they rank below active ones (recall weights them) but are never out of
#: reach -- one tier list for every search path.
RECALLABLE_TIERS: tuple[str, ...] = ("working", "recall", "episodic", "archived")

#: The beliefs recall may return: current, not invalidated.
BELIEF_ACTIVE_SQL = "valid_until IS NULL AND invalidated_at IS NULL"

_NUMPY_CHUNK = 2048


def _current_model_name() -> str:
    try:
        from kazma_core.memory.embedder import get_embedding_model_name

        return str(get_embedding_model_name() or "")
    except Exception:  # noqa: BLE001 -- unknown model: compare by dimension only
        logger.debug("[vector_engine] embedding model name unreadable", exc_info=True)
        return ""


class VectorEngine:
    """Read-only exact vector similarity over the V2 memory tables.

    Args:
        conn: An open connection to ``memory_state.db``. The engine does
            NOT own this connection (shared with the recall engine).
        model: The embedding model the query vectors come from. ``None``
            reads the configured model; ``""`` compares by dimension only.
    """

    def __init__(self, conn: sqlite3.Connection, *, model: str | None = None) -> None:
        self.conn = conn
        self.model = _current_model_name() if model is None else str(model)
        self.has_sqlite_vec = False
        self.has_numpy = False
        self._numpy: Any = None
        self._init_backends()

    def _init_backends(self) -> None:
        """Probe sqlite-vec, then NumPy. Both are best-effort."""
        try:
            self.conn.enable_load_extension(True)
            import sqlite_vec  # type: ignore[import-untyped]

            sqlite_vec.load(self.conn)
            self.conn.execute("SELECT vec_version()").fetchone()
            self.has_sqlite_vec = True
        except Exception as exc:  # noqa: BLE001 -- optional native extension
            self.has_sqlite_vec = False
            logger.debug("[vector_engine] sqlite-vec unavailable (%s)", exc)
        finally:
            try:
                self.conn.enable_load_extension(False)
            except Exception:  # noqa: BLE001 -- connection without the switch
                logger.debug("[vector_engine] could not disable extension loading", exc_info=True)
        try:
            import numpy as np  # type: ignore[import-untyped]

            self._numpy = np
            self.has_numpy = True
        except ImportError:
            self.has_numpy = False
        if not (self.has_sqlite_vec or self.has_numpy):
            logger.warning(
                "[vector_engine] no sqlite-vec AND no numpy -- "
                "vector search disabled (FTS5-only retrieval)"
            )

    @property
    def available(self) -> bool:
        """True if ANY vector backend is usable."""
        return self.has_sqlite_vec or self.has_numpy

    # ── Public API ─────────────────────────────────────────────────────

    def search(
        self,
        query_vec: list[float] | None,
        *,
        tenant_id: str = "default",
        tier: str | list[str] | tuple[str, ...] | None = "recall",
        limit: int = 10,
    ) -> list[tuple[str, float]]:
        """Rank EVERY eligible episode by cosine; return ``(id, similarity)``.

        ``tier`` restricts to one tier, a list of tiers, or ``None`` for all.
        """
        where = ["tenant_id = ?"]
        params: list[Any] = [tenant_id]
        if isinstance(tier, (list, tuple)):
            tiers = [str(t) for t in tier if t]
            if tiers:
                where.append(f"tier IN ({','.join('?' for _ in tiers)})")
                params.extend(tiers)
        elif tier:
            where.append("tier = ?")
            params.append(str(tier))
        return self._exact("episodes", where, params, query_vec, limit)

    def search_beliefs(
        self,
        query_vec: list[float] | None,
        *,
        tenant_id: str = "default",
        limit: int = 10,
    ) -> list[tuple[str, float]]:
        """Rank EVERY current belief by cosine; return ``(id, similarity)``."""
        return self._exact(
            "beliefs", ["tenant_id = ?", BELIEF_ACTIVE_SQL], [tenant_id], query_vec, limit
        )

    def comparable_clause(self, dim: int) -> tuple[str, list[Any]]:
        """SQL predicate for rows whose vector can be compared with a *dim* query."""
        sql = "embedding IS NOT NULL AND length(embedding) = ?"
        params: list[Any] = [int(dim) * 4]
        if self.model:
            sql += (
                " AND (embedding_model_version IS NULL OR embedding_model_version = ''"
                " OR embedding_model_version = ?)"
            )
            params.append(self.model)
        return sql, params

    # ── Exact search ───────────────────────────────────────────────────

    def _exact(
        self,
        table: str,
        where: list[str],
        params: list[Any],
        query_vec: list[float] | None,
        limit: int,
    ) -> list[tuple[str, float]]:
        if not query_vec or limit <= 0 or not self.available:
            return []
        clause, clause_params = self.comparable_clause(len(query_vec))
        predicate = " AND ".join([*where, clause])
        all_params = [*params, *clause_params]
        if self.has_sqlite_vec:
            try:
                return self._exact_sqlite_vec(table, predicate, all_params, query_vec, limit)
            except sqlite3.Error:
                logger.warning(
                    "[vector_engine] sqlite-vec search of %s failed; using numpy", table,
                    exc_info=True,
                )
        if self.has_numpy:
            try:
                return self._exact_numpy(table, predicate, all_params, query_vec, limit)
            except (sqlite3.Error, ValueError):
                logger.warning("[vector_engine] numpy search of %s failed", table, exc_info=True)
        return []

    def _query_blob(self, query_vec: list[float]) -> bytes:
        if self._numpy is not None:
            return self._numpy.asarray(query_vec, dtype=self._numpy.float32).tobytes()
        import struct

        return struct.pack(f"{len(query_vec)}f", *query_vec)

    def _exact_sqlite_vec(
        self, table: str, predicate: str, params: list[Any], query_vec: list[float], limit: int
    ) -> list[tuple[str, float]]:
        # A zero vector has no direction: vec_distance_cosine returns NULL for
        # it, and NULLs sort FIRST -- filter them out before ordering.
        sql = (
            "SELECT id, d FROM ("
            f"SELECT id, vec_distance_cosine(embedding, ?) AS d FROM {table} WHERE {predicate}"
            ") WHERE d IS NOT NULL ORDER BY d ASC LIMIT ?"
        )
        rows = self.conn.execute(sql, [self._query_blob(query_vec), *params, int(limit)]).fetchall()
        return [(str(r[0]), 1.0 - float(r[1])) for r in rows]

    def _exact_numpy(
        self, table: str, predicate: str, params: list[Any], query_vec: list[float], limit: int
    ) -> list[tuple[str, float]]:
        np = self._numpy
        q = np.asarray(query_vec, dtype=np.float32)
        qn = float(np.linalg.norm(q))
        if qn == 0.0:
            return []
        q = q / qn
        best: list[tuple[float, str]] = []
        cur = self.conn.execute(f"SELECT id, embedding FROM {table} WHERE {predicate}", params)
        while True:
            chunk = cur.fetchmany(_NUMPY_CHUNK)
            if not chunk:
                break
            ids = [str(r[0]) for r in chunk]
            mat = np.frombuffer(b"".join(bytes(r[1]) for r in chunk), dtype=np.float32)
            mat = mat.reshape(len(chunk), q.shape[0])
            norms = np.linalg.norm(mat, axis=1)
            sims = np.divide(mat @ q, norms, out=np.full(len(chunk), np.nan, dtype=np.float32),
                             where=norms > 0)
            for eid, sim in zip(ids, sims.tolist()):
                if sim != sim:  # NaN: zero vector, no direction
                    continue
                if len(best) < limit:
                    heapq.heappush(best, (sim, eid))
                elif sim > best[0][0]:
                    heapq.heapreplace(best, (sim, eid))
        return [(eid, float(sim)) for sim, eid in sorted(best, reverse=True)]
