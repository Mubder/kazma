"""Chat-app replies go through an outbox (resilience plan F3).

On the live install 57 Slack timeouts, about 65 Discord drops and 15
Telegram failures each lost a reply: ``GatewayManager.send`` tried once, and
a reply the platform did not take was gone. Now every reply Kazma sends to a
chat (``OutboundMessage.durable``, the default) is written here first, in
``chat_outbox.db`` beside the other stores:

* ``begin`` records it as ``sending`` before the first try, which runs at
  once as before (no added wait for the person);
* the try's report (``send_report``) decides what happens next: taken by the
  platform -> ``sent``; nothing arrived -> ``pending``, tried again after
  30 s, doubling to 15 minutes, for up to two hours (``GatewayManager``'s
  outbox loop); part of it arrived -> ``partial``, and the answer was lost so
  it may have arrived -> ``unknown``: neither is ever sent again, since that
  would show the person the same words twice;
* a reply still not taken after two hours is ``undeliverable``: one alert
  (the bell and the ops channels), never a silent loss;
* the next reply to a chat whose earlier reply ended ``partial``,
  ``unknown`` or ``undeliverable`` opens with a line saying an earlier
  message may not have reached them in full (``take_notice``), in their
  language;
* a row left ``sending`` by a process that died is ``unknown`` at the next
  start (``recover``): it may have left before the process died, and the rule
  is never twice.

A settled row keeps no text (its payload is emptied) and is purged after
seven days by the maintenance sweep "chat outbox retention". The agent's own
sends and scheduled deliveries (``make_gateway_send_handler``) are not
durable here: their callers read the answer and keep their own outbox
(scheduled jobs, AGENTS.md section 16E), and two outboxes would deliver
twice. Every method blocks (SQLite): call it off the loop.
"""

from __future__ import annotations

import base64
import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["GIVE_UP_AFTER_S", "RETRY_WAITS_S", "ChatOutbox"]

#: Waits between tries of a reply nothing of which arrived; the last repeats.
RETRY_WAITS_S = (30.0, 60.0, 120.0, 240.0, 480.0, 900.0)
#: A reply not taken this long after it was first tried is undeliverable.
GIVE_UP_AFTER_S = 2 * 3600.0
#: A claimed row's lease (the loop's sends are bounded well under it).
LEASE_S = 300.0
#: Settled rows (no text left in them) are kept this long, then purged.
SETTLED_KEEP_S = 7 * 86400.0
#: A reply bigger than this (attachments included) is not kept: sent once.
MAX_PAYLOAD_CHARS = 12 * 1024 * 1024

_SETTLED = ("sent", "partial", "unknown", "undeliverable")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    id              TEXT PRIMARY KEY,
    target_id       TEXT NOT NULL,
    platform        TEXT NOT NULL,
    payload         TEXT,
    state           TEXT NOT NULL,
    owner           TEXT,
    attempts        INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL,
    next_attempt_at REAL NOT NULL,
    lease_until     REAL,
    settled_at      REAL,
    last_error      TEXT
);
CREATE INDEX IF NOT EXISTS outbox_due ON outbox (state, next_attempt_at);
CREATE TABLE IF NOT EXISTS owed_notices (
    target_id TEXT PRIMARY KEY,
    since     REAL NOT NULL
);
"""


def _retry_wait(attempts: int) -> float:
    """The wait after the *attempts*-th try of a reply nothing of which arrived."""
    return RETRY_WAITS_S[max(0, min(attempts - 1, len(RETRY_WAITS_S) - 1))]


def _encode_outbound(outbound: Any) -> str | None:
    """The reply as JSON, or None when it cannot be kept (too big, or
    metadata JSON cannot hold): such a reply is sent once, as before."""
    attachments = []
    for att in getattr(outbound, "attachments", None) or []:
        data = getattr(att, "data", None)
        attachments.append({
            "kind": att.kind, "mime": att.mime, "filename": att.filename,
            "data": base64.b64encode(data).decode("ascii") if data else None,
            "url": getattr(att, "url", None), "meta": getattr(att, "meta", None) or {},
        })
    try:
        text = json.dumps({
            "target_id": outbound.target_id, "text": outbound.text,
            "context_metadata": outbound.context_metadata, "attachments": attachments,
        }, ensure_ascii=False)
    except (TypeError, ValueError):
        return None
    return text if len(text) <= MAX_PAYLOAD_CHARS else None


def _decode_outbound(payload: str) -> Any:
    from kazma_gateway.gateway import Attachment, OutboundMessage

    data = json.loads(payload)
    atts = [
        Attachment(kind=a["kind"], mime=a["mime"], filename=a.get("filename") or "",
                   data=base64.b64decode(a["data"]) if a.get("data") else None,
                   url=a.get("url"), meta=a.get("meta") or {})
        for a in data.get("attachments") or []
    ]
    return OutboundMessage(target_id=data["target_id"], text=data.get("text") or "",
                           attachments=atts, context_metadata=data.get("context_metadata") or {})


class ChatOutbox:
    """The durable outbox of chat-app replies (see the module docstring)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        # This process's mark on the rows it is sending: a row marked by
        # another is a dead process's (one Kazma per data folder).
        self.owner = uuid.uuid4().hex
        self._schema_ready = False

    # -- the connection -----------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=FULL")
        if not self._schema_ready:
            conn.executescript(_SCHEMA)
            self._schema_ready = True
        return conn

    def _tx(self, work):
        """Run ``work(conn)`` in one BEGIN IMMEDIATE, rolled back if it raises."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            committed = False
            try:
                result = work(conn)
                conn.execute("COMMIT")
                committed = True
            finally:
                if not committed:
                    conn.execute("ROLLBACK")
            return result
        finally:
            conn.close()

    # -- one reply ----------------------------------------------------------------

    def begin(self, outbound: Any, now: float | None = None) -> str | None:
        """Record *outbound* as being sent now. Its row id, or None when it
        cannot be kept (then it is sent once, as before)."""
        payload = _encode_outbound(outbound)
        if payload is None:
            logger.info("[chat_outbox] a reply to %s is too big to keep; sent once",
                        outbound.target_id.split(":", 1)[0])
            return None
        now = time.time() if now is None else now
        row_id = uuid.uuid4().hex
        platform = outbound.target_id.split(":", 1)[0]
        self._tx(lambda c: c.execute(
            "INSERT INTO outbox (id, target_id, platform, payload, state, owner, attempts, "
            "created_at, next_attempt_at, lease_until) VALUES (?, ?, ?, ?, 'sending', ?, 1, ?, ?, ?)",
            (row_id, outbound.target_id, platform, payload, self.owner, now, now, now + LEASE_S)))
        return row_id

    def finish(self, row_id: str, outcome: str, now: float | None = None, error: str = "") -> str:
        """Record one try's *outcome* (``sent``, ``not_sent``, ``partial``,
        ``unknown``). Returns the row's state now."""
        now = time.time() if now is None else now

        def work(c: sqlite3.Connection) -> str:
            row = c.execute("SELECT target_id, attempts, created_at, state FROM outbox WHERE id = ?",
                            (row_id,)).fetchone()
            if row is None or row["state"] != "sending":
                return row["state"] if row is not None else "missing"
            if outcome == "sent":
                state = "sent"
            elif outcome in ("partial", "unknown"):
                state = outcome
            elif now - row["created_at"] >= GIVE_UP_AFTER_S:
                state = "undeliverable"
            else:
                c.execute(
                    "UPDATE outbox SET state = 'pending', owner = NULL, lease_until = NULL, "
                    "next_attempt_at = ?, last_error = ? WHERE id = ?",
                    (now + _retry_wait(row["attempts"]), error[:300] or None, row_id))
                return "pending"
            c.execute(
                "UPDATE outbox SET state = ?, payload = NULL, owner = NULL, lease_until = NULL, "
                "settled_at = ?, last_error = ? WHERE id = ?",
                (state, now, error[:300] or None, row_id))
            if state != "sent":
                c.execute("INSERT OR IGNORE INTO owed_notices (target_id, since) VALUES (?, ?)",
                          (row["target_id"], now))
            return state

        return self._tx(work)

    def claim_due(self, now: float | None = None, limit: int = 20) -> tuple[list[tuple[str, Any, float]],
                                                                           list[dict[str, Any]]]:
        """Claim the replies due another try. Returns ``(to_send, gave_up)``:
        ``(row id, reply, first tried at)`` for each to send, and the rows
        that ran out of time while waiting (now ``undeliverable``)."""
        now = time.time() if now is None else now

        def work(c: sqlite3.Connection):
            rows = c.execute(
                "SELECT id, target_id, platform, payload, attempts, created_at, last_error FROM outbox "
                "WHERE state = 'pending' AND next_attempt_at <= ? ORDER BY created_at LIMIT ?",
                (now, limit)).fetchall()
            to_send, gave_up = [], []
            for row in rows:
                if now - row["created_at"] >= GIVE_UP_AFTER_S:
                    c.execute("UPDATE outbox SET state = 'undeliverable', payload = NULL, settled_at = ? "
                              "WHERE id = ?", (now, row["id"]))
                    c.execute("INSERT OR IGNORE INTO owed_notices (target_id, since) VALUES (?, ?)",
                              (row["target_id"], now))
                    gave_up.append({"platform": row["platform"], "attempts": row["attempts"],
                                    "error": row["last_error"] or ""})
                    continue
                c.execute("UPDATE outbox SET state = 'sending', owner = ?, attempts = attempts + 1, "
                          "lease_until = ? WHERE id = ?", (self.owner, now + LEASE_S, row["id"]))
                to_send.append((row["id"], row["payload"], row["created_at"]))
            return to_send, gave_up

        claimed, gave_up = self._tx(work)
        replies = []
        for row_id, payload, first in claimed:
            try:
                replies.append((row_id, _decode_outbound(payload), first))
            except (ValueError, KeyError, TypeError):
                logger.warning("[chat_outbox] a kept reply could not be read; dropped")
                self.finish(row_id, "unknown", now, "unreadable payload")
        return replies, gave_up

    def recover(self, now: float | None = None) -> int:
        """At start: a row another (dead) process was sending may have left
        before it died: ``unknown``, never sent again. Returns how many."""
        now = time.time() if now is None else now

        def work(c: sqlite3.Connection) -> int:
            rows = c.execute("SELECT id, target_id FROM outbox WHERE state = 'sending' AND owner != ?",
                             (self.owner,)).fetchall()
            for row in rows:
                c.execute("UPDATE outbox SET state = 'unknown', payload = NULL, owner = NULL, "
                          "lease_until = NULL, settled_at = ?, last_error = 'the sending process ended' "
                          "WHERE id = ?", (now, row["id"]))
                c.execute("INSERT OR IGNORE INTO owed_notices (target_id, since) VALUES (?, ?)",
                          (row["target_id"], now))
            return len(rows)

        recovered = self._tx(work)
        if recovered:
            logger.warning("[chat_outbox] %d reply(ies) were being sent when Kazma stopped; "
                           "they may have arrived and are not sent again", recovered)
        return recovered

    def take_notice(self, target_id: str) -> bool:
        """Whether a reply to *target_id* owes the "an earlier message may not
        have arrived" line; owed once, so taking it clears it."""
        return bool(self._tx(lambda c: c.execute(
            "DELETE FROM owed_notices WHERE target_id = ?", (target_id,)).rowcount))

    def owe_notice(self, target_id: str, now: float | None = None) -> None:
        """Put a taken notice back (the reply that took it could not be kept)."""
        now = time.time() if now is None else now
        self._tx(lambda c: c.execute(
            "INSERT OR IGNORE INTO owed_notices (target_id, since) VALUES (?, ?)", (target_id, now)))

    # -- reading and retention --------------------------------------------------------

    def counts(self) -> dict[str, int]:
        """Rows per state (settled ones of the last seven days)."""
        conn = self._connect()
        try:
            rows = conn.execute("SELECT state, COUNT(*) AS n FROM outbox GROUP BY state").fetchall()
        finally:
            conn.close()
        return {row["state"]: int(row["n"]) for row in rows}

    def prune(self, now: float | None = None) -> int:
        """Purge settled rows older than SETTLED_KEEP_S. Returns how many."""
        now = time.time() if now is None else now
        marks = ",".join("?" for _ in _SETTLED)
        return int(self._tx(lambda c: c.execute(
            f"DELETE FROM outbox WHERE state IN ({marks}) AND settled_at < ?",  # noqa: S608 -- fixed marks
            (*_SETTLED, now - SETTLED_KEEP_S)).rowcount))
