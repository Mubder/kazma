"""Database migration framework for Kazma SQLite stores.

Provides versioned schema migrations with atomic application
and rollback capability.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Migration:
    """A single database migration."""
    version: int
    name: str
    up_sql: str
    down_sql: str | None = None  # Optional rollback SQL


class MigrationRunner:
    """Runs migrations against a SQLite database."""
    
    def __init__(self, db_path: str | Path, table: str = "schema_migrations") -> None:
        self.db_path = Path(db_path)
        self.table = table
        self.migrations: list[Migration] = []
    
    def add_migration(self, migration: Migration) -> None:
        """Add a migration to the plan."""
        self.migrations.append(migration)
        self.migrations.sort(key=lambda m: m.version)
    
    def add_migrations(self, migrations: list[Migration]) -> None:
        """Add multiple migrations."""
        for m in migrations:
            self.add_migration(m)
    
    def _get_connection(self):
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        from kazma_core.config_store import apply_sqlite_pragmas

        apply_sqlite_pragmas(conn)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _ensure_migration_table(self, conn) -> None:
        """Create migration tracking table if it doesn't exist."""
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.table} (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at REAL NOT NULL,
                success INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.commit()
    
    def _get_applied_versions(self, conn) -> set[int]:
        """Get set of already applied migration versions."""
        self._ensure_migration_table(conn)
        rows = conn.execute(f"SELECT version FROM {self.table} WHERE success = 1").fetchall()
        return {row["version"] for row in rows}
    
    def _record_migration(self, conn, migration: Migration, success: bool) -> None:
        """Record migration application."""
        conn.execute(
            f"INSERT OR REPLACE INTO {self.table} (version, name, applied_at, success) VALUES (?, ?, ?, ?)",
            (migration.version, migration.name, time.time(), 1 if success else 0),
        )
        conn.commit()
    
    def run(self, target_version: int | None = None) -> list[Migration]:
        """Run all pending migrations up to target_version.
        
        Args:
            target_version: If set, only migrate up to this version (inclusive).
            
        Returns:
            List of migrations that were applied.
        """
        conn = self._get_connection()
        try:
            applied = self._get_applied_versions(conn)
            pending = [m for m in self.migrations if m.version not in applied]
            
            if target_version is not None:
                pending = [m for m in pending if m.version <= target_version]
            
            applied_migrations = []
            
            for migration in pending:
                logger.info(f"Applying migration {migration.version}: {migration.name}")
                try:
                    conn.execute("BEGIN")
                    conn.executescript(migration.up_sql)
                    self._record_migration(conn, migration, True)
                    conn.execute("COMMIT")
                    applied_migrations.append(migration)
                    logger.info(f"Migration {migration.version} applied successfully")
                except Exception as e:
                    conn.execute("ROLLBACK")
                    self._record_migration(conn, migration, False)
                    logger.error(f"Migration {migration.version} failed: {e}")
                    raise
            
            return applied_migrations
        finally:
            conn.close()
    
    def rollback(self, target_version: int) -> list[Migration]:
        """Rollback migrations down to target_version (exclusive).
        
        Args:
            target_version: Rollback to just above this version.
            
        Returns:
            List of migrations that were rolled back.
        """
        conn = self._get_connection()
        try:
            applied = self._get_applied_versions(conn)
            to_rollback = [m for m in self.migrations if m.version in applied and m.version > target_version]
            to_rollback.sort(key=lambda m: m.version, reverse=True)
            
            rolled_back = []
            
            for migration in to_rollback:
                if migration.down_sql is None:
                    raise ValueError(f"Migration {migration.version} has no rollback SQL")
                
                logger.info(f"Rolling back migration {migration.version}: {migration.name}")
                try:
                    conn.execute("BEGIN")
                    conn.executescript(migration.down_sql)
                    conn.execute(f"DELETE FROM {self.table} WHERE version = ?", (migration.version,))
                    conn.execute("COMMIT")
                    rolled_back.append(migration)
                    logger.info(f"Migration {migration.version} rolled back successfully")
                except Exception as e:
                    conn.execute("ROLLBACK")
                    logger.error(f"Rollback of migration {migration.version} failed: {e}")
                    raise
            
            return rolled_back
        finally:
            conn.close()
    
    def status(self) -> dict[str, Any]:
        """Get migration status."""
        conn = self._get_connection()
        try:
            applied = self._get_applied_versions(conn)
            pending = [m for m in self.migrations if m.version not in applied]
            
            rows = conn.execute(f"SELECT * FROM {self.table} ORDER BY version").fetchall()
            history = [dict(row) for row in rows]
            
            return {
                "applied_count": len(applied),
                "pending_count": len(pending),
                "latest_applied": max(applied) if applied else None,
                "pending_versions": [m.version for m in pending],
                "history": history,
            }
        finally:
            conn.close()


# ─── Built-in Migrations ───────────────────────────────────────────────

# ConfigStore migrations
CONFIG_STORE_MIGRATIONS = [
    Migration(
        version=1,
        name="initial_schema",
        up_sql="""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'general',
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_settings_category ON settings(category);
        """,
        down_sql="DROP TABLE IF EXISTS settings;",
    ),
    Migration(
        version=2,
        name="add_swarm_output_target",
        up_sql="""
            -- No schema change needed, just ensure table exists
        """,
    ),
    Migration(
        version=3,
        name="add_category_index",
        up_sql="""
            CREATE INDEX IF NOT EXISTS idx_settings_category ON settings(category);
        """,
    ),
]

# TaskStore migrations
TASK_STORE_MIGRATIONS = [
    Migration(
        version=1,
        name="initial_task_schema",
        up_sql="""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                prompt TEXT NOT NULL,
                workers TEXT NOT NULL DEFAULT '[]',
                metadata TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                result TEXT,
                error TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at);
        """,
        down_sql="DROP TABLE IF EXISTS tasks;",
    ),
    Migration(
        version=2,
        name="add_worker_results",
        up_sql="""
            CREATE TABLE IF NOT EXISTS task_worker_results (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                worker_name TEXT NOT NULL,
                status TEXT NOT NULL,
                output TEXT,
                error TEXT,
                started_at REAL,
                completed_at REAL,
                FOREIGN KEY (task_id) REFERENCES tasks(id)
            );
            CREATE INDEX IF NOT EXISTS idx_worker_results_task ON task_worker_results(task_id);
        """,
        down_sql="DROP TABLE IF EXISTS task_worker_results;",
    ),
]

# SessionStore migrations
SESSION_STORE_MIGRATIONS = [
    Migration(
        version=1,
        name="initial_session_schema",
        up_sql="""
            CREATE TABLE IF NOT EXISTS sessions (
                thread_id TEXT PRIMARY KEY,
                context TEXT NOT NULL DEFAULT '{}',
                platform TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                user_id TEXT,
                username TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_platform ON sessions(platform);
            CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at);
        """,
        down_sql="DROP TABLE IF EXISTS sessions;",
    ),
]


def get_runner(db_path: str | Path, store_type: str) -> MigrationRunner:
    """Get a configured MigrationRunner for a store type.
    
    Args:
        db_path: Path to SQLite database file.
        store_type: One of "config", "task", "session".
        
    Returns:
        MigrationRunner with appropriate migrations loaded.
    """
    runner = MigrationRunner(db_path)
    
    if store_type == "config":
        runner.add_migrations(CONFIG_STORE_MIGRATIONS)
    elif store_type == "task":
        runner.add_migrations(TASK_STORE_MIGRATIONS)
    elif store_type == "session":
        runner.add_migrations(SESSION_STORE_MIGRATIONS)
    else:
        raise ValueError(f"Unknown store_type: {store_type}")
    
    return runner


def run_startup_migrations(db_paths: dict[str, str]) -> dict[str, list[Migration]]:
    """Run all startup migrations for all stores.
    
    Args:
        db_paths: Dict mapping store_type to db_path.
        
    Returns:
        Dict of store_type -> list of applied migrations.
    """
    results = {}
    
    for store_type, db_path in db_paths.items():
        try:
            runner = get_runner(db_path, store_type)
            applied = runner.run()
            results[store_type] = applied
            if applied:
                logger.info(f"Applied {len(applied)} migrations to {store_type} store")
        except Exception as e:
            logger.error(f"Failed to run migrations for {store_type}: {e}")
            raise
    
    return results