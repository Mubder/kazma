"""Immutable ordered thread manifests with atomic reservations and attended leases."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
import uuid
from typing import Any

from kazma_core.x_api.account_binding import credential_revision
from kazma_core.x_api.ownership import x_tenant_id
from kazma_core.x_api.publication_store import PublicationConflictError, get_publication_store, payload_hash

SCHEMA = """
CREATE TABLE IF NOT EXISTS x_threads (
 id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, account_id TEXT NOT NULL,
 credential_revision TEXT NOT NULL, intent_key TEXT NOT NULL,
 manifest TEXT NOT NULL, digest TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
 state TEXT NOT NULL DEFAULT 'review', token TEXT NOT NULL, expires_at REAL NOT NULL,
 owner TEXT NOT NULL DEFAULT '', lease_until REAL NOT NULL DEFAULT 0,
 created_at REAL NOT NULL, updated_at REAL NOT NULL, actor TEXT NOT NULL DEFAULT '',
 UNIQUE(tenant_id, account_id, intent_key)
);
CREATE TABLE IF NOT EXISTS x_thread_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, thread_id TEXT NOT NULL,
 revision INTEGER NOT NULL, at REAL NOT NULL, event TEXT NOT NULL, actor TEXT NOT NULL,
 manifest_hash TEXT NOT NULL
);
"""


def _event(conn: Any, ident: str, event: str, actor: str) -> None:
    row = conn.execute("SELECT * FROM x_threads WHERE id = ?", (ident,)).fetchone()
    conn.execute("INSERT INTO x_thread_events (tenant_id, thread_id, revision, at, event, actor, manifest_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (row["tenant_id"], ident, row["revision"], time.time(), event, actor, row["digest"]))


def _manifest_digest(segments: list[str]) -> str:
    return hashlib.sha256(json.dumps(segments, ensure_ascii=False).encode()).hexdigest()


def stage_thread(segments: list[str], *, intent_key: str, cfg: Any, actor: str = "operator") -> dict[str, Any]:
    """Reserve every segment in one transaction or leave no thread/commitment."""
    store, tenant, now = get_publication_store(), x_tenant_id(), time.time()
    digest = _manifest_digest(segments)
    with store._connection(transaction=True) as conn:
        prior = conn.execute("SELECT * FROM x_threads WHERE tenant_id = ? AND account_id = ? AND intent_key = ?",
                             (tenant, cfg.account_id, intent_key)).fetchone()
        if prior:
            if prior["digest"] != digest:
                raise PublicationConflictError("The thread intent already names different ordered text.")
            ident = prior["id"]
        else:
            ident = uuid.uuid4().hex
            operations = []
            for index, text in enumerate(segments):
                row = store.reserve(tenant_id=tenant, account_id=cfg.account_id,
                    credential_revision=credential_revision(cfg.credentials), idempotency_key=f"thread:{ident}:{index}",
                    text=text, reply_to_id="", due_at=now, max_day=cfg.max_posts_per_day,
                    max_month=cfg.max_posts_per_month, duplicate_days=cfg.duplicate_window_days,
                    origin="thread", origin_ref=ident, state="awaiting_approval",
                    metadata={"thread_id": ident, "segment": index, "handle": cfg.handle}, _conn=conn)
                operations.append({"text": text, "operation_id": row["id"]})
            conn.execute("INSERT INTO x_threads (id, tenant_id, account_id, credential_revision, intent_key, manifest, digest, token, expires_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (ident, tenant, cfg.account_id, credential_revision(cfg.credentials), intent_key,
                          json.dumps(operations, ensure_ascii=False), digest, secrets.token_hex(32), now + 86400, now, now))
            _event(conn, ident, "staged", actor)
    return thread_detail(ident)


def thread_detail(ident: str) -> dict[str, Any]:
    store, tenant = get_publication_store(), x_tenant_id()
    with store._connection() as conn:
        row = conn.execute("SELECT * FROM x_threads WHERE id = ? AND tenant_id = ?", (ident, tenant)).fetchone()
        if row is None:
            raise ValueError("Unknown thread.")
        segments = []
        for index, item in enumerate(json.loads(row["manifest"])):
            op = conn.execute("SELECT * FROM x_operations WHERE id = ? AND tenant_id = ?", (item["operation_id"], tenant)).fetchone()
            if op is None:
                raise ValueError("Thread operation is missing; restore and inspect before proceeding.")
            segments.append({"index": index, **item, **{key: op[key] for key in ("state", "tweet_id", "reply_to_id", "outcome", "reason")}})
        unknown = any(item["state"] in ("sending", "outcome_unknown") for item in segments)
        finished = all(item["state"] == "published" for item in segments)
        history = [dict(event) for event in conn.execute("SELECT revision, at, event, actor, manifest_hash FROM x_thread_events WHERE thread_id = ? AND tenant_id = ? ORDER BY id DESC LIMIT 50", (ident, tenant))]
        return {"id": ident, "revision": row["revision"], "account_id": row["account_id"],
                "state": "published" if finished else ("outcome_unknown" if unknown else row["state"]),
                "segments": segments, "published_count": sum(item["state"] == "published" for item in segments),
                "approval_token": row["token"], "expires_at": row["expires_at"],
                "can_resume": not unknown and not finished and row["state"] not in ("running", "cancelled"),
                "can_cancel": bool(row["token"]) and not finished and row["state"] not in ("running", "cancelled"),
                "can_review": not finished and row["state"] != "cancelled" and row["lease_until"] < time.time(), "history": history}


def thread_page(*, cursor: str = "", limit: int = 25) -> dict[str, Any]:
    """Tenant-scoped keyset traversal keeps older recovery manifests reachable."""
    from kazma_core.db.keyset import decode_cursor, encode_cursor

    boundary = decode_cursor(cursor, size=2)
    store, tenant = get_publication_store(), x_tenant_id()
    limit = max(1, min(limit, 100))
    clause, params = "", [tenant]
    if boundary:
        clause = " AND (created_at < ? OR (created_at = ? AND id < ?))"
        params.extend([boundary[0], boundary[0], boundary[1]])
    with store._connection() as conn:
        rows = conn.execute("SELECT id, created_at FROM x_threads WHERE tenant_id = ?" + clause
                            + " ORDER BY created_at DESC, id DESC LIMIT ?", (*params, limit + 1)).fetchall()
    more, rows = len(rows) > limit, rows[:limit]
    return {"threads": [thread_detail(row["id"]) for row in rows],
            "next_cursor": encode_cursor((rows[-1]["created_at"], rows[-1]["id"])) if more else ""}


def _claim_thread(ident: str, *, revision: int, token: str, cfg: Any, actor: str) -> str:
    store, tenant, now = get_publication_store(), x_tenant_id(), time.time()
    owner = uuid.uuid4().hex
    with store._connection(transaction=True) as conn:
        row = conn.execute("SELECT * FROM x_threads WHERE id = ? AND tenant_id = ?", (ident, tenant)).fetchone()
        if (not token or row is None or row["revision"] != revision or not secrets.compare_digest(row["token"], token)
                or row["expires_at"] < now or row["state"] in ("published", "cancelled")
                or row["lease_until"] > now):
            raise PublicationConflictError("Thread approval is stale, expired or already claimed. Reload and review.")
        if row["account_id"] != cfg.account_id or row["credential_revision"] != credential_revision(cfg.credentials):
            raise PublicationConflictError("Thread account or credentials changed. Review under its original account.")
        conn.execute("UPDATE x_threads SET state = 'running', revision = revision + 1, token = '', owner = ?, lease_until = ?, actor = ?, updated_at = ? WHERE id = ?",
                     (owner, now + 120, actor, now, ident))
        _event(conn, ident, "approved_remaining", actor)
    return owner


def _activate_segment(ident: str, index: int, *, owner: str) -> str | None:
    """Only confirmed predecessors can supply a target; unknowns cannot activate."""
    store, tenant, now = get_publication_store(), x_tenant_id(), time.time()
    with store._connection(transaction=True) as conn:
        thread = conn.execute("SELECT * FROM x_threads WHERE id = ? AND tenant_id = ? AND owner = ? AND state = 'running' AND lease_until > ?",
                              (ident, tenant, owner, now)).fetchone()
        if thread is None:
            raise PublicationConflictError("Thread execution lease ended; review before resuming.")
        manifest = json.loads(thread["manifest"])
        item = manifest[index]
        op = conn.execute("SELECT * FROM x_operations WHERE id = ?", (item["operation_id"],)).fetchone()
        if op["state"] == "published":
            return None
        if op["state"] not in ("awaiting_approval", "failed_permanent") or (op["state"] == "failed_permanent" and op["outcome"] not in ("not_sent", "rejected")):
            raise PublicationConflictError("A segment may have been sent; reconcile its exact receipt before resuming.")
        target = ""
        if index:
            previous = conn.execute("SELECT state, tweet_id FROM x_operations WHERE id = ?", (manifest[index - 1]["operation_id"],)).fetchone()
            if previous["state"] != "published" or not previous["tweet_id"]:
                raise PublicationConflictError("The preceding segment has no confirmed remote result.")
            target = previous["tweet_id"]
        conn.execute("UPDATE x_operations SET state = 'scheduled', reply_to_id = ?, payload_hash = ?, due_at = ?, version = version + 1, updated_at = ? WHERE id = ?",
                     (target, payload_hash(item["text"], target), now, now, item["operation_id"]))
        conn.execute("UPDATE x_threads SET lease_until = ?, updated_at = ? WHERE id = ?", (now + 120, now, ident))
        store._event(conn, conn.execute("SELECT * FROM x_operations WHERE id = ?", (item["operation_id"],)).fetchone(), {"action": "thread_segment_approved"})
        return item["operation_id"]


def _finish_thread(ident: str, *, owner: str) -> None:
    store, tenant, now = get_publication_store(), x_tenant_id(), time.time()
    with store._connection(transaction=True) as conn:
        thread = conn.execute("SELECT * FROM x_threads WHERE id = ? AND tenant_id = ? AND owner = ?", (ident, tenant, owner)).fetchone()
        if thread is None:
            return
        operations = [conn.execute("SELECT state FROM x_operations WHERE id = ?", (item["operation_id"],)).fetchone()[0]
                      for item in json.loads(thread["manifest"])]
        state = "published" if all(value == "published" for value in operations) else "partial"
        conn.execute("UPDATE x_threads SET state = ?, revision = revision + 1, token = ?, expires_at = ?, owner = '', lease_until = 0, updated_at = ? WHERE id = ?",
                     (state, secrets.token_hex(32), now + 86400, now, ident))
        _event(conn, ident, state, thread["actor"])
        from kazma_core.x_api.notifications import enqueue

        enqueue(conn, tenant=tenant, key=f"thread:{ident}:{thread['revision'] + 1}",
                message=f"X thread {state}. Review ordered segment outcomes in X Studio. Thread: {ident}")


def cancel_thread(ident: str, *, revision: int, token: str, actor: str = "operator") -> dict[str, Any]:
    """Cancel unsent segments atomically; preserve all possibly/actually sent posts."""
    store, tenant, now = get_publication_store(), x_tenant_id(), time.time()
    with store._connection(transaction=True) as conn:
        thread = conn.execute("SELECT * FROM x_threads WHERE id = ? AND tenant_id = ?", (ident, tenant)).fetchone()
        if (not token or thread is None or thread["revision"] != revision or not secrets.compare_digest(thread["token"], token)
                or thread["state"] in ("running", "published", "cancelled")):
            raise PublicationConflictError("Thread changed or is running; reload before cancelling.")
        for item in json.loads(thread["manifest"]):
            changed = conn.execute("UPDATE x_operations SET state = 'cancelled', version = version + 1, updated_at = ? WHERE id = ? AND state IN ('awaiting_approval', 'failed_permanent', 'scheduled', 'deferred')",
                         (now, item["operation_id"]))
            if changed.rowcount:
                store._event(conn, conn.execute("SELECT * FROM x_operations WHERE id = ?", (item["operation_id"],)).fetchone(), {"action": "thread_cancelled", "actor": actor})
        conn.execute("UPDATE x_threads SET state = 'cancelled', revision = revision + 1, token = '', updated_at = ? WHERE id = ?", (now, ident))
        _event(conn, ident, "cancelled_remaining", actor)
    return thread_detail(ident)


def review_thread(ident: str, *, revision: int, actor: str = "operator") -> dict[str, Any]:
    """Renew attended review after expiry/crash without granting execution."""
    store, tenant, now = get_publication_store(), x_tenant_id(), time.time()
    with store._connection(transaction=True) as conn:
        thread = conn.execute("SELECT * FROM x_threads WHERE id = ? AND tenant_id = ?", (ident, tenant)).fetchone()
        if (thread is None or thread["revision"] != revision or thread["lease_until"] > now
                or thread["state"] in ("published", "cancelled")):
            raise PublicationConflictError("Thread is active or changed; reload before review.")
        for item in json.loads(thread["manifest"]):
            op = conn.execute("SELECT * FROM x_operations WHERE id = ?", (item["operation_id"],)).fetchone()
            if op["state"] == "sending" and (op["lease_until"] or 0) > now:
                raise PublicationConflictError("A segment send is still in flight; wait for its outcome.")
            if op["state"] in ("scheduled", "deferred", "sending"):
                state = "outcome_unknown" if op["state"] == "sending" else "awaiting_approval"
                conn.execute("UPDATE x_operations SET state = ?, version = version + 1, updated_at = ? WHERE id = ?", (state, now, op["id"]))
                store._event(conn, conn.execute("SELECT * FROM x_operations WHERE id = ?", (op["id"],)).fetchone(), {"action": "thread_recovery_review"})
        conn.execute("UPDATE x_threads SET state = 'review', revision = revision + 1, token = ?, expires_at = ?, owner = '', lease_until = 0, updated_at = ? WHERE id = ?",
                     (secrets.token_hex(32), now + 86400, now, ident))
        _event(conn, ident, "review_refreshed", actor)
    return thread_detail(ident)
