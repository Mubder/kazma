"""Memories stranded in a table nothing reads -- put back where recall looks.

On 2026-08-02 and 08-03 a one-off operation, never committed to this
repository, moved 329 episodes out of ``episodes`` into a table it created,
``episodes_archive`` (July's memories carried over from the first memory
system, 39 notes the user asked Kazma to keep, 10 chat turns), and 326 rows
out of ``entities`` into ``entities_archive``. No code reads either table, so
recall could not reach those memories: stored, and never found. Found on the
live install on 2026-09-26 during the test-data cleanup.

:func:`restore_legacy_episode_archive` puts each one back as a cold memory --
tier ``archived``, which recall searches (at 0.98 weight) and revives when a
question needs it -- with its own id, text, vector, tenant and time. Skipped,
never overwritten: an id ``episodes`` already holds, and a text the tenant
already holds under another id (the same memory, filed twice). The legacy
row stays where it is; running again finds everything present.

``entities_archive`` is left alone on purpose: its 11 "concepts" are junk
nodes (bare numbers, file names) and its 315 "memory_chunk" rows are
200-character truncations of memories held in full elsewhere (256 of them)
or of smoke-test notes (59) -- archived for a reason, and a truncation is not
a memory worth restoring over the real one.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from typing import Any

__all__ = ["LEGACY_EPISODE_TABLE", "legacy_archive_counts", "restore_legacy_episode_archive"]

logger = logging.getLogger(__name__)

LEGACY_EPISODE_TABLE = "episodes_archive"
# Columns the restore sets itself rather than copying.
_SET_HERE = frozenset({"tier", "metadata_json", "created_at", "last_accessed", "expires_at"})
_JULIAN_UNIX_EPOCH = 2440587.5  # julianday('1970-01-01')


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(r[1]) for r in conn.execute(f"PRAGMA table_info('{table}')")]


def _legacy_columns(conn: sqlite3.Connection) -> list[str]:
    """The legacy table's columns, or [] when this database never had one."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (LEGACY_EPISODE_TABLE,)
    ).fetchone()
    return _columns(conn, LEGACY_EPISODE_TABLE) if row else []


def _epoch(value: Any) -> float | None:
    """A timestamp as Unix seconds. The operation wrote some as julian days."""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if x <= 0:
        return None
    return (x - _JULIAN_UNIX_EPOCH) * 86400.0 if x < 1e7 else x


def _text_key(user_text: Any, summary_text: Any, assistant_text: Any) -> str:
    text = str(user_text or summary_text or assistant_text or "")
    return re.sub(r"\s+", " ", text).strip().casefold()


def _texts_by_tenant(conn: sqlite3.Connection) -> dict[str, set[str]]:
    held: dict[str, set[str]] = {}
    for tenant, user, summary, answer in conn.execute(
        "SELECT tenant_id, user_text, summary_text, assistant_text FROM episodes"
    ):
        key = _text_key(user, summary, answer)
        if key:
            held.setdefault(str(tenant or "default"), set()).add(key)
    return held


def _row_value(row: Any, cols: list[str], name: str) -> Any:
    return row[cols.index(name)] if name in cols else None


def legacy_archive_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """``total`` stranded rows, how many are back (``restored``), how many
    the tenant already held under another id (``duplicate``), and
    ``pending``. All zero on a database without the legacy table."""
    cols = _legacy_columns(conn)
    out = {"total": 0, "restored": 0, "duplicate": 0, "pending": 0}
    if not cols:
        return out
    present = {
        r[0]
        for r in conn.execute(
            f"SELECT a.id FROM {LEGACY_EPISODE_TABLE} a JOIN episodes e ON e.id = a.id"
        )
    }
    held = _texts_by_tenant(conn)
    for row in conn.execute(f"SELECT * FROM {LEGACY_EPISODE_TABLE}"):
        out["total"] += 1
        if row[cols.index("id")] in present:
            out["restored"] += 1
            continue
        key = _text_key(
            _row_value(row, cols, "user_text"),
            _row_value(row, cols, "summary_text"),
            _row_value(row, cols, "assistant_text"),
        )
        tenant = str(_row_value(row, cols, "tenant_id") or "default")
        if key and key in held.get(tenant, set()):
            out["duplicate"] += 1
        else:
            out["pending"] += 1
    return out


def restore_legacy_episode_archive(conn: sqlite3.Connection) -> dict[str, int]:
    """Put every stranded episode back as a cold memory; report the counts.

    Idempotent, and it never changes a row ``episodes`` already holds.
    """
    cols = _legacy_columns(conn)
    report = {"restored": 0, "present": 0, "duplicate": 0, "empty": 0}
    if not cols:
        return report
    live_cols = set(_columns(conn, "episodes"))
    copied = [c for c in cols if c in live_cols and c not in _SET_HERE]
    if "id" not in copied:
        return report
    present = {r[0] for r in conn.execute("SELECT id FROM episodes")}
    held = _texts_by_tenant(conn)
    rows = conn.execute(f"SELECT * FROM {LEGACY_EPISODE_TABLE}").fetchall()
    insert_cols = [*copied, "tier", "metadata_json", "created_at", "last_accessed", "expires_at"]
    sql = (
        f"INSERT OR IGNORE INTO episodes ({', '.join(insert_cols)}) "
        f"VALUES ({', '.join('?' * len(insert_cols))})"
    )
    restored_ids: list[str] = []
    for row in rows:
        eid = row[cols.index("id")]
        if eid in present:
            report["present"] += 1
            continue
        key = _text_key(
            _row_value(row, cols, "user_text"),
            _row_value(row, cols, "summary_text"),
            _row_value(row, cols, "assistant_text"),
        )
        if not key:
            report["empty"] += 1
            continue
        tenant = str(_row_value(row, cols, "tenant_id") or "default")
        if key in held.get(tenant, set()):
            report["duplicate"] += 1
            continue
        try:
            meta = json.loads(_row_value(row, cols, "metadata_json") or "{}")
        except (TypeError, ValueError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        meta["restored_from"] = LEGACY_EPISODE_TABLE
        archived_at = _epoch(_row_value(row, cols, "archived_at"))
        if archived_at is not None:
            meta["legacy_archived_at"] = archived_at
        created = _epoch(_row_value(row, cols, "created_at")) or archived_at or time.time()
        values = [row[cols.index(c)] for c in copied] + [
            "archived",
            json.dumps(meta, ensure_ascii=False),
            created,
            _epoch(_row_value(row, cols, "last_accessed")),
            None,
        ]
        if conn.execute(sql, values).rowcount:
            report["restored"] += 1
            restored_ids.append(str(eid))
            held.setdefault(tenant, set()).add(key)
    conn.commit()
    if restored_ids:
        from kazma_core.memory.state_backend import remirror_episode_by_id

        for eid in restored_ids:
            remirror_episode_by_id(conn, eid)
        logger.info(
            "[memory] put %d memories back from the legacy %s table where recall "
            "looks (%d were already held under another id)",
            report["restored"],
            LEGACY_EPISODE_TABLE,
            report["duplicate"],
        )
    return report
