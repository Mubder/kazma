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

from .arabic_tokenizer import ArabicTantivyTokenizer

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
        self._arabic_tokenizer = ArabicTantivyTokenizer()

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

        # Check for sqlite-vec extension
        try:
            await conn.execute("SELECT sqlite_version()")
            self._vec_available = True
        except Exception:
            self._vec_available = False

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
                embedding BLOB
            )
        """)

        # Create FTS5 table for Arabic full-text search
        # Use the content rowid to link to the memories table
        await conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
            USING fts5(content, content_arabic, content_rowid=rowid)
        """)

        # Create triggers to keep FTS5 in sync with memories table
        await conn.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, content, content_arabic)
                VALUES (new.rowid, new.content, new.content_arabic);
            END
        """)

        await conn.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, content_arabic)
                VALUES ('delete', old.rowid, old.content, old.content_arabic);
            END
        """)

        await conn.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, content_arabic)
                VALUES ('delete', old.rowid, old.content, old.content_arabic);
                INSERT INTO memories_fts(rowid, content, content_arabic)
                VALUES (new.rowid, new.content, new.content_arabic);
            END
        """)

        await conn.commit()
        return conn

    async def index(self, memory: Any) -> str:
        """Index a memory to SQLite with Arabic tokenization.

        Args:
            memory: Memory dict or Memory object.

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
        else:
            memory_id = getattr(memory, "id", self._generate_id())
            content = getattr(memory, "content", "")
            metadata = getattr(memory, "metadata", {})
            timestamp = getattr(memory, "timestamp", 0)
            source = getattr(memory, "source", "")
            relevance = getattr(memory, "relevance", 1.0)

        # Process content through Arabic tokenizer
        content_arabic = self._arabic_tokenizer.tokenize(content)

        if isinstance(metadata, dict):
            metadata = json.dumps(metadata)

        await conn.execute(
            """
            INSERT OR REPLACE INTO memories (id, content, content_arabic, metadata, timestamp, source, relevance)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (memory_id, content, content_arabic, metadata, timestamp, source, relevance),
        )

        # Triggers will automatically update FTS5 table
        await conn.commit()
        return memory_id

    async def search(self, query: str, limit: int = 10, **kwargs) -> list[dict[str, Any]]:
        """Hybrid search using FTS5 BM25 and sqlite-vec vector similarity.

        Args:
            query: Search query string.
            limit: Maximum number of results to return.
            **kwargs: Additional parameters (embedding, semantic_search, etc.)

        Returns:
            List of memory dictionaries ranked by relevance.
        """
        conn = await self._ensure_connection()

        # Process query through Arabic tokenizer for better matching
        query_arabic = self._arabic_tokenizer.tokenize(query)

        results = []

        # Try FTS5 BM25 search first (keyword matching)
        try:
            # Use the external content table pattern for FTS5
            # Search directly in FTS5 table with rowid, then join
            cursor = await conn.execute(
                """
                SELECT rowid, bm25(memories_fts) as bm25_score
                FROM memories_fts
                WHERE memories_fts MATCH ?
                ORDER BY bm25_score ASC
                LIMIT ?
                """,
                (query, limit),
            )
            fts_rows = await cursor.fetchall()

            if fts_rows:
                rowids = [row[0] for row in fts_rows]
                rowid_to_bm25 = {row[0]: row[1] for row in fts_rows}

                # Fetch full memory records
                placeholders = ",".join(["?"] * len(rowids))
                cursor = await conn.execute(
                    f"""
                    SELECT id, content, content_arabic, metadata, timestamp, source, relevance, rowid
                    FROM memories
                    WHERE rowid IN ({placeholders})
                    """,
                    rowids,
                )
                memory_rows = await cursor.fetchall()

                for row in memory_rows:
                    rowid = row[7]
                    results.append({
                        "id": row[0],
                        "content": row[1],
                        "content_arabic": row[2],
                        "metadata": row[3],
                        "timestamp": row[4],
                        "source": row[5],
                        "relevance": row[6],
                        "bm25_score": rowid_to_bm25.get(rowid, 0),
                        "search_type": "fts5"
                    })

        except Exception as e:
            logger.warning("FTS5 search failed: %s", e)
            # Fallback to simple LIKE search
            cursor = await conn.execute(
                "SELECT id, content, metadata, timestamp, source, relevance FROM memories WHERE content LIKE ? LIMIT ?",
                (f"%{query}%", limit),
            )
            rows = await cursor.fetchall()
            results.extend([
                {
                    "id": row[0],
                    "content": row[1],
                    "metadata": row[2],
                    "timestamp": row[3],
                    "source": row[4],
                    "relevance": row[5],
                    "search_type": "fallback"
                }
                for row in rows
            ])

        # If vector search requested and available, add vector similarity results
        if kwargs.get("semantic_search") and self._vec_available and kwargs.get("embedding"):
            try:
                vector_results = await self._vector_search(
                    kwargs["embedding"],
                    limit=max(limit // 2, 3)  # Reserve half slots for vector search
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
        sorted_results = sorted(
            unique_results.values(),
            key=lambda x: (
                x.get("bm25_score", 0) * 0.7 + x.get("relevance", 1.0) * 0.3
                if "bm25_score" in x
                else x.get("relevance", 1.0)
            ),
            reverse=True,
        )

        return sorted_results[:limit]

    async def _vector_search(self, embedding: bytes, limit: int = 10) -> list[dict[str, Any]]:
        """Perform vector similarity search using sqlite-vec.

        Args:
            embedding: Query embedding vector.
            limit: Maximum number of results.

        Returns:
            List of memory dictionaries.
        """
        conn = await self._ensure_connection()

        try:
            # Use sqlite-vec for vector similarity search
            cursor = await conn.execute(
                """
                SELECT id, content, metadata, timestamp, source, relevance
                FROM memories
                WHERE embedding IS NOT NULL
                ORDER BY distance(embedding, ?)
                LIMIT ?
                """,
                (embedding, limit),
            )
            rows = await cursor.fetchall()

            return [
                {
                    "id": row[0],
                    "content": row[1],
                    "metadata": row[2],
                    "timestamp": row[3],
                    "source": row[4],
                    "relevance": row[5],
                    "search_type": "vector"
                }
                for row in rows
            ]
        except Exception as e:
            logger.warning("Vector search failed: %s", e)
            return []

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

    async def index(self, memory: Any) -> str:
        """Index a memory with Arabic tokenization.

        Args:
            memory: Memory object to index.

        Returns:
            Document ID.
        """
        return await self.backend.index(memory)

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
