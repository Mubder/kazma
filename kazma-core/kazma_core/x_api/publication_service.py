"""The single X network-write service, with durable ownership and repairable views."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import replace
from typing import Any

from kazma_core.x_api.account_binding import credential_revision, record_account
from kazma_core.x_api.client import XApiError
from kazma_core.x_api.config import XConfig
from kazma_core.x_api.ownership import x_tenant_id
from kazma_core.x_api.publication_store import (
    PublicationConflictError,
    PublicationPolicyError,
    get_publication_store,
)

logger = logging.getLogger(__name__)

#: A scheduled post that provably never left (connect refused or timed out)
#: is tried again after this long, up to this many attempts in all.
_NOT_SENT_RETRY_S = 60.0
_NOT_SENT_ATTEMPTS = 3

#: Finished, unpublished operations, delivered notices and done projections
#: older than this are pruned, at most once an hour (SCH-E13).
_RETENTION_DAYS = 90
_last_retention = 0.0


def _retain() -> dict[str, int]:
    """Retention for the publication store; blocking, at most hourly."""
    global _last_retention
    now = time.time()
    if now - _last_retention < 3600:
        return {}
    _last_retention = now
    try:
        pruned = get_publication_store().prune_terminal(older_than_days=_RETENTION_DAYS, now=now)
    except Exception:  # noqa: BLE001 — retention never stops the fire cycle
        logger.warning("[x-publication] retention failed", exc_info=True)
        return {}
    if any(pruned.values()):
        logger.info("[x-publication] pruned %s", pruned)
    return pruned


def operation_inventory(*, limit: int = 50, state: str = "", query: str = "", cursor: str = "") -> dict[str, Any]:
    """Read-only diagnostic contract; do not expose lease tokens or credential digests."""
    store, tenant = get_publication_store(), x_tenant_id()
    keys = ("id", "account_id", "kind", "origin", "origin_ref", "state", "version", "text", "reply_to_id",
            "due_at", "created_at", "updated_at", "published_at", "tweet_id", "attempts", "outcome", "reason")
    page = store.operation_page(tenant_id=tenant, limit=limit, state=state, query=query, cursor=cursor)
    rows = [{key: row[key] for key in keys} for row in page.pop("rows")]
    return {**page, "operations": rows, "publication": store.summary(tenant_id=tenant)}


def queue_inventory(*, limit: int = 50, cursor: str = "", include_finished: bool = False) -> dict[str, Any]:
    """The Studio renders operation truth, even if legacy projections are stale."""
    page = get_publication_store().queue_page(tenant_id=x_tenant_id(), limit=limit, cursor=cursor, include_finished=include_finished)
    cfg = _config()
    keys = ("id", "version", "account_id", "state", "text", "reply_to_id", "due_at", "created_at", "tweet_id", "attempts", "outcome", "reason")
    items = []
    for row in page.pop("rows"):
        bound = row["account_id"] == cfg.account_id and row["credential_revision"] == credential_revision(cfg.credentials)
        items.append({**{key: row[key] for key in keys},
                      "can_reschedule": bound and cfg.can_post() and row["state"] in ("scheduled", "deferred", "awaiting_approval"),
                      "can_cancel": row["state"] in ("scheduled", "deferred", "awaiting_approval"),
                      "needs_reconciliation": row["state"] in ("sending", "outcome_unknown")})
    return {**page, "items": items}


def update_schedule(ident: str, *, expected_version: int, action: str, due_at: float | None = None) -> bool:
    """An attended action binds the operation revision the operator reviewed."""
    store, tenant, cfg = get_publication_store(), x_tenant_id(), _config()
    row = store.get(ident, tenant_id=tenant)
    if row is None or row["origin"] != "schedule":
        return False
    if action == "cancel":
        changed = store.cancel(ident, tenant_id=tenant, expected_version=expected_version)
    elif action == "reschedule":
        if (not cfg.can_post() or not cfg.account_id or cfg.account_id != row["account_id"]
                or credential_revision(cfg.credentials) != row["credential_revision"]):
            raise PublicationPolicyError("Verify the original connected account before rescheduling this booking.")
        if due_at is None:
            raise ValueError("A future publication time is required.")
        changed = store.reschedule(ident, tenant_id=tenant, expected_version=expected_version,
                                   due_at=due_at, max_day=cfg.max_posts_per_day, max_month=cfg.max_posts_per_month)
    else:
        raise ValueError("Unsupported queue action.")
    if changed:
        _project_pending()
    return changed


def _config() -> XConfig:
    from kazma_core.x_api.config import get_x_config

    return get_x_config()


def _client(cfg: XConfig) -> Any:
    from kazma_core.x_api.client import XClient

    return XClient(cfg.credentials)


def _limits(cfg: XConfig) -> dict[str, int]:
    return {"max_day": cfg.max_posts_per_day, "max_month": cfg.max_posts_per_month,
            "duplicate_days": cfg.duplicate_window_days}


def _reply_limits() -> dict[str, int]:
    from kazma_core.x_api.stance import get_reply_config

    cfg = get_reply_config()
    return {"daily": cfg.max_replies_per_day, "target": cfg.max_replies_per_target_per_day,
            "cooldown": cfg.cooldown_per_thread_s}


def _backfill(cfg: XConfig) -> None:
    """Idempotent known-receipt import, before quota authority is used."""
    from kazma_core.x_api.ledger import get_ledger

    get_publication_store().import_history(get_ledger().publication_history(), tenant_id=x_tenant_id(),
                                          account_id=cfg.account_id, credential_revision=credential_revision(cfg.credentials))
    from kazma_core.x_api.reply_store import get_reply_store

    get_publication_store().import_reply_history(get_reply_store().publication_history(), tenant_id=x_tenant_id(), account_id=cfg.account_id)
    _migrate_legacy_schedule(cfg)


def _migrate_legacy_schedule(cfg: XConfig) -> None:
    from kazma_core.x_api.schedule import get_x_scheduled_store

    legacy = get_x_scheduled_store()
    rows = legacy.fence_legacy(tenant_id=x_tenant_id())
    if cfg.account_id:
        store = get_publication_store()
        for post in rows:
            store.import_legacy_schedule(post, account_id=cfg.account_id, credential_revision=credential_revision(cfg.credentials))
        if rows:
            _project_pending()


async def _verified_config(cfg: XConfig) -> XConfig:
    if cfg.account_id:
        return cfg
    # An attended immediate post may verify the currently supplied account.
    # A queued operation is never redirected this way: dispatch compares its binding.
    me = await _client(cfg).verify_credentials()
    ident = await asyncio.to_thread(record_account, cfg.credentials, me)
    return replace(cfg, account_id=ident)


def _reserve(cfg: XConfig, *, text: str, reply_to_id: str, due_at: float,
             idempotency_key: str, origin: str, origin_ref: str = "", kind: str = "post",
             metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    _backfill(cfg)
    metadata = dict(metadata or {})
    if metadata.get("summon_id"):
        from kazma_core.x_api.reply import reply_target_id
        from kazma_core.x_api.reply_store import get_reply_store
        from kazma_core.x_api.stance import get_reply_config

        rec = get_reply_store().get(metadata["summon_id"])
        if (rec is None or rec.status != "sending" or rec.draft_text != text
                or reply_target_id(rec.summon_id, rec.parent_id) != reply_to_id):
            raise PublicationPolicyError("Reply publication must bind a currently claimed stored draft.")
        from kazma_core.x_api.approval import binding_hold

        hold = binding_hold(rec.decision.get("approval_basis"))
        if hold:
            raise PublicationPolicyError(hold)
        metadata.update(reply_target=rec.target_handle, reply_conversation=rec.parent_id,
                        operator_summon=get_reply_config().is_trusted_summoner(rec.summoner))
    return get_publication_store().reserve(tenant_id=x_tenant_id(), account_id=cfg.account_id,
        credential_revision=credential_revision(cfg.credentials), idempotency_key=idempotency_key,
        text=text, reply_to_id=reply_to_id, due_at=due_at, origin=origin, origin_ref=origin_ref,
        kind=kind, metadata=metadata, reply_limits=_reply_limits() if metadata.get("summon_id") else {}, **_limits(cfg))


def _project_pending() -> dict[str, Any]:
    """Replay views only; projection repair never sends to X."""
    from kazma_core.agent.artifacts import get_artifact_store
    from kazma_core.x_api.ledger import get_ledger
    from kazma_core.x_api.reply_store import get_reply_store
    from kazma_core.x_api.schedule import get_x_scheduled_store

    store, tenant = get_publication_store(), x_tenant_id()
    repaired, failures = 0, []
    for entry in store.outbox(tenant_id=tenant):
        row = store.get(entry["operation_id"], tenant_id=tenant)
        if row is None:
            continue
        metadata = json.loads(row["metadata"])
        try:
            if row["origin"] == "schedule":
                get_x_scheduled_store().project_operation(row)
            if row["state"] == "published":
                if row["kind"] == "delete":
                    get_ledger().mark_deleted(row["tweet_id"])
                else:
                    get_ledger().record(tweet_id=row["tweet_id"], text=row["text"], handle=metadata.get("handle", ""))
                if metadata.get("summon_id"):
                    get_reply_store().mark_posted(metadata["summon_id"], tweet_id=row["tweet_id"], draft=row["text"])
            if metadata.get("proposal_id") and (row["state"] == "published" or row["origin"] == "schedule"):
                get_artifact_store().proposal_posted(metadata["proposal_id"], tenant_id=tenant,
                    via="x_studio_schedule" if row["origin"] == "schedule" else "x_studio_post",
                    used_ref=row["tweet_id"] or row["id"])
            if store.finish_projection(entry["id"], tenant_id=tenant, expected_version=row["version"]):
                repaired += 1
        except Exception:
            logger.exception("[x-publication] projection needs repair for %s", row["id"])
            store.finish_projection(entry["id"], tenant_id=tenant, expected_version=row["version"], error="Projection failed; see server log.")
            failures.append(row["id"])
    return {"repaired": repaired, "failed": failures}


def _reconcile_receipts() -> int:
    """Only exact operation-correlated successful API responses prove acceptance."""
    from datetime import datetime

    from kazma_core.x_api.audit import get_x_audit

    store, tenant = get_publication_store(), x_tenant_id()
    repaired = 0
    for state in ("sending", "outcome_unknown"):
        for row in store.list_operations(tenant_id=tenant, state=state, limit=500):
            for receipt in get_x_audit().query(operation_id=row["id"], status="success", limit=10):
                try:
                    response = json.loads(receipt["response_body"] or "{}")
                    data = response.get("data", response)
                    if row["kind"] == "delete":
                        valid = (receipt["method"] == "DELETE" and receipt["endpoint"] == "/2/tweets/" + row["reply_to_id"]
                                 and isinstance(data, dict) and data.get("deleted") is True)
                        tweet_id = row["reply_to_id"]
                    else:
                        request = json.loads(receipt["request_body"] or "{}")
                        target = (request.get("reply") or {}).get("in_reply_to_tweet_id", "")
                        valid = receipt["method"] == "POST" and receipt["endpoint"] == "/2/tweets" and request.get("text") == row["text"] and target == row["reply_to_id"]
                        tweet_id = str(data.get("id") or "") if isinstance(data, dict) else ""
                    if valid and tweet_id.isascii() and tweet_id.isdigit() and receipt["http_status"] in (200, 201):
                        repaired += int(store.confirm_receipt(row["id"], tenant_id=tenant, expected_version=row["version"],
                            tweet_id=tweet_id, receipt_id=receipt["id"], confirmed_at=datetime.fromisoformat(receipt["ts"]).timestamp()))
                        break
                except (ValueError, TypeError, AttributeError):
                    continue
    return repaired


#: The window of the account's posts a check reads around an unknown post's
#: request: X stamps a post when it is made, after the request left.
_CHECK_BEFORE_S = 120.0
_CHECK_AFTER_S = 900.0
#: X shows the account's latest posts a few seconds late, so a check waits
#: this long after the request.
_CHECK_SETTLE_S = 60.0
_SNOWFLAKE_EPOCH_MS = 1288834974657


def _post_text(post: dict[str, Any]) -> str:
    """A post's text as it was written: the long-form text when there is
    one, every t.co link back to the address written, entities unescaped,
    whitespace runs as one space."""
    import html

    note = post.get("note_tweet") if isinstance(post.get("note_tweet"), dict) else {}
    source = note if note.get("text") else post
    text = str(source.get("text") or "")
    for url in ((source.get("entities") or {}).get("urls") or []):
        if isinstance(url, dict) and url.get("url") and url.get("expanded_url"):
            text = text.replace(str(url["url"]), str(url["expanded_url"]))
    return " ".join(html.unescape(text).split())


def _same_post(post: dict[str, Any], row: dict[str, Any]) -> bool:
    """The post X shows is the operation's: the same words and, for a
    reply, the same parent. X starts a reply with the parent's handles."""
    import re

    want = " ".join(str(row["text"]).split())
    if row["reply_to_id"]:
        parents = {str(ref.get("id")) for ref in post.get("referenced_tweets") or []
                   if isinstance(ref, dict) and ref.get("type") == "replied_to"}
        if row["reply_to_id"] not in parents:
            return False
    got = _post_text(post)
    return got == want or (bool(row["reply_to_id"]) and re.sub(r"^(?:@\w+\s+)+", "", got) == want)


def _post_time(tweet_id: str) -> float:
    """When X made a post, from its id."""
    return ((int(tweet_id) >> 22) + _SNOWFLAKE_EPOCH_MS) / 1000.0


def _utc(epoch: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _deleted_nearby(store: Any, tenant: str, row: dict[str, Any], start: float, end: float) -> str:
    """A post of this account made in the window that Kazma deleted: the
    likely answer when the post is gone, said as a likelihood (its text is
    not on X to compare)."""
    for state in ("published", "outcome_unknown"):
        for other in store.list_operations(tenant_id=tenant, state=state, limit=500):
            target = str(other["reply_to_id"] or "")
            if (other["kind"] == "delete" and other["account_id"] == row["account_id"]
                    and target.isdigit() and start <= _post_time(target) <= end):
                done = "deleted" if state == "published" else "asked X to delete"
                lag = _post_time(target) - float(row["sent_at"] or row["created_at"])
                return (f" Kazma {done} post {target}, made {lag:.0f} s after this request: "
                        "most likely this one.")
    return ""


async def check_on_x(ident: str, *, expected_version: int | None = None) -> dict[str, Any]:
    """Ask X what became of an operation whose answer was lost, and settle it.

    A post or delete whose answer never came back stays ``outcome_unknown``
    (it is never sent again, §29C), and only a recorded X answer could settle
    it -- a timeout leaves none, so it stayed unknown for good and its text
    could never be posted again (live 2026-10-28: a metrics post X published
    3 s after the request, then its delete, both unknown). Two reads settle
    it: a delete by looking the post up (gone: done; still there: it may be
    sent again), a post by reading the account's posts around the request
    (there: published, with its id; not there: ``not_on_x``). Anything X does
    not answer plainly leaves the operation as it is.
    """
    tenant = await asyncio.to_thread(x_tenant_id)
    store = await asyncio.to_thread(get_publication_store)
    row = await asyncio.to_thread(store.get, ident, tenant_id=tenant)
    if row is None:
        return {"ok": False, "code": "not_found", "error": "No such X operation."}
    if row["state"] != "outcome_unknown":
        return {"ok": False, "code": "not_unknown", "state": row["state"],
                "error": "This operation's outcome is already known."}
    if expected_version is not None and int(row["version"]) != int(expected_version):
        return {"ok": False, "code": "conflict", "error": "The operation changed. Refresh and check again."}
    sent = float(row["sent_at"] or row["created_at"])
    if time.time() - sent < _CHECK_SETTLE_S:
        return {"ok": False, "code": "too_soon", "error": "X shows new posts a little late. Check again in a minute."}
    cfg = await asyncio.to_thread(_config)
    if not cfg.account_id or cfg.account_id != row["account_id"]:
        return {"ok": False, "code": "account_changed",
                "error": "Kazma is connected to another X account now. Check this one on X itself."}
    client = _client(cfg)
    checked = _utc(time.time())
    try:
        if row["kind"] == "delete":
            target = str(row["reply_to_id"])
            found = await client.lookup_post(target) is None
            tweet_id, at = target, None
            reason = (f"Checked on X {checked}: the post is gone." if found else
                      f"Checked on X {checked}: the post is still there; the delete did not take effect. Delete it again.")
        else:
            start, end = sent - _CHECK_BEFORE_S, sent + _CHECK_AFTER_S
            posts = await client.own_posts(cfg.account_id, start=start, end=end)
            matches = [post for post in posts if _same_post(post, row)]
            if len(matches) > 1:
                return {"ok": False, "code": "ambiguous",
                        "error": f"X shows {len(matches)} posts with this text then. Check on X which one is this."}
            found = bool(matches)
            tweet_id = str(matches[0].get("id") or "") if found else ""
            at = _post_time(tweet_id) if found and tweet_id.isdigit() else None
            reason = (f"Checked on X {checked}: published." if found else
                      f"Checked on X {checked}: not among this account's posts from {_utc(start)} to {_utc(end)}. "
                      "It never went out, or it was deleted since."
                      + await asyncio.to_thread(_deleted_nearby, store, tenant, row, start, end))
    except XApiError as exc:
        return {"ok": False, "code": "x_error", "error": f"X did not answer the check: {exc}"}
    settled = await asyncio.to_thread(store.settle_checked, ident, tenant_id=tenant, expected_version=int(row["version"]),
                                      found=found, tweet_id=tweet_id, at=at, reason=reason)
    if not settled:
        return {"ok": False, "code": "conflict", "error": "The operation changed while it was checked. Refresh."}
    logger.info("[x-publication] checked %s on X: %s", ident, "found" if found else "not found")
    repair = await asyncio.to_thread(_project_pending)
    _, result = _result(await asyncio.to_thread(store.get, ident, tenant_id=tenant), repair_needed=bool(repair["failed"]))
    return {**result, "ok": True, "found": found, "reason": reason}


async def _send(client: Any, row: dict[str, Any]) -> str:
    if row["origin"] == "thread":
        from kazma_core.x_api.threads import thread_dispatch_allowed

        if not await asyncio.to_thread(thread_dispatch_allowed, row):
            raise XApiError("Thread revision or ordered execution permission changed; review before sending.", outcome="not_sent")
    from kazma_core.x_api.operation_context import operation_scope
    from kazma_core.x_api.shadow import record_shadow_intent, shadow_transport_blocked

    if shadow_transport_blocked():
        record_shadow_intent(row)
        raise XApiError("Shadow preflight recorded; no X request sent.", outcome="not_sent")

    with operation_scope(row["id"]):
        if row["kind"] == "delete":
            await client.delete_tweet(row["reply_to_id"])
            return row["reply_to_id"]
        tweet = await client.create_tweet(row["text"], reply_to_id=row["reply_to_id"])
        return str(tweet.get("id") or "")


def _result(row: dict[str, Any], *, repair_needed: bool = False) -> tuple[bool, dict[str, Any]]:
    success = row["state"] == "published"
    unknown = row["state"] in ("sending", "outcome_unknown")
    field = "deleted" if row["kind"] == "delete" else "posted"
    return success, {field: True if success else (None if unknown else False),
        "operation_id": row["id"], "state": row["state"], "version": row["version"],
        "outcome": "confirmed" if success else ("unknown" if unknown else row["outcome"] or "not_sent"),
        "retry_allowed": row["state"] == "failed_permanent" and row["outcome"] in ("rejected", "not_sent"),
        "tweet_id": row["tweet_id"], "url": f"https://x.com/i/web/status/{row['tweet_id']}" if row["tweet_id"] else "",
        "text": row["text"], "error": "" if success else row["reason"] or ("Publication already claimed; verify its outcome." if unknown else "Publication is held or not due."),
        "repair_needed": repair_needed}


async def _dispatch_operation(ident: str, *, cfg: XConfig | None = None) -> tuple[bool, dict[str, Any]]:
    """An operation identity and exact account binding precede every network write."""
    from kazma_core.x_api.policy import evaluate_delete, evaluate_post
    from kazma_core.x_api.schedule import x_schedule_enabled

    tenant = await asyncio.to_thread(x_tenant_id)  # a settings read (audit 2026-10-26 AUD-111)
    store = await asyncio.to_thread(get_publication_store)
    row = await asyncio.to_thread(store.get, ident, tenant_id=tenant)
    if row is None:
        return False, {"posted": False, "outcome": "not_sent", "error": "Unknown publication operation."}
    if row["origin"] == "thread":
        from kazma_core.x_api.threads import thread_dispatch_allowed

        if not await asyncio.to_thread(thread_dispatch_allowed, row):
            return False, {"posted": False, "outcome": "not_sent", "error": "Use the reviewed whole-thread action to send this segment."}
    if row["state"] not in ("scheduled", "deferred"):
        repair = await asyncio.to_thread(_project_pending) if row["state"] == "published" else {}
        ok, result = _result(row, repair_needed=bool(repair.get("failed")))
        return ok, {**result, "replayed": True}
    if row["origin"] == "schedule" and time.time() - row["due_at"] > 300:
        await asyncio.to_thread(store.hold, ident, tenant_id=tenant,
                               reason="Scheduled time was missed by more than five minutes. Review and reschedule; no automatic catch-up.")
        await asyncio.to_thread(_project_pending)
        return _result(await asyncio.to_thread(store.get, ident, tenant_id=tenant))
    cfg = cfg or await asyncio.to_thread(_config)
    if not cfg.account_id or row["account_id"] != cfg.account_id or row["credential_revision"] != credential_revision(cfg.credentials):
        reason = "Account or credentials changed; verify the account and review this exact queued publication."
        await asyncio.to_thread(store.hold, ident, tenant_id=tenant, reason=reason)
        await asyncio.to_thread(_project_pending)
        return _result(await asyncio.to_thread(store.get, ident, tenant_id=tenant))
    if row["origin"] == "schedule" and not x_schedule_enabled():
        return False, {"posted": False, "operation_id": ident, "outcome": "not_sent", "error": "X scheduling is disabled."}
    policy = await asyncio.to_thread(evaluate_delete, cfg=cfg) if row["kind"] == "delete" else await asyncio.to_thread(
        evaluate_post, row["text"], cfg=cfg, reply_to_id=row["reply_to_id"], check_history=False)
    if not policy.allow:
        await asyncio.to_thread(store.hold, ident, tenant_id=tenant, reason=policy.reason)
        await asyncio.to_thread(_project_pending)
        return _result(await asyncio.to_thread(store.get, ident, tenant_id=tenant))
    owner = uuid.uuid4().hex
    try:
        claimed = await asyncio.to_thread(store.claim, ident, owner=owner, tenant_id=tenant, account_id=cfg.account_id,
            credential_revision=credential_revision(cfg.credentials), max_day=cfg.max_posts_per_day, max_month=cfg.max_posts_per_month,
            reply_limits=await asyncio.to_thread(_reply_limits) if json.loads(row["metadata"]).get("summon_id") else {})
    except PublicationPolicyError as exc:
        await asyncio.to_thread(store.hold, ident, tenant_id=tenant, reason=str(exc))
        await asyncio.to_thread(_project_pending)
        return _result(await asyncio.to_thread(store.get, ident, tenant_id=tenant))
    if claimed is None:
        ok, result = _result(await asyncio.to_thread(store.get, ident, tenant_id=tenant))
        return ok, {**result, "replayed": True}
    client = _client(cfg)
    try:
        # Re-read live posting policy after claim, immediately before dispatch.
        live = await asyncio.to_thread(_config)
        if not live.can_post() or (live.account_id and live.account_id != cfg.account_id) or credential_revision(live.credentials) != credential_revision(cfg.credentials):
            await asyncio.to_thread(store.fail, ident, owner=owner, outcome="not_sent", reason="Posting disabled or account changed before dispatch.")
            await asyncio.to_thread(_project_pending)
            return _result(await asyncio.to_thread(store.get, ident, tenant_id=tenant))
        if row["kind"] == "post":
            if json.loads(row["metadata"]).get("summon_id"):
                from kazma_core.x_api.approval import binding_hold, evidence_binding_hold
                from kazma_core.x_api.reply_store import get_reply_store

                rec = await asyncio.to_thread(get_reply_store().get, json.loads(row["metadata"])["summon_id"])
                hold = "Reply claim is no longer available." if rec is None else await asyncio.to_thread(binding_hold, rec.decision.get("approval_basis"))
                if not hold:
                    hold = await asyncio.to_thread(evidence_binding_hold, rec.decision)
                if hold:
                    await asyncio.to_thread(store.fail, ident, owner=owner, outcome="not_sent", reason=hold)
                    await asyncio.to_thread(_project_pending)
                    return _result(await asyncio.to_thread(store.get, ident, tenant_id=tenant))
            live_policy = await asyncio.to_thread(evaluate_post, row["text"], cfg=live, reply_to_id=row["reply_to_id"], check_history=False)
            try:
                caps_ok = await asyncio.to_thread(store.validate_claim_caps, ident, tenant_id=tenant, owner=owner,
                                                 max_day=live.max_posts_per_day, max_month=live.max_posts_per_month,
                                                 reply_limits=await asyncio.to_thread(_reply_limits) if json.loads(row["metadata"]).get("summon_id") else {})
            except PublicationPolicyError as exc:
                caps_ok = False
                reason = str(exc)
            else:
                reason = live_policy.reason if not live_policy.allow else "Publication claim is no longer owned."
            if not live_policy.allow or not caps_ok:
                await asyncio.to_thread(store.fail, ident, owner=owner, outcome="not_sent", reason=reason)
                await asyncio.to_thread(_project_pending)
                return _result(await asyncio.to_thread(store.get, ident, tenant_id=tenant))
        tweet_id = await _send(client, row)
        if not tweet_id.isascii() or not tweet_id.isdigit():
            raise XApiError("X returned no valid confirmation identifier; verify on X before any resend.", outcome="unknown")
    except asyncio.CancelledError:
        await asyncio.shield(asyncio.to_thread(store.fail, ident, owner=owner, outcome="unknown", reason="Send interrupted; verify on X."))
        await asyncio.shield(asyncio.to_thread(_project_pending))
        raise
    except XApiError as exc:
        from kazma_core.x_api.scheduled_fire import _parse_retry_wait

        defer = time.time() + _parse_retry_wait(exc) if exc.status == 429 and row["origin"] == "schedule" and claimed["attempts"] < 8 else None
        if (defer is None and exc.outcome == "not_sent" and row["origin"] == "schedule"
                and claimed["attempts"] < _NOT_SENT_ATTEMPTS):
            # Provably unsent (the connection never opened): try again in a
            # minute, a bounded number of times, inside the missed-fire window.
            # It became failed_permanent at once (Scheduled audit SCH-E13).
            defer = time.time() + _NOT_SENT_RETRY_S
        await asyncio.to_thread(store.fail, ident, owner=owner, outcome=exc.outcome, reason=str(exc), defer_until=defer)
        await asyncio.to_thread(_project_pending)
        return _result(await asyncio.to_thread(store.get, ident, tenant_id=tenant))
    except Exception:
        logger.exception("[x-publication] send outcome unknown for %s", ident)
        await asyncio.to_thread(store.fail, ident, owner=owner, outcome="unknown", reason="X request failed. Details are in the server log. Verify on X before any resend.")
        await asyncio.to_thread(_project_pending)
        return _result(await asyncio.to_thread(store.get, ident, tenant_id=tenant))
    try:
        confirmed = await asyncio.to_thread(store.confirm, ident, owner=owner, tweet_id=tweet_id)
    except Exception:
        logger.exception("[x-publication] X confirmed %s but operation %s needs durable-result repair", tweet_id, ident)
        confirmed = False
    if not confirmed:
        # No second send. Preserve the known result for the caller even when the
        # DB refused its confirmation; API audit can assist later reconciliation.
        field = "deleted" if row["kind"] == "delete" else "posted"
        return True, {field: True, "operation_id": ident, "tweet_id": tweet_id, "text": row["text"],
                      "url": f"https://x.com/i/web/status/{tweet_id}", "outcome": "confirmed", "repair_needed": True}
    repair = await asyncio.to_thread(_project_pending)
    return _result(await asyncio.to_thread(store.get, ident, tenant_id=tenant), repair_needed=bool(repair["failed"]))


async def publish(*, text: str, reply_to_id: str = "", idempotency_key: str = "",
                  origin: str = "immediate", origin_ref: str = "", metadata: dict[str, Any] | None = None) -> tuple[bool, dict[str, Any]]:
    from kazma_core.x_api.policy import evaluate_post

    cfg = await asyncio.to_thread(_config)
    body = (text or "").strip()
    decision = await asyncio.to_thread(evaluate_post, body, cfg=cfg, reply_to_id=reply_to_id, check_history=False)
    if not decision.allow:
        return False, {"posted": False, "outcome": "not_sent", "error": decision.reason}
    try:
        cfg = await _verified_config(cfg)
        row = await asyncio.to_thread(_reserve, cfg, text=body, reply_to_id=reply_to_id, due_at=time.time(),
            idempotency_key=idempotency_key or uuid.uuid4().hex, origin=origin, origin_ref=origin_ref,
            metadata={"handle": cfg.handle, **(metadata or {})})
    except (PublicationPolicyError, PublicationConflictError, XApiError, ValueError) as exc:
        return False, {"posted": False, "outcome": "not_sent", "error": str(exc)}
    return await _dispatch_operation(row["id"], cfg=cfg)


async def delete(*, tweet_id: str, idempotency_key: str = "") -> tuple[bool, dict[str, Any]]:
    from kazma_core.x_api.policy import evaluate_delete

    tid = (tweet_id or "").strip()
    if not tid.isascii() or not tid.isdigit():
        return False, {"deleted": False, "outcome": "not_sent", "error": "tweet_id must be a numeric X identifier."}
    cfg = await asyncio.to_thread(_config)
    decision = await asyncio.to_thread(evaluate_delete, cfg=cfg)  # settings (audit 2026-10-26 AUD-111)
    if not decision.allow:
        return False, {"deleted": False, "outcome": "not_sent", "error": decision.reason}
    try:
        cfg = await _verified_config(cfg)
        key = idempotency_key or "delete:" + tid
        for revision in range(2, 12):
            row = await asyncio.to_thread(_reserve, cfg, text="", reply_to_id=tid, due_at=time.time(),
                idempotency_key=key, kind="delete", origin="delete")
            # A delete that ended unsent (refused, or checked on X and still
            # there) is asked again under a new key revision, as a booking
            # is: the fixed key replayed the ended one for good.
            if idempotency_key or not row.get("_replayed") or row["state"] not in _REBOOKABLE_STATES:
                break
            key = f"delete:{tid}:r{revision}"
    except (PublicationPolicyError, PublicationConflictError, XApiError, ValueError) as exc:
        return False, {"deleted": False, "outcome": "not_sent", "error": str(exc)}
    return await _dispatch_operation(row["id"], cfg=cfg)


#: A booking key whose operation ended this way may be booked again, under a
#: new key revision (``<key>:r<n>``): a draft cancelled or refused can be
#: scheduled anew. A published one cannot (the duplicate rule holds it too).
_REBOOKABLE_STATES = frozenset({"cancelled", "failed_permanent", "expired"})
_LIVE_SCHEDULE_STATES = frozenset({"scheduled", "deferred", "awaiting_approval"})


def schedule(*, text: str, fire_at: float, reply_to_id: str = "", idempotency_key: str = "",
             metadata: dict[str, Any] | None = None) -> tuple[bool, dict[str, Any]]:
    """Book a scheduled post. ``(ok, payload)``; ``ok`` only when it is scheduled.

    The payload always reports the STORED booking -- its time and state. A
    booking key is "proposal:<id>" from chat and the page, so booking the same
    draft again replayed the first operation whatever had become of it, and
    answered ``scheduled: True`` with the newly requested time over a
    cancelled row that would never fire (Scheduled audit SCH-E08). Now a key
    whose booking ended (:data:`_REBOOKABLE_STATES`) books a new revision; one
    still booked at another time is a conflict naming that time; one already
    published is a conflict.
    """
    from datetime import datetime

    from kazma_core.cron.scheduler import resolve_cron_zone
    from kazma_core.x_api.policy import evaluate_post
    from kazma_core.x_api.schedule import get_x_scheduled_store

    cfg = _config()
    import math

    if not math.isfinite(fire_at) or fire_at <= time.time():
        return False, {"error": "Choose a future, finite publication time."}
    body = (text or "").strip()
    decision = evaluate_post(body, cfg=cfg, reply_to_id=reply_to_id, check_history=False)
    if not decision.allow:
        return False, {"error": decision.reason}
    if not cfg.account_id:
        return False, {"error": "Verify the connected account with Test in Settings → X before scheduling. Existing work remains held."}
    zone, zone_name = resolve_cron_zone()

    def _when(epoch: float) -> str:
        # The operator's zone, named in ``tz`` -- the server's own local zone
        # was used here (SCH-E13).
        return datetime.fromtimestamp(float(epoch), tz=zone).isoformat(timespec="seconds")

    base_key = idempotency_key or uuid.uuid4().hex
    key = base_key
    row: dict[str, Any] = {}
    try:
        for revision in range(2, 12):
            row = _reserve(cfg, text=body, reply_to_id=reply_to_id, due_at=fire_at,
                           idempotency_key=key, origin="schedule", metadata=metadata)
            if not row.get("_replayed"):
                break
            if row["state"] in _REBOOKABLE_STATES:
                key = f"{base_key}:r{revision}"
                continue
            if row["state"] in _LIVE_SCHEDULE_STATES and abs(float(row["due_at"]) - fire_at) <= 1.0:
                break  # the same booking asked again: report it
            return False, {
                "code": "booking_conflict",
                "error": (f"This draft is already booked ({row['state']}) for {_when(row['due_at'])} {zone_name}. "
                          "Reschedule or cancel that booking in X Studio instead of booking it again."
                          if row["state"] in _LIVE_SCHEDULE_STATES else
                          f"This draft's booking is {row['state']}; it cannot be booked again."),
                "operation_id": row["id"], "state": row["state"], "fire_at": _when(row["due_at"]),
                "tz": zone_name,
            }
        else:
            return False, {"code": "booking_conflict", "error": "This draft was booked too many times."}
    except (PublicationPolicyError, PublicationConflictError) as exc:
        return False, {"error": ("An identical tweet is already scheduled or held. " if "Duplicate" in str(exc) else "") + str(exc)}
    repair = _project_pending()
    legacy = get_x_scheduled_store().by_operation(row["id"], tenant_id=x_tenant_id())
    scheduled = row["state"] == "scheduled"
    payload = {"scheduled": scheduled, "id": legacy.id if legacy else None, "operation_id": row["id"],
               "version": row["version"], "state": row["state"], "text": body,
               "fire_at": _when(row["due_at"]), "tz": zone_name, "reply_to_id": reply_to_id,
               "repair_needed": bool(repair["failed"])}
    if not scheduled:
        payload["code"] = "booking_held"
        payload["error"] = f"The booking is {row['state']}: {row.get('reason') or 'review it in X Studio'}."
    return scheduled, payload


def cancel_schedule(post_id: int) -> bool:
    """Tenant-scoped cancellation, with the operation claim as the authority."""
    from kazma_core.x_api.schedule import get_x_scheduled_store

    tenant = x_tenant_id()
    legacy = get_x_scheduled_store()
    post = legacy.get(post_id, tenant_id=tenant)
    if post is None:
        return False
    if not post.operation_id:
        return legacy.cancel(post_id, tenant_id=tenant)
    store = get_publication_store()
    row = store.get(post.operation_id, tenant_id=tenant)
    if row is None or not store.cancel(row["id"], tenant_id=tenant, expected_version=row["version"]):
        return False
    _project_pending()
    return True


async def fire_legacy_schedule(post: Any, cfg: XConfig) -> tuple[bool, dict[str, Any]]:
    """Legacy bookings have no verified account approval; migration holds them."""
    await asyncio.to_thread(_migrate_legacy_schedule, cfg)
    from kazma_core.x_api.schedule import get_x_scheduled_store

    current = await asyncio.to_thread(lambda: get_x_scheduled_store().get(post.id, tenant_id=post.tenant_id))
    if current and current.operation_id:
        row = await asyncio.to_thread(get_publication_store().get, current.operation_id, tenant_id=post.tenant_id)
        return _result(row)
    return False, {"outcome": "not_sent", "error": "Legacy account binding unavailable; verify the connected account and review this booking."}


async def fire_due_operations() -> None:
    from kazma_core.tenant_context import tenant_scope
    from kazma_core.x_api.notifications import drain_notifications
    from kazma_core.x_api.schedule import get_x_scheduled_store

    store = await asyncio.to_thread(get_publication_store)
    await asyncio.to_thread(store.recover)
    for tenant in await asyncio.to_thread(store.unconfirmed_tenants):
        with tenant_scope(tenant):
            await asyncio.to_thread(_reconcile_receipts)
    legacy = await asyncio.to_thread(get_x_scheduled_store)
    for tenant in await asyncio.to_thread(legacy.legacy_tenants):
        with tenant_scope(tenant):
            cfg = await asyncio.to_thread(_config)
            await asyncio.to_thread(_migrate_legacy_schedule, cfg)
    for tenant in await asyncio.to_thread(store.repair_tenants):
        with tenant_scope(tenant):
            await asyncio.to_thread(_project_pending)
    from kazma_core.x_api.schedule import x_schedule_enabled

    if not x_schedule_enabled():
        await drain_notifications()
        return
    due = await asyncio.to_thread(store.due)
    for row in due:
        with tenant_scope(row["tenant_id"]):
            try:
                await _dispatch_operation(row["id"])
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — one bad row must not stop the rows after it
                # It aborted the whole cycle, so every later due post waited for
                # the next one -- or missed its window (Scheduled audit SCH-E13).
                # Hold it with a notice; a claimed one is recovered as unknown.
                logger.exception("[x-publication] dispatch of %s failed; holding it for review", row["id"])
                try:
                    await asyncio.to_thread(store.hold, row["id"], tenant_id=row["tenant_id"],
                                            reason="Kazma hit an error preparing this post; review it in X Studio.")
                    await asyncio.to_thread(_project_pending)
                except Exception:  # noqa: BLE001
                    logger.warning("[x-publication] could not hold %s", row["id"], exc_info=True)
    await drain_notifications()
    await asyncio.to_thread(_retain)
