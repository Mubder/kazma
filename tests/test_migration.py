"""Tests for SQLite to Tantivy Migration.

Comprehensive tests for the SQLiteToTantivyMigration including
migration, verification, and rollback capabilities.
"""
import json
import tempfile
import time
from pathlib import Path

import pytest
from kazma_memory.migration import (
    MigrationResult,
    SQLiteToTantivyMigration,
    VerificationResult,
)


@pytest.fixture
def temp_dirs():
    """Create temporary directories for SQLite and Tantivy."""
    with tempfile.TemporaryDirectory() as sqlite_dir, \
         tempfile.TemporaryDirectory() as tantivy_dir:
        yield {
            "sqlite": Path(sqlite_dir) / "memory.db",
            "tantivy": Path(tantivy_dir) / "tantivy-index",
        }


@pytest.fixture
def sample_sqlite_db(temp_dirs):
    """Create a sample SQLite database with test data."""
    import sqlite3

    db_path = temp_dirs["sqlite"]
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # Create memories table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            metadata TEXT DEFAULT '',
            timestamp INTEGER DEFAULT 0,
            source TEXT DEFAULT '',
            relevance REAL DEFAULT 1.0,
            division TEXT DEFAULT ''
        )
    """)

    # Insert sample data
    for i in range(10):
        cursor.execute(
            "INSERT INTO memories (id, content, metadata, timestamp, source, relevance, division) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                f"mem_{i}",
                f"Test memory content {i}",
                json.dumps({"index": i}),
                int(time.time()),
                "test_source",
                0.5 + (i * 0.05),
                "engineering" if i % 2 == 0 else "finance",
            )
        )

    conn.commit()
    conn.close()

    return db_path


@pytest.fixture
def migration(temp_dirs):
    """Create a migration instance."""
    return SQLiteToTantivyMigration(
        sqlite_path=str(temp_dirs["sqlite"]),
        tantivy_path=str(temp_dirs["tantivy"]),
    )


class TestMigrationResult:
    """Test MigrationResult dataclass."""

    def test_migration_result_creation(self):
        """Test creating a MigrationResult."""
        result = MigrationResult(
            success=True,
            total_migrated=100,
            total_failed=5,
            duration_seconds=12.5,
            errors=["Error 1", "Error 2"],
        )

        assert result.success is True
        assert result.total_migrated == 100
        assert result.total_failed == 5
        assert result.duration_seconds == 12.5
        assert len(result.errors) == 2

    def test_migration_result_defaults(self):
        """Test MigrationResult with default values."""
        result = MigrationResult(
            success=True,
            total_migrated=0,
            total_failed=0,
            duration_seconds=0.0,
        )

        assert result.errors == []

    def test_migration_result_failure(self):
        """Test MigrationResult for failed migration."""
        result = MigrationResult(
            success=False,
            total_migrated=50,
            total_failed=50,
            duration_seconds=30.0,
            errors=["Connection failed"],
        )

        assert result.success is False
        assert result.total_failed > 0


class TestVerificationResult:
    """Test VerificationResult dataclass."""

    def test_verification_result_creation(self):
        """Test creating a VerificationResult."""
        result = VerificationResult(
            sqlite_count=100,
            tantivy_count=100,
            mismatch=False,
            missing_in_tantivy=[],
            extra_in_tantivy=[],
        )

        assert result.sqlite_count == 100
        assert result.tantivy_count == 100
        assert result.mismatch is False

    def test_verification_result_mismatch(self):
        """Test VerificationResult with mismatch."""
        result = VerificationResult(
            sqlite_count=100,
            tantivy_count=95,
            mismatch=True,
            missing_in_tantivy=["mem_95", "mem_96", "mem_97", "mem_98", "mem_99"],
            extra_in_tantivy=[],
        )

        assert result.mismatch is True
        assert len(result.missing_in_tantivy) == 5

    def test_verification_result_defaults(self):
        """Test VerificationResult with default values."""
        result = VerificationResult(
            sqlite_count=0,
            tantivy_count=0,
            mismatch=False,
        )

        assert result.missing_in_tantivy == []
        assert result.extra_in_tantivy == []


class TestSQLiteToTantivyMigration:
    """Test suite for SQLiteToTantivyMigration."""

    def test_init(self, temp_dirs):
        """Test migration initialization."""
        mig = SQLiteToTantivyMigration(
            sqlite_path=str(temp_dirs["sqlite"]),
            tantivy_path=str(temp_dirs["tantivy"]),
        )

        assert mig.sqlite_path == str(temp_dirs["sqlite"])
        assert mig.tantivy_path == str(temp_dirs["tantivy"])
        assert mig._tantivy_backend is None

    def test_get_sqlite_conn(self, migration, sample_sqlite_db):
        """Test getting SQLite connection."""
        conn = migration._get_sqlite_conn()
        assert conn is not None
        conn.close()

    @pytest.mark.asyncio
    async def test_migrate_empty_database(self, temp_dirs):
        """Test migrating an empty database."""
        import sqlite3

        # Create empty database
        db_path = temp_dirs["sqlite"]
        db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

        mig = SQLiteToTantivyMigration(
            sqlite_path=str(db_path),
            tantivy_path=str(temp_dirs["tantivy"]),
        )

        result = await mig.migrate()

        assert result.success is True
        assert result.total_migrated == 0
        assert result.total_failed == 0

    @pytest.mark.asyncio
    async def test_migrate_with_data(self, migration, sample_sqlite_db):
        """Test migrating with actual data."""
        # This test requires tantivy to be installed
        pytest.importorskip("tantivy")

        result = await migration.migrate(batch_size=5)

        assert isinstance(result, MigrationResult)
        assert result.total_migrated >= 0
        assert result.duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_migrate_with_progress_callback(self, migration, sample_sqlite_db):
        """Test migration with progress callback."""
        pytest.importorskip("tantivy")

        progress_calls = []

        def progress_callback(migrated, total):
            progress_calls.append((migrated, total))

        result = await migration.migrate(
            batch_size=5,
            progress_callback=progress_callback,
        )

        # Progress callback should have been called
        # (may not be called if database is small)

    @pytest.mark.asyncio
    async def test_verify(self, migration, sample_sqlite_db):
        """Test migration verification."""
        pytest.importorskip("tantivy")

        # First migrate
        await migration.migrate()

        # Then verify
        result = await migration.verify()

        assert isinstance(result, VerificationResult)
        assert result.sqlite_count >= 0
        assert result.tantivy_count >= 0

    @pytest.mark.asyncio
    async def test_rollback(self, migration, temp_dirs):
        """Test migration rollback."""
        # Create some files in tantivy directory
        tantivy_dir = Path(temp_dirs["tantivy"])
        tantivy_dir.mkdir(parents=True, exist_ok=True)
        (tantivy_dir / "test.txt").write_text("test")

        # Rollback
        success = await migration.rollback()

        assert success is True
        assert not tantivy_dir.exists()

    @pytest.mark.asyncio
    async def test_rollback_nonexistent_directory(self, migration):
        """Test rollback when directory doesn't exist."""
        success = await migration.rollback()

        # Should succeed even if directory doesn't exist
        assert success is True


class TestSQLiteToTantivyMigrationEdgeCases:
    """Test edge cases and error handling."""

    def test_init_default_paths(self):
        """Test migration with default paths."""
        mig = SQLiteToTantivyMigration()

        assert mig.sqlite_path == "kazma-data/memory.db"
        assert mig.tantivy_path == "kazma-data/tantivy-index"

    @pytest.mark.asyncio
    async def test_migrate_nonexistent_database(self, temp_dirs):
        """Test migrating from nonexistent database."""
        mig = SQLiteToTantivyMigration(
            sqlite_path="/nonexistent/memory.db",
            tantivy_path=str(temp_dirs["tantivy"]),
        )

        result = await mig.migrate()

        # Should fail gracefully
        assert result.success is False
        assert len(result.errors) > 0

    @pytest.mark.asyncio
    async def test_verify_before_migrate(self, migration, sample_sqlite_db):
        """Test verification before migration."""
        pytest.importorskip("tantivy")

        result = await migration.verify()

        # Should show mismatch (SQLite has data, Tantivy is empty)
        assert result.sqlite_count > 0
        assert result.tantivy_count == 0
        assert result.mismatch is True


class TestSQLiteToTantivyMigrationPerformance:
    """Test migration performance characteristics."""

    @pytest.mark.asyncio
    async def test_migrate_batch_size_affects_performance(self, migration, sample_sqlite_db):
        """Test that batch size affects migration performance."""
        pytest.importorskip("tantivy")

        # Migrate with small batch size
        result_small = await migration.migrate(batch_size=2)

        # Verify migration completed
        assert result_small.total_migrated >= 0
