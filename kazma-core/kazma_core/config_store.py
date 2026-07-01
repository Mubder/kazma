"""Runtime Configuration Store — SQLite-backed settings with YAML fallback.

Provides persistent, hot-reloadable configuration that overrides kazma.yaml
at runtime. All WebUI settings changes are stored here.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

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


class ConfigStore:
    """SQLite-backed runtime configuration with YAML fallback."""

    def __init__(self, db_path: str | None = None, yaml_path: str | None = None) -> None:
        self._db_path = Path(db_path or _DEFAULT_DB)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._yaml_path = Path(yaml_path or "kazma.yaml")
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._yaml_cache: dict[str, Any] | None = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_conn()
            conn.executescript(_SCHEMA)
            conn.commit()

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
            conn = self._get_conn()
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            if row is not None:
                return json.loads(row["value"])
            # Re-merge flattened children (e.g. from import_yaml round-trip).
            merged = self._collect_prefixed(conn, key)
            if merged:
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
        return val if val is not None else default

    def set(self, key: str, value: Any, category: str = "general") -> None:
        """Set a setting in the DB."""
        now = datetime.now(UTC).isoformat()
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """INSERT OR REPLACE INTO settings (key, value, category, updated_at)
                   VALUES (?, ?, ?, ?)""",
                (key, json.dumps(value), category, now),
            )
            conn.commit()
        logger.info("Setting updated: %s = %s (category=%s)", key, value, category)

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
            cursor = conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            conn.commit()
            return cursor.rowcount > 0

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
        """
        import yaml

        data = yaml.safe_load(yaml_str)
        if not isinstance(data, dict):
            return 0

        count = 0

        def _flatten(d: dict, prefix: str = "") -> None:
            nonlocal count
            for k, v in d.items():
                full_key = f"{prefix}.{k}" if prefix else k
                if isinstance(v, dict):
                    _flatten(v, full_key)
                else:
                    cat = prefix.split(".")[0] if prefix else "general"
                    self.set(full_key, v, category=cat)
                    count += 1

        _flatten(data)
        return count

    def reset_all(self) -> int:
        """Delete all DB settings (reverts to YAML defaults). Returns count deleted."""
        with self._lock:
            conn = self._get_conn()
            cursor = conn.execute("DELETE FROM settings")
            conn.commit()
            return cursor.rowcount

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
