"""Durable state for X auto-replies: idempotency and per-target caps.

Two jobs the post ledger cannot do:

**Idempotency.** The mentions poller restarts. Without a record of which
summons have been handled, a restart re-reads the same mention window and
replies again — and :class:`XClient` deliberately never retries writes,
precisely so that a double-post can only ever come from a caller like this
one. Every summon is claimed here BEFORE any draft or publish, so a crash
between draft and post leaves a row in ``drafting`` rather than a silent
re-run.

**Per-target caps.** ``x_api.policy`` caps total posts per day/month and
blocks duplicates. It does not know that four of today's eight posts were
replies aimed at the same account. Replying repeatedly to one person is what
gets reported as targeted harassment regardless of how mild each reply is,
so the count belongs here, keyed by target.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from kazma_core.config_store import apply_sqlite_pragmas
from kazma_core.x_api.ownership import x_tenant_id

logger = logging.getLogger(__name__)

__all__ = [
    "ReplyRecord",
    "XReplyStore",
    "get_reply_store",
    "reset_reply_store",
    "STATUS_DRAFTING",
    "STATUS_AWAITING",
    "STATUS_POSTED",
    "STATUS_SKIPPED",
    "STATUS_FAILED",
]

STATUS_DRAFTING = "drafting"
STATUS_AWAITING = "awaiting_approval"
STATUS_POSTED = "posted"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"
STATUS_SENDING = "sending"
STATUS_UNKNOWN = "outcome_unknown"
STATUS_RETRY_PENDING = "retry_pending"

_CREATE = """
CREATE TABLE IF NOT EXISTS x_replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    summon_id TEXT NOT NULL,
    parent_id TEXT NOT NULL DEFAULT '',
    target_handle TEXT NOT NULL DEFAULT '',
    summoner TEXT NOT NULL DEFAULT '',
    subject_id TEXT NOT NULL DEFAULT '',
    -- The other half of the conversation. Without these the log shows
    -- Kazma talking to itself: a handle, a draft, and no idea what was
    -- said to provoke it. Stored at claim time because the poller has
    -- them in hand and re-fetching later costs read quota (and on a
    -- deleted tweet is impossible).
    parent_text TEXT NOT NULL DEFAULT '',
    summon_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'drafting',
    draft_text TEXT NOT NULL DEFAULT '',
    proposal_id TEXT NOT NULL DEFAULT '',
    tweet_id TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    tenant_id TEXT NOT NULL DEFAULT 'default',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    publish_outcome TEXT NOT NULL DEFAULT '',
    archived_at REAL,
    decision_json TEXT NOT NULL DEFAULT '{}',
    attempt_no INTEGER NOT NULL DEFAULT 1,
    revision INTEGER NOT NULL DEFAULT 1,
    UNIQUE (tenant_id, summon_id)
);
CREATE INDEX IF NOT EXISTS idx_x_replies_status ON x_replies(status, created_at);
CREATE INDEX IF NOT EXISTS idx_x_replies_target ON x_replies(target_handle, created_at);
CREATE INDEX IF NOT EXISTS idx_x_replies_parent ON x_replies(parent_id, created_at);
CREATE TABLE IF NOT EXISTS x_reply_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    summon_id TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    event TEXT NOT NULL,
    record_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_x_reply_history_scope ON x_reply_history(tenant_id, summon_id, id);

-- The mentions cursor. One row; X's since_id is monotonic per account.
CREATE TABLE IF NOT EXISTS x_mentions_cursor (
    k TEXT PRIMARY KEY,
    since_id TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS x_closed_threads (
    conversation_id TEXT PRIMARY KEY,
    closed_at REAL NOT NULL,
    closed_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS x_thread_permissions (
    tenant_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    state TEXT NOT NULL,
    actor TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (tenant_id, conversation_id)
);
"""


class ReplyRecord:
    """Read-only view of one row."""

    def __init__(self, row: sqlite3.Row) -> None:
        self.id = int(row["id"])
        self.summon_id = str(row["summon_id"])
        self.parent_id = str(row["parent_id"])
        self.target_handle = str(row["target_handle"])
        self.summoner = str(row["summoner"])
        self.subject_id = str(row["subject_id"])
        self.parent_text = str(row["parent_text"] or "")
        self.summon_text = str(row["summon_text"] or "")
        self.status = str(row["status"])
        self.draft_text = str(row["draft_text"])
        self.proposal_id = str(row["proposal_id"])
        self.tweet_id = str(row["tweet_id"])
        self.reason = str(row["reason"])
        self.tenant_id = str(row["tenant_id"])
        self.created_at = float(row["created_at"])
        self.updated_at = float(row["updated_at"])
        self.publish_outcome = str(row["publish_outcome"] or "")
        self.attempt_no = int(row["attempt_no"])
        self.revision = int(row["revision"])
        try:
            self.decision = json.loads(row["decision_json"] or "{}")
        except (ValueError, TypeError):
            self.decision = {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "summon_id": self.summon_id,
            "parent_id": self.parent_id,
            "target": self.target_handle,
            "summoner": self.summoner,
            "subject": self.subject_id,
            "parent_text": self.parent_text,
            "summon_text": self.summon_text,
            "status": self.status,
            "draft": self.draft_text,
            "proposal_id": self.proposal_id,
            "tweet_id": self.tweet_id,
            "reason": self.reason,
            "publish_outcome": self.publish_outcome,
            "decision": {key: value for key, value in self.decision.items() if key != "approval_basis"},
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "attempt_no": self.attempt_no,
            "revision": self.revision,
            "approval_token": self.approval_token,
        }

    @property
    def approval_token(self) -> str:
        """Revision identifier for the exact conversation card an operator saw."""
        payload = [self.tenant_id, self.id, self.attempt_no, self.revision, self.updated_at,
                   self.summon_id, self.parent_id, self.draft_text, self.subject_id, self.decision]
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class XReplyStore:
    """SQLite WAL store for auto-reply state."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            from kazma_core.paths import data_dir

            db_path = data_dir() / "x_replies.db"
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=5.0)
        apply_sqlite_pragmas(conn)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(_CREATE)
                from kazma_core.x_api.notifications import SCHEMA
                conn.executescript(SCHEMA)
                # Additive migration for stores created before the
                # conversation log existed. CREATE TABLE IF NOT EXISTS is
                # a no-op on an existing table, so new columns need this
                # or an upgraded install silently keeps the old shape.
                from kazma_core.db.sqlite_columns import add_missing_columns

                add_missing_columns(
                    conn,
                    'x_replies',
                    (
                        ('parent_text', "TEXT NOT NULL DEFAULT ''"),
                        ('summon_text', "TEXT NOT NULL DEFAULT ''"),
                        ('publish_outcome', "TEXT NOT NULL DEFAULT ''"),
                        ('archived_at', "REAL"),
                        ('decision_json', "TEXT NOT NULL DEFAULT '{}'"),
                        ('attempt_no', "INTEGER NOT NULL DEFAULT 1"),
                        ('revision', "INTEGER NOT NULL DEFAULT 1"),
                    ),
                )
                conn.commit()
                self._migrate_tenant_uniqueness(conn)
            finally:
                if conn.in_transaction:
                    conn.rollback()
                conn.close()

    @staticmethod
    def _migrate_tenant_uniqueness(conn: sqlite3.Connection) -> None:
        """Replace the legacy global summon constraint, retaining rows and IDs."""
        def has_global_constraint() -> bool:
            for index in conn.execute("PRAGMA index_list(x_replies)"):
                if index["unique"]:
                    name = str(index["name"]).replace('"', '""')
                    columns = [row["name"] for row in conn.execute(f'PRAGMA index_info("{name}")')]
                    if columns == ["summon_id"]:
                        return True
            return False

        if not has_global_constraint():
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            if has_global_constraint():
                columns = [row["name"] for row in conn.execute("PRAGMA table_info(x_replies)")]
                sequence = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'x_replies'").fetchone()
                create_table = _CREATE.split(";", 1)[0].replace("IF NOT EXISTS x_replies", "x_replies_scoped")
                conn.execute(create_table)
                names = ", ".join('"' + column.replace('"', '""') + '"' for column in columns)
                conn.execute(f"INSERT INTO x_replies_scoped ({names}) SELECT {names} FROM x_replies")
                conn.execute("DROP TABLE x_replies")
                conn.execute("ALTER TABLE x_replies_scoped RENAME TO x_replies")
                if sequence:
                    conn.execute("INSERT INTO sqlite_sequence (name, seq) SELECT 'x_replies', ? "
                                 "WHERE NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'x_replies')", (sequence["seq"],))
                    conn.execute("UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = 'x_replies'", (sequence["seq"],))
                for name, fields in (("status", "status, created_at"), ("target", "target_handle, created_at"), ("parent", "parent_id, created_at")):
                    conn.execute(f"CREATE INDEX idx_x_replies_{name} ON x_replies({fields})")
            conn.commit()
        finally:
            if conn.in_transaction:
                conn.rollback()

    # ── Claim / idempotency ───────────────────────────────────────────

    def claim(
        self,
        *,
        summon_id: str,
        parent_id: str,
        target_handle: str,
        summoner: str,
        parent_text: str = "",
        summon_text: str = "",
        tenant_id: str | None = None,
    ) -> bool:
        """Claim *summon_id* for processing. False if already claimed.

        The UNIQUE constraint is the lock. Two pollers (or a poller and a
        manual ``/x`` command) racing on the same mention both call this;
        exactly one gets True. The loser must do nothing — not even draft,
        because drafting costs an LLM call.
        """
        now = time.time()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """INSERT INTO x_replies
                       (summon_id, parent_id, target_handle, summoner, status,
                        parent_text, summon_text, tenant_id, created_at,
                        updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(summon_id), str(parent_id), str(target_handle).lstrip("@").lower(),
                        str(summoner).lstrip("@").lower(), STATUS_DRAFTING,
                        str(parent_text or "")[:16000], str(summon_text or "")[:4000],
                        str(tenant_id or x_tenant_id()), now, now,
                    ),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                conn.rollback()
                cur = conn.execute(
                    "UPDATE x_replies SET status = ?, parent_id = ?, target_handle = ?, summoner = ?, "
                    "parent_text = ?, summon_text = ?, updated_at = ?, attempt_no = attempt_no + 1, revision = revision + 1, "
                    "subject_id = '', draft_text = '', proposal_id = '', tweet_id = '', reason = '', "
                    "publish_outcome = '', decision_json = '{}' "
                    "WHERE summon_id = ? AND tenant_id = ? AND status = ? AND archived_at IS NULL",
                    (STATUS_DRAFTING, str(parent_id), str(target_handle).lstrip("@").lower(),
                     str(summoner).lstrip("@").lower(), str(parent_text or "")[:16000],
                     str(summon_text or "")[:4000], now, str(summon_id),
                     str(tenant_id or x_tenant_id()), STATUS_RETRY_PENDING),
                )
                conn.commit()
                return cur.rowcount == 1
            finally:
                if conn.in_transaction:
                    conn.rollback()
                conn.close()

    def seen(self, summon_id: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT 1 FROM x_replies WHERE summon_id = ? AND tenant_id = ?",
                    (str(summon_id), x_tenant_id()),
                ).fetchone()
                return row is not None
            finally:
                if conn.in_transaction:
                    conn.rollback()
                conn.close()

    # ── Transitions ───────────────────────────────────────────────────

    def record_decision(self, summon_id: str, *, draft: str, subject_id: str, decision: dict[str, Any]) -> None:
        """Retain rejected and held candidate checks without altering execution state."""
        self._update(summon_id, allowed_statuses=(STATUS_DRAFTING,), draft_text=draft, subject_id=subject_id,
                     decision_json=json.dumps(decision, ensure_ascii=False))

    def notification_current(self, notice: dict[str, Any]) -> bool:
        summon, revision = notice["event_key"].removeprefix("reply:").rsplit(":", 1)
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT revision, status, archived_at FROM x_replies WHERE summon_id = ? AND tenant_id = ?", (summon, notice["tenant_id"])).fetchone()
                return bool(row and row["revision"] == int(revision) and row["status"] == STATUS_AWAITING and row["archived_at"] is None)
            finally:
                if conn.in_transaction:
                    conn.rollback()
                conn.close()

    def claim_notification(self, *, owner: str) -> dict[str, Any] | None:
        from kazma_core.x_api.notifications import claim

        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = claim(conn, now=time.time(), owner=owner)
                conn.commit()
                return row
            finally:
                if conn.in_transaction:
                    conn.rollback()
                conn.close()

    def finish_notification(self, row: dict[str, Any], *, owner: str, delivered: bool, superseded: bool = False) -> None:
        from kazma_core.x_api.notifications import finish

        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                finish(conn, row, owner=owner, delivered=delivered, superseded=superseded)
                conn.commit()
            finally:
                if conn.in_transaction:
                    conn.rollback()
                conn.close()

    def _update(self, summon_id: str, *, allowed_statuses: tuple[str, ...] = (), **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k} = ?" for k in fields)
        condition = " AND status IN (" + ",".join("?" for _ in allowed_statuses) + ")" if allowed_statuses else ""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                cur = conn.execute(
                    f"UPDATE x_replies SET {cols}, revision = revision + 1 WHERE summon_id = ? AND tenant_id = ?{condition}",
                    (*fields.values(), str(summon_id), x_tenant_id(), *allowed_statuses),
                )
                if cur.rowcount and fields.get("status") == STATUS_AWAITING:
                    from kazma_core.x_api.notifications import enqueue

                    row = conn.execute("SELECT * FROM x_replies WHERE summon_id = ? AND tenant_id = ?", (str(summon_id), x_tenant_id())).fetchone()
                    rec = ReplyRecord(row)
                    message = (f"X reply needs review — {rec.subject_id}\n"
                               f"Source: https://x.com/i/web/status/{rec.parent_id}\n"
                               f"{rec.reason}\n\n{rec.draft_text}\n\n"
                               f"Approve this revision: /x approve {rec.summon_id} {rec.approval_token}")
                    enqueue(conn, tenant=row["tenant_id"], key=f"reply:{summon_id}:{rec.revision}", message=message)
                conn.commit()
            finally:
                if conn.in_transaction:
                    conn.rollback()
                conn.close()

    def mark_awaiting(self, summon_id: str, *, draft: str, subject_id: str,
                      proposal_id: str = "", decision: dict[str, Any] | None = None, reason: str = "") -> None:
        self._update(summon_id, status=STATUS_AWAITING, draft_text=draft,
                     allowed_statuses=(STATUS_DRAFTING, STATUS_AWAITING),
                     subject_id=subject_id, proposal_id=proposal_id, publish_outcome="",
                     decision_json=json.dumps(decision or {}, ensure_ascii=False), reason=reason)

    @staticmethod
    def _snapshot(conn: sqlite3.Connection, row: sqlite3.Row, event: str, actor: str) -> None:
        conn.execute(
            "INSERT INTO x_reply_history (tenant_id, summon_id, attempt_no, event, record_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (row["tenant_id"], row["summon_id"], row["attempt_no"], event,
             json.dumps({**dict(row), "decision_actor": str(actor)[:200]}, ensure_ascii=False), time.time()),
        )

    def claim_approval(self, summon_id: str, *, expected_updated_at: float, expected_revision: int, actor: str = "operator") -> bool:
        """Atomically bind approval to the exact stored revision and own its send."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                before = conn.execute("SELECT * FROM x_replies WHERE summon_id = ? AND tenant_id = ? AND revision = ?",
                                      (str(summon_id), x_tenant_id(), expected_revision)).fetchone()
                cur = conn.execute(
                    "UPDATE x_replies SET status = ?, updated_at = ?, revision = revision + 1 "
                    "WHERE summon_id = ? AND tenant_id = ? AND updated_at = ? AND revision = ? "
                    "AND (status = ? OR (status = ? AND publish_outcome IN ('rejected', 'not_sent'))) "
                    "AND archived_at IS NULL",
                    (STATUS_SENDING, time.time(), str(summon_id), x_tenant_id(), expected_updated_at, expected_revision,
                     STATUS_AWAITING, STATUS_FAILED),
                )
                if cur.rowcount == 1:
                    self._snapshot(conn, before, "approved", actor)
                conn.commit()
                return cur.rowcount == 1
            finally:
                if conn.in_transaction:
                    conn.rollback()
                conn.close()

    def mark_publish_failure(self, summon_id: str, reason: str, *, outcome: str) -> None:
        """Unknown sends remain held; only typed refusals can be approved again."""
        safe = outcome in ("rejected", "not_sent")
        self._update(summon_id, status=STATUS_FAILED if safe else STATUS_UNKNOWN,
                     reason=reason[:500], publish_outcome=outcome if safe else "unknown")

    def mark_sending(self, summon_id: str, *, draft: str, subject_id: str, decision: dict[str, Any] | None = None) -> None:
        """Persist the send boundary before an automatic publication."""
        self._update(summon_id, status=STATUS_SENDING, draft_text=draft, subject_id=subject_id,
                     allowed_statuses=(STATUS_DRAFTING,),
                     decision_json=json.dumps(decision or {}, ensure_ascii=False))

    def deny_awaiting(self, summon_id: str, *, expected_updated_at: float, expected_revision: int, actor: str = "operator") -> bool:
        """Deny only an approval that no sender has claimed."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                before = conn.execute("SELECT * FROM x_replies WHERE summon_id = ? AND tenant_id = ? AND revision = ?",
                                      (str(summon_id), x_tenant_id(), expected_revision)).fetchone()
                cur = conn.execute(
                    "UPDATE x_replies SET status = ?, reason = ?, updated_at = ?, revision = revision + 1 "
                    "WHERE summon_id = ? AND tenant_id = ? AND status = ? AND updated_at = ? AND revision = ?",
                    (STATUS_SKIPPED, "operator denied", time.time(), str(summon_id), x_tenant_id(), STATUS_AWAITING, expected_updated_at, expected_revision),
                )
                if cur.rowcount == 1:
                    self._snapshot(conn, before, "denied", actor)
                conn.commit()
                return cur.rowcount == 1
            finally:
                if conn.in_transaction:
                    conn.rollback()
                conn.close()

    def mark_posted(self, summon_id: str, *, tweet_id: str, draft: str = "",
                    subject_id: str = "") -> None:
        fields: dict[str, Any] = {"status": STATUS_POSTED, "tweet_id": str(tweet_id), "publish_outcome": "confirmed"}
        if draft:
            fields["draft_text"] = draft
        if subject_id:
            fields["subject_id"] = subject_id
        self._update(summon_id, **fields)

    def mark_skipped(self, summon_id: str, reason: str) -> None:
        self._update(summon_id, status=STATUS_SKIPPED, reason=reason[:500],
                     allowed_statuses=(STATUS_DRAFTING, STATUS_AWAITING, STATUS_FAILED, STATUS_SKIPPED))

    def mark_failed(self, summon_id: str, reason: str) -> None:
        self._update(summon_id, status=STATUS_FAILED, reason=reason[:500],
                     allowed_statuses=(STATUS_DRAFTING, STATUS_AWAITING, STATUS_FAILED, STATUS_SKIPPED))

    def mark_interrupted(self, summon_id: str, reason: str) -> None:
        """An interrupted send is unknown; interrupted generation is safe to redo."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE x_replies SET status = CASE WHEN status = ? THEN ? ELSE ? END, "
                    "publish_outcome = CASE WHEN status = ? THEN 'unknown' ELSE publish_outcome END, "
                    "reason = ?, updated_at = ?, revision = revision + 1 WHERE summon_id = ? AND tenant_id = ? AND status NOT IN (?, ?)",
                    (STATUS_SENDING, STATUS_UNKNOWN, STATUS_FAILED, STATUS_SENDING,
                     reason[:500], time.time(), str(summon_id), x_tenant_id(), STATUS_POSTED, STATUS_UNKNOWN),
                )
                conn.commit()
            finally:
                if conn.in_transaction:
                    conn.rollback()
                conn.close()

    def forget(self, summon_id: str, *, expected_updated_at: float | None = None, expected_revision: int | None = None) -> bool:
        """Remove a row from the log. Posted tweets must be deleted on X first."""
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "UPDATE x_replies SET archived_at = ? WHERE summon_id = ? AND tenant_id = ? "
                    "AND (? IS NULL OR updated_at = ?) AND (? IS NULL OR revision = ?)",
                    (time.time(), str(summon_id), x_tenant_id(), expected_updated_at, expected_updated_at, expected_revision, expected_revision),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                if conn.in_transaction:
                    conn.rollback()
                conn.close()

    def release(self, summon_id: str, *, actor: str = "operator") -> bool:
        """Retain the previous attempt before making a safe row reclaimable."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM x_replies WHERE summon_id = ? AND tenant_id = ? "
                    "AND archived_at IS NULL AND (status IN (?, ?, ?) OR (status = ? AND updated_at < ?))",
                    (str(summon_id), x_tenant_id(), STATUS_SKIPPED, STATUS_FAILED, STATUS_AWAITING,
                     STATUS_DRAFTING, time.time() - 600),
                ).fetchone()
                if row is None:
                    conn.rollback()
                    return False
                self._snapshot(conn, row, "retry_requested", actor)
                conn.execute("UPDATE x_replies SET status = ?, updated_at = ?, revision = revision + 1 WHERE id = ?",
                             (STATUS_RETRY_PENDING, time.time(), row["id"]))
                conn.commit()
                return True
            finally:
                if conn.in_transaction:
                    conn.rollback()
                conn.close()

    def history(self, summon_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Previous candidates and decisions, scoped to the current tenant."""
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM x_reply_history WHERE tenant_id = ? AND summon_id = ? ORDER BY id DESC LIMIT ?",
                    (x_tenant_id(), str(summon_id), max(1, min(200, int(limit)))),
                ).fetchall()
                return [{"attempt_no": row["attempt_no"], "event": row["event"],
                         "record": json.loads(row["record_json"]), "created_at": row["created_at"]} for row in rows]
            finally:
                if conn.in_transaction:
                    conn.rollback()
                conn.close()

    def get(self, summon_id: str, *, include_archived: bool = False) -> ReplyRecord | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM x_replies WHERE summon_id = ? AND tenant_id = ? AND (? OR archived_at IS NULL)",
                    (str(summon_id), x_tenant_id(), include_archived),
                ).fetchone()
                return ReplyRecord(row) if row else None
            finally:
                if conn.in_transaction:
                    conn.rollback()
                conn.close()

    # ── Caps ──────────────────────────────────────────────────────────

    def posted_since(self, epoch: float) -> int:
        """Replies actually POSTED since *epoch*. Drafts do not count."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM x_replies "
                    "WHERE status = ? AND updated_at >= ? AND tenant_id = ?",
                    (STATUS_POSTED, float(epoch), x_tenant_id()),
                ).fetchone()
                return int(row["n"] if row else 0)
            finally:
                if conn.in_transaction:
                    conn.rollback()
                conn.close()

    def posted_to_target_since(self, handle: str, epoch: float) -> int:
        h = (handle or "").lstrip("@").lower()
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM x_replies "
                    "WHERE status = ? AND target_handle = ? AND updated_at >= ? AND tenant_id = ?",
                    (STATUS_POSTED, h, float(epoch), x_tenant_id()),
                ).fetchone()
                return int(row["n"] if row else 0)
            finally:
                if conn.in_transaction:
                    conn.rollback()
                conn.close()

    def last_reply_in_thread(self, parent_id: str) -> float:
        """Epoch of the most recent POSTED reply under *parent_id*, or 0.0."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT MAX(updated_at) AS t FROM x_replies "
                    "WHERE status = ? AND parent_id = ? AND tenant_id = ?",
                    (STATUS_POSTED, str(parent_id), x_tenant_id()),
                ).fetchone()
                return float(row["t"] or 0.0) if row else 0.0
            finally:
                if conn.in_transaction:
                    conn.rollback()
                conn.close()

    def close_conversation(self, conversation_id: str, *, closed_by: str = "") -> None:
        self.set_thread_permission(conversation_id, state="closed", actor=closed_by, reopen=True)
        return

    def set_thread_permission(self, conversation_id: str, *, state: str, actor: str, reopen: bool = False) -> None:
        """Persist trusted authority; re-reading an old open post cannot undo close."""
        if state not in ("open", "closed") or not str(conversation_id or "").strip():
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO x_thread_permissions VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(tenant_id, conversation_id) DO UPDATE SET "
                    "state = excluded.state, actor = excluded.actor, updated_at = excluded.updated_at "
                    "WHERE x_thread_permissions.state != 'closed' OR ?",
                    (x_tenant_id(), str(conversation_id).strip(), state, str(actor), time.time(), bool(reopen)),
                )
                conn.commit()
            finally:
                if conn.in_transaction:
                    conn.rollback()
                conn.close()

    def publication_history(self) -> list[dict[str, Any]]:
        """Confirmed reply receipts for transactional quota migration."""
        with self._lock:
            conn = self._connect()
            try:
                return [dict(row) for row in conn.execute("SELECT * FROM x_replies WHERE tenant_id = ? AND status = ?", (x_tenant_id(), STATUS_POSTED))]
            finally:
                if conn.in_transaction:
                    conn.rollback()
                conn.close()

    def thread_permission(self, conversation_id: str) -> str:
        """Tenant-scoped authority, with closed-only legacy compatibility."""
        cid = str(conversation_id or "").strip()
        if not cid:
            return ""
        with self._lock:
            conn = self._connect()
            try:
                tenant = x_tenant_id()
                row = conn.execute("SELECT state FROM x_thread_permissions WHERE tenant_id = ? AND conversation_id = ?", (tenant, cid)).fetchone()
                if row:
                    return str(row["state"])
                if tenant == "default" and conn.execute("SELECT 1 FROM x_closed_threads WHERE conversation_id = ?", (cid,)).fetchone():
                    return "closed"
                return ""
            finally:
                if conn.in_transaction:
                    conn.rollback()
                conn.close()

    def is_conversation_closed(self, conversation_id: str) -> bool:
        return self.thread_permission(conversation_id) == "closed"

    def recent(self, limit: int = 20) -> list[ReplyRecord]:
        """Newest mention on X first — not last time we touched the row.

        Retrying retains the row's original creation time. Tweet ids are
        snowflakes: larger id is later on X. Manual ``/x roast`` rows have
        no snowflake and sort after real mentions, by created_at.
        """
        cap = max(1, min(200, int(limit)))
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT * FROM x_replies WHERE tenant_id = ? AND archived_at IS NULL", (x_tenant_id(),)).fetchall()
            finally:
                if conn.in_transaction:
                    conn.rollback()
                conn.close()
        recs = [ReplyRecord(r) for r in rows]
        recs.sort(key=lambda r: _mention_recency_key(r), reverse=True)
        return recs[:cap]

    def conversation_page(self, *, cursor: str = "", query: str = "", state: str = "", limit: int = 30,
                          side: str = "", mood: str = "") -> dict[str, Any]:
        """Search all active conversations; each page has a stable creation boundary."""
        from kazma_core.db.keyset import decode_cursor, encode_cursor

        boundary = decode_cursor(cursor, size=2)
        if len(query) > 200:
            raise ValueError("Search exceeds 200 characters")
        from kazma_core.x_api.reply_style import TONES

        if side not in ("", "support", "against", "written") or (mood and mood not in TONES):
            raise ValueError("Unknown stance or tone filter")
        cap = max(1, min(100, int(limit)))
        where = ("tenant_id = ? AND archived_at IS NULL AND (? = '' OR status = ?) "
                 "AND (? = '' OR instr(lower(parent_text || ' ' || draft_text || ' ' || summon_id || ' ' || target_handle), lower(?)) > 0) "
                 "AND (? = '' OR json_extract(CASE WHEN json_valid(decision_json) THEN decision_json ELSE '{}' END, '$.effective_policy.side') = ? "
                 "OR (? = 'written' AND json_extract(CASE WHEN json_valid(decision_json) THEN decision_json ELSE '{}' END, '$.effective_policy.side') = '')) "
                 "AND (? = '' OR json_extract(CASE WHEN json_valid(decision_json) THEN decision_json ELSE '{}' END, '$.effective_policy.mood') = ?)")
        args = (x_tenant_id(), state, state, query, query, side, side, side, mood, mood)
        paging = " AND (created_at < ? OR (created_at = ? AND summon_id < ?))" if boundary else ""
        with self._lock:
            conn = self._connect()
            try:
                count = conn.execute("SELECT COUNT(*) FROM x_replies WHERE " + where, args).fetchone()[0]
                rows = [ReplyRecord(row) for row in conn.execute("SELECT * FROM x_replies WHERE " + where + paging +
                        " ORDER BY created_at DESC, summon_id DESC LIMIT ?", (*args, *((boundary[0], boundary[0], boundary[1]) if boundary else ()), cap + 1))]
            finally:
                conn.close()
        more = len(rows) > cap
        rows = rows[:cap]
        next_cursor = encode_cursor((rows[-1].created_at, rows[-1].summon_id)) if rows and more else ""
        return {"rows": rows, "count": count, "next_cursor": next_cursor}

    # ── Mentions cursor ───────────────────────────────────────────────

    @staticmethod
    def _cursor_key(account_id: str = "") -> str:
        tenant = x_tenant_id()
        if account_id:
            if not account_id.isascii() or not account_id.isdigit():
                raise ValueError("Mentions cursor requires a verified numeric X account ID.")
            return "account:" + json.dumps([tenant, account_id], separators=(",", ":"))
        return "mentions" if tenant == "default" else "mentions:" + tenant

    def get_since_id(self, *, account_id: str = "") -> str:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT since_id FROM x_mentions_cursor WHERE k = ?",
                    (self._cursor_key(account_id),),
                ).fetchone()
                return str(row["since_id"]) if row else ""
            finally:
                if conn.in_transaction:
                    conn.rollback()
                conn.close()

    def set_since_id(self, since_id: str, *, account_id: str = "") -> None:
        sid = str(since_id or "").strip()
        if not sid:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """INSERT INTO x_mentions_cursor (k, since_id, updated_at)
                       VALUES (?, ?, ?)
                       ON CONFLICT(k) DO UPDATE SET since_id = excluded.since_id,
                                                    updated_at = excluded.updated_at""",
                    (self._cursor_key(account_id), sid, time.time()),
                )
                conn.commit()
            finally:
                if conn.in_transaction:
                    conn.rollback()
                conn.close()


def _mention_recency_key(rec: ReplyRecord) -> tuple[int, int]:
    """Sort key: larger is newer. Snowflake mentions beat manual roasts."""
    sid = str(rec.summon_id or "").strip()
    if sid.isdigit():
        return (1, int(sid))
    return (0, int((rec.created_at or 0.0) * 1000))


_store: XReplyStore | None = None
_store_lock = threading.Lock()


def get_reply_store() -> XReplyStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = XReplyStore()
    return _store


def reset_reply_store(db_path: str | Path | None = None) -> XReplyStore:
    """Test hook — rebind the singleton to a temp path."""
    global _store
    with _store_lock:
        _store = XReplyStore(db_path)
    return _store
