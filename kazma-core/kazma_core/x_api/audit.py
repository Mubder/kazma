"""X integration audit log — every output, full content, timestamped.

Operator decision 2026-08-27: all X (Twitter) activity must leave an
immutable audit trail — one row per API call with the local date/time,
action, endpoint, HTTP status, the FULL request payload and the FULL
response body (success AND error). Lives in ``kazma-data/x_audit.db``
(SQLite WAL), deliberately separate from the post ledger
(``x_posts.db`` — quota/dedup control flow): the audit log is append-only
truth and is never consulted to gate a request.

Design notes:
  * ``log_x_event`` is best-effort and NEVER raises — an audit failure must
    not break or block the X call it is recording (mirrors the lifecycle
    notifier / proxy provider posture).
  * One short-lived connection per write (WAL + busy_timeout) so background
    posts never contend with UI reads.
  * No automatic pruning: an audit log is kept whole. Operators can purge
    manually via ``purge_x_audit(older_than_days=...)`` if ever needed.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from kazma_core.config_store import apply_sqlite_pragmas

logger = logging.getLogger(__name__)

__all__ = [
    "XAuditLog",
    "enrich_row",
    "get_x_audit",
    "log_x_event",
    "query_x_audit",
    "purge_x_audit",
    "reset_x_audit",
]

_CREATE = """
CREATE TABLE IF NOT EXISTS x_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    action TEXT NOT NULL,
    method TEXT NOT NULL DEFAULT '',
    endpoint TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'success',
    http_status INTEGER,
    tweet_id TEXT,
    request_body TEXT,
    response_body TEXT,
    duration_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_x_audit_ts ON x_audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_x_audit_action ON x_audit_log(action);
"""


def _dumps(payload: Any) -> str:
    """Full-fidelity JSON text — no truncation, no secret stripping (the
    payload never contains tokens; OAuth lives in headers, not the body)."""
    try:
        return json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        return str(payload)


def _loads(raw: Any) -> dict:
    """Parse a stored JSON body into a dict; returns {} on any failure."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def enrich_row(row: dict[str, Any]) -> dict[str, Any]:
    """Derive human-readable fields from the stored JSON bodies.

    Adds (never removes) keys so the audit table can show the actual post
    text instead of a raw JSON hover blob:

      * ``text``        — the post body (request ``text``, falling back to the
                          ``data.text`` X echoes on success; for ``delete`` rows
                          the removed post's text is pulled from the ledger).
      * ``reply_to``    — ``reply.in_reply_to_tweet_id`` (thread context).
      * ``post_url``    — ``https://x.com/i/web/status/{tweet_id}`` when known.
      * ``error_detail``— short message extracted from error responses.

    Best-effort and never raises — an enrichment failure must not break the
    audit feed (mirrors the never-raises posture of ``log_x_event``). The raw
    ``request_body``/``response_body`` columns stay in the row untouched.
    """
    out = dict(row)
    try:
        req = _loads(row.get("request_body"))
        resp = _loads(row.get("response_body"))
        action = str(row.get("action") or "")
        tweet_id = str(row.get("tweet_id") or "").strip()

        text = ""
        if isinstance(req.get("text"), str) and req["text"].strip():
            text = req["text"]

        reply_to = ""
        reply = req.get("reply")
        if isinstance(reply, dict):
            reply_to = str(reply.get("in_reply_to_tweet_id") or "")

        # X echoes the created post in the success payload — use it as a
        # fallback source of truth for the text (and the id, if missing).
        data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
        if not text and isinstance(data.get("text"), str) and data["text"].strip():
            text = data["text"]
        if not tweet_id and data.get("id"):
            tweet_id = str(data["id"])

        # Delete rows carry only the id; recover the removed text from the ledger.
        if action == "delete" and not text and tweet_id:
            try:
                from kazma_core.x_api.ledger import get_ledger

                text = get_ledger().text_for_tweet(tweet_id) or ""
            except Exception:
                text = ""

        error_detail = ""
        if str(row.get("status") or "") != "success":
            detail = resp.get("detail") or resp.get("error") or ""
            if isinstance(detail, str):
                error_detail = detail[:300]

        out["text"] = text
        out["reply_to"] = reply_to
        out["error_detail"] = error_detail
        out["post_url"] = f"https://x.com/i/web/status/{tweet_id}" if tweet_id else ""
        if tweet_id:
            out["tweet_id"] = tweet_id
    except Exception:  # noqa: BLE001
        # Enrichment is cosmetic — never let it break the audit feed.
        pass
    return out


class XAuditLog:
    """Append-only SQLite audit store for the X integration."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            from kazma_core.paths import data_dir

            db_path = data_dir() / "x_audit.db"
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
                conn.commit()
            finally:
                conn.close()

    def log(
        self,
        *,
        action: str,
        method: str = "",
        endpoint: str = "",
        status: str = "success",
        http_status: int | None = None,
        tweet_id: str | None = None,
        request_body: Any = None,
        response_body: Any = None,
        duration_ms: int | None = None,
        ts: str | None = None,
    ) -> int | None:
        """Append one audit row. Returns the row id (or None on failure).

        ``ts`` defaults to the LOCAL time as a human-readable ISO 8601
        date-and-time with timezone offset (e.g. ``2026-08-27T14:03:21+03:00``).
        """
        row_ts = ts or datetime.now().astimezone().isoformat(timespec="milliseconds")
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "INSERT INTO x_audit_log "
                    "(ts, action, method, endpoint, status, http_status, tweet_id, "
                    " request_body, response_body, duration_ms) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        row_ts,
                        action,
                        method,
                        endpoint,
                        status,
                        http_status,
                        tweet_id,
                        _dumps(request_body) if request_body is not None else None,
                        _dumps(response_body) if response_body is not None else None,
                        duration_ms,
                    ),
                )
                conn.commit()
                return int(cur.lastrowid) if cur.lastrowid else None
            finally:
                conn.close()

    def query(
        self,
        *,
        limit: int = 100,
        action: str | None = None,
        status: str | None = None,
        tweet_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Newest-first audit rows (dicts) for surfacing in tooling/UI."""
        sql = "SELECT * FROM x_audit_log"
        clauses: list[str] = []
        params: list[Any] = []
        if action:
            clauses.append("action = ?")
            params.append(action)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if tweet_id:
            clauses.append("tweet_id = ?")
            params.append(tweet_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(max(1, min(limit, 5000))))
        conn = self._connect()
        try:
            return [enrich_row(dict(r)) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def purge(self, *, older_than_days: int) -> int:
        """Manual retention helper — deletes rows older than N days."""
        cutoff = (datetime.now().astimezone() - timedelta(days=older_than_days)).isoformat(timespec="milliseconds")
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute("DELETE FROM x_audit_log WHERE ts < ?", (cutoff,))
                conn.commit()
                return cur.rowcount or 0
            finally:
                conn.close()


# ── Module-level singleton — call sites stay one-liners ────────────────

_audit: XAuditLog | None = None
_audit_lock = threading.Lock()


def get_x_audit() -> XAuditLog:
    global _audit
    if _audit is None:
        with _audit_lock:
            if _audit is None:
                _audit = XAuditLog()
    return _audit


def reset_x_audit(db_path: str | Path | None = None) -> XAuditLog:
    """(Re)create the singleton — test isolation helper."""
    global _audit
    with _audit_lock:
        _audit = XAuditLog(db_path)
    return _audit


def log_x_event(
    *,
    action: str,
    method: str = "",
    endpoint: str = "",
    status: str = "success",
    http_status: int | None = None,
    tweet_id: str | None = None,
    request_body: Any = None,
    response_body: Any = None,
    duration_ms: int | None = None,
) -> None:
    """Best-effort audit append from the X client — never raises."""
    try:
        get_x_audit().log(
            action=action,
            method=method,
            endpoint=endpoint,
            status=status,
            http_status=http_status,
            tweet_id=tweet_id,
            request_body=request_body,
            response_body=response_body,
            duration_ms=duration_ms,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[x-audit] failed to record %s: %s", action, exc)


def query_x_audit(**kwargs: Any) -> list[dict[str, Any]]:
    return get_x_audit().query(**kwargs)


def purge_x_audit(*, older_than_days: int) -> int:
    return get_x_audit().purge(older_than_days=older_than_days)
