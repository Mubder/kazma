"""Search Backend Router — Routes between SQLite and Tantivy based on workload.

Provides automatic backend selection based on document count,
with redundancy through dual indexing.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SQLiteMemoryBackend:
    """SQLite-based memory backend (existing implementation)."""
    
    def __init__(self, db_path: str = "kazma-data/memory.db"):
        """Initialize SQLite backend."""
        self.db_path = db_path
    
    async def search(self, query: str, **kwargs) -> List[Dict[str, Any]]:
        """Search memories in SQLite."""
        # Placeholder for existing SQLite search implementation
        return []
    
    async def index(self, memory: Any) -> str:
        """Index a memory to SQLite."""
        # Placeholder for existing SQLite indexing
        return ""


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
        self._document_count: Optional[int] = None

    async def search(self, query: str, **kwargs) -> List[Any]:
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

    async def get_backend_info(self) -> Dict[str, Any]:
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
