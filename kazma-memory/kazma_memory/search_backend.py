"""SQLite Search Backend — Enhanced with FTS5 and Arabic Tokenization.

Provides hybrid search combining:
- FTS5 full-text search with Arabic tokenization (BM25 ranking)
- sqlite-vec vector similarity search for semantic matching
- Optimized for edge deployment with no external dependencies.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import aiosqlite

from .arabic_tokenizer import ArabicTokenizer
from kazma_core.tenant_context import get_current_tenant_id

logger = logging.getLogger(__name__)


def _build_fts_query(text: str) -> str:
    """Build a safe FTS5 MATCH query from arbitrary user input.

    Raw user text (URLs, punctuation like ``?``/``:``/``/``) is not valid
    FTS5 syntax and raises "syntax error near ...". Wrapping the whole
    string in a double-quoted phrase makes FTS5 treat it verbatim, and
    embedded double-quotes are escaped by doubling them.
    """
    text = (text or "").strip()
    if not text:
        return '""'
    escaped = text.replace('"', '""')
    return f'"{escaped}"'


class SQLiteMemoryBackend:
    """SQLite-based memory backend with enhanced Arabic search.

    Uses:
    - sqlite-vec extension for semantic similarity search (vector embeddings)
    - FTS5 full-text search with Arabic tokenization for keyword matching
    - Hybrid search combining BM25 and vector similarity for optimal results
    """

    def __init__(self, db_path: str = "kazma-data/memory.db"):
        """Initialize SQLite backend.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = db_path
        self._conn: Any = None
        self._vec_available = False
        self._arabic_tokenizer = ArabicTokenizer()

    async def _ensure_connection(self) -> Any:
        """Ensure database connection is established."""
        if self._conn is None:
            self._conn = await self._connect()
        return self._conn

    async def _connect(self) -> Any:
        """Create database connection and initialize schema."""
        conn = await aiosqlite.connect(self.db_path)
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA busy_timeout=5000")

        # sqlite-vec: prefer the official PyPI package (platform wheels).
        # Bare load_extension("vec0") only works when a system binary is on
        # the SQLite path — that was silently failing even after pip install.
        # Note: L3 stores embeddings as BLOBs and does cosine in Python;
        # vec0 here is optional (future vec table / diagnostics only).
        self._vec_available = False
        try:
            await conn.enable_load_extension(True)
        except Exception:
            pass
        try:
            await conn.execute("SELECT vec_version()")
            self._vec_available = True
        except Exception:
            pass
        if not self._vec_available:
            try:
                import sqlite_vec

                path = sqlite_vec.loadable_path()
                await conn.load_extension(path)
                await conn.execute("SELECT vec_version()")
                self._vec_available = True
                logger.info("[SQLiteMemoryBackend] sqlite-vec loaded via package path")
            except Exception as exc:
                logger.debug(
                    "[SQLiteMemoryBackend] sqlite_vec package load failed: %s", exc
                )
        if not self._vec_available:
            try:
                await conn.load_extension("vec0")
                await conn.execute("SELECT vec_version()")
                self._vec_available = True
            except Exception:
                self._vec_available = False
        try:
            await conn.enable_load_extension(False)
        except Exception:
            pass

        # Canonical table + FTS + safe triggers (see kazma_core.memory.schema).
        # Always reinstall triggers — legacy FTS5 'delete' command form raised
        # SQL logic error on every UPDATE (blocked timestamp/embedding writes).
        # NOTE: kazma_core.memory.schema was removed in the V1→V2 memory
        # cutover — this legacy backend now degrades LOUDLY instead of
        # crashing the connect path (fresh DBs will lack the V1 tables).
        try:
            from kazma_core.memory.schema import ensure_memories_schema_async
        except ImportError:
            logger.warning(
                "[SQLiteMemoryBackend] kazma_core.memory.schema was removed in "
                "the V2 memory cutover — skipping V1 schema ensure (legacy "
                "backend; fresh DBs will not have the V1 memories/FTS tables)."
            )
        else:
            await ensure_memories_schema_async(conn)
        return conn

    async def index(self, memory: Any, tenant_id: str | None = None) -> str:
        """Index a memory to SQLite with Arabic tokenization.

        Args:
            memory: Memory dict or Memory object.
            tenant_id: Optional tenant isolation ID.

        Returns:
            Document ID.
        """
        conn = await self._ensure_connection()

        # Extract fields from memory
        if isinstance(memory, dict):
            memory_id = memory.get("id", self._generate_id())
            content = memory.get("content", "")
            metadata = memory.get("metadata", {})
            timestamp = memory.get("timestamp", 0)
            source = memory.get("source", "")
            relevance = memory.get("relevance", 1.0)
            embedding = memory.get("embedding", None)
            resolved_tenant = tenant_id or memory.get("tenant_id") or (metadata.get("tenant_id") if isinstance(metadata, dict) else None)
            resolved_tenant = resolved_tenant if resolved_tenant is not None else get_current_tenant_id()
        else:
            memory_id = getattr(memory, "id", self._generate_id())
            content = getattr(memory, "content", "")
            metadata = getattr(memory, "metadata", {})
            timestamp = getattr(memory, "timestamp", 0)
            source = getattr(memory, "source", "")
            relevance = getattr(memory, "relevance", 1.0)
            embedding = getattr(memory, "embedding", None)
            resolved_tenant = tenant_id or getattr(memory, "tenant_id", None) or (metadata.get("tenant_id") if isinstance(metadata, dict) else None)
            resolved_tenant = resolved_tenant if resolved_tenant is not None else get_current_tenant_id()

        # Never persist timestamp=0 for new content when caller forgot it
        try:
            ts_int = int(timestamp or 0)
        except (TypeError, ValueError):
            ts_int = 0
        if ts_int <= 0:
            try:
                from kazma_core.swarm.memory.embedder import resolve_unix_timestamp

                meta_for_ts = metadata if isinstance(metadata, dict) else {}
                ts_int = resolve_unix_timestamp(meta_for_ts)
            except Exception:
                import time as _time

                ts_int = int(_time.time())
        timestamp = ts_int

        # Auto-embed when BLOB missing so L3 semantic search is real
        if not embedding:
            try:
                from kazma_core.swarm.memory.embedder import encode_text_to_blob

                embedding = encode_text_to_blob(str(content or ""))
            except Exception:
                embedding = None

        # Process content through Arabic tokenizer
        content_arabic = self._arabic_tokenizer.tokenize(content)

        if isinstance(metadata, dict):
            if "timestamp" not in metadata:
                metadata = {**metadata, "timestamp": timestamp}
            metadata = json.dumps(metadata, ensure_ascii=False, default=str)

        await conn.execute(
            """
            INSERT OR REPLACE INTO memories (id, content, content_arabic, metadata, timestamp, source, relevance, embedding, tenant_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (memory_id, content, content_arabic, metadata, timestamp, source, relevance, embedding, resolved_tenant),
        )

        # Triggers will automatically update FTS5 table
        await conn.commit()
        return memory_id

    async def search(self, query: str, limit: int = 10, **kwargs) -> list[dict[str, Any]]:
        """Hybrid search using FTS5 BM25 and sqlite-vec vector similarity.

        Args:
            query: Search query string.
            limit: Maximum number of results to return.
            **kwargs: Additional parameters (embedding, semantic_search, tenant_id, etc.)

        Returns:
            List of memory dictionaries ranked by relevance.
        """
        conn = await self._ensure_connection()
        tenant_id = kwargs.get("tenant_id") if kwargs.get("tenant_id") is not None else get_current_tenant_id()

        # Process query through Arabic tokenizer for better matching.
        # The tokenizer normalizes Alef/Yeh/Teh-Marbuta, strips diacritics
        # and tatweel — the SAME transformations applied at index time to
        # ``content_arabic``.  Using the tokenized query makes Arabic
        # search symmetric (index and query normalized identically).
        query_arabic = self._arabic_tokenizer.tokenize(query)
        # Use the tokenized form if it produced something different, so
        # the MATCH hits the normalized ``content_arabic`` column.
        raw_fts_query = query_arabic if query_arabic else query
        # Wrap in a safe double-quoted phrase to avoid FTS5 syntax errors
        # from arbitrary input (URLs, "?", ":", "/", etc.).
        fts_query = _build_fts_query(raw_fts_query)

        results = []

        # Try FTS5 BM25 search first (keyword matching)
        try:
            # Search directly in FTS5 table with rowid.
            # Hard tenant filter (P6): when tenant_id is set, only exact matches
            # (NULL/shared rows are excluded — single-tenant installs leave
            # tenant_id unset so this branch is not used).
            if tenant_id is not None:
                cursor = await conn.execute(
                    """
                    SELECT f.memory_id, bm25(f.memories_fts) as bm25_score
                    FROM memories_fts f
                    JOIN memories m ON m.id = f.memory_id
                    WHERE f.memories_fts MATCH ? AND m.tenant_id = ?
                    ORDER BY bm25_score ASC
                    LIMIT ?
                    """,
                    (fts_query, tenant_id, limit),
                )
            else:
                cursor = await conn.execute(
                    """
                    SELECT memory_id, bm25(memories_fts) as bm25_score
                    FROM memories_fts
                    WHERE memories_fts MATCH ?
                    ORDER BY bm25_score ASC
                    LIMIT ?
                    """,
                    (fts_query, limit),
                )
            fts_rows = await cursor.fetchall()

            if fts_rows:
                memory_ids = [row[0] for row in fts_rows]
                id_to_bm25 = {row[0]: row[1] for row in fts_rows}

                # Fetch full memory records by id
                placeholders = ",".join(["?"] * len(memory_ids))
                if tenant_id is not None:
                    cursor = await conn.execute(
                        f"""
                        SELECT id, content, content_arabic, metadata, timestamp, source, relevance
                        FROM memories
                        WHERE id IN ({placeholders}) AND tenant_id = ?
                        """,
                        memory_ids + [tenant_id],
                    )
                else:
                    cursor = await conn.execute(
                        f"""
                        SELECT id, content, content_arabic, metadata, timestamp, source, relevance
                        FROM memories
                        WHERE id IN ({placeholders})
                        """,
                        memory_ids,
                    )
                memory_rows = await cursor.fetchall()

                for row in memory_rows:
                    mid = row[0]
                    results.append(
                        {
                            "id": row[0],
                            "content": row[1],
                            "content_arabic": row[2],
                            "metadata": row[3],
                            "timestamp": row[4],
                            "source": row[5],
                            "relevance": row[6],
                            "bm25_score": id_to_bm25.get(mid, 0),
                            "search_type": "fts5",
                        }
                    )

        except Exception as e:
            logger.warning("FTS5 search failed: %s", e)
            # Fallback to simple LIKE search
            if tenant_id is not None:
                cursor = await conn.execute(
                    "SELECT id, content, metadata, timestamp, source, relevance FROM memories WHERE content LIKE ? AND tenant_id = ? LIMIT ?",
                    (f"%{query}%", tenant_id, limit),
                )
            else:
                cursor = await conn.execute(
                    "SELECT id, content, metadata, timestamp, source, relevance FROM memories WHERE content LIKE ? LIMIT ?",
                    (f"%{query}%", limit),
                )
            rows = await cursor.fetchall()
            results.extend(
                [
                    {
                        "id": row[0],
                        "content": row[1],
                        "metadata": row[2],
                        "timestamp": row[3],
                        "source": row[4],
                        "relevance": row[5],
                        "search_type": "fallback",
                    }
                    for row in rows
                ]
            )

        # Hybrid: always blend BLOB cosine search when embeddings exist.
        # (Previously gated on kwargs + broken vec0 load — L3 semantic was dead.)
        want_semantic = kwargs.get("semantic_search", True)
        if want_semantic is not False:
            try:
                emb = kwargs.get("embedding")
                if not emb:
                    emb = await self._encode_query_blob(query)
                if emb:
                    vector_results = await self._vector_search(
                        emb,
                        limit=max(limit, 5),
                        tenant_id=tenant_id,
                    )
                    results.extend(vector_results)
            except Exception as e:
                logger.warning("Vector search failed: %s", e)

        # Deduplicate and sort by relevance/bm25_score
        unique_results = {}
        for result in results:
            result_id = result["id"]
            if result_id not in unique_results:
                unique_results[result_id] = result

        # Sort by combined relevance score (positive-better).
        # BM25 scores are negative-better → negate. Vector uses cosine similarity.
        def _rank_key(x: dict[str, Any]) -> float:
            if "bm25_score" in x and "similarity" in x:
                return (-float(x["bm25_score"]) * 0.55) + (
                    float(x.get("similarity") or 0) * 0.45
                )
            if "bm25_score" in x:
                return -float(x["bm25_score"]) * 0.7 + float(x.get("relevance") or 1.0) * 0.3
            if "similarity" in x:
                return float(x.get("similarity") or 0) * 0.95 + float(
                    x.get("relevance") or 1.0
                ) * 0.05
            return float(x.get("relevance") or 1.0)

        sorted_results = sorted(
            unique_results.values(),
            key=_rank_key,
            reverse=True,
        )

        return sorted_results[:limit]

    async def _encode_query_blob(self, query: str) -> bytes | None:
        """Encode query text to the same float32 BLOB format as stored rows."""
        try:
            from kazma_core.swarm.memory.embedder import encode_text_to_blob

            # encode is sync / may load MiniLM — offload via asyncio.to_thread
            import asyncio

            return await asyncio.to_thread(encode_text_to_blob, query or "")
        except Exception:
            logger.debug("query embed failed", exc_info=True)
            return None

    async def _has_any_embeddings(self) -> bool:
        """Cheap probe: skip vector path when every BLOB is null."""
        try:
            conn = await self._ensure_connection()
            cur = await conn.execute(
                "SELECT 1 FROM memories WHERE embedding IS NOT NULL AND length(embedding) > 0 LIMIT 1"
            )
            return await cur.fetchone() is not None
        except Exception:
            return False

    async def _vector_search(self, embedding: bytes, limit: int = 10, tenant_id: str | None = None) -> list[dict[str, Any]]:
        """Perform vector similarity search using cosine distance in Python.

        The ``memories`` table stores embeddings as raw BLOBs.  Rather than
        relying on a non-existent ``distance()`` SQL function, we fetch
        rows that have embeddings and compute cosine similarity in Python.
        Cap candidates for large stores so hybrid search stays responsive.

        Args:
            embedding: Query embedding as a serialized byte string (float32 array).
            limit: Maximum number of results.
            tenant_id: Optional tenant isolation ID.

        Returns:
            List of memory dictionaries with a ``similarity`` score (0–1).
        """
        import math
        import struct

        if not await self._has_any_embeddings():
            return []

        conn = await self._ensure_connection()

        # Deserialize the query embedding (assume float32 little-endian)
        try:
            query_vec = list(struct.unpack(f"<{len(embedding) // 4}f", embedding))
        except Exception:
            logger.warning("Could not deserialize query embedding for vector search")
            return []
        if not query_vec:
            return []

        query_norm = math.sqrt(sum(v * v for v in query_vec))
        if query_norm == 0:
            return []

        # Cap scan for large corpora (newest first by timestamp)
        scan_cap = max(limit * 40, 500)
        if tenant_id is not None:
            cursor = await conn.execute(
                """
                SELECT id, content, metadata, timestamp, source, relevance, embedding
                FROM memories
                WHERE embedding IS NOT NULL AND length(embedding) > 0 AND tenant_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (tenant_id, scan_cap),
            )
        else:
            cursor = await conn.execute(
                """
                SELECT id, content, metadata, timestamp, source, relevance, embedding
                FROM memories
                WHERE embedding IS NOT NULL AND length(embedding) > 0
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (scan_cap,),
            )
        rows = await cursor.fetchall()

        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            emb_bytes = row[6]
            if not emb_bytes:
                continue
            try:
                vec = list(struct.unpack(f"<{len(emb_bytes) // 4}f", emb_bytes))
            except Exception:
                continue
            if len(vec) != len(query_vec):
                continue
            norm = math.sqrt(sum(v * v for v in vec))
            if norm == 0:
                continue
            dot = sum(a * b for a, b in zip(query_vec, vec))
            similarity = dot / (query_norm * norm)
            scored.append(
                (
                    similarity,
                    {
                        "id": row[0],
                        "content": row[1],
                        "metadata": row[2],
                        "timestamp": row[3],
                        "source": row[4],
                        "relevance": row[5],
                        "similarity": similarity,
                        "search_type": "vector",
                    },
                )
            )

        # Sort by similarity descending (most similar first), take top-`limit`
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored[:limit]]

    def _generate_id(self) -> str:
        """Generate a unique memory ID."""
        import uuid

        return f"mem_{uuid.uuid4().hex[:16]}"

    async def count(self) -> int:
        """Get total document count.

        Returns:
            Number of documents in the database.
        """
        conn = await self._ensure_connection()
        cursor = await conn.execute("SELECT COUNT(*) FROM memories")
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def close(self) -> None:
        """Close database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None


class SearchBackend:
    """SQLite-only search backend with Arabic FTS5 and vector search.

    Provides optimized search for edge deployment:
    - FTS5 full-text search with Arabic tokenization
    - sqlite-vec vector similarity search
    - Hybrid BM25 + vector ranking
    - Zero external dependencies beyond SQLite
    """

    def __init__(self, db_path: str = "kazma-data/memory.db"):
        """Initialize SQLite search backend.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.backend = SQLiteMemoryBackend(db_path)

    async def search(self, query: str, limit: int = 10, **kwargs) -> list[dict[str, Any]]:
        """Perform hybrid search with Arabic tokenization.

        Args:
            query: Search query string.
            limit: Maximum number of results.
            **kwargs: Additional parameters (semantic_search, embedding, etc.).

        Returns:
            List of search results ranked by relevance.
        """
        return await self.backend.search(query, limit=limit, **kwargs)

    async def index(self, memory: Any, tenant_id: str | None = None) -> str:
        """Index a memory with Arabic tokenization.

        Args:
            memory: Memory object to index.
            tenant_id: Optional tenant isolation ID.

        Returns:
            Document ID.
        """
        return await self.backend.index(memory, tenant_id=tenant_id)

    async def count(self) -> int:
        """Get total document count.

        Returns:
            Number of indexed documents.
        """
        return await self.backend.count()

    async def close(self) -> None:
        """Close database connection."""
        await self.backend.close()

    async def get_backend_info(self) -> dict[str, Any]:
        """Get information about the search backend.

        Returns:
            Dictionary with backend information.
        """
        return {
            "backend_type": "sqlite",
            "fts5_enabled": True,
            "vector_search_enabled": self.backend._vec_available,
            "arabic_tokenization": True,
            "document_count": await self.backend.count(),
        }
