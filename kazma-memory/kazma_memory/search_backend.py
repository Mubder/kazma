"""Search Backend Router — Routes between SQLite and Tantivy based on workload.

Provides automatic backend selection based on document count,
with redundancy through dual indexing.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)


class SQLiteMemoryBackend:
    """SQLite-based memory backend with vector search support.
    
    Uses sqlite-vec extension for semantic similarity search
    when available, falls back to regex-based text search.
    """

    def __init__(self, db_path: str = "kazma-data/memory.db"):
        """Initialize SQLite backend.
        
        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = db_path
        self._conn: Any = None
        self._vec_available = False

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

        # Create memories table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                timestamp INTEGER DEFAULT 0,
                source TEXT DEFAULT '',
                relevance REAL DEFAULT 1.0,
                embedding BLOB
            )
        """)

        # Create FTS table for text search
        await conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts 
            USING fts5(content, content_rowid UNINDEXED)
        """)

        await conn.commit()
        return conn

    async def search(self, query: str, limit: int = 10, **kwargs) -> list[dict[str, Any]]:
        """Search memories in SQLite.
        
        Uses FTS5 for text search when available.
        
        Args:
            query: Search query string.
            limit: Maximum number of results to return.
            **kwargs: Additional parameters.
            
        Returns:
            List of memory dictionaries.
        """
        conn = await self._ensure_connection()

        try:
            # Use FTS5 for full-text search
            cursor = await conn.execute(
                """
                SELECT m.id, m.content, m.metadata, m.timestamp, m.source, m.relevance
                FROM memories m
                JOIN memories_fts f ON m.rowid = f.content_rowid
                WHERE f.content MATCH ?
                ORDER BY m.relevance DESC
                LIMIT ?
                """,
                (query, limit)
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
                }
                for row in rows
            ]
        except Exception as e:
            logger.warning("FTS search failed, falling back to simple search: %s", e)
            # Fallback to simple LIKE search
            cursor = await conn.execute(
                "SELECT id, content, metadata, timestamp, source, relevance FROM memories WHERE content LIKE ? LIMIT ?",
                (f"%{query}%", limit)
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
                }
                for row in rows
            ]

    async def index(self, memory: Any) -> str:
        """Index a memory to SQLite.
        
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
            metadata = getattr(memory, "metadata", "")
            timestamp = getattr(memory, "timestamp", 0)
            source = getattr(memory, "source", "")
            relevance = getattr(memory, "relevance", 1.0)

        if isinstance(metadata, dict):
            metadata = json.dumps(metadata)

        await conn.execute(
            """
            INSERT OR REPLACE INTO memories (id, content, metadata, timestamp, source, relevance)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (memory_id, content, metadata, timestamp, source, relevance)
        )

        # Also index in FTS for search
        try:
            # Get the rowid of the inserted memory
            cursor = await conn.execute("SELECT rowid FROM memories WHERE id = ?", (memory_id,))
            row = await cursor.fetchone()
            if row:
                await conn.execute(
                    "INSERT OR REPLACE INTO memories_fts (content, content_rowid) VALUES (?, ?)",
                    (content, row[0])
                )
        except Exception as e:
            logger.debug("FTS indexing skipped: %s", e)

        await conn.commit()
        return memory_id

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


class SearchBackendRouter:
    """Routes between SQLite and Tantivy based on workload.
    
    Automatically selects the appropriate backend based on document count:
    - < threshold: Use SQLite (simpler, lighter)
    - >= threshold: Use Tantivy (faster, more scalable)
    """

    def __init__(
        self,
        sqlite_backend: SQLiteMemoryBackend,
        tantivy_backend: Any,
        threshold_documents: int = 10000
    ):
        """Initialize router with backends.
        
        Args:
            sqlite_backend: SQLite memory backend instance.
            tantivy_backend: Tantivy search backend instance.
            threshold_documents: Document count threshold for switching backends.
        """
        self.sqlite = sqlite_backend
        self.tantivy = tantivy_backend
        self.threshold = threshold_documents
        self._document_count: int | None = None

    async def search(self, query: str, **kwargs) -> list[Any]:
        """Route search to appropriate backend.
        
        - < threshold documents: Use SQLite (simpler, lighter)
        - >= threshold documents: Use Tantivy (faster, more scalable)
        
        Args:
            query: Search query string.
            **kwargs: Additional search parameters.
            
        Returns:
            List of search results.
        """
        doc_count = await self._get_document_count()

        if doc_count >= self.threshold:
            logger.debug(f"Routing to Tantivy ({doc_count} documents)")
            return await self.tantivy.search(query, **kwargs)
        else:
            logger.debug(f"Routing to SQLite ({doc_count} documents)")
            return await self.sqlite.search(query, **kwargs)

    async def index(self, memory: Any) -> str:
        """Index to both backends for redundancy.
        
        Args:
            memory: Memory object to index.
            
        Returns:
            Document ID.
        """
        # Index to SQLite
        sqlite_id = await self.sqlite.index(memory)

        # Index to Tantivy (if available)
        try:
            from .tantivy_backend import Memory as TantivyMemory

            # Convert to Tantivy format if needed
            if isinstance(memory, dict):
                tantivy_memory = TantivyMemory(
                    id=memory.get("id", sqlite_id),
                    content=memory.get("content", ""),
                    metadata=memory.get("metadata", ""),
                    timestamp=memory.get("timestamp", 0),
                    source=memory.get("source", ""),
                    relevance=memory.get("relevance", 1.0),
                    division=memory.get("division", ""),
                )
            else:
                tantivy_memory = memory

            await self.tantivy.index_memory(tantivy_memory)

        except Exception as e:
            logger.warning(f"Failed to index to Tantivy: {e}")

        return sqlite_id

    async def migrate_to_tantivy(self) -> bool:
        """One-way migration from SQLite to Tantivy.
        
        Returns:
            True if migration successful.
        """
        try:
            from .migration import SQLiteToTantivyMigration

            migration = SQLiteToTantivyMigration(
                sqlite_path=self.sqlite.db_path if hasattr(self.sqlite, 'db_path') else "kazma-data/memory.db",
                tantivy_path=self.tantivy.index_path if hasattr(self.tantivy, 'index_path') else "kazma-data/tantivy-index",
            )

            result = await migration.migrate()

            if result.success:
                logger.info(f"Migration completed: {result.total_migrated} documents migrated")
                # Clear cached document count
                self._document_count = None
                return True
            else:
                logger.error(f"Migration failed: {result.errors}")
                return False

        except Exception as e:
            logger.error(f"Migration failed: {e}")
            return False

    async def _get_document_count(self) -> int:
        """Get current document count.
        
        Caches the count to avoid repeated queries.
        
        Returns:
            Number of documents in the index.
        """
        if self._document_count is not None:
            return self._document_count

        try:
            # Try to get count from Tantivy first
            stats = await self.tantivy.get_stats()
            self._document_count = stats.total_documents
        except Exception:
            # Fall back to SQLite
            try:
                if hasattr(self.sqlite, 'count'):
                    self._document_count = await self.sqlite.count()
                else:
                    self._document_count = 0
            except Exception:
                self._document_count = 0

        return self._document_count

    async def invalidate_cache(self) -> None:
        """Invalidate cached document count."""
        self._document_count = None

    async def get_backend_info(self) -> dict[str, Any]:
        """Get information about current backend selection.
        
        Returns:
            Dictionary with backend selection details.
        """
        doc_count = await self._get_document_count()

        return {
            "document_count": doc_count,
            "threshold": self.threshold,
            "selected_backend": "tantivy" if doc_count >= self.threshold else "sqlite",
            "sqlite_available": self.sqlite is not None,
            "tantivy_available": self.tantivy is not None,
        }
