"""SQLite sandbox mailbox backend.

Each space has its own practice mailbox (Spaces S1-17): every row names its
space (``tenant_id``, the owner's ``default`` for a mailbox from before), the
key is the space and the id (the seed's ids are in every space), and every
statement names the space the mailbox was opened for -- the owner's while
Spaces is not enforced, as before, else the bound one
(``connected_accounts.connected_space``). One mailbox for every space was one
Sent folder member A's ``email_send`` wrote and member B's ``email_list``
read.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kazma_skills.native.email_manager.models import (
    CategorizeRequest,
    EmailMessage,
    ListQuery,
    SendRequest,
    SendResult,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    tenant_id TEXT NOT NULL DEFAULT 'default',
    id TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    from_addr TEXT NOT NULL DEFAULT '',
    to_addrs TEXT NOT NULL DEFAULT '[]',
    cc_addrs TEXT NOT NULL DEFAULT '[]',
    date TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    snippet TEXT NOT NULL DEFAULT '',
    unread INTEGER NOT NULL DEFAULT 0,
    starred INTEGER NOT NULL DEFAULT 0,
    labels TEXT NOT NULL DEFAULT '[]',
    folder TEXT NOT NULL DEFAULT 'INBOX',
    thread_id TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (tenant_id, id)
);
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_email_space_folder ON messages(tenant_id, folder)",
    "CREATE INDEX IF NOT EXISTS idx_email_space_unread ON messages(tenant_id, unread)",
)


def _default_db_path() -> Path:
    # No CWD-relative fallback: `Path("kazma-data")` wrote a second mailbox
    # wherever the process happened to start (the class store_registry's
    # CWD gate exists for). kazma-core is a hard dependency of this package.
    from kazma_core.paths import data_dir

    return data_dir() / "sandbox_emails.db"


def _seed_path() -> Path:
    return Path(__file__).resolve().parent.parent / "seed" / "sandbox_seed.json"


def _mailbox_space() -> str:
    """The space a mailbox opened now is for: the owner's while Spaces is
    not enforced, else the bound one (``NoSpaceBound`` with none: no
    practice mailbox is opened for no one)."""
    from kazma_core.spaces.connected_accounts import connected_space

    return connected_space()


#: The mailboxes this process has made ready, by file and space: the schema
#: step (a write transaction) runs once each, not at every construction --
#: the router makes a backend for every call.
_READY: set[tuple[str, str]] = set()
_READY_LOCK = threading.Lock()


class SandboxBackend:
    name = "sandbox"

    def __init__(self, db_path: str | Path | None = None, *, space: str | None = None) -> None:
        self.space = space if space is not None else _mailbox_space()
        self.db_path = Path(db_path) if db_path else _default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        ready = (str(self.db_path.resolve()), self.space)
        with _READY_LOCK:
            if ready in _READY:
                return
        self._init_db()
        with _READY_LOCK:
            _READY.add(ready)

    def _connect(self) -> AbstractContextManager[sqlite3.Connection]:
        """One short-lived connection that commits AND closes on exit."""
        from kazma_core.db.sqlite_session import committed_and_closed

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return committed_and_closed(conn)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            columns = [row["name"] for row in conn.execute("PRAGMA table_info(messages)")]
            if columns and "tenant_id" not in columns:
                _key_by_space(conn)
            else:
                conn.execute(_SCHEMA)
            for statement in _INDEXES:
                conn.execute(statement)
            count = conn.execute("SELECT COUNT(*) AS c FROM messages WHERE tenant_id = ?",
                                 (self.space,)).fetchone()["c"]
            if count == 0:
                self._seed(conn)

    def _seed(self, conn: sqlite3.Connection) -> None:
        seed_file = _seed_path()
        if not seed_file.is_file():
            return
        data = json.loads(seed_file.read_text(encoding="utf-8"))
        for m in data.get("messages", []):
            conn.execute(
                """
                INSERT OR REPLACE INTO messages
                (tenant_id, id, subject, from_addr, to_addrs, cc_addrs, date, body, snippet,
                 unread, starred, labels, folder, thread_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    self.space,
                    m["id"],
                    m.get("subject", ""),
                    m.get("from_addr", ""),
                    json.dumps(m.get("to_addrs", [])),
                    json.dumps(m.get("cc_addrs", [])),
                    m.get("date", ""),
                    m.get("body", ""),
                    m.get("snippet", ""),
                    1 if m.get("unread") else 0,
                    1 if m.get("starred") else 0,
                    json.dumps(m.get("labels", [])),
                    m.get("folder", "INBOX"),
                    m.get("thread_id", ""),
                ),
            )
        conn.commit()

    def _row_to_msg(self, row: sqlite3.Row) -> EmailMessage:
        return EmailMessage(
            id=row["id"],
            subject=row["subject"],
            from_addr=row["from_addr"],
            to_addrs=json.loads(row["to_addrs"] or "[]"),
            cc_addrs=json.loads(row["cc_addrs"] or "[]"),
            date=row["date"],
            body=row["body"],
            snippet=row["snippet"],
            unread=bool(row["unread"]),
            starred=bool(row["starred"]),
            labels=json.loads(row["labels"] or "[]"),
            folder=row["folder"],
            thread_id=row["thread_id"] or "",
            provider="sandbox",
        )

    async def list_messages(self, query: ListQuery) -> list[EmailMessage]:
        folder = (query.folder or "INBOX").strip() or "INBOX"
        # Normalize common names
        if folder.lower() == "inbox":
            folder = "INBOX"
        limit = max(1, min(50, int(query.limit or 20)))
        offset = max(0, int(query.offset or 0))
        clauses = ["tenant_id = ?", "folder = ?"]
        params: list[Any] = [self.space, folder]
        if query.unread_only:
            clauses.append("unread = 1")
        if query.query:
            q = f"%{query.query.strip()}%"
            clauses.append(
                "(subject LIKE ? OR from_addr LIKE ? OR body LIKE ? OR snippet LIKE ?)"
            )
            params.extend([q, q, q, q])
        where = " AND ".join(clauses)
        sql = (
            f"SELECT * FROM messages WHERE {where} "
            f"ORDER BY date DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_msg(r) for r in rows]

    async def get_message(self, message_id: str) -> EmailMessage:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM messages WHERE tenant_id = ? AND id = ?", (self.space, message_id)
            ).fetchone()
        if not row:
            raise KeyError(f"Message not found: {message_id}")
        return self._row_to_msg(row)

    async def send(self, req: SendRequest) -> SendResult:
        mid = req.client_request_id or f"sbx-{uuid.uuid4().hex[:10]}"
        now = datetime.now(UTC).isoformat()
        folder = "Drafts" if req.action == "draft" else "Sent"
        labels = ["Drafts"] if req.action == "draft" else ["Sent"]
        subject = req.subject
        body = req.body
        if req.action == "reply" and req.message_id:
            try:
                orig = await self.get_message(req.message_id)
                if not subject.lower().startswith("re:"):
                    subject = f"Re: {orig.subject}"
                body = f"{req.body}\n\n--- Original ---\nFrom: {orig.from_addr}\n{orig.body}"
            except KeyError:
                pass
        if req.action == "forward" and req.message_id:
            try:
                orig = await self.get_message(req.message_id)
                if not subject.lower().startswith("fwd:"):
                    subject = f"Fwd: {orig.subject}"
                body = f"{req.body}\n\n--- Forwarded ---\nFrom: {orig.from_addr}\n{orig.body}"
            except KeyError:
                pass
        to_list = req.to if isinstance(req.to, list) else [req.to]
        snippet = (body or "")[:80].replace("\n", " ")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO messages
                (tenant_id, id, subject, from_addr, to_addrs, cc_addrs, date, body, snippet,
                 unread, starred, labels, folder, thread_id)
                VALUES (?,?,?,?,?,?,?,?,?,0,0,?,?,?)
                """,
                (
                    self.space,
                    mid,
                    subject,
                    "you@example.com",
                    json.dumps(to_list),
                    json.dumps(req.cc or []),
                    now,
                    body,
                    snippet,
                    json.dumps(labels),
                    folder,
                    req.message_id or mid,
                ),
            )
            conn.commit()
        return SendResult(
            ok=True,
            message_id=mid,
            detail=f"Sandbox {'draft saved' if req.action == 'draft' else 'sent'} to {', '.join(to_list)}",
            draft=(req.action == "draft"),
        )

    async def delete(self, message_id: str, permanent: bool = False) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM messages WHERE tenant_id = ? AND id = ?", (self.space, message_id)
            ).fetchone()
            if not row:
                raise KeyError(f"Message not found: {message_id}")
            if permanent:
                conn.execute("DELETE FROM messages WHERE tenant_id = ? AND id = ?", (self.space, message_id))
            else:
                labels = json.loads(row["labels"] or "[]")
                if "Trash" not in labels:
                    labels.append("Trash")
                conn.execute(
                    "UPDATE messages SET folder = 'Trash', labels = ? WHERE tenant_id = ? AND id = ?",
                    (json.dumps(labels), self.space, message_id),
                )
            conn.commit()

    async def categorize(self, req: CategorizeRequest) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM messages WHERE tenant_id = ? AND id = ?", (self.space, req.message_id)
            ).fetchone()
            if not row:
                raise KeyError(f"Message not found: {req.message_id}")
            labels = set(json.loads(row["labels"] or "[]"))
            unread = row["unread"]
            starred = row["starred"]
            folder = row["folder"]
            if req.mark_read is True:
                unread = 0
            elif req.mark_read is False:
                unread = 1
            if req.star is True:
                starred = 1
                labels.add("Important")
            elif req.star is False:
                starred = 0
                labels.discard("Important")
            for lab in req.add_labels or []:
                labels.add(lab)
            for lab in req.remove_labels or []:
                labels.discard(lab)
            if req.move_to_folder:
                folder = req.move_to_folder
                labels.add(req.move_to_folder)
            conn.execute(
                "UPDATE messages SET unread=?, starred=?, labels=?, folder=? WHERE tenant_id=? AND id=?",
                (unread, starred, json.dumps(sorted(labels)), folder, self.space, req.message_id),
            )
            conn.commit()


def _key_by_space(conn: sqlite3.Connection) -> None:
    """A mailbox from before spaces (``id`` its key, no space): rebuilt, in
    the caller's write transaction, keyed by space and id, every message the
    owner's -- the one mailbox there was."""
    conn.execute(_SCHEMA.replace("CREATE TABLE IF NOT EXISTS messages", "CREATE TABLE messages_by_space"))
    conn.execute(
        "INSERT INTO messages_by_space (tenant_id, id, subject, from_addr, to_addrs, cc_addrs, date, body, "
        "snippet, unread, starred, labels, folder, thread_id) "
        "SELECT 'default', id, subject, from_addr, to_addrs, cc_addrs, date, body, "
        "snippet, unread, starred, labels, folder, thread_id FROM messages"
    )
    conn.execute("DROP TABLE messages")
    conn.execute("ALTER TABLE messages_by_space RENAME TO messages")
