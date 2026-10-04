"""Attended ordered threads; every segment uses the normal publication service."""

from __future__ import annotations

import asyncio
import json
import time
from contextvars import ContextVar
from typing import Any

from kazma_core.x_api import publication_service as publications
from kazma_core.x_api import thread_store
from kazma_core.x_api.ownership import x_tenant_id
from kazma_core.x_api.publication_store import PublicationConflictError, get_publication_store

_execution: ContextVar[tuple[str, str] | None] = ContextVar("x_thread_execution", default=None)


def thread_dispatch_allowed(row: dict[str, Any]) -> bool:
    """A generic publication caller cannot send a reserved thread segment."""
    scoped = _execution.get()
    if not scoped or row["origin_ref"] != scoped[0]:
        return False
    store = get_publication_store()
    with store._connection() as conn:
        thread = conn.execute("SELECT * FROM x_threads WHERE id = ? AND tenant_id = ? AND owner = ? AND state = 'running' AND lease_until > ?",
                              (scoped[0], x_tenant_id(), scoped[1], time.time())).fetchone()
        if thread is None:
            return False
        manifest = json.loads(thread["manifest"])
        if thread_store._manifest_digest([item["text"] for item in manifest]) != thread["digest"]:
            return False
        index = next((index for index, item in enumerate(manifest) if item["operation_id"] == row["id"] and item["text"] == row["text"]), None)
        if index is None or row["account_id"] != thread["account_id"] or row["credential_revision"] != thread["credential_revision"]:
            return False
        if index == 0:
            return not row["reply_to_id"]
        prior = conn.execute("SELECT state, tweet_id FROM x_operations WHERE id = ?", (manifest[index - 1]["operation_id"],)).fetchone()
        return prior["state"] == "published" and bool(prior["tweet_id"]) and row["reply_to_id"] == prior["tweet_id"]


def prepare_thread(segments: list[str], *, intent_key: str, actor: str = "operator") -> dict[str, Any]:
    from kazma_core.x_api.policy import evaluate_post

    if not isinstance(segments, list) or not 2 <= len(segments) <= 25 or not intent_key or len(intent_key) > 200:
        raise ValueError("A thread needs 2–25 ordered segments and a bounded intent key.")
    cfg = publications._config()
    if not cfg.account_id:
        raise ValueError("Verify the connected account in Settings → X before reviewing threads.")
    for segment in segments:
        if not isinstance(segment, str) or not segment.strip() or len(segment) > 32000:
            raise ValueError("Every segment needs bounded nonempty text.")
        decision = evaluate_post(segment, cfg=cfg, check_history=False)
        if not decision.allow:
            raise ValueError(decision.reason)
    publications._backfill(cfg)
    return thread_store.stage_thread([segment.strip() for segment in segments], intent_key=intent_key, cfg=cfg, actor=actor)


async def publish_thread(ident: str, *, revision: int, token: str, actor: str) -> dict[str, Any]:
    cfg = await asyncio.to_thread(publications._config)
    if not cfg.can_post():
        raise ValueError("X publishing is disabled or paused. Review the connector before sending.")
    detail = await asyncio.to_thread(thread_store.thread_detail, ident)
    if detail["state"] == "outcome_unknown":
        raise PublicationConflictError("A segment outcome is unknown. Reconcile its exact receipt; resending is blocked.")
    owner = await asyncio.to_thread(thread_store._claim_thread, ident, revision=revision, token=token, cfg=cfg, actor=actor)
    scoped = _execution.set((ident, owner))
    try:
        for index in range(len(detail["segments"])):
            operation = await asyncio.to_thread(thread_store._activate_segment, ident, index, owner=owner)
            if operation is None:
                continue
            ok, _ = await publications._dispatch_operation(operation, cfg=cfg)
            # A returned remote success whose DB confirmation failed still has
            # no durable predecessor: stop rather than threading from inference.
            stored = await asyncio.to_thread(get_publication_store().get, operation, tenant_id=x_tenant_id())
            if not ok or stored["state"] != "published":
                break
    finally:
        _execution.reset(scoped)
        await asyncio.shield(asyncio.to_thread(thread_store._finish_thread, ident, owner=owner))
    return await asyncio.to_thread(thread_store.thread_detail, ident)
