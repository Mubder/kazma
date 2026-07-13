"""Runtime Configuration Store — SQLite-backed settings with YAML fallback.

Provides persistent, hot-reloadable configuration that overrides kazma.yaml
at runtime. All WebUI settings changes are stored here.

Concurrency model:
    WAL journaling + ``busy_timeout=5000`` allow concurrent readers and a
    single writer without "database is locked" errors. A process-wide
    singleton (``get_config_store()``) ensures all components share one
    connection and one ``threading.Lock``, so cross-component writes
    coordinate correctly. Multi-key writes should use ``batch_set()`` or
    the ``transaction()`` context manager for atomicity.

    All stores should use ``apply_sqlite_pragmas(conn)`` for consistency.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import yaml

logger = logging.getLogger(__name__)


def get_kazma_secret() -> str:
    """Central getter for KAZMA_SECRET (env → ConfigStore → auto-gen).

    Resolution order:
      1. ``KAZMA_SECRET`` environment variable
      2. Empty string if ``KAZMA_AUTH_DISABLED`` is set
      3. Empty string under pytest (preserve open/closed test configs)
      4. ``security.secret`` in ConfigStore
      5. Auto-generate + persist 32-char hex token (non-test only)

    UI auth, Hub, MCP, and approve paths should call this helper.
    """
    env_secret = os.environ.get("KAZMA_SECRET", "").strip()
    if env_secret:
        return env_secret

    if os.environ.get("KAZMA_AUTH_DISABLED", "").lower() in ("true", "1", "yes"):
        return ""

    import sys

    if "pytest" in sys.modules:
        return ""

    try:
        store = get_config_store()
        db_secret = store.get("security.secret")
        if db_secret:
            return str(db_secret).strip()

        import secrets

        new_secret = secrets.token_hex(16)
        store.set("security.secret", new_secret, category="security")
        logger.warning(
            "[SECURITY] Auto-generated KAZMA_SECRET — set KAZMA_SECRET env var to persist",
        )
        return new_secret
    except Exception as exc:
        logger.debug("[SECURITY] Could not load/generate ConfigStore secret: %s", exc)
        return ""


def get_or_create_disclosure_key(store: "ConfigStore" | None = None) -> str:
    """Get or create the disclosure HMAC key.
    
    If KAZMA_DISCLOSURE_KEY is set in environment, use it.
    Otherwise, generate a secure random key and persist it in ConfigStore.
    
    Args:
        store: ConfigStore instance (uses global singleton if None)
        
    Returns:
        The disclosure key (32 bytes hex = 64 chars)
    """
    import secrets
    
    # Check environment first
    env_key = os.environ.get("KAZMA_DISCLOSURE_KEY", "").strip()
    if env_key:
        return env_key
    
    # Use global store if not provided
    if store is None:
        store = get_config_store()
    
    # Try to get from store
    key = store.get("security.disclosure_key")
    if key:
        return key
    
    # Generate new key and persist
    new_key = secrets.token_hex(32)  # 32 bytes = 64 hex chars
    store.set("security.disclosure_key", new_key)
    logger.info("[ConfigStore] Generated new disclosure key")
    return new_key


def apply_sqlite_pragmas(conn: sqlite3.Connection, *, busy_timeout: int = 5000) -> None:
    """Apply standardized pragmas to a (sync) Kazma SQLite connection.

    - WAL + busy_timeout + synchronous=NORMAL
    """
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={busy_timeout}")
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception as exc:
        logger.warning("[SQLite] Failed to apply pragmas: %s", exc)


async def apply_sqlite_pragmas_async(conn: Any, *, busy_timeout: int = 5000) -> None:
    """Async version for aiosqlite connections (agent_runner checkpoints, graph_builder, gateway stores)."""
    try:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute(f"PRAGMA busy_timeout={busy_timeout}")
        await conn.execute("PRAGMA synchronous=NORMAL")
    except Exception as exc:
        logger.warning("[SQLite] Failed to apply async pragmas: %s", exc)

_DEFAULT_DB = "kazma-data/settings.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_settings_category ON settings(category);
"""


_MISSING = object()


# ─── Migration Framework ────────────────────────────────────────────────

class Migration:
    """A single database migration."""
    
    def __init__(self, version: int, name: str, sql: str) -> None:
        self.version = version
        self.name = name
        self.sql = sql
    
    def __repr__(self) -> str:
        return f"Migration(v={self.version}, name={self.name})"


# ConfigStore schema migrations.
# Versions start at 100 to avoid collision with migrations.py's
# CONFIG_STORE_MIGRATIONS (v1-v3). These run automatically on init;
# the migrations.py runner is used by the admin UI.
CONFIG_STORE_MIGRATIONS: list[Migration] = [
    Migration(
        version=100,
        name="initial_schema",
        sql="""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'general',
            updated_at TEXT NOT NULL,
            scope TEXT DEFAULT 'global'
        );
        CREATE INDEX IF NOT EXISTS idx_settings_category ON settings(category);
        CREATE INDEX IF NOT EXISTS idx_settings_scope ON settings(scope);
        """,
    ),
    # v101 was "add_scope_column" (ALTER TABLE). Now a no-op because
    # the column is included in the initial_schema above. Kept as a
    # placeholder for databases already migrated to v101.
    Migration(
        version=101,
        name="add_scope_column",
        sql="""
        -- No-op: scope column is included in initial_schema (v100).
        -- Existing DBs that had the column added via old v2 ALTER TABLE
        -- are unaffected; fresh DBs get it from the CREATE TABLE.
        """,
    ),
]


class MigrationRunner:
    """Runs database migrations for a store."""
    
    def __init__(self, db_path: str, migrations: list[Migration]) -> None:
        self.db_path = db_path
        self.migrations = sorted(migrations, key=lambda m: m.version)
        self._migration_table = "schema_migrations"
    
    def _ensure_migration_table(self, conn: sqlite3.Connection) -> None:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {self._migration_table} (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
        """)
    
    def get_applied_versions(self, conn: sqlite3.Connection) -> set[int]:
        self._ensure_migration_table(conn)
        cursor = conn.execute(f"SELECT version FROM {self._migration_table}")
        return {row[0] for row in cursor.fetchall()}
    
    def run(self) -> list[Migration]:
        """Run all pending migrations. Returns list of applied migrations."""
        import sqlite3
        from datetime import UTC, datetime
        
        applied = []
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        apply_sqlite_pragmas(conn)
        
        try:
            applied_versions = self.get_applied_versions(conn)
            
            for migration in self.migrations:
                if migration.version in applied_versions:
                    continue
                
                logger.info(f"[Migration] Applying {migration} to {self.db_path}")
                conn.execute("BEGIN")
                try:
                    conn.executescript(migration.sql)
                    conn.execute(
                        f"INSERT INTO {self._migration_table} (version, name, applied_at) VALUES (?, ?, ?)",
                        (migration.version, migration.name, datetime.now(UTC).isoformat()),
                    )
                    conn.execute("COMMIT")
                    applied.append(migration)
                    logger.info(f"[Migration] Applied {migration} successfully")
                except Exception as e:
                    conn.execute("ROLLBACK")
                    logger.error(f"[Migration] Failed to apply {migration}: {e}")
                    raise
        finally:
            conn.close()
        
        return applied


def run_config_store_migrations(db_path: str) -> list[Migration]:
    """Run ConfigStore migrations. Returns list of applied migrations."""
    runner = MigrationRunner(db_path, CONFIG_STORE_MIGRATIONS)
    return runner.run()


class ConfigStoreProtocol(Protocol):
    """Protocol defining the ConfigStore interface for type safety."""
    
    def get(self, key: str, default: Any = None) -> Any: ...
    def set(self, key: str, value: Any) -> None: ...
    def batch_set(self, items: list[tuple[str, Any, str]]) -> int: ...
    def transaction(self): ...
    def get_category(self, category: str) -> dict[str, Any]: ...
    def get_all(self) -> dict[str, dict[str, Any]]: ...
    def delete(self, key: str) -> bool: ...
    def export_yaml(self) -> str: ...
    def import_yaml(self, yaml_str: str) -> int: ...
    def reconcile_from_yaml(self) -> int: ...
    def reset_all(self) -> int: ...
    def close(self) -> None: ...


class _InMemoryStore:
    """Thread-safe in-memory fallback with TTL eviction.
    
    Implements ConfigStoreProtocol for use when SQLite is unavailable.
    Uses threading.Lock for thread safety (not asyncio.Lock) to match
    ConfigStore's synchronous interface.
    """
    
    def __init__(self, max_entries: int = 10_000, ttl_seconds: int = 3600) -> None:
        self._data: dict[str, Any] = {}
        self._timestamps: dict[str, float] = {}
        self._max_entries = max_entries
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
    
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            self._evict_expired()
            return self._data.get(key, default)
    
    def set(self, key: str, value: Any, category: str = "general") -> None:
        with self._lock:
            self._evict_expired()
            if len(self._data) >= self._max_entries:
                self._evict_oldest()
            self._data[key] = value
            self._timestamps[key] = time.monotonic()
    
    def batch_set(self, items: list[tuple[str, Any, str]]) -> int:
        with self._lock:
            self._evict_expired()
            for key, value, _category in items:
                if len(self._data) >= self._max_entries:
                    self._evict_oldest()
                self._data[key] = value
                self._timestamps[key] = time.monotonic()
            return len(items)
    
    def _evict_oldest(self) -> None:
        """Evict the oldest 10% of entries."""
        if not self._timestamps:
            return
        oldest = sorted(self._timestamps.items(), key=lambda x: x[1])[:max(1, self._max_entries // 10)]
        for k, _ in oldest:
            self._data.pop(k, None)
            self._timestamps.pop(k, None)
    
    def _evict_expired(self) -> None:
        """Remove expired entries. Must be called with lock held."""
        now = time.monotonic()
        expired = [k for k, ts in self._timestamps.items() if now - ts > self._ttl]
        for k in expired:
            self._data.pop(k, None)
            self._timestamps.pop(k, None)
    
    def get_category(self, category: str) -> dict[str, Any]:
        with self._lock:
            self._evict_expired()
            return {k: v for k, v in self._data.items() if k.startswith(category + ".")}
    
    def get_all(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            self._evict_expired()
            return {"general": dict(self._data)}
    
    def delete(self, key: str) -> bool:
        with self._lock:
            if key in self._data:
                self._data.pop(key)
                self._timestamps.pop(key, None)
                return True
            return False
    
    def export_yaml(self) -> str:
        with self._lock:
            self._evict_expired()
            return yaml.dump(self._data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    
    def import_yaml(self, yaml_str: str) -> int:
        data = yaml.safe_load(yaml_str)
        if not isinstance(data, dict):
            return 0
        items = []
        def _flatten(d: dict, prefix: str = "") -> None:
            for k, v in d.items():
                full_key = f"{prefix}.{k}" if prefix else k
                if isinstance(v, dict):
                    _flatten(v, full_key)
                else:
                    items.append((full_key, v, "general"))
        _flatten(data)
        return self.batch_set(items)
    
    def reconcile_from_yaml(self) -> int:
        return 0
    
    def reset_all(self) -> int:
        with self._lock:
            count = len(self._data)
            self._data.clear()
            self._timestamps.clear()
            return count

    def close(self) -> None:
        with self._lock:
            self._data.clear()
            self._timestamps.clear()



class ConfigStore:
    """SQLite-backed runtime configuration with YAML fallback."""

    def __init__(self, db_path: str | None = None, yaml_path: str | None = None) -> None:
        self._db_path = Path(db_path or _DEFAULT_DB)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._yaml_path = Path(yaml_path or "kazma.yaml")
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._yaml_cache: dict[str, Any] | None = None
        self._cache: dict[str, Any] = {}
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self._db_path),
                check_same_thread=False,
                isolation_level=None,
            )
            self._conn.row_factory = sqlite3.Row
            apply_sqlite_pragmas(self._conn)
        return self._conn

    def _init_db(self) -> None:
        """Initialize database schema and run migrations."""
        with self._lock:
            conn = self._get_conn()
            # Run migrations instead of simple schema creation
            run_config_store_migrations(str(self._db_path))

    def _load_yaml(self) -> dict[str, Any]:
        """Load and cache the base YAML config."""
        if self._yaml_cache is not None:
            return self._yaml_cache
        if self._yaml_path.exists():
            with open(self._yaml_path) as f:
                self._yaml_cache = yaml.safe_load(f) or {}
        else:
            self._yaml_cache = {}
        return self._yaml_cache

    def invalidate_yaml_cache(self) -> None:
        """Force re-read of kazma.yaml on next access."""
        self._yaml_cache = None
        with self._lock:
            self._cache.clear()

    def reload_from_root(self, root_path: str | Path) -> None:
        """Update the yaml_path to the new workspace root and invalidate cache."""
        self._yaml_path = Path(root_path) / "kazma.yaml"
        self.invalidate_yaml_cache()
        logger.info("[ConfigStore] Hot-reloaded configurations from new root: %s", root_path)

    # ── Public API ────────────────────────────────────────────────────

    def _collect_prefixed(self, conn: sqlite3.Connection, prefix: str) -> dict[str, Any]:
        """Collect DB rows whose key starts with ``prefix.`` and de-dot them.

        Returns a nested dict. For example rows ``swarm.output_target.platform``
        and ``swarm.output_target.chat_id`` with prefix ``swarm.output_target``
        return ``{"platform": ..., "chat_id": ...}``.
        """
        like = prefix + ".%"
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE ?", (like,)
        ).fetchall()
        if not rows:
            return {}
        result: dict[str, Any] = {}
        for row in rows:
            sub_key = row["key"][len(prefix) + 1:]  # strip "prefix."
            parts = sub_key.split(".")
            target = result
            for part in parts[:-1]:
                if part not in target or not isinstance(target[part], dict):
                    target[part] = {}
                target = target[part]
            target[parts[-1]] = json.loads(row["value"])
        return result

    def get(self, key: str, default: Any = None) -> Any:
        """Get a setting. DB overrides YAML.

        Resolution order:
            1. Exact DB key match.
            2. DB child keys (``key.*``) merged into a dict — so a value
               flattened by ``import_yaml`` is reassembled transparently.
            3. YAML dotted-key lookup.
        """
        with self._lock:
            if key in self._cache:
                cached = self._cache[key]
                if cached is _MISSING:
                    return default
                return cached

            conn = self._get_conn()
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            if row is not None:
                val = json.loads(row["value"])
                self._cache[key] = val
                return val
            # Re-merge flattened children (e.g. from import_yaml round-trip).
            merged = self._collect_prefixed(conn, key)
            if merged:
                self._cache[key] = merged
                return merged

        # Fall back to YAML (supports dotted keys like "llm.model")
        yaml_data = self._load_yaml()
        parts = key.split(".")
        val: Any = yaml_data
        for part in parts:
            if isinstance(val, dict):
                val = val.get(part)
            else:
                val = None
                break

        with self._lock:
            if val is not None:
                self._cache[key] = val
                return val
            else:
                self._cache[key] = _MISSING
                return default

    def set(self, key: str, value: Any, category: str = "general") -> None:
        """Set a setting in the DB."""
        now = datetime.now(UTC).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("BEGIN")
                conn.execute(
                    """INSERT OR REPLACE INTO settings (key, value, category, updated_at)
                       VALUES (?, ?, ?, ?)""",
                    (key, json.dumps(value), category, now),
                )
                conn.execute("COMMIT")
                self._cache.clear()
            except Exception:
                logger.debug("set() write failed, rolling back for key=%s", key)
                conn.execute("ROLLBACK")
                raise
        logger.info("Setting updated: %s = %s (category=%s)", key, value, category)

    def batch_set(self, items: list[tuple[str, Any, str]]) -> int:
        """Atomically set multiple keys in a single transaction.

        All writes succeed or all roll back — a crash or exception mid-batch
        leaves the DB unchanged. Use this instead of looping ``set()`` when
        writing multiple related keys (e.g. config import, settings save).

        Args:
            items: List of ``(key, value, category)`` tuples.

        Returns:
            Number of keys written.
        """
        if not items:
            return 0
        now = datetime.now(UTC).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("BEGIN")
                for key, value, category in items:
                    conn.execute(
                        """INSERT OR REPLACE INTO settings (key, value, category, updated_at)
                           VALUES (?, ?, ?, ?)""",
                        (key, json.dumps(value), category, now),
                    )
                conn.execute("COMMIT")
                self._cache.clear()
            except Exception:
                conn.execute("ROLLBACK")
                raise
        logger.info("Batch set: %d keys updated atomically", len(items))
        return len(items)

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for an atomic multi-operation transaction.

        All DB operations inside the ``with`` block run in a single
        transaction. If any exception occurs, the transaction rolls back.

        Example::

            with store.transaction() as conn:
                conn.execute("INSERT ...", ...)
                conn.execute("UPDATE ...", ...)
        """
        with self._lock:
            conn = self._get_conn()
            conn.execute("BEGIN")
            try:
                yield conn
                conn.execute("COMMIT")
                self._cache.clear()
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def get_category(self, category: str) -> dict[str, Any]:
        """Get all settings in a category from DB."""
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute("SELECT key, value FROM settings WHERE category = ?", (category,)).fetchall()
        return {row["key"]: json.loads(row["value"]) for row in rows}

    def get_all(self) -> dict[str, dict[str, Any]]:
        """Get all settings grouped by category."""
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute("SELECT key, value, category FROM settings ORDER BY category, key").fetchall()
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            cat = row["category"]
            if cat not in result:
                result[cat] = {}
            result[cat][row["key"]] = json.loads(row["value"])
        return result

    def delete(self, key: str) -> bool:
        """Delete a setting. Returns True if a row was deleted."""
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("BEGIN")
                cursor = conn.execute("DELETE FROM settings WHERE key = ?", (key,))
                conn.execute("COMMIT")
                if cursor.rowcount > 0:
                    self._cache.clear()
                    return True
                return False
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def export_yaml(self) -> str:
        """Export current settings as YAML, merging DB overrides with base YAML."""
        base = self._load_yaml()
        db_settings = self.get_all()

        # Merge DB settings into base config using dotted keys
        for category, settings in db_settings.items():
            for key, value in settings.items():
                parts = key.split(".")
                target = base
                for part in parts[:-1]:
                    if part not in target or not isinstance(target[part], dict):
                        target[part] = {}
                    target = target[part]
                target[parts[-1]] = value

        return yaml.dump(base, default_flow_style=False, allow_unicode=True, sort_keys=False)

    def import_yaml(self, yaml_str: str) -> int:
        """Import settings from YAML string. Returns number of settings imported.

        Nested YAML mappings are flattened to dotted keys. Dict/list values
        are preserved through ``get()`` which re-merges flattened children
        transparently (so export→import round-trips are lossless).

        The import is **atomic** — all keys are written in a single
        transaction via ``batch_set()``. A crash mid-import rolls back.
        """
        data = yaml.safe_load(yaml_str)
        if not isinstance(data, dict):
            return 0

        items: list[tuple[str, Any, str]] = []

        def _flatten(d: dict, prefix: str = "") -> None:
            for k, v in d.items():
                full_key = f"{prefix}.{k}" if prefix else k
                if isinstance(v, dict):
                    _flatten(v, full_key)
                else:
                    cat = prefix.split(".")[0] if prefix else "general"
                    items.append((full_key, v, cat))

        _flatten(data)
        return self.batch_set(items)

    def reconcile_from_yaml(self) -> int:
        """Seed DB with kazma.yaml values for keys not already in the DB.

        This is the startup reconciliation step that makes ConfigStore the
        authoritative source: on first run (or when new YAML keys appear),
        YAML values are copied into SQLite so all components read from one
        place. Existing DB keys are **never overwritten** — user-made
        settings changes always win.

        Returns the number of new keys seeded.
        """
        if not self._yaml_path.exists():
            return 0

        yaml_text = self._yaml_path.read_text()
        data = yaml.safe_load(yaml_text)
        if not isinstance(data, dict):
            return 0

        # Collect all YAML leaf values as (key, value, category).
        yaml_items: list[tuple[str, Any, str]] = []

        def _flatten(d: dict, prefix: str = "") -> None:
            for k, v in d.items():
                full_key = f"{prefix}.{k}" if prefix else k
                if isinstance(v, dict):
                    _flatten(v, full_key)
                else:
                    cat = prefix.split(".")[0] if prefix else "general"
                    yaml_items.append((full_key, v, cat))

        _flatten(data)

        # Find which keys are NOT already in the DB.
        with self._lock:
            conn = self._get_conn()
            existing_rows = conn.execute("SELECT key FROM settings").fetchall()
            existing_keys = {row["key"] for row in existing_rows}

        new_items = [
            (key, value, cat)
            for key, value, cat in yaml_items
            if key not in existing_keys
        ]

        if not new_items:
            return 0

        logger.info(
            "[ConfigStore] Reconciling %d new keys from kazma.yaml into SQLite",
            len(new_items),
        )
        return self.batch_set(new_items)

    def reset_all(self) -> int:
        """Delete all DB settings (reverts to YAML defaults). Returns count deleted."""
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("BEGIN")
                cursor = conn.execute("DELETE FROM settings")
                conn.execute("COMMIT")
                self._cache.clear()
                return cursor.rowcount
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None


# ══════════════════════════════════════════════════════════════════════════
# Process-wide singleton — ensures all components share one connection + lock
# ══════════════════════════════════════════════════════════════════════════

_config_store: ConfigStore | None = None


def get_config_store() -> ConfigStore:
    """Return the shared ConfigStore singleton.

    Lazily creates a default instance on first call. All components should
    use this instead of constructing ``ConfigStore()`` directly, so they
    share one SQLite connection and one ``threading.Lock`` for write
    coordination.

    On SQLite initialization failure, falls back to an in-memory store
    rather than returning None, preventing downstream AttributeError.
    """
    global _config_store
    if _config_store is None:
        try:
            _config_store = ConfigStore()
        except Exception as e:
            logger.error(f"Failed to initialize ConfigStore (SQLite): {e}. Using in-memory fallback.")
            _config_store = _InMemoryStore()  # type: ignore[assignment]
    return _config_store


def set_config_store(store: ConfigStore) -> None:
    """Replace the shared singleton (used by tests and explicit init)."""
    global _config_store
    _config_store = store


def reset_config_store() -> None:
    """Drop the singleton reference (used by test teardown)."""
    global _config_store
    if _config_store is not None:
        _config_store.close()
    _config_store = None


def get_validated_config() -> KazmaConfig:
    """Get fully validated configuration from ConfigStore.
    
    Reads all settings from the store (DB + YAML) and validates
    against the KazmaConfig Pydantic model. Raises ValidationError
    if configuration is inconsistent.
    
    Returns:
        KazmaConfig: Validated configuration object.
    """
    from kazma_core.config_schema import KazmaConfig
    
    store = get_config_store()
    flat = {}
    
    # Get all settings from store
    if hasattr(store, 'get_all'):
        all_settings = store.get_all()
        for category, settings in all_settings.items():
            for key, value in settings.items():
                flat[key] = value
    else:
        # Fallback for in-memory store
        flat = dict(store._data) if hasattr(store, '_data') else {}
    
    return KazmaConfig.from_flat_dict(flat)
