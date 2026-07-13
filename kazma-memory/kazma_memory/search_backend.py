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

        # Check for sqlite-vec extension — must actually probe vec_version(),
        # not sqlite_version() (which exists in every SQLite build).
        try:
            await conn.enable_load_extension(True)
            await conn.load_extension("vec0")
            await conn.execute("SELECT vec_version()")
            self._vec_available = True
        except Exception:
            self._vec_available = False
        finally:
            try:
                await conn.enable_load_extension(False)
            except Exception:
                pass

        # Create memories table with vector embedding support
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                content_arabic TEXT,
                metadata TEXT DEFAULT '{}',
                timestamp INTEGER DEFAULT 0,
                source TEXT DEFAULT '',
                relevance REAL DEFAULT 1.0,
                embedding BLOB,
                tenant_id TEXT
            )
        """)

        # Auto-migration: add tenant_id if not present in existing databases
        try:
            await conn.execute("ALTER TABLE memories ADD COLUMN tenant_id TEXT")
        except Exception:
            pass  # Already exists

        # Create FTS5 table for full-text search (self-contained)
        # memory_id stores the memories.id for reliable joins.
        # Triggers keep it in sync with the memories table.
        await conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
            USING fts5(memory_id, content, content_arabic)
        """)

        # Create triggers to keep FTS5 in sync with memories table
        await conn.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(memory_id, content, content_arabic)
                VALUES (new.id, new.content, new.content_arabic);
            END
        """)

        await conn.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, memory_id, content, content_arabic)
                VALUES ('delete', old.id, old.content, old.content_arabic);
            END
        """)

        await conn.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, memory_id, content, content_arabic)
                VALUES ('delete', old.id, old.content, old.content_arabic);
                INSERT INTO memories_fts(memory_id, content, content_arabic)
                VALUES (new.id, new.content, new.content_arabic);
            END
        """)

        await conn.commit()
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

        # Process content through Arabic tokenizer
        content_arabic = self._arabic_tokenizer.tokenize(content)

        if isinstance(metadata, dict):
            metadata = json.dumps(metadata)

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
        fts_query = query_arabic if query_arabic else query

        results = []

        # Try FTS5 BM25 search first (keyword matching)
        try:
            # Search directly in FTS5 table with rowid, and filter by tenant_id on memories join if specified
            if tenant_id is not None:
                cursor = await conn.execute(
                    """
                    SELECT f.memory_id, bm25(f.memories_fts) as bm25_score
                    FROM memories_fts f
                    JOIN memories m ON m.id = f.memory_id
                    WHERE f.memories_fts MATCH ? AND (m.tenant_id = ? OR m.tenant_id IS NULL)
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
                        WHERE id IN ({placeholders}) AND (tenant_id = ? OR tenant_id IS NULL)
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
                    "SELECT id, content, metadata, timestamp, source, relevance FROM memories WHERE content LIKE ? AND (tenant_id = ? OR tenant_id IS NULL) LIMIT ?",
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

        # If vector search requested and available, add vector similarity results
        if kwargs.get("semantic_search") and self._vec_available and kwargs.get("embedding"):
            try:
                vector_results = await self._vector_search(
                    kwargs["embedding"],
                    limit=max(limit // 2, 3),  # Reserve half slots for vector search
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

        # Sort by combined relevance score
        # BM25 scores are negative-better (more negative = more relevant),
        # so negate them so all scores are positive-better for consistent sorting.
        sorted_results = sorted(
            unique_results.values(),
            key=lambda x: (
                -x.get("bm25_score", 0) * 0.7 + x.get("relevance", 1.0) * 0.3
                if "bm25_score" in x
                else x.get("relevance", 1.0)
            ),
            reverse=True,
        )

        return sorted_results[:limit]

    async def _vector_search(self, embedding: bytes, limit: int = 10, tenant_id: str | None = None) -> list[dict[str, Any]]:
        """Perform vector similarity search using cosine distance in Python.

        The ``memories`` table stores embeddings as raw BLOBs.  Rather than
        relying on a non-existent ``distance()`` SQL function, we fetch all
        rows that have embeddings and compute cosine similarity in Python.
        This is correct for small-to-medium memory sets; for large-scale
        vector search, migrate to a proper vec0 virtual table (as done in
        ``swarm/memory/sqlite_vec.py``).

        Args:
            embedding: Query embedding as a serialized byte string (float32 array).
            limit: Maximum number of results.
            tenant_id: Optional tenant isolation ID.

        Returns:
            List of memory dictionaries with a ``similarity`` score (0–1).
        """
        import math
        import struct

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

        # Fetch candidate rows with embeddings
        if tenant_id is not None:
            cursor = await conn.execute(
                """
                SELECT id, content, metadata, timestamp, source, relevance, embedding
                FROM memories
                WHERE embedding IS NOT NULL AND (tenant_id = ? OR tenant_id IS NULL)
                """,
                (tenant_id,),
            )
        else:
            cursor = await conn.execute(
                """
                SELECT id, content, metadata, timestamp, source, relevance, embedding
                FROM memories
                WHERE embedding IS NOT NULL
                """,
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
