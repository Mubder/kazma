"""Checkpoint retention -- bound the LangGraph checkpoint store, on both backends.

Audit M-G1: LangGraph persists a checkpoint on every superstep, each a full
SupervisorState (messages and up to 200 tool results). Nothing deleted old
ones, so the store grew until the operator deleted chats by hand.

One policy for both backends:

* Every chat keeps its newest :data:`KEEP_PER_THREAD` (200) checkpoints --
  /undo, /replay, resume and the step history only need recent ones.
* A chat idle for the retention days (default 30) keeps its newest
  :data:`INACTIVE_KEEP` (10): still resumable, its old history gone.
* A chat written in the last :data:`ACTIVE_GRACE_S` (ten minutes) is left for
  the next pass, because a run may be writing it.
* ``checkpoints.retention_days`` (Settings -> System) sets the days, and 0
  keeps every checkpoint; ``KAZMA_CHECKPOINT_RETENTION_DAYS`` overrides the
  setting when set. The variable used to be only an on/off switch (its value
  was never used as days).

A checkpoint's time comes from its id: LangGraph ids are uuid6, which carry
their creation time. The inactive rule used to read ``metadata.ts``, which
LangGraph never writes, so on SQLite it never applied.

**Postgres (2026-09-27).** It was skipped as "ops-owned" and nothing pruned
it: live that day the three checkpoint tables held 2.9 GB of a 3.0 GB
database, one chat 3,969 checkpoints. A checkpoint's channel values live in
``checkpoint_blobs``, one row per channel version, shared by every checkpoint
that did not change that channel. A blob is deleted only when no kept
checkpoint names its version AND a kept checkpoint names a newer version of
the same channel: the saver writes a new checkpoint's blobs before the
checkpoint itself (autocommit), and such a blob is newer than every named
one, so it stays.

**SQLite.** langgraph-checkpoint-sqlite keeps pending writes in ``writes``.
The prune used to name ``checkpoint_writes`` -- the Postgres table -- so the
writes of every pruned checkpoint stayed behind, unreadable; an idle chat's
are now removed with it.

Runs on the 15-minute maintenance cadence (``memory.worker_bootstrap``,
"checkpoint retention"); it used to be a loop of its own that logged its
failures at DEBUG.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_RETENTION_DAYS",
    "MAX_RETENTION_DAYS",
    "RETENTION_KEY",
    "checkpoint_time",
    "parse_retention_days",
    "retention_setting",
    "run_checkpoint_retention",
]

RETENTION_KEY = "checkpoints.retention_days"
RETENTION_ENV = "KAZMA_CHECKPOINT_RETENTION_DAYS"
DEFAULT_RETENTION_DAYS = 30
MAX_RETENTION_DAYS = 3650
KEEP_PER_THREAD = 200
INACTIVE_KEEP = 10
ACTIVE_GRACE_S = 600.0

#: Gregorian epoch (1582-10-15) to Unix epoch, in 100 ns units: uuid time.
_UUID_EPOCH_OFFSET = 0x01B21DD213814000

_warned: set[str] = set()


def parse_retention_days(value: Any) -> int | None:
    """*value* as whole days from 0 to 3650, or ``None`` when it is not one."""
    if isinstance(value, bool):
        return None
    try:
        days = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return days if 0 <= days <= MAX_RETENTION_DAYS else None


def _warn_once(key: str, message: str, *args: Any) -> None:
    if key not in _warned:
        _warned.add(key)
        logger.warning(message, *args)


def retention_setting(config_store: Any = None) -> dict[str, Any]:
    """The retention days in force and where they come from.

    ``source`` is ``env`` (``KAZMA_CHECKPOINT_RETENTION_DAYS``), ``setting``
    (``checkpoints.retention_days``) or ``default``. A value that is not a
    whole number of days is ignored, with one warning, rather than read as
    "keep nothing" or "keep everything".
    """
    raw_env = (os.environ.get(RETENTION_ENV) or "").strip()
    if raw_env:
        days = parse_retention_days(raw_env)
        if days is not None:
            return {"days": days, "source": "env"}
        _warn_once(
            f"env:{raw_env}",
            "[checkpoint-retention] %s=%r is not a whole number of days from 0 to %d; ignored",
            RETENTION_ENV, raw_env, MAX_RETENTION_DAYS,
        )
    if config_store is None:
        from kazma_core.config_store import get_config_store

        config_store = get_config_store()
    raw = config_store.get(RETENTION_KEY)
    if raw is None or str(raw).strip() == "":
        return {"days": DEFAULT_RETENTION_DAYS, "source": "default"}
    days = parse_retention_days(raw)
    if days is None:
        _warn_once(
            f"setting:{raw}",
            "[checkpoint-retention] %s=%r is not a whole number of days from 0 to %d; "
            "using %d",
            RETENTION_KEY, raw, MAX_RETENTION_DAYS, DEFAULT_RETENTION_DAYS,
        )
        return {"days": DEFAULT_RETENTION_DAYS, "source": "default"}
    return {"days": days, "source": "setting"}


def checkpoint_time(checkpoint_id: str | None) -> float | None:
    """Unix time a LangGraph checkpoint was created, read from its uuid6 id.

    ``None`` for an id that is not a version-6 uuid: the chat's age is then
    unknown, and it is left alone.
    """
    try:
        u = uuid.UUID(str(checkpoint_id))
    except (TypeError, ValueError):
        return None
    if u.version != 6:
        return None
    ticks = ((u.int >> 96) << 28) | (((u.int >> 80) & 0xFFFF) << 12) | ((u.int >> 64) & 0x0FFF)
    return (ticks - _UUID_EPOCH_OFFSET) / 1e7


def _keep_for(newest_id: str | None, *, now: float, days: int) -> int | None:
    """How many checkpoints a chat keeps, or ``None`` to leave it this pass."""
    born = checkpoint_time(newest_id)
    if born is None or now - born < ACTIVE_GRACE_S:
        return None
    if days > 0 and now - born > days * 86400:
        return INACTIVE_KEEP
    return KEEP_PER_THREAD


# ── SQLite ─────────────────────────────────────────────────────────────────


def _sqlite_checkpoint_files() -> list[Path]:
    """Every SQLite checkpoint store of this install: the server's and the
    gateway's per-tenant ones (``file_checkpoints.db`` is the IDE's undo
    store, another schema)."""
    from kazma_core.paths import data_dir

    root = Path(data_dir())
    files = [root / "checkpoints.db", *sorted(root.glob("checkpoints_*.db"))]
    return [p for p in files if p.is_file()]


def _prune_sqlite_checkpoints(
    db_path: str | Path,
    *,
    days: int,
    now: float | None = None,
    thread_ids: Iterable[str] | None = None,
) -> dict[str, int]:
    """Apply the policy to one SQLite checkpoint store -- to *thread_ids*
    only when given. Raises on a store it cannot read; returns
    ``{"rows": n, "threads": t}``."""
    stamp = time.time() if now is None else now
    only = None if thread_ids is None else {str(t) for t in thread_ids}
    conn = sqlite3.connect(str(db_path), timeout=5)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "checkpoints" not in tables:
            return {"rows": 0, "threads": 0}
        has_writes = "writes" in tables
        groups = conn.execute(
            "SELECT thread_id, checkpoint_ns, COUNT(*), MAX(checkpoint_id) "
            "FROM checkpoints GROUP BY thread_id, checkpoint_ns"
        ).fetchall()
        rows = threads = 0
        for thread_id, ns, count, newest in groups:
            if only is not None and thread_id not in only:
                continue
            keep = _keep_for(newest, now=stamp, days=days)
            if keep is None:
                continue
            removed = 0
            if count > keep:
                doomed = [
                    r[0] for r in conn.execute(
                        "SELECT checkpoint_id FROM checkpoints WHERE thread_id = ? AND checkpoint_ns = ? "
                        "ORDER BY checkpoint_id DESC LIMIT -1 OFFSET ?",
                        (thread_id, ns, keep),
                    )
                ]
                for start in range(0, len(doomed), 500):
                    part = doomed[start:start + 500]
                    marks = ",".join("?" * len(part))
                    removed += max(conn.execute(
                        f"DELETE FROM checkpoints WHERE thread_id = ? AND checkpoint_ns = ? "
                        f"AND checkpoint_id IN ({marks})",
                        (thread_id, ns, *part),
                    ).rowcount or 0, 0)
            if has_writes:
                # The writes of every checkpoint no longer there -- this
                # pass's and those the old prune left behind.
                removed += max(conn.execute(
                    "DELETE FROM writes WHERE thread_id = ? AND checkpoint_ns = ? "
                    "AND checkpoint_id NOT IN (SELECT checkpoint_id FROM checkpoints "
                    "WHERE thread_id = ? AND checkpoint_ns = ?)",
                    (thread_id, ns, thread_id, ns),
                ).rowcount or 0, 0)
            conn.commit()
            if removed:
                rows += removed
                threads += 1
        return {"rows": rows, "threads": threads}
    finally:
        conn.close()


# ── Postgres ───────────────────────────────────────────────────────────────

_PG_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")

#: Blobs of one chat no kept checkpoint names, below the newest named version
#: of their channel. The version is "<32-digit counter>.<random>"; the counter
#: decides, and a version without one is never deleted. A checkpoint whose
#: ``channel_versions`` is not an object names nothing (``jsonb_each_text``
#: would raise on it and stop the whole chat's pass).
_PG_DELETE_BLOBS = """
    WITH refs AS (
        SELECT e.key AS channel, e.value AS version
        FROM checkpoints c,
             jsonb_each_text(CASE WHEN jsonb_typeof(c.checkpoint -> 'channel_versions') = 'object'
                                  THEN c.checkpoint -> 'channel_versions'
                                  ELSE '{}'::jsonb END) e
        WHERE c.thread_id = %(t)s AND c.checkpoint_ns = %(ns)s
    ), newest AS (
        SELECT channel, max(substring(version from '^[0-9]+')::numeric) AS v
        FROM refs GROUP BY channel
    )
    DELETE FROM checkpoint_blobs b
    USING newest n
    WHERE b.thread_id = %(t)s AND b.checkpoint_ns = %(ns)s
      AND n.channel = b.channel
      AND substring(b.version from '^[0-9]+')::numeric < n.v
      AND NOT EXISTS (SELECT 1 FROM refs r WHERE r.channel = b.channel AND r.version = b.version)
"""


def _prune_postgres_checkpoints(
    *,
    days: int,
    now: float | None = None,
    pool: Any = None,
    thread_ids: Iterable[str] | None = None,
) -> dict[str, int]:
    """Apply the policy to the Postgres checkpoint tables -- to *thread_ids*
    only when given. One transaction per chat: a chat that fails is rolled
    back and named in one WARNING, and the others are still pruned. Raises
    when the database cannot be read at all."""
    stamp = time.time() if now is None else now
    only = None if thread_ids is None else {str(t) for t in thread_ids}
    if pool is None:
        from kazma_core.db.postgres_pool import get_postgres_pool

        pool = get_postgres_pool()
        if pool is None:
            raise RuntimeError("Postgres pool unavailable")
    from kazma_core.db.pg_helpers import store_errors

    rows = threads = 0
    failed: list[str] = []
    first_error = ""
    with pool.connection() as conn:
        present = {
            r["table_name"] if isinstance(r, dict) else r[0]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name = ANY(%s)",
                (list(_PG_TABLES),),
            ).fetchall()
        }
        if set(_PG_TABLES) - present:
            conn.rollback()
            return {"rows": 0, "threads": 0}
        groups = conn.execute(
            "SELECT thread_id, checkpoint_ns, count(*) AS n, max(checkpoint_id) AS newest "
            "FROM checkpoints GROUP BY thread_id, checkpoint_ns"
        ).fetchall()
        conn.commit()
        for g in groups:
            thread_id, ns, count, newest = (
                (g["thread_id"], g["checkpoint_ns"], g["n"], g["newest"]) if isinstance(g, dict) else g
            )
            if only is not None and thread_id not in only:
                continue
            keep = _keep_for(newest, now=stamp, days=days)
            if keep is None or count <= keep:
                continue
            try:
                doomed = [
                    r["checkpoint_id"] if isinstance(r, dict) else r[0]
                    for r in conn.execute(
                        "SELECT checkpoint_id FROM checkpoints WHERE thread_id = %s AND checkpoint_ns = %s "
                        "ORDER BY checkpoint_id DESC OFFSET %s",
                        (thread_id, ns, keep),
                    ).fetchall()
                ]
                removed = conn.execute(
                    "DELETE FROM checkpoint_writes WHERE thread_id = %s AND checkpoint_ns = %s "
                    "AND checkpoint_id = ANY(%s)", (thread_id, ns, doomed),
                ).rowcount or 0
                removed += conn.execute(
                    "DELETE FROM checkpoints WHERE thread_id = %s AND checkpoint_ns = %s "
                    "AND checkpoint_id = ANY(%s)", (thread_id, ns, doomed),
                ).rowcount or 0
                removed += conn.execute(_PG_DELETE_BLOBS, {"t": thread_id, "ns": ns}).rowcount or 0
                conn.commit()
            except store_errors() as exc:  # one chat's failure must not stop the rest
                conn.rollback()
                failed.append(str(thread_id))
                first_error = first_error or f"{type(exc).__name__}: {exc}"[:300]
                continue
            rows += max(removed, 0)
            threads += 1
    if failed:
        logger.warning(
            "[checkpoint-retention] %d chat(s) could not be pruned and were left as they "
            "were (first: %s): %s", len(failed), failed[0], first_error,
        )
    return {"rows": rows, "threads": threads, "failed": len(failed)}


def run_checkpoint_retention(*, now: float | None = None) -> dict[str, Any]:
    """One retention pass over every checkpoint store of this install.

    Returns what it did; raises when a store could not be read (the
    maintenance runner logs it at WARNING and the next pass tries again).
    """
    setting = retention_setting()
    days = int(setting["days"])
    if days == 0:
        return {"skipped": "keeps every checkpoint", **setting}
    out: dict[str, Any] = {"rows": 0, "threads": 0, **setting}
    for path in _sqlite_checkpoint_files():
        res = _prune_sqlite_checkpoints(path, days=days, now=now)
        out["rows"] += res["rows"]
        out["threads"] += res["threads"]
    from kazma_core.db.backend import is_postgres

    if is_postgres():
        res = _prune_postgres_checkpoints(days=days, now=now)
        out["rows"] += res["rows"]
        out["threads"] += res["threads"]
    if out["rows"]:
        logger.info(
            "[checkpoint-retention] removed %d rows of old step history across %d chat(s) "
            "(%d days, %s)", out["rows"], out["threads"], days, setting["source"],
        )
    return out
