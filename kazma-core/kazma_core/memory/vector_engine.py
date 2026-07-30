"""V2 Vector Engine — sqlite-vec with guarded NumPy fallback.

Three-tier capability ladder (per resolution #1 — zero-dependency by
default, NumPy only when already present via the ``[rag]`` extra):

1. **sqlite-vec** (preferred) — native C-extension virtual table, fastest.
2. **NumPy fallback** — in-memory cosine similarity over float32 BLOBs.
   Activated only when ``numpy`` is importable; never a hard dependency.
3. **Degraded** — no vector path; the caller falls back to FTS5-only.

The engine queries the V2 ``episodes`` table (where the consolidator
stores embeddings alongside the tiered text). It is scraping/LLM-scoped
to read-only retrieval — never writes.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["VectorEngine"]


class VectorEngine:
    """Read-only vector similarity over the V2 ``episodes`` table.

    Args:
        conn: An open connection to ``memory_state.db``. The engine does
            NOT own this connection (shared with the recall engine).
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.has_sqlite_vec = False
        self.has_numpy = False
        self._numpy: Any = None  # the numpy module, lazily bound
        self._init_backends()

    def _init_backends(self) -> None:
        """Probe sqlite-vec, then NumPy. Both are best-effort."""
        # ── Tier 1: sqlite-vec C-extension ──
        try:
            self.conn.enable_load_extension(True)
            import sqlite_vec  # type: ignore[import-untyped]

            sqlite_vec.load(self.conn)
            # Smoke-test: confirm the extension actually responds.
            self.conn.execute("SELECT vec_version()").fetchone()
            self.has_sqlite_vec = True
            logger.info("[vector_engine] sqlite-vec C-extension active")
        except Exception as exc:
            self.has_sqlite_vec = False
            logger.debug("[vector_engine] sqlite-vec unavailable (%s)", exc)
            # Disable extension loading only if we enabled it
            try:
                self.conn.enable_load_extension(False)
            except Exception:
                pass

        # ── Tier 2: NumPy fallback (guarded — never a hard dep) ──
        # Per resolution #1: numpy is pulled transitively by [rag]; a
        # minimal `pip install kazma` has no numpy. Guard keeps it optional.
        if not self.has_sqlite_vec:
            try:
                import numpy as np  # type: ignore[import-untyped]

                self._numpy = np
                self.has_numpy = True
                logger.info("[vector_engine] NumPy fallback active")
            except ImportError:
                self.has_numpy = False
                logger.warning(
                    "[vector_engine] no sqlite-vec AND no numpy — "
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
        tier: str | None = "recall",
        limit: int = 10,
    ) -> list[tuple[str, float]]:
        """Return ``(episode_id, similarity)`` pairs ranked by cosine.

        Args:
            query_vec: The query embedding (float list). If None or the
                engine is unavailable, returns ``[]`` (caller falls back
                to FTS5).
            tenant_id: Tenant isolation filter.
            tier: Restrict to a specific tier ('recall'|'episodic'|...).
                None = search all tiers.
            limit: Max results.

        Returns:
            List of ``(episode_id, cosine_score)`` descending. Empty if
            the engine is unavailable or no embeddings match.
        """
        if query_vec is None or not self.available:
            return []
        if self.has_sqlite_vec:
            return self._search_sqlite_vec(query_vec, tenant_id, tier, limit)
        return self._search_numpy(query_vec, tenant_id, tier, limit)

    # ── Tier 1: sqlite-vec ─────────────────────────────────────────────

    def _search_sqlite_vec(
        self,
        query_vec: list[float],
        tenant_id: str,
        tier: str | None,
        limit: int,
    ) -> list[tuple[str, float]]:
        """Native sqlite-vec cosine search via a transient vec0 view.

        The V2 ``episodes`` table stores embeddings as float32 BLOBs in
        ``metadata_json`` under key ``embedding`` (written by the
        consolidator). We build a transient vec0 virtual table from the
        candidate rows, query it, then drop it.
        """
        np = self._numpy  # used for BLOB packing even under sqlite-vec
        try:
            dim = len(query_vec)
            # Fetch candidate episode ids + embedding blobs
            rows = self._fetch_candidate_embeddings(tenant_id, tier, limit * 4)
            if not rows:
                return []
            # Build a transient vec0 table
            vtab = "_v2_query_vec"
            self.conn.execute(f"DROP TABLE IF EXISTS {vtab}")
            self.conn.execute(
                f"CREATE VIRTUAL TABLE {vtab} USING vec0(id TEXT PRIMARY KEY, embedding float[{dim}])"
            )
            for eid, blob in rows:
                if blob and len(blob) == dim * 4:
                    self.conn.execute(
                        f"INSERT INTO {vtab} (id, embedding) VALUES (?, ?)",
                        (eid, sqlite3.Binary(blob)),
                    )
            # Pack query
            if np is not None:
                qblob = sqlite3.Binary(np.asarray(query_vec, dtype=np.float32).tobytes())
            else:
                qblob = sqlite3.Binary(self._pack_floats(query_vec))
            results = self.conn.execute(
                f"SELECT id, distance FROM {vtab} WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                (qblob, limit),
            ).fetchall()
            self.conn.execute(f"DROP TABLE IF EXISTS {vtab}")
            # vec0 'distance' is cosine *distance* (0=identical); convert to similarity
            return [(r[0], 1.0 - float(r[1])) for r in results]
        except Exception as exc:
            logger.debug("[vector_engine] sqlite-vec query failed: %s", exc)
            # Fall through to numpy if available
            if self.has_numpy:
                return self._search_numpy(query_vec, tenant_id, tier, limit)
            return []

    # ── Tier 2: NumPy fallback ─────────────────────────────────────────

    def _search_numpy(
        self,
        query_vec: list[float],
        tenant_id: str,
        tier: str | None,
        limit: int,
    ) -> list[tuple[str, float]]:
        """In-memory cosine similarity via NumPy."""
        np = self._numpy
        if np is None:
            return []
        try:
            rows = self._fetch_candidate_embeddings(tenant_id, tier, limit * 4)
            if not rows:
                return []
            q = np.asarray(query_vec, dtype=np.float32)
            qn = np.linalg.norm(q) + 1e-9
            scored: list[tuple[str, float]] = []
            for eid, blob in rows:
                if not blob or len(blob) != len(query_vec) * 4:
                    continue
                v = np.frombuffer(blob, dtype=np.float32)
                sim = float(np.dot(q, v) / (qn * (np.linalg.norm(v) + 1e-9)))
                scored.append((eid, sim))
            scored.sort(key=lambda x: x[1], reverse=True)
            return scored[:limit]
        except Exception as exc:
            logger.debug("[vector_engine] numpy fallback failed: %s", exc)
            return []

    # ── Helpers ────────────────────────────────────────────────────────

    def _fetch_candidate_embeddings(
        self,
        tenant_id: str,
        tier: str | None,
        limit: int,
    ) -> list[tuple[str, bytes]]:
        """Read (episode_id, embedding_blob) from episodes with embeddings.

        Embeddings live in the dedicated ``episodes.embedding`` BLOB
        column (written by the consolidator in Phase 3). Rows without an
        embedding are skipped. Tenant + tier filtered.

        Note: embeddings are NEVER stored inside ``metadata_json`` — raw
        bytes are not JSON-serializable.
        """
        sql = (
            "SELECT id, embedding FROM episodes "
            "WHERE tenant_id = ? AND embedding IS NOT NULL"
        )
        params: list[Any] = [tenant_id]
        if tier:
            sql += " AND tier = ?"
            params.append(tier)
        sql += " LIMIT ?"
        params.append(limit * 4)
        try:
            rows = self.conn.execute(sql, params).fetchall()
            return [(r[0], r[1]) for r in rows if r[1]]
        except Exception as exc:
            logger.debug("[vector_engine] candidate fetch failed: %s", exc)
            return []

    @staticmethod
    def _pack_floats(vec: list[float]) -> bytes:
        """Pack a float list into a float32 BLOB without numpy (struct)."""
        import struct

        return struct.pack(f"{len(vec)}f", *vec)
