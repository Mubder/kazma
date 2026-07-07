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
import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import os

logger = logging.getLogger(__name__)


def get_kazma_secret() -> str:
    """Central getter for KAZMA_SECRET (env-based shared secret for HITL/auth).

    All components (Hub, UI, MCP, approve) should use this instead of direct os.environ
    to allow future migration to ConfigStore persistence or other sources.
    """
    return os.environ.get("KAZMA_SECRET", "").strip()


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
        with self._lock:
            conn = self._get_conn()
            conn.executescript(_SCHEMA)

    def _load_yaml(self) -> dict[str, Any]:
        """Load and cache the base YAML config."""
        if self._yaml_cache is not None:
            return self._yaml_cache
        if self._yaml_path.exists():
            import yaml

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
        import yaml

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
        import yaml

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
        import yaml

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
    """
    global _config_store
    if _config_store is None:
        _config_store = ConfigStore()
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
