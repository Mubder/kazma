"""Approval for a process that has no bus.

``kazma mcp`` is spawned by an MCP client, not by the Kazma server. The
approval bus that carries "may I run shell_exec?" to a human lives in the
server's process, and a child process cannot reach an in-memory bus. So
``SafetyMiddleware.check()`` saw ``NullBusAdapter``, failed closed, and denied
every danger tool — correctly, but uselessly. The MCP server then withheld all
55 of them rather than publish tools that could only ever be refused
(``docs/MCP_SERVER.md``).

This is the missing half: a second approval path that does not need the bus.

**The gate registry is already a cross-process store.** ``hitl_gates`` is a
SQLite table in the data dir, the dashboard renders every pending row it finds
there, and ``claim_gate`` records the human's decision. A process with no bus
can therefore register a gate and wait for the row to change state — the human
sees the same card, in the same place, and clicks the same button.

Two things make this safe rather than merely convenient:

**It fails closed in every direction.** No registry, no live watcher, an
unreadable database, a timeout, an error mid-poll — all return ``False``. The
only path to ``True`` is a row this process wrote reaching ``decision ==
"approve"``. A denial is the default and never needs anything to go right.

**It requires proof that someone is actually watching.** Registering a gate
into a database nobody reads is worse than refusing up front: the caller waits
out the full timeout and is denied anyway, having learned nothing. So a live
Kazma instance heartbeats into the same database (:func:`record_watcher`,
called from the approval-timeout watchdog), and this path refuses to engage
without a fresh beat. The heartbeat is written by the loop that *reads pending
gates*, so it is evidence of the exact behaviour depended on, not a separate
liveness claim that can drift away from it.

The gate is still ``LocalToolRegistry.execute()`` → ``SafetyMiddleware``. This
module adds a transport for a decision, never a decision of its own, and the
MCP server contains none of it (architecture note H-8).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass

logger = logging.getLogger(__name__)

__all__ = [
    "WATCHER_STALE_SECONDS",
    "Watcher",
    "bridge_enabled",
    "live_watcher",
    "record_watcher",
    "request_approval_via_registry",
]

#: How old a heartbeat may be and still count as "someone is watching".
#:
#: The watchdog ticks every 30s, so this is four missed beats. Tighter would
#: flap on a loaded box and silently stop publishing danger tools; looser would
#: keep promising a human who went home. Overridable for tests and for an
#: operator whose scan interval differs.
WATCHER_STALE_SECONDS = float(os.getenv("KAZMA_WATCHER_STALE_SECONDS") or 120.0)

#: How often to look for the human's answer. Approvals are a human-latency
#: event; polling faster buys nothing and wakes the disk for no reason.
_POLL_INTERVAL_SECONDS = 1.0

_WATCHER_SCHEMA = """
CREATE TABLE IF NOT EXISTS hitl_watchers (
  watcher_id TEXT PRIMARY KEY,
  kind       TEXT NOT NULL DEFAULT 'ui',
  last_seen  REAL NOT NULL,
  detail     TEXT NOT NULL DEFAULT ''
);
"""

#: Stable per-process id, so restarts replace their own row instead of
#: accumulating one per boot.
_WATCHER_ID = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"

#: Which database the watcher table has been created in. Keyed by path, not a
#: bare bool: tests repoint the registry with ``set_db_path_for_tests`` and a
#: process-wide "done" flag would skip creating the table in the new file.
_schema_ready: set[str] = set()


@dataclass(frozen=True)
class Watcher:
    """A process that has recently read pending gates from this database."""

    watcher_id: str
    kind: str
    last_seen: float
    detail: str = ""

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.last_seen)


def bridge_enabled() -> bool:
    """``KAZMA_BUS_BRIDGE`` kill-switch (default ON).

    Off restores the previous behaviour exactly: no bus, no approval, danger
    tools withheld. An operator who wants that back should not have to
    downgrade to get it.
    """
    raw = (os.environ.get("KAZMA_BUS_BRIDGE") or "").strip().lower()
    return raw not in ("0", "false", "off", "no")


def _connect() -> sqlite3.Connection:
    """Open the gate database — the same file, resolved the same way.

    Deliberately routed through ``hitl_gates._connect`` rather than rebuilding
    the path here. Two processes disagreeing about which database is "the" gate
    database is the failure this whole module is trying to avoid, and a second
    copy of the path logic is how that disagreement starts.
    """
    from kazma_core.safety import hitl_gates

    return hitl_gates._connect()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    path = gate_db_path()
    if path in _schema_ready:
        return
    conn.executescript(_WATCHER_SCHEMA)
    conn.commit()
    _schema_ready.add(path)


def gate_db_path() -> str:
    """The gate database this process would use. For banners and diagnostics."""
    from kazma_core.safety import hitl_gates

    return hitl_gates._db_path()


def record_watcher(kind: str = "ui", detail: str = "") -> None:
    """Announce that this process is reading pending gates.

    Best-effort and never raises: a failed heartbeat must not take down the
    watchdog that writes it. The cost of a missed beat is that a bus-less
    process declines to offer danger tools, which is the safe direction.
    """
    if not bridge_enabled():
        return
    try:
        conn = _connect()
    except Exception:  # pragma: no cover - defensive
        logger.debug("[BusBridge] watcher heartbeat skipped (no db)", exc_info=True)
        return
    try:
        _ensure_schema(conn)
        conn.execute(
            "INSERT INTO hitl_watchers (watcher_id, kind, last_seen, detail) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(watcher_id) DO UPDATE SET "
            "last_seen=excluded.last_seen, kind=excluded.kind, detail=excluded.detail",
            (_WATCHER_ID, kind, time.time(), detail[:200]),
        )
        # Forget processes that are long gone, so the table cannot grow without
        # bound on a box that restarts often. Ten stale windows is well past
        # any doubt about whether they are coming back.
        conn.execute(
            "DELETE FROM hitl_watchers WHERE last_seen < ?",
            (time.time() - WATCHER_STALE_SECONDS * 10,),
        )
        conn.commit()
    except Exception:  # pragma: no cover - defensive
        logger.debug("[BusBridge] watcher heartbeat failed", exc_info=True)
    finally:
        conn.close()


def live_watcher(max_age_seconds: float | None = None) -> Watcher | None:
    """The freshest watcher, or ``None`` if nobody is watching this database.

    ``None`` is the answer for every failure too — an unreadable database, a
    missing table, an empty table. A bridge that cannot prove someone is
    watching must behave exactly like a bridge that is not there.
    """
    if not bridge_enabled():
        return None
    limit = WATCHER_STALE_SECONDS if max_age_seconds is None else max_age_seconds
    try:
        conn = _connect()
    except Exception:
        return None
    try:
        _ensure_schema(conn)
        cur = conn.execute(
            "SELECT watcher_id, kind, last_seen, detail FROM hitl_watchers "
            "WHERE last_seen >= ? ORDER BY last_seen DESC LIMIT 1",
            (time.time() - limit,),
        )
        row = cur.fetchone()
    except Exception:
        return None
    finally:
        conn.close()
    if row is None:
        return None
    return Watcher(
        watcher_id=str(row["watcher_id"]),
        kind=str(row["kind"]),
        last_seen=float(row["last_seen"]),
        detail=str(row["detail"] or ""),
    )


async def request_approval_via_registry(
    *,
    tool_name: str,
    tool_args: str | None = None,
    thread_id: str = "",
    timeout: float = 300.0,
    origin: str = "bus_bridge",
) -> bool:
    """Register a gate, wait for a human, return their decision.

    ``True`` only when a person approved. Everything else — no watcher, no
    registry, a write that failed, a timeout, an exception — is ``False``.
    """
    if not bridge_enabled():
        return False
    try:
        from kazma_core.safety.hitl_gates import (
            GateRow,
            gate_for,
            gate_registry_enabled,
            make_gate_id,
            register_gate,
        )
    except Exception:  # pragma: no cover - defensive
        logger.warning("[BusBridge] gate registry unavailable", exc_info=True)
        return False

    if not gate_registry_enabled():
        return False
    watcher = live_watcher()
    if watcher is None:
        logger.warning(
            "[BusBridge] no live watcher on %s — refusing %s rather than "
            "queueing an approval nobody will see",
            gate_db_path(),
            tool_name,
        )
        return False

    # A bridge gate has no chat session to inherit tenancy from, so stamp the
    # calling process's tenant explicitly. The dashboard admits these rows on
    # tenant match alone (they fail the usual session-ownership check, having
    # no session), and an unstamped row would be admitted by whoever looked
    # first.
    try:
        from kazma_core.tenant_context import get_current_tenant_id

        tenant = (get_current_tenant_id() or "default").strip() or "default"
    except Exception:
        tenant = "default"

    tid = thread_id or f"mcp:{_WATCHER_ID}"
    gate_id = make_gate_id(tid, tool_name, tool_args, seq=int(time.time() * 1000) % 1_000_000)
    try:
        await asyncio.to_thread(
            register_gate,
            GateRow(
                gate_id=gate_id,
                thread_id=tid,
                tool=tool_name,
                mechanism=origin,
                tenant_id=tenant,
                message=(tool_args or "")[:500],
            ),
            ttl_seconds=timeout,
        )
    except Exception:
        logger.warning("[BusBridge] could not register gate for %s", tool_name, exc_info=True)
        return False

    logger.info(
        "[BusBridge] %s is waiting for approval (gate=%s, watcher=%s, %.0fs)",
        tool_name,
        gate_id,
        watcher.kind,
        timeout,
    )

    deadline = time.monotonic() + max(1.0, timeout)
    while time.monotonic() < deadline:
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        try:
            row = await asyncio.to_thread(gate_for, gate_id)
        except Exception:
            logger.warning("[BusBridge] gate read failed; denying", exc_info=True)
            return False
        if row is None:
            # The row we just wrote is gone. Something else is administering
            # this database and we cannot tell approve from deny, so deny.
            logger.warning("[BusBridge] gate %s vanished; denying", gate_id)
            return False
        if row.state == "pending":
            continue
        decision = (row.decision or "").strip().lower()
        approved = decision in ("approve", "yolo")
        logger.info(
            "[BusBridge] gate %s -> %s (%s) by %s",
            gate_id,
            row.state,
            decision or "no decision",
            row.actor or "unknown",
        )
        return approved

    logger.warning("[BusBridge] approval for %s timed out after %.0fs", tool_name, timeout)
    return False
