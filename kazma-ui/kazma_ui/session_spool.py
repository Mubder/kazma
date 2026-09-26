"""Local spool for chat-session saves the primary store refused.

A reply the session store could not write used to exist only in the
process's memory. The next restart, LRU eviction, or ``_refresh_from_db``
(the Web UI calls it for every gateway session) replaced it with the stale
database copy, and the reply was gone. On 2026-09-24 a NUL character in one
tool result made Postgres refuse every save of one chat; the restart that
picked up the fix discarded the only copy of a finished 2,882-char answer.

The spool closes that class, whatever the cause of the refusal:

* ``SessionManager.put()`` writes the primary store. If that raises, the
  whole session is written HERE, and the save counts as durable.
* Every load overlays the spooled snapshot, so the transcript shows the
  reply even while the primary still refuses it, across restarts.
* The next successful primary write of that session clears its entry (the
  session it wrote already carried the spooled rows). Boot retries the rest.

It is SQLite even under a Postgres backend, in its own file next to the
sessions DB: it must not share the failure mode of the store it protects.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from typing import Any

from kazma_core.config_store import apply_sqlite_pragmas

__all__ = ["SessionSpool", "merge_spooled", "spool_path_for"]

logger = logging.getLogger(__name__)


def spool_path_for(db_path: str) -> str:
    """The spool file that belongs to a sessions DB (``:memory:`` stays in memory)."""
    if not db_path or db_path == ":memory:":
        return ":memory:"
    from kazma_core.paths import chat_spool_db

    return str(chat_spool_db(db_path))


class SessionSpool:
    """One snapshot per ``(tenant_id, session_id)``; the newest save wins."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        if path != ":memory:":
            from pathlib import Path

            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        apply_sqlite_pragmas(self._conn)
        with self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS spool ("
                " tenant_id TEXT NOT NULL,"
                " session_id TEXT NOT NULL,"
                " payload TEXT NOT NULL,"
                " spooled_at TEXT NOT NULL,"
                " error TEXT DEFAULT '',"
                " PRIMARY KEY (tenant_id, session_id))"
            )
        self._keys: set[tuple[str, str]] = {
            (str(t), str(s))
            for t, s in self._conn.execute("SELECT tenant_id, session_id FROM spool")
        }

    def keys(self) -> set[tuple[str, str]]:
        """Spooled ``(tenant_id, session_id)`` pairs. No I/O: callers ask per load."""
        with self._lock:
            return set(self._keys)

    def has(self, tenant_id: str, session_id: str) -> bool:
        with self._lock:
            return (tenant_id, session_id) in self._keys

    def save(self, tenant_id: str, session_id: str, payload: dict[str, Any], error: str = "") -> None:
        """Store the snapshot durably. Raises if it could not be stored."""
        from datetime import UTC, datetime

        from kazma_core.db.pg_helpers import json_dumps

        with self._lock:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO spool (tenant_id, session_id, payload, spooled_at, error)"
                    " VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT (tenant_id, session_id) DO UPDATE SET"
                    " payload = excluded.payload, spooled_at = excluded.spooled_at,"
                    " error = excluded.error",
                    (
                        tenant_id,
                        session_id,
                        json_dumps(payload),
                        datetime.now(UTC).isoformat(),
                        str(error)[:500],
                    ),
                )
            self._keys.add((tenant_id, session_id))

    def get(self, tenant_id: str, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            if (tenant_id, session_id) not in self._keys:
                return None
            row = self._conn.execute(
                "SELECT payload FROM spool WHERE tenant_id = ? AND session_id = ?",
                (tenant_id, session_id),
            ).fetchone()
        if not row:
            return None
        try:
            payload = json.loads(row[0])
        except (TypeError, ValueError):
            logger.error(
                "[SessionSpool] unreadable snapshot for %s:%s kept for inspection",
                tenant_id,
                session_id,
            )
            return None
        return payload if isinstance(payload, dict) else None

    def discard(self, tenant_id: str, session_id: str) -> None:
        with self._lock:
            if (tenant_id, session_id) not in self._keys:
                return
            with self._conn:
                self._conn.execute(
                    "DELETE FROM spool WHERE tenant_id = ? AND session_id = ?",
                    (tenant_id, session_id),
                )
            self._keys.discard((tenant_id, session_id))

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error as exc:
                logger.debug("[SessionSpool] close: %s", exc)


# ── Merge ────────────────────────────────────────────────────────────


def _identity(msg: Any) -> tuple:
    if not isinstance(msg, dict):
        return ("raw", json.dumps(msg, sort_keys=True, default=str))
    role = str(msg.get("role") or "")
    if msg.get("turn_id"):
        return ("turn", role, str(msg.get("turn_id")))
    content = msg.get("content")
    if not isinstance(content, str):
        content = json.dumps(content, sort_keys=True, default=str)
    return ("row", role, str(msg.get("ts") or msg.get("timestamp") or ""), content[:200])


def _rev(msg: Any) -> int:
    try:
        return int(msg.get("rev") or 0) if isinstance(msg, dict) else 0
    except (TypeError, ValueError):
        return 0


def _ts(msg: Any) -> str:
    return str(msg.get("ts") or msg.get("timestamp") or "") if isinstance(msg, dict) else ""


def _union(spooled: list, primary: list) -> list:
    """Spool order, plus primary-only rows placed by their predecessor and time.

    A primary-only row goes after its primary predecessor, then past any
    spool-only rows that are older than it -- so a reply spooled at 10:01
    stays above a question another writer stored at 10:30.
    """
    out = list(spooled)
    primary_keys = {_identity(m) for m in primary}
    anchor = -1
    for msg in primary:
        key = _identity(msg)
        index = {_identity(m): j for j, m in enumerate(out)}
        if key in index:
            i = index[key]
            if _rev(msg) > _rev(out[i]):
                out[i] = msg
            anchor = i
            continue
        pos = anchor + 1
        t = _ts(msg)
        while (
            t
            and pos < len(out)
            and _identity(out[pos]) not in primary_keys
            and _ts(out[pos])
            and _ts(out[pos]) <= t
        ):
            pos += 1
        out.insert(pos, msg)
        anchor = pos
    return out


def merge_spooled(primary: dict[str, Any] | None, spooled: dict[str, Any]) -> dict[str, Any]:
    """The session to serve when a spooled snapshot exists.

    The spool holds this process's last intent for the session, so it wins
    outright unless the primary row was written AFTER it (another writer
    got through in the meantime). Then the messages are unioned, so neither
    side's rows are dropped.
    """
    if not primary:
        return dict(spooled)
    p_at = str(primary.get("updated_at") or "")
    s_at = str(spooled.get("updated_at") or "")
    if not p_at or p_at <= s_at:
        return dict(spooled)
    merged = dict(primary)
    merged["messages"] = _union(
        list(spooled.get("messages") or []), list(primary.get("messages") or [])
    )
    merged["total_tokens"] = max(
        int(primary.get("total_tokens") or 0), int(spooled.get("total_tokens") or 0)
    )
    merged["total_cost"] = max(
        float(primary.get("total_cost") or 0.0), float(spooled.get("total_cost") or 0.0)
    )
    return merged
