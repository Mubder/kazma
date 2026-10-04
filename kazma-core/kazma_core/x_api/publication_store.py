"""Transactional send ownership, rolling quota reservations and projection outbox.

Only a conditional transition from scheduled/deferred to sending grants network
permission. Expired sends remain unknown. SQLite coordination requires the same
database on a supported local filesystem; separate hosts are not coordinated.
"""

from __future__ import annotations

import base64
import bisect
import hashlib
import json
import math
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any

from kazma_core.config_store import apply_sqlite_pragmas

_SCHEMA = """
CREATE TABLE IF NOT EXISTS x_operations (
 id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, account_id TEXT NOT NULL,
 credential_revision TEXT NOT NULL, idempotency_key TEXT NOT NULL,
 payload_hash TEXT NOT NULL, text TEXT NOT NULL, text_hash TEXT NOT NULL,
 reply_to_id TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT 'post',
 origin TEXT NOT NULL, origin_ref TEXT NOT NULL DEFAULT '',
 state TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
 due_at REAL NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
 sent_at REAL, published_at REAL, deleted_at REAL, tweet_id TEXT NOT NULL DEFAULT '',
 owner TEXT NOT NULL DEFAULT '', lease_until REAL, attempts INTEGER NOT NULL DEFAULT 0,
 outcome TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
 metadata TEXT NOT NULL DEFAULT '{}',
 UNIQUE(tenant_id, account_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_x_ops_due ON x_operations(state, due_at);
CREATE INDEX IF NOT EXISTS idx_x_ops_quota ON x_operations(tenant_id, account_id, due_at);
CREATE TABLE IF NOT EXISTS x_operation_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT NOT NULL,
 tenant_id TEXT NOT NULL, state TEXT NOT NULL, version INTEGER NOT NULL,
 at REAL NOT NULL, detail TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS x_projection_outbox (
 id INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT NOT NULL UNIQUE,
 tenant_id TEXT NOT NULL, created_at REAL NOT NULL, done_at REAL,
 attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT ''
 , operation_version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS x_decisions (
 id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, summon_id TEXT NOT NULL,
 revision INTEGER NOT NULL, draft_hash TEXT NOT NULL, state TEXT NOT NULL,
 record TEXT NOT NULL, created_at REAL NOT NULL,
 UNIQUE(tenant_id, summon_id, revision)
);
"""


class PublicationConflictError(ValueError):
    """The same intent key names another payload or revision."""


class PublicationPolicyError(ValueError):
    """A reservation would violate a cap or duplicate commitment."""


def payload_hash(text: str, reply_to_id: str, kind: str = "post") -> str:
    payload = json.dumps({"text": text, "reply_to_id": reply_to_id, "kind": kind}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class _PublicationStore:
    """Short, closed connections and BEGIN IMMEDIATE for compound mutations."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            from kazma_core.paths import data_dir

            db_path = data_dir() / "x_publications.db"
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.executescript(_SCHEMA)
            from kazma_core.x_api.notifications import SCHEMA
            conn.executescript(SCHEMA)
            from kazma_core.x_api.composer import SCHEMA as COMPOSER_SCHEMA

            conn.executescript(COMPOSER_SCHEMA)
            from kazma_core.x_api.thread_store import SCHEMA as THREAD_SCHEMA

            conn.executescript(THREAD_SCHEMA)
            from kazma_core.db.sqlite_columns import add_missing_columns

            add_missing_columns(conn, "x_projection_outbox", (("operation_version", "INTEGER NOT NULL DEFAULT 1"),))

    @contextmanager
    def _connection(self, *, transaction: bool = False) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            apply_sqlite_pragmas(conn)
            if transaction:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        finally:
            if conn.in_transaction:
                conn.rollback()
            conn.close()

    @staticmethod
    def _event(conn: sqlite3.Connection, row: Any, detail: dict[str, Any] | None = None) -> None:
        conn.execute("INSERT INTO x_operation_events (operation_id, tenant_id, state, version, at, detail) VALUES (?, ?, ?, ?, ?, ?)",
                     (row["id"], row["tenant_id"], row["state"], row["version"], time.time(), json.dumps(detail or {}, ensure_ascii=False)))
        if row["origin"] == "schedule":
            conn.execute("INSERT INTO x_projection_outbox (operation_id, tenant_id, created_at, operation_version) VALUES (?, ?, ?, ?) "
                         "ON CONFLICT(operation_id) DO UPDATE SET operation_version = excluded.operation_version, done_at = NULL",
                         (row["id"], row["tenant_id"], time.time(), row["version"]))

        if row["origin"] == "schedule" and row["state"] in ("published", "outcome_unknown", "failed_permanent", "awaiting_approval", "expired"):
            from kazma_core.x_api.notifications import enqueue

            outcome = "Published" if row["state"] == "published" else ("Publication outcome unknown; verify on X before any resend" if row["state"] == "outcome_unknown" else "Publication held; review X Studio")
            message = f"X schedule: {outcome}\nOperation: {row['id']}\n{row['reason']}"
            if row["tweet_id"]:
                message += f"\nhttps://x.com/i/web/status/{row['tweet_id']}"
            enqueue(conn, tenant=row["tenant_id"], key=f"operation:{row['id']}:{row['version']}", message=message)

    def notification_current(self, notice: dict[str, Any]) -> bool:
        parts = notice["event_key"].split(":")
        with self._connection() as conn:
            if parts[0] == "health" and len(parts) == 4 and parts[1] in ("mentions", "scheduler"):
                latest = conn.execute("SELECT MAX(id) FROM x_notification_outbox WHERE tenant_id = ? AND event_key LIKE ?",
                                      (notice["tenant_id"], f"health:{parts[1]}:%")).fetchone()[0]
                return latest == notice["id"]
            if len(parts) != 3 or parts[0] not in ("thread", "operation"):
                return False
            _, ident, revision = parts
            if parts[0] == "thread":
                row = conn.execute("SELECT revision, state FROM x_threads WHERE id = ? AND tenant_id = ?", (ident, notice["tenant_id"])).fetchone()
                return bool(row and row["revision"] == int(revision) and row["state"] in ("published", "partial"))
            row = conn.execute("SELECT version, state FROM x_operations WHERE id = ? AND tenant_id = ?", (ident, notice["tenant_id"])).fetchone()
            return bool(row and row["version"] == int(revision))

    def claim_notification(self, *, owner: str) -> dict[str, Any] | None:
        from kazma_core.x_api.notifications import claim

        with self._connection(transaction=True) as conn:
            return claim(conn, now=time.time(), owner=owner)

    def finish_notification(self, row: dict[str, Any], *, owner: str, delivered: bool, superseded: bool = False) -> None:
        from kazma_core.x_api.notifications import finish

        with self._connection(transaction=True) as conn:
            finish(conn, row, owner=owner, delivered=delivered, superseded=superseded)

    @staticmethod
    def _quota(conn: sqlite3.Connection, *, tenant_id: str, account_id: str, due_at: float,
               max_day: int, max_month: int, exclude: str = "") -> None:
        rows = conn.execute(
            "SELECT CASE WHEN state = 'published' THEN published_at "
            "WHEN state IN ('sending', 'outcome_unknown') THEN COALESCE(sent_at, due_at) ELSE due_at END AS at "
            "FROM x_operations WHERE tenant_id = ? AND account_id = ? AND kind = 'post' AND id != ? "
            "AND state IN ('awaiting_approval', 'scheduled', 'deferred', 'sending', 'outcome_unknown', 'published')",
            (tenant_id, account_id, exclude),
        ).fetchall()
        times = sorted([float(r["at"]) for r in rows if r["at"] is not None] + [due_at])
        for window, cap, label in ((86400, max_day, "Daily"), (30 * 86400, max_month, "Monthly")):
            for end in times:
                if due_at <= end < due_at + window:
                    count = bisect.bisect_right(times, end) - bisect.bisect_right(times, end - window)
                    if count > cap:
                        raise PublicationPolicyError(f"{label} cap reached ({cap}) in an affected rolling window, including reserved posts.")

    def reserve(self, *, tenant_id: str, account_id: str, credential_revision: str,
                idempotency_key: str, text: str, reply_to_id: str, due_at: float,
                max_day: int, max_month: int, duplicate_days: int,
                origin: str, origin_ref: str = "", kind: str = "post",
                metadata: dict[str, Any] | None = None, state: str = "scheduled",
                reply_limits: dict[str, int] | None = None,
                _conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        """Validate commitments and create an operation in one write transaction."""
        from kazma_core.x_api.ledger import text_hash

        if not all((tenant_id, account_id, credential_revision, idempotency_key)) or len(idempotency_key) > 300:
            raise ValueError("Publication requires tenant, account, credentials and a bounded intent key.")
        if kind not in ("post", "delete") or state not in ("scheduled", "awaiting_approval") or not math.isfinite(due_at):
            raise ValueError("Invalid publication kind, state or due time.")
        digest, content_hash = payload_hash(text, reply_to_id, kind), text_hash(text)
        now = time.time()
        if _conn is not None and not _conn.in_transaction:
            raise ValueError("Compound reservation requires an active write transaction.")
        with (nullcontext(_conn) if _conn is not None else self._connection(transaction=True)) as conn:
            existing = conn.execute("SELECT * FROM x_operations WHERE tenant_id = ? AND account_id = ? AND idempotency_key = ?",
                                    (tenant_id, account_id, idempotency_key)).fetchone()
            if existing:
                if existing["payload_hash"] != digest:
                    raise PublicationConflictError("The publication key is already bound to different text or target.")
                return dict(existing)
            if kind == "post":
                duplicate = conn.execute(
                    "SELECT id, state FROM x_operations WHERE tenant_id = ? AND account_id = ? AND text_hash = ? "
                    "AND kind = 'post' AND deleted_at IS NULL AND (state IN ('scheduled', 'deferred', 'sending', 'outcome_unknown', 'awaiting_approval') "
                    "OR (state = 'published' AND published_at >= ?)) LIMIT 1",
                    (tenant_id, account_id, content_hash, now - max(1, duplicate_days) * 86400),
                ).fetchone()
                if duplicate:
                    raise PublicationPolicyError(f"Duplicate commitment {duplicate['id']} ({duplicate['state']}); verify it before any new send.")
                self._quota(conn, tenant_id=tenant_id, account_id=account_id, due_at=due_at, max_day=max_day, max_month=max_month)
                self._reply_quota(conn, tenant_id=tenant_id, account_id=account_id, due_at=due_at,
                                  metadata=metadata or {}, limits=reply_limits or {})
            ident = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO x_operations (id, tenant_id, account_id, credential_revision, idempotency_key, payload_hash, "
                "text, text_hash, reply_to_id, kind, origin, origin_ref, state, due_at, created_at, updated_at, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ident, tenant_id, account_id, credential_revision, idempotency_key, digest, text, content_hash,
                 reply_to_id, kind, origin, origin_ref, state, due_at, now, now, json.dumps(metadata or {}, ensure_ascii=False)),
            )
            row = conn.execute("SELECT * FROM x_operations WHERE id = ?", (ident,)).fetchone()
            self._event(conn, row, {"action": "reserved"})
            return dict(row)

    @staticmethod
    def _reply_quota(conn: sqlite3.Connection, *, tenant_id: str, account_id: str, due_at: float,
                     metadata: dict[str, Any], limits: dict[str, int], exclude: str = "") -> None:
        if not metadata.get("summon_id"):
            return
        rows = conn.execute("SELECT CASE WHEN state = 'published' THEN published_at WHEN state IN ('sending', 'outcome_unknown') "
                            "THEN COALESCE(sent_at, due_at) ELSE due_at END AS at, metadata FROM x_operations "
                            "WHERE tenant_id = ? AND account_id = ? AND id != ? AND kind = 'post' AND "
                            "state IN ('awaiting_approval', 'scheduled', 'deferred', 'sending', 'outcome_unknown', 'published')",
                            (tenant_id, account_id, exclude)).fetchall()
        prior = [(float(r["at"]), json.loads(r["metadata"])) for r in rows if r["at"] is not None and json.loads(r["metadata"]).get("summon_id")]
        for field, label, applicable in (("daily", "Daily reply", prior),
                                         ("target", "Reply target", [(at, meta) for at, meta in prior if meta.get("reply_target") == metadata.get("reply_target")])):
            cap = limits.get(field, 0)
            if not cap or (field == "target" and metadata.get("operator_summon")):
                continue
            times = sorted([at for at, _ in applicable] + [due_at])
            for end in times:
                if due_at <= end < due_at + 86400 and bisect.bisect_right(times, end) - bisect.bisect_right(times, end - 86400) > cap:
                    raise PublicationPolicyError(f"{label} cap reached ({cap}), including reply reservations.")
        cooldown = limits.get("cooldown", 0)
        if cooldown and metadata.get("reply_conversation") and not metadata.get("operator_summon"):
            if any(meta.get("reply_conversation") == metadata["reply_conversation"] and abs(at - due_at) < cooldown for at, meta in prior):
                raise PublicationPolicyError("Reply conversation cooldown includes a reserved or possibly sent reply.")

    def get(self, ident: str, *, tenant_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM x_operations WHERE id = ? AND tenant_id = ?", (ident, tenant_id)).fetchone()
            return dict(row) if row else None

    def claim(self, ident: str, *, owner: str, tenant_id: str, account_id: str,
              credential_revision: str, lease_seconds: float = 120,
              max_day: int | None = None, max_month: int | None = None,
              reply_limits: dict[str, int] | None = None) -> dict[str, Any] | None:
        """Persist sending BEFORE network IO. Claimed work is never cancellable."""
        now = time.time()
        with self._connection(transaction=True) as conn:
            row = conn.execute("SELECT * FROM x_operations WHERE id = ? AND tenant_id = ?", (ident, tenant_id)).fetchone()
            if not row or row["state"] not in ("scheduled", "deferred") or row["due_at"] > now:
                return None
            if row["account_id"] != account_id or row["credential_revision"] != credential_revision:
                return None
            if row["kind"] == "post" and max_day is not None and max_month is not None:
                self._quota(conn, tenant_id=tenant_id, account_id=account_id, due_at=now,
                            max_day=max_day, max_month=max_month, exclude=ident)
                self._reply_quota(conn, tenant_id=tenant_id, account_id=account_id, due_at=now,
                                  metadata=json.loads(row["metadata"]), limits=reply_limits or {}, exclude=ident)
            conn.execute("UPDATE x_operations SET state = 'sending', owner = ?, lease_until = ?, sent_at = ?, attempts = attempts + 1, version = version + 1, updated_at = ? WHERE id = ?",
                         (owner, now + max(30, min(600, lease_seconds)), now, now, ident))
            claimed = conn.execute("SELECT * FROM x_operations WHERE id = ?", (ident,)).fetchone()
            self._event(conn, claimed, {"action": "send_claim"})
            return dict(claimed)

    def validate_claim_caps(self, ident: str, *, tenant_id: str, owner: str, max_day: int, max_month: int,
                            reply_limits: dict[str, int] | None = None) -> bool:
        """Revalidate live limits on the owned claim immediately before dispatch."""
        with self._connection(transaction=True) as conn:
            row = conn.execute("SELECT * FROM x_operations WHERE id = ? AND tenant_id = ? AND owner = ? AND state = 'sending'",
                               (ident, tenant_id, owner)).fetchone()
            if row is None:
                return False
            if row["kind"] == "post":
                self._quota(conn, tenant_id=tenant_id, account_id=row["account_id"], due_at=time.time(),
                            max_day=max_day, max_month=max_month, exclude=ident)
                self._reply_quota(conn, tenant_id=tenant_id, account_id=row["account_id"], due_at=time.time(),
                                  metadata=json.loads(row["metadata"]), limits=reply_limits or {}, exclude=ident)
            return True

    def confirm(self, ident: str, *, owner: str, tweet_id: str) -> bool:
        """Known remote result and repair obligation commit together."""
        if not tweet_id.isascii() or not tweet_id.isdigit():
            raise ValueError("Confirmation requires a valid X identifier.")
        now = time.time()
        with self._connection(transaction=True) as conn:
            cur = conn.execute("UPDATE x_operations SET state = 'published', outcome = 'confirmed', tweet_id = ?, published_at = ?, updated_at = ?, version = version + 1, lease_until = NULL WHERE id = ? AND owner = ? AND state = 'sending'",
                               (tweet_id, now, now, ident, owner))
            if cur.rowcount != 1:
                return False
            row = conn.execute("SELECT * FROM x_operations WHERE id = ?", (ident,)).fetchone()
            self._event(conn, row, {"action": "confirmed", "tweet_id": tweet_id})
            conn.execute("INSERT INTO x_projection_outbox (operation_id, tenant_id, created_at, operation_version) VALUES (?, ?, ?, ?) "
                         "ON CONFLICT(operation_id) DO UPDATE SET operation_version = excluded.operation_version, done_at = NULL, last_error = ''",
                         (ident, row["tenant_id"], now, row["version"]))
            if row["kind"] == "delete":
                conn.execute("UPDATE x_operations SET deleted_at = ? WHERE tenant_id = ? AND account_id = ? AND kind = 'post' AND tweet_id = ?", (now, row["tenant_id"], row["account_id"], tweet_id))
            return True

    def fail(self, ident: str, *, owner: str, outcome: str, reason: str, defer_until: float | None = None) -> bool:
        """Only a typed rejection can release quota or enter a deferred state."""
        safe = outcome in ("rejected", "not_sent")
        state = "deferred" if safe and defer_until is not None else ("failed_permanent" if safe else "outcome_unknown")
        with self._connection(transaction=True) as conn:
            cur = conn.execute("UPDATE x_operations SET state = ?, outcome = ?, reason = ?, due_at = COALESCE(?, due_at), updated_at = ?, version = version + 1, lease_until = NULL WHERE id = ? AND owner = ? AND state = 'sending'",
                               (state, outcome if safe else "unknown", reason[:500], defer_until, time.time(), ident, owner))
            if cur.rowcount != 1:
                return False
            self._event(conn, conn.execute("SELECT * FROM x_operations WHERE id = ?", (ident,)).fetchone())
            return True

    def confirm_receipt(self, ident: str, *, tenant_id: str, expected_version: int, tweet_id: str,
                        receipt_id: int, confirmed_at: float) -> bool:
        """Repair a correlated validated API receipt, never infer success from similarity."""
        if not tweet_id.isascii() or not tweet_id.isdigit() or not math.isfinite(confirmed_at):
            raise ValueError("Receipt has no valid confirmation identifier or time.")
        with self._connection(transaction=True) as conn:
            row = conn.execute("SELECT * FROM x_operations WHERE id = ? AND tenant_id = ? AND version = ? AND state IN ('sending', 'outcome_unknown')",
                               (ident, tenant_id, expected_version)).fetchone()
            if row is None:
                return False
            conn.execute("UPDATE x_operations SET state = 'published', outcome = 'confirmed', tweet_id = ?, published_at = ?, "
                         "updated_at = ?, version = version + 1, lease_until = NULL, reason = '' WHERE id = ?",
                         (tweet_id, confirmed_at, time.time(), ident))
            updated = conn.execute("SELECT * FROM x_operations WHERE id = ?", (ident,)).fetchone()
            self._event(conn, updated, {"action": "confirmed_receipt_repair", "receipt_id": receipt_id})
            conn.execute("INSERT INTO x_projection_outbox (operation_id, tenant_id, created_at, operation_version) VALUES (?, ?, ?, ?) "
                         "ON CONFLICT(operation_id) DO UPDATE SET operation_version = excluded.operation_version, done_at = NULL",
                         (ident, tenant_id, time.time(), updated["version"]))
            if row["kind"] == "delete":
                conn.execute("UPDATE x_operations SET deleted_at = ? WHERE tenant_id = ? AND account_id = ? AND kind = 'post' AND tweet_id = ?",
                             (confirmed_at, tenant_id, row["account_id"], tweet_id))
            return True

    def unconfirmed_tenants(self) -> list[str]:
        with self._connection() as conn:
            return [str(r[0]) for r in conn.execute("SELECT DISTINCT tenant_id FROM x_operations WHERE state IN ('sending', 'outcome_unknown')")]

    def recover(self, *, now: float | None = None) -> int:
        """Crash recovery never returns a possibly sent operation to the queue."""
        ts = now if now is not None else time.time()
        with self._connection(transaction=True) as conn:
            rows = conn.execute("SELECT * FROM x_operations WHERE state = 'sending' AND lease_until < ?", (ts,)).fetchall()
            for row in rows:
                conn.execute("UPDATE x_operations SET state = 'outcome_unknown', outcome = 'unknown', reason = 'Interrupted send; verify on X before any new send', version = version + 1, updated_at = ? WHERE id = ?", (ts, row["id"]))
                self._event(conn, conn.execute("SELECT * FROM x_operations WHERE id = ?", (row["id"],)).fetchone(), {"action": "recovery"})
            return len(rows)

    def cancel(self, ident: str, *, tenant_id: str, expected_version: int) -> bool:
        with self._connection(transaction=True) as conn:
            cur = conn.execute("UPDATE x_operations SET state = 'cancelled', version = version + 1, updated_at = ? WHERE id = ? AND tenant_id = ? AND version = ? AND state IN ('scheduled', 'deferred', 'awaiting_approval')", (time.time(), ident, tenant_id, expected_version))
            if cur.rowcount != 1:
                return False
            self._event(conn, conn.execute("SELECT * FROM x_operations WHERE id = ?", (ident,)).fetchone())
            return True

    def reschedule(self, ident: str, *, tenant_id: str, expected_version: int, due_at: float,
                   max_day: int, max_month: int) -> bool:
        if not math.isfinite(due_at) or due_at <= time.time():
            raise ValueError("Choose a future, finite publication time.")
        with self._connection(transaction=True) as conn:
            row = conn.execute("SELECT * FROM x_operations WHERE id = ? AND tenant_id = ? AND version = ? AND state IN ('scheduled', 'deferred', 'awaiting_approval')", (ident, tenant_id, expected_version)).fetchone()
            if not row:
                return False
            self._quota(conn, tenant_id=tenant_id, account_id=row["account_id"], due_at=due_at, max_day=max_day, max_month=max_month, exclude=ident)
            conn.execute("UPDATE x_operations SET due_at = ?, state = 'scheduled', version = version + 1, updated_at = ? WHERE id = ?", (due_at, time.time(), ident))
            self._event(conn, conn.execute("SELECT * FROM x_operations WHERE id = ?", (ident,)).fetchone(), {"action": "rescheduled"})
            return True

    def outbox(self, *, tenant_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._connection() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM x_projection_outbox WHERE tenant_id = ? AND done_at IS NULL ORDER BY id LIMIT ?", (tenant_id, max(1, min(limit, 500)))).fetchall()]

    def repair_tenants(self) -> list[str]:
        """Internal maintenance inventory; restore tenant context before each repair."""
        with self._connection() as conn:
            return [str(r[0]) for r in conn.execute("SELECT DISTINCT tenant_id FROM x_projection_outbox WHERE done_at IS NULL")]

    def summary(self, *, tenant_id: str, account_id: str = "") -> dict[str, Any]:
        now = time.time()
        with self._connection() as conn:
            scope = (tenant_id, account_id, account_id)
            states = {r["state"]: r["n"] for r in conn.execute(
                "SELECT state, COUNT(*) AS n FROM x_operations WHERE tenant_id = ? AND (? = '' OR account_id = ?) GROUP BY state", scope)}
            counts = conn.execute("SELECT SUM(CASE WHEN published_at >= ? THEN 1 ELSE 0 END) AS day, "
                                  "SUM(CASE WHEN published_at >= ? THEN 1 ELSE 0 END) AS month FROM x_operations "
                                  "WHERE tenant_id = ? AND (? = '' OR account_id = ?) AND state = 'published' AND kind = 'post'",
                                  (now - 86400, now - 30 * 86400, *scope)).fetchone()
            repairs = conn.execute("SELECT COUNT(*) FROM x_projection_outbox WHERE tenant_id = ? AND done_at IS NULL", (tenant_id,)).fetchone()[0]
            notices = conn.execute("SELECT COUNT(*) AS n, MIN(created_at) AS oldest FROM x_notification_outbox WHERE tenant_id = ? AND delivered_at IS NULL AND next_attempt < 1000000000000", (tenant_id,)).fetchone()
            oldest_due = conn.execute("SELECT MIN(due_at) FROM x_operations WHERE tenant_id = ? AND state IN ('scheduled', 'deferred')", (tenant_id,)).fetchone()[0]
            return {"pending_notifications": notices["n"], "oldest_notification_at": notices["oldest"], "oldest_due_at": oldest_due, "states": states, "posts_today": counts["day"] or 0, "posts_30d": counts["month"] or 0,
                    "pending_repairs": repairs, "coordination": "shared_local_sqlite"}

    def finish_projection(self, ident: int, *, tenant_id: str, error: str = "", expected_version: int | None = None) -> bool:
        with self._connection(transaction=True) as conn:
            cur = conn.execute("UPDATE x_projection_outbox SET attempts = attempts + 1, last_error = ?, done_at = ? WHERE id = ? AND tenant_id = ? AND done_at IS NULL AND (? IS NULL OR operation_version = ?)", (error[:500], None if error else time.time(), ident, tenant_id, expected_version, expected_version))
            return cur.rowcount == 1

    def import_history(self, rows: list[dict[str, Any]], *, tenant_id: str, account_id: str, credential_revision: str) -> int:
        """Backfill confirmed legacy quota receipts once; never enqueue a remote write."""
        from kazma_core.x_api.ledger import text_hash

        added = 0
        with self._connection(transaction=True) as conn:
            for source in rows:
                tweet_id = str(source.get("tweet_id") or "")
                if not tweet_id.isdigit() or not tweet_id.isascii():
                    continue
                if conn.execute("SELECT 1 FROM x_operations WHERE tenant_id = ? AND tweet_id = ? AND kind = 'post'", (tenant_id, tweet_id)).fetchone():
                    continue
                text = str(source.get("text_preview") or "")
                created = float(source.get("created_at") or 0)
                ident = uuid.uuid4().hex
                conn.execute("INSERT INTO x_operations (id, tenant_id, account_id, credential_revision, idempotency_key, payload_hash, text, text_hash, kind, origin, state, due_at, created_at, updated_at, published_at, tweet_id, outcome, deleted_at, metadata) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'post', 'legacy', 'published', ?, ?, ?, ?, ?, 'confirmed', ?, ?)",
                             (ident, tenant_id, account_id, credential_revision, "legacy:" + tweet_id,
                              payload_hash(text, ""), text, str(source.get("text_hash") or text_hash(text)),
                              created, created, created, created, tweet_id, source.get("deleted_at"),
                              '{"legacy": true, "text_complete": false}'))
                self._event(conn, conn.execute("SELECT * FROM x_operations WHERE id = ?", (ident,)).fetchone(), {"action": "legacy_backfill"})
                added += 1
        return added

    def import_reply_history(self, rows: list[dict[str, Any]], *, tenant_id: str, account_id: str) -> None:
        """Attach historical reply quota identity to imported confirmed post receipts."""
        with self._connection(transaction=True) as conn:
            for source in rows:
                row = conn.execute("SELECT id, metadata FROM x_operations WHERE tenant_id = ? AND account_id = ? AND tweet_id = ? AND kind = 'post'",
                                   (tenant_id, account_id, source["tweet_id"])).fetchone()
                if row is None:
                    continue
                metadata = json.loads(row["metadata"])
                if metadata.get("summon_id"):
                    continue
                metadata.update(summon_id=source["summon_id"], reply_target=source["target_handle"], reply_conversation=source["parent_id"])
                conn.execute("UPDATE x_operations SET metadata = ? WHERE id = ?", (json.dumps(metadata), row["id"]))

    def due(self, *, now: float | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Internal scheduler inventory across tenants; authorization is restored per row."""
        with self._connection() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM x_operations WHERE state IN ('scheduled', 'deferred') AND due_at <= ? AND origin = 'schedule' ORDER BY due_at LIMIT ?", (now or time.time(), max(1, min(limit, 500)))).fetchall()]

    def import_legacy_schedule(self, post: Any, *, account_id: str, credential_revision: str) -> dict[str, Any]:
        """Retain an existing commitment without granting an account-unbound approval."""
        from kazma_core.x_api.ledger import text_hash

        now, reference = time.time(), str(post.id)
        unknown = post.status == "outcome_unknown"
        state = "outcome_unknown" if unknown else "awaiting_approval"
        with self._connection(transaction=True) as conn:
            existing = conn.execute("SELECT * FROM x_operations WHERE tenant_id = ? AND origin = 'schedule' AND origin_ref = ?",
                                    (post.tenant_id, reference)).fetchone()
            if existing:
                return dict(existing)
            ident = uuid.uuid4().hex
            metadata = {"legacy_schedule_id": post.id, "legacy_account_unbound": True, "thread_id": post.thread_id,
                        "delivery_target": post.delivery_target, "tz": post.tz}
            conn.execute("INSERT INTO x_operations (id, tenant_id, account_id, credential_revision, idempotency_key, payload_hash, "
                         "text, text_hash, reply_to_id, kind, origin, origin_ref, state, due_at, created_at, updated_at, sent_at, "
                         "attempts, outcome, reason, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'post', 'schedule', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (ident, post.tenant_id, account_id, credential_revision, "legacy-schedule:" + reference,
                          payload_hash(post.text, post.reply_to_id), post.text, text_hash(post.text), post.reply_to_id,
                          reference, state, post.fire_at, post.created_at, now, now if unknown else None,
                          post.attempts, "unknown" if unknown else "not_sent", post.error, json.dumps(metadata)))
            row = conn.execute("SELECT * FROM x_operations WHERE id = ?", (ident,)).fetchone()
            self._event(conn, row, {"action": "legacy_booking_held"})
            return dict(row)

    def hold(self, ident: str, *, tenant_id: str, reason: str) -> bool:
        """A live account/policy change holds unsent work instead of rerouting it."""
        with self._connection(transaction=True) as conn:
            cur = conn.execute("UPDATE x_operations SET state = 'awaiting_approval', reason = ?, version = version + 1, updated_at = ? WHERE id = ? AND tenant_id = ? AND state IN ('scheduled', 'deferred')", (reason[:500], time.time(), ident, tenant_id))
            if cur.rowcount == 1:
                self._event(conn, conn.execute("SELECT * FROM x_operations WHERE id = ?", (ident,)).fetchone(), {"action": "held"})
            return cur.rowcount == 1

    def events(self, ident: str, *, tenant_id: str) -> list[dict[str, Any]]:
        with self._connection() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM x_operation_events WHERE operation_id = ? AND tenant_id = ? ORDER BY id", (ident, tenant_id)).fetchall()]

    def list_operations(self, *, tenant_id: str, state: str = "", before: float | None = None,
                        limit: int = 100) -> list[dict[str, Any]]:
        with self._connection() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM x_operations WHERE tenant_id = ? AND (? = '' OR state = ?) AND (? IS NULL OR created_at < ?) ORDER BY created_at DESC, id DESC LIMIT ?",
                                                (tenant_id, state, state, before, before, max(1, min(limit, 500)))).fetchall()]

    def operation_page(self, *, tenant_id: str, state: str = "", query: str = "", cursor: str = "", limit: int = 50) -> dict[str, Any]:
        """Search the full operation history with stable timestamp/ID boundaries."""
        from kazma_core.db.keyset import decode_cursor, encode_cursor

        boundary = decode_cursor(cursor, size=2)
        if len(query) > 200:
            raise ValueError("Search exceeds 200 characters")
        cap = max(1, min(100, int(limit)))
        where = "tenant_id = ? AND (? = '' OR state = ?) AND (? = '' OR instr(lower(text || ' ' || tweet_id || ' ' || id), lower(?)) > 0)"
        args = (tenant_id, state, state, query, query)
        paging = " AND (created_at < ? OR (created_at = ? AND id < ?))" if boundary else ""
        with self._connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM x_operations WHERE " + where, args).fetchone()[0]
            rows = [dict(row) for row in conn.execute("SELECT * FROM x_operations WHERE " + where + paging +
                    " ORDER BY created_at DESC, id DESC LIMIT ?", (*args, *((boundary[0], boundary[0], boundary[1]) if boundary else ()), cap + 1))]
        more = len(rows) > cap
        rows = rows[:cap]
        next_cursor = encode_cursor((rows[-1]["created_at"], rows[-1]["id"])) if rows and more else ""
        return {"rows": rows, "count": count, "next_cursor": next_cursor}

    def queue_page(self, *, tenant_id: str, cursor: str = "", limit: int = 50, include_finished: bool = False) -> dict[str, Any]:
        """Canonical schedule inventory with keyset paging and full-store counts."""
        boundary, ident = None, ""
        if cursor:
            if len(cursor) > 512:
                raise ValueError("Invalid queue cursor")
            try:
                decoded = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
                boundary, ident = decoded
                if type(boundary) not in (int, float) or not math.isfinite(boundary) or not isinstance(ident, str):
                    raise ValueError("Invalid queue cursor")
            except (ValueError, TypeError, UnicodeError) as exc:
                raise ValueError("Invalid queue cursor") from exc
        cap = max(1, min(100, int(limit)))
        with self._connection() as conn:
            counts = {row["state"]: row["n"] for row in conn.execute(
                "SELECT state, COUNT(*) AS n FROM x_operations WHERE tenant_id = ? AND origin = 'schedule' GROUP BY state", (tenant_id,))}
            rows = [dict(row) for row in conn.execute(
                "SELECT * FROM x_operations WHERE tenant_id = ? AND origin = 'schedule' "
                "AND (? OR state NOT IN ('published', 'cancelled')) "
                "AND (? IS NULL OR created_at < ? OR (created_at = ? AND id < ?)) "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (tenant_id, include_finished, boundary, boundary, boundary, ident, cap + 1))]
        more, rows = len(rows) > cap, rows[:cap]
        next_cursor = base64.urlsafe_b64encode(json.dumps([rows[-1]["created_at"], rows[-1]["id"]]).encode()).decode() if more else ""
        return {"rows": rows, "counts": counts, "next_cursor": next_cursor,
                "count": sum(n for state, n in counts.items() if include_finished or state not in ("published", "cancelled"))}


_store: _PublicationStore | None = None
_test_store: _PublicationStore | None = None
_store_lock = threading.Lock()


def get_publication_store() -> _PublicationStore:
    global _store
    with _store_lock:
        if _test_store is not None:
            return _test_store
        from kazma_core.paths import data_dir

        path = data_dir() / "x_publications.db"
        if _store is None or _store.path != path:
            _store = _PublicationStore(path)
        return _store


def _set_publication_store_for_tests(store: _PublicationStore | None) -> None:
    """Explicit isolated test binding; no live database is opened by this hook."""
    global _test_store
    with _store_lock:
        _test_store = store
