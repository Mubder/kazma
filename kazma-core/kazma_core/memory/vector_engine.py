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
import threading
from typing import Any

from kazma_core.spaces.scope import space_named_or_bound

logger = logging.getLogger(__name__)

__all__ = ["BELIEF_ACTIVE_SQL", "NARROW_AUDIT_EVERY", "RECALLABLE_TIERS", "VectorEngine", "narrowing_health"]

#: Every episode tier recall may return. Archived memories are recallable:
#: they rank below active ones (recall weights them) but are never out of
#: reach -- one tier list for every search path.
RECALLABLE_TIERS: tuple[str, ...] = ("working", "recall", "episodic", "archived")

#: The beliefs recall may return: current, not invalidated.
BELIEF_ACTIVE_SQL = "valid_until IS NULL AND invalidated_at IS NULL"

_NUMPY_CHUNK = 2048
#: More candidates than this (most rows without a code: a model switch, the
#: first passes of the code sweep) and the search reads every row instead.
_NARROW_IN_MAX = 20_000
#: The first narrowed search and every this-many-th also read every row
#: (:meth:`VectorEngine._narrowed`): what narrowing missed is counted.
NARROW_AUDIT_EVERY = 50
_NARROW_LOCK = threading.Lock()
_NARROW_STATE: dict[str, int] = {"searches": 0, "audited": 0, "differed": 0}


def narrowing_health() -> dict[str, int]:
    """This process's narrowed searches, how many were also read in full,
    and how many of those differed (memory health)."""
    with _NARROW_LOCK:
        return dict(_NARROW_STATE)


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
        tenant_id: str | None = None,
        tier: str | list[str] | tuple[str, ...] | None = "recall",
        limit: int = 10,
    ) -> list[tuple[str, float]]:
        """Rank EVERY eligible episode by cosine; return ``(id, similarity)``.

        ``tier`` restricts to one tier, a list of tiers, or ``None`` for all.
        """
        tenant_id = space_named_or_bound(tenant_id)
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
        return self._exact("episodes", where, params, query_vec, limit, narrow=("episode", tenant_id))

    def search_beliefs(
        self,
        query_vec: list[float] | None,
        *,
        tenant_id: str | None = None,
        limit: int = 10,
    ) -> list[tuple[str, float]]:
        """Rank EVERY current belief by cosine; return ``(id, similarity)``."""
        tenant_id = space_named_or_bound(tenant_id)
        return self._exact(
            "beliefs", ["tenant_id = ?", BELIEF_ACTIVE_SQL], [tenant_id], query_vec, limit,
            narrow=("belief", tenant_id),
        )

    def nearest_entities(
        self,
        query_vec: list[float] | None,
        *,
        tenant_id: str,
        limit: int = 1,
    ) -> list[tuple[str, float]]:
        """The tenant's entities whose stored name vector is nearest:
        ``(id, similarity)``. An entity without a comparable vector is left
        out until vector repair stores one (Memory audit M18)."""
        return self._exact("entities", ["tenant_id = ?"], [tenant_id], query_vec, limit)

    def similarities(
        self,
        query_vec: list[float] | None,
        ids: list[str],
        *,
        kind: str = "episode",
    ) -> dict[str, float]:
        """Cosine of exactly these rows with the question, by id.

        Recall judges every candidate on meaning, including one only its
        words found. A row without a comparable vector (none, another size,
        another model's) is left out: its similarity is unknown, not zero.
        """
        wanted = list(dict.fromkeys(str(i) for i in ids if i))
        if not wanted:
            return {}
        table = "beliefs" if kind == "belief" else "episodes"
        where = [f"id IN ({','.join('?' for _ in wanted)})"]
        return dict(self._exact(table, where, wanted, query_vec, len(wanted)))

    def best_passages(
        self,
        query_vec: list[float] | None,
        *,
        tenant_id: str | None = None,
        tier: list[str] | tuple[str, ...] = RECALLABLE_TIERS,
        limit: int = 10,
        episode_ids: list[str] | None = None,
    ) -> dict[str, tuple[float, str, int, int]]:
        """Each memory's best passage for the question (the memory plan's W5,
        ``memory.passages``): ``{episode id: (similarity, side, start,
        stop)}``, the best *limit* memories (or exactly *episode_ids*), every
        passage of the space's recallable memories compared, comparable
        vectors only. Empty when the memory has no passage table."""
        if not query_vec or limit <= 0 or not self.available:
            return {}
        tenant_id = space_named_or_bound(tenant_id)
        tiers = [str(t) for t in tier if t]
        where = ["p.tenant_id = ?", "e.id = p.episode_id", f"e.tier IN ({','.join('?' for _ in tiers)})",
                 "length(p.embedding) = ?"]
        params: list[Any] = [tenant_id, *tiers, len(query_vec) * 4]
        if self.model:
            where.append("(p.embedding_model_version = '' OR p.embedding_model_version = ?)")
            params.append(self.model)
        if episode_ids is not None:
            wanted = list(dict.fromkeys(str(i) for i in episode_ids if i))
            if not wanted:
                return {}
            where.append(f"p.episode_id IN ({','.join('?' for _ in wanted)})")
            params.extend(wanted)
            limit = len(wanted)
        predicate = " AND ".join(where)
        try:
            rows = (self._passages_sqlite_vec(predicate, params, query_vec, limit) if self.has_sqlite_vec
                    else self._passages_numpy(predicate, params, query_vec, limit))
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return {}
            raise
        return {eid: (sim, side, start, stop) for eid, sim, side, start, stop in rows}

    def _passages_sqlite_vec(self, predicate: str, params: list[Any], query_vec: list[float],
                             limit: int) -> list[tuple[str, float, str, int, int]]:
        # MIN() with bare columns: SQLite reads them from the row holding the
        # minimum, the memory's best passage. A zero vector's NULL is skipped.
        sql = (
            "SELECT p.episode_id, MIN(vec_distance_cosine(p.embedding, ?)) AS d, p.side, p.start, p.stop "
            f"FROM episode_passages p, episodes e WHERE {predicate} "  # nosec B608 - '?' placeholders and fixed text
            "GROUP BY p.episode_id HAVING d IS NOT NULL ORDER BY d ASC LIMIT ?"
        )
        rows = self.conn.execute(sql, [self._query_blob(query_vec), *params, int(limit)]).fetchall()
        return [(str(r[0]), 1.0 - float(r[1]), str(r[2]), int(r[3]), int(r[4])) for r in rows]

    def _passages_numpy(self, predicate: str, params: list[Any], query_vec: list[float],
                        limit: int) -> list[tuple[str, float, str, int, int]]:
        np = self._numpy
        if np is None:
            return []
        q = np.asarray(query_vec, dtype=np.float32)
        qn = float(np.linalg.norm(q))
        if qn == 0.0:
            return []
        q = q / qn
        best: dict[str, tuple[float, str, int, int]] = {}
        cur = self.conn.execute(
            f"SELECT p.episode_id, p.side, p.start, p.stop, p.embedding FROM episode_passages p, episodes e "  # nosec B608 - '?' placeholders and fixed text
            f"WHERE {predicate}", params)
        while True:
            chunk = cur.fetchmany(_NUMPY_CHUNK)
            if not chunk:
                break
            mat = np.frombuffer(b"".join(bytes(r[4]) for r in chunk), dtype=np.float32).reshape(len(chunk), q.shape[0])
            norms = np.linalg.norm(mat, axis=1)
            sims = np.divide(mat @ q, norms, out=np.full(len(chunk), np.nan, dtype=np.float32), where=norms > 0)
            for r, sim in zip(chunk, sims.tolist()):
                if sim != sim:
                    continue
                eid = str(r[0])
                if eid not in best or sim > best[eid][0]:
                    best[eid] = (float(sim), str(r[1]), int(r[2]), int(r[3]))
        ranked = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)[:limit]
        return [(eid, sim, side, start, stop) for eid, (sim, side, start, stop) in ranked]

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
        *,
        narrow: tuple[str, str] | None = None,
    ) -> list[tuple[str, float]]:
        if not query_vec or limit <= 0 or not self.available:
            return []
        clause, clause_params = self.comparable_clause(len(query_vec))
        predicate = " AND ".join([*where, clause])
        all_params = [*params, *clause_params]
        if narrow is not None and self.has_numpy:
            narrowed = self._narrowed(table, predicate, all_params, query_vec, limit, narrow)
            if narrowed is not None:
                return narrowed
        return self._exact_scan(table, predicate, all_params, query_vec, limit)

    def _narrowed(
        self, table: str, predicate: str, params: list[Any], query_vec: list[float], limit: int,
        narrow: tuple[str, str],
    ) -> list[tuple[str, float]] | None:
        """Past :data:`vector_codes.NARROW_FROM` coded rows of the kind in the
        space: the candidates by their codes, re-scored exactly
        (:mod:`kazma_core.memory.vector_codes`). None: search in full."""
        from kazma_core.memory import vector_codes

        kind, tenant = narrow
        try:
            if vector_codes.coded_count(self.conn, kind, tenant) < vector_codes.NARROW_FROM:
                return None
            rowids = vector_codes.candidates(self.conn, self._numpy, kind=kind, predicate=predicate,
                                             params=params, query_vec=query_vec, limit=limit)
        except (sqlite3.Error, ValueError):
            logger.warning("[vector_engine] narrowing %s failed; searching in full", table, exc_info=True)
            return None
        if len(rowids) > _NARROW_IN_MAX:
            return None  # most rows have no code yet (a model switch): the full scan
        found = self._rescore(table, predicate, params, rowids, query_vec, limit) if rowids else []
        with _NARROW_LOCK:
            _NARROW_STATE["searches"] += 1
            audit = (_NARROW_STATE["searches"] - 1) % NARROW_AUDIT_EVERY == 0
        if audit:
            # Exact search stays the truth: the first narrowed search and
            # every NARROW_AUDIT_EVERY-th also read every row, and a
            # difference is said, and the full answer used.
            full = self._exact_scan(table, predicate, params, query_vec, limit)
            with _NARROW_LOCK:
                _NARROW_STATE["audited"] += 1
                differed = [i for i, _s in full] != [i for i, _s in found]
                if differed:
                    _NARROW_STATE["differed"] += 1
            if differed:
                logger.warning("[vector_engine] the narrowed %s search differed from the full scan "
                               "(%d candidates for %d): the full answer is used", table, len(rowids), limit)
                return full
        return found

    def _rescore(
        self, table: str, predicate: str, params: list[Any], rowids: list[int], query_vec: list[float],
        limit: int,
    ) -> list[tuple[str, float]]:
        """The exact scores of exactly these rows, by the full scan's own
        query (rowid order, so equal scores keep a full scan's order)."""
        marks = ",".join("?" for _ in rowids)
        return self._exact_scan(table, f"{predicate} AND rowid IN ({marks})", [*params, *rowids],
                                query_vec, limit)

    def _exact_scan(
        self, table: str, predicate: str, all_params: list[Any], query_vec: list[float], limit: int,
    ) -> list[tuple[str, float]]:
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
