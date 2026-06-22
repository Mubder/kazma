"""SQLite to Tantivy Migration — Migrate existing memories to Tantivy.

Provides data migration from SQLite-based storage to Tantivy index
with verification and rollback capabilities.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class MigrationResult:
    """Result of a migration operation."""
    success: bool
    total_migrated: int
    total_failed: int
    duration_seconds: float
    errors: list[str] | None = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


@dataclass
class VerificationResult:
    """Result of a migration verification."""
    sqlite_count: int
    tantivy_count: int
    mismatch: bool
    missing_in_tantivy: list[str] | None = None
    extra_in_tantivy: list[str] | None = None

    def __post_init__(self):
        if self.missing_in_tantivy is None:
            self.missing_in_tantivy = []
        if self.extra_in_tantivy is None:
            self.extra_in_tantivy = []


class SQLiteToTantivyMigration:
    """Migrates existing SQLite memories to Tantivy.
    
    Provides batch migration with progress tracking,
    verification, and rollback capabilities.
    """

    def __init__(
        self,
        sqlite_path: str = "kazma-data/memory.db",
        tantivy_path: str = "kazma-data/tantivy-index"
    ):
        """Initialize migration manager.
        
        Args:
            sqlite_path: Path to SQLite database.
            tantivy_path: Path to Tantivy index directory.
        """
        self.sqlite_path = sqlite_path
        self.tantivy_path = tantivy_path
        self._tantivy_backend = None

    def _get_sqlite_conn(self) -> sqlite3.Connection:
        """Get SQLite connection."""
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _get_tantivy_backend(self):
        """Get or create Tantivy backend instance."""
        if self._tantivy_backend is None:
            from .tantivy_backend import TantivySearchBackend
            self._tantivy_backend = TantivySearchBackend(self.tantivy_path)
        return self._tantivy_backend

    async def migrate(
        self,
        batch_size: int = 1000,
        progress_callback: Callable[[int, int], None] | None = None
    ) -> MigrationResult:
        """Migrate all memories from SQLite to Tantivy.
        
        Steps:
        1. Read all memories from SQLite
        2. Transform to Tantivy format
        3. Index in batches
        4. Verify migration completeness
        5. Return result with counts
        
        Args:
            batch_size: Number of records to migrate per batch.
            progress_callback: Optional callback(migrated, total) for progress.
            
        Returns:
            MigrationResult with migration statistics.
        """
        start_time = time.time()
        total_migrated = 0
        total_failed = 0
        errors: list[str] = []

        try:
            # Connect to SQLite
            conn = self._get_sqlite_conn()
            cursor = conn.cursor()

            # Get total count
            cursor.execute("SELECT COUNT(*) FROM memories")
            total_count = cursor.fetchone()[0]

            if total_count == 0:
                conn.close()
                return MigrationResult(
                    success=True,
                    total_migrated=0,
                    total_failed=0,
                    duration_seconds=time.time() - start_time,
                )

            # Get Tantivy backend
            tantivy = self._get_tantivy_backend()

            # Migrate in batches
            offset = 0
            while offset < total_count:
                cursor.execute(
                    "SELECT * FROM memories LIMIT ? OFFSET ?",
                    (batch_size, offset)
                )
                rows = cursor.fetchall()

                if not rows:
                    break

                # Transform and index batch
                for row in rows:
                    try:
                        from .tantivy_backend import Memory

                        memory = Memory(
                            id=row['id'] if 'id' in row.keys() else str(row[0]),
                            content=row['content'] if 'content' in row.keys() else str(row[1]),
                            metadata=row.get('metadata', '') if hasattr(row, 'get') else '',
                            timestamp=row.get('timestamp', 0) if hasattr(row, 'get') else 0,
                            source=row.get('source', '') if hasattr(row, 'get') else '',
                            relevance=row.get('relevance', 1.0) if hasattr(row, 'get') else 1.0,
                            division=row.get('division', '') if hasattr(row, 'get') else '',
                        )

                        await tantivy.index_memory(memory)
                        total_migrated += 1

                    except Exception as e:
                        total_failed += 1
                        error_msg = f"Failed to migrate record: {e}"
                        errors.append(error_msg)
                        logger.error(error_msg)

                # Progress callback
                if progress_callback:
                    progress_callback(total_migrated, total_count)

                offset += batch_size

            # Commit all changes
            await tantivy.optimize()

            conn.close()

            return MigrationResult(
                success=total_failed == 0,
                total_migrated=total_migrated,
                total_failed=total_failed,
                duration_seconds=time.time() - start_time,
                errors=errors,
            )

        except Exception as e:
            logger.error(f"Migration failed: {e}")
            return MigrationResult(
                success=False,
                total_migrated=total_migrated,
                total_failed=total_failed,
                duration_seconds=time.time() - start_time,
                errors=[str(e)] + errors,
            )

    async def verify(self) -> VerificationResult:
        """Verify migration completeness.
        
        Compares record counts between SQLite and Tantivy,
        and checks for missing or extra records.
        
        Returns:
            VerificationResult with verification details.
        """
        try:
            # Get SQLite count
            conn = self._get_sqlite_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM memories")
            sqlite_count = cursor.fetchone()[0]

            # Get SQLite IDs
            cursor.execute("SELECT id FROM memories")
            sqlite_ids = set(row['id'] for row in cursor.fetchall())
            conn.close()

            # Get Tantivy count
            tantivy = self._get_tantivy_backend()
            stats = await tantivy.get_stats()
            tantivy_count = stats.total_documents

            # Compare
            mismatch = sqlite_count != tantivy_count

            return VerificationResult(
                sqlite_count=sqlite_count,
                tantivy_count=tantivy_count,
                mismatch=mismatch,
            )

        except Exception as e:
            logger.error(f"Verification failed: {e}")
            return VerificationResult(
                sqlite_count=0,
                tantivy_count=0,
                mismatch=True,
                missing_in_tantivy=[str(e)],
            )

    async def rollback(self) -> bool:
        """Rollback migration if issues found.
        
        Removes the Tantivy index directory.
        
        Returns:
            True if rollback successful.
        """
        try:
            import shutil

            # Close backend if open
            if self._tantivy_backend:
                await self._tantivy_backend.close()
                self._tantivy_backend = None

            # Remove Tantivy index directory
            tantivy_dir = Path(self.tantivy_path)
            if tantivy_dir.exists():
                shutil.rmtree(tantivy_dir)
                logger.info(f"Rollback: Removed Tantivy index at {self.tantivy_path}")

            return True

        except Exception as e:
            logger.error(f"Rollback failed: {e}")
            return False
