"""Durable X notices carried by the existing ops alert transport.

Rows live beside the state change in its database. Delivery is at least once:
a crash after acknowledgement may repeat a notice, never its publication.
Only a confirmed transport route acknowledges a row. Disabled/unrouted notices
remain visible and back off; changing routing does not erase the obligation.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
import uuid
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS x_notification_outbox (
 id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL,
 event_key TEXT NOT NULL, message TEXT NOT NULL, created_at REAL NOT NULL,
 delivered_at REAL, attempts INTEGER NOT NULL DEFAULT 0,
 next_attempt REAL NOT NULL DEFAULT 0, lease_owner TEXT NOT NULL DEFAULT '',
 lease_until REAL NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '',
 UNIQUE(tenant_id, event_key)
);
"""


def enqueue(conn: sqlite3.Connection, *, tenant: str, key: str, message: str) -> None:
    """Call within the same transaction that records the source transition."""
    if not message or len(message) > 4000:
        raise ValueError("Notification exceeds the acknowledged transport boundary.")
    conn.execute("INSERT OR IGNORE INTO x_notification_outbox "
                 "(tenant_id, event_key, message, created_at) VALUES (?, ?, ?, ?)",
                 (tenant, key, message, time.time()))


def claim(conn: sqlite3.Connection, *, now: float, owner: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM x_notification_outbox WHERE delivered_at IS NULL "
                       "AND next_attempt <= ? AND lease_until <= ? ORDER BY id LIMIT 1", (now, now)).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE x_notification_outbox SET lease_owner = ?, lease_until = ?, "
                 "attempts = attempts + 1 WHERE id = ?", (owner, now + 60, row["id"]))
    return dict(row)


def finish(conn: sqlite3.Connection, row: dict[str, Any], *, owner: str, delivered: bool, superseded: bool = False) -> None:
    now = time.time()
    delay = 10**12 if superseded else min(3600, 30 * 2 ** min(int(row["attempts"]), 7))
    conn.execute("UPDATE x_notification_outbox SET delivered_at = ?, next_attempt = ?, "
                 "lease_until = 0, lease_owner = '', last_error = ? WHERE id = ? AND lease_owner = ?",
                 (now if delivered else None, now + delay, "Superseded revision; delivery suppressed." if superseded else ("" if delivered else "No acknowledged ops route; will retry."), row["id"], owner))


async def _drain_store(store: Any, *, limit: int = 20) -> int:
    """Offload database work and acknowledge actual delivery, not dispatch intent."""
    from kazma_core.observability.ops_alerts import deliver_acknowledged
    from kazma_core.tenant_context import tenant_scope

    delivered = 0
    for _ in range(limit):
        owner = uuid.uuid4().hex
        row = await asyncio.to_thread(store.claim_notification, owner=owner)
        if row is None:
            break
        current = await asyncio.to_thread(store.notification_current, row)
        if not current:
            await asyncio.to_thread(store.finish_notification, row, owner=owner, delivered=False, superseded=True)
            continue
        with tenant_scope(row["tenant_id"]):
            taken = await deliver_acknowledged(row["message"])
        await asyncio.to_thread(store.finish_notification, row, owner=owner, delivered=taken)
        delivered += int(taken)
    return delivered


async def drain_notifications() -> int:
    from kazma_core.x_api.publication_store import get_publication_store
    from kazma_core.x_api.reply_store import get_reply_store

    publications = await asyncio.to_thread(get_publication_store)
    replies = await asyncio.to_thread(get_reply_store)
    return await _drain_store(publications) + await _drain_store(replies)
