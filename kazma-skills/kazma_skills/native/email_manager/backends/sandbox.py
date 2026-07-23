"""SQLite sandbox mailbox backend."""

from __future__ import annotations

import json
import sqlite3
import uuid
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
    id TEXT PRIMARY KEY,
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
    thread_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_email_folder ON messages(folder);
CREATE INDEX IF NOT EXISTS idx_email_unread ON messages(unread);
"""


def _default_db_path() -> Path:
    try:
        from kazma_core.paths import data_dir

        return data_dir() / "sandbox_emails.db"
    except Exception:
        return Path("kazma-data") / "sandbox_emails.db"


def _seed_path() -> Path:
    return Path(__file__).resolve().parent.parent / "seed" / "sandbox_seed.json"


class SandboxBackend:
    name = "sandbox"

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else _default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            count = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
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
                (id, subject, from_addr, to_addrs, cc_addrs, date, body, snippet,
                 unread, starred, labels, folder, thread_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
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
        clauses = ["folder = ?"]
        params: list[Any] = [folder]
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
                "SELECT * FROM messages WHERE id = ?", (message_id,)
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
                (id, subject, from_addr, to_addrs, cc_addrs, date, body, snippet,
                 unread, starred, labels, folder, thread_id)
                VALUES (?,?,?,?,?,?,?,?,0,0,?,?,?)
                """,
                (
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
                "SELECT * FROM messages WHERE id = ?", (message_id,)
            ).fetchone()
            if not row:
                raise KeyError(f"Message not found: {message_id}")
            if permanent:
                conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
            else:
                labels = json.loads(row["labels"] or "[]")
                if "Trash" not in labels:
                    labels.append("Trash")
                conn.execute(
                    "UPDATE messages SET folder = 'Trash', labels = ? WHERE id = ?",
                    (json.dumps(labels), message_id),
                )
            conn.commit()

    async def categorize(self, req: CategorizeRequest) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM messages WHERE id = ?", (req.message_id,)
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
                "UPDATE messages SET unread=?, starred=?, labels=?, folder=? WHERE id=?",
                (unread, starred, json.dumps(sorted(labels)), folder, req.message_id),
            )
            conn.commit()
