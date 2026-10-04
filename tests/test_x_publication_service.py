"""Network-write invariants through the real shared service and real databases."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    from kazma_core.x_api import config, ledger, publication_service, schedule
    from kazma_core.x_api.config import XConfig, XCredentials
    from kazma_core.x_api.publication_store import get_publication_store

    cfg = XConfig(enabled=True, credentials=XCredentials("k", "s", "t", "ts"), handle="kazma",
                  account_id="123", max_posts_per_day=2, max_posts_per_month=10,
                  max_mentions=2, max_cashtags=1, max_hashtags=4, max_chars=280,
                  duplicate_window_days=30, kill_switch=False)
    state = {"cfg": cfg, "calls": [], "error": None, "wait": None}
    posts = ledger.XPostLedger(tmp_path / "posts.db")
    scheduled = schedule.XScheduledStore(tmp_path / "schedule.db")
    monkeypatch.setattr(config, "get_x_config", lambda: state["cfg"])
    monkeypatch.setattr(ledger, "get_ledger", lambda: posts)
    monkeypatch.setattr(schedule, "get_x_scheduled_store", lambda: scheduled)

    class Client:
        async def create_tweet(self, text, *, reply_to_id=""):
            state["calls"].append((text, reply_to_id))
            if state["wait"]:
                await state["wait"].wait()
            if state["error"]:
                raise state["error"]
            return {"id": str(900 + len(state["calls"]))}

    monkeypatch.setattr(publication_service, "_client", lambda cfg: Client())
    return publication_service, get_publication_store(), posts, scheduled, state


async def test_concurrent_same_intent_sends_once(env):
    service, store, posts, _, state = env
    outcomes = await asyncio.gather(*(service.publish(text="One intent", idempotency_key="same") for _ in range(2)))
    assert len(state["calls"]) == 1
    assert len({result["operation_id"] for _, result in outcomes}) == 1
    assert (await service.publish(text="One intent", idempotency_key="same"))[1]["replayed"]
    assert posts.count_since(0) == 1


async def test_unknown_response_is_not_resubmitted(env):
    from kazma_core.x_api.client import XApiError

    service, _, _, _, state = env
    state["error"] = XApiError("Response lost", outcome="unknown")
    ok, first = await service.publish(text="Possible post", idempotency_key="unknown")
    assert not ok and first["posted"] is None and first["state"] == "outcome_unknown"
    await service.publish(text="Possible post", idempotency_key="unknown")
    assert not (await service.publish(text="Possible post", idempotency_key="different"))[0]
    assert len(state["calls"]) == 1


async def test_projection_failure_preserves_known_result_and_replays_without_send(env, monkeypatch):
    service, store, posts, _, state = env
    record = posts.record
    monkeypatch.setattr(posts, "record", lambda **kwargs: (_ for _ in ()).throw(OSError("disk refused")))
    ok, result = await service.publish(text="Known success", idempotency_key="receipt")
    assert ok and result["posted"] is True and result["repair_needed"]
    assert store.get(result["operation_id"], tenant_id="default")["state"] == "published"
    monkeypatch.setattr(posts, "record", record)
    await asyncio.to_thread(service._project_pending)
    assert posts.count_since(0) == 1
    assert store.outbox(tenant_id="default") == []
    await service.publish(text="Known success", idempotency_key="receipt")
    assert len(state["calls"]) == 1


async def test_scheduled_and_immediate_posts_share_final_cap(env):
    service, _, _, _, state = env
    state["cfg"] = replace(state["cfg"], max_posts_per_day=1)
    ok, _ = await asyncio.to_thread(service.schedule, text="Reserved", fire_at=time.time() + 60)
    assert ok
    ok, result = await service.publish(text="Immediate", idempotency_key="cap")
    assert not ok and "cap" in result["error"] and state["calls"] == []


async def test_future_reservations_do_not_consume_todays_capacity(env):
    service, _, _, _, state = env
    state["cfg"] = replace(state["cfg"], max_posts_per_day=1)
    assert (await asyncio.to_thread(service.schedule, text="Later", fire_at=time.time() + 86410))[0]
    assert (await service.publish(text="Now", idempotency_key="now"))[0]


async def test_account_change_holds_approved_schedule(env):
    service, store, _, scheduled, state = env
    ok, result = await asyncio.to_thread(service.schedule, text="Bound account", fire_at=time.time() + 60)
    assert ok
    with store._connection() as conn:
        conn.execute("UPDATE x_operations SET due_at = ? WHERE id = ?", (time.time() - 1, result["operation_id"]))
    state["cfg"] = replace(state["cfg"], account_id="456")
    await service.fire_due_operations()
    row = store.get(result["operation_id"], tenant_id="default")
    assert row["state"] == "awaiting_approval" and state["calls"] == []
    assert scheduled.get(result["id"]).status == "held"


async def test_cancellation_during_network_stays_unknown(env):
    service, store, _, _, state = env
    state["wait"] = asyncio.Event()
    task = asyncio.create_task(service.publish(text="Interrupted", idempotency_key="cancel"))
    for _ in range(200):
        if state["calls"]:
            break
        await asyncio.sleep(0.005)
    assert state["calls"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    rows = store.list_operations(tenant_id="default")
    assert rows[0]["state"] == "outcome_unknown"
    await service.publish(text="Interrupted", idempotency_key="cancel")
    assert len(state["calls"]) == 1


def test_old_projection_cannot_overwrite_cancelled_state(env):
    service, store, _, scheduled, _ = env
    _, result = service.schedule(text="Cancelled", fire_at=time.time() + 60)
    old = store.get(result["operation_id"], tenant_id="default")
    assert service.cancel_schedule(result["id"])
    scheduled.project_operation(old)
    assert scheduled.get(result["id"]).status == "cancelled"


def test_stale_outbox_ack_does_not_hide_new_transition(env):
    service, store, _, _, _ = env
    _, result = service.schedule(text="Repair race", fire_at=time.time() + 60)
    row = store.get(result["operation_id"], tenant_id="default")
    store.reschedule(row["id"], tenant_id="default", expected_version=row["version"],
                     due_at=time.time() + 120, max_day=2, max_month=10)
    entry = store.outbox(tenant_id="default")[0]
    assert not store.finish_projection(entry["id"], tenant_id="default", expected_version=row["version"])
    assert store.outbox(tenant_id="default")


async def test_unbound_legacy_booking_is_held_not_redirected(env):
    service, store, _, scheduled, state = env
    ident = scheduled.add(text="Old booking", fire_at=time.time() - 60)
    await service.fire_due_operations()
    post = scheduled.get(ident)
    assert post.status == "held" and post.operation_id and state["calls"] == []
    rows = store.list_operations(tenant_id="default")
    assert len(rows) == 1 and rows[0]["state"] == "awaiting_approval"
    await service.fire_due_operations()
    assert len(store.list_operations(tenant_id="default")) == 1


async def test_unmanaged_old_send_stays_unknown_after_migration(env):
    service, store, _, scheduled, state = env
    ident = scheduled.add(text="Old send", fire_at=time.time() - 60)
    scheduled.claim_send(ident, tenant_id="default")
    await service.fire_due_operations()
    assert scheduled.get(ident).status == "outcome_unknown"
    assert store.list_operations(tenant_id="default")[0]["state"] == "outcome_unknown"
    assert state["calls"] == []


async def test_fenced_legacy_commitments_count_before_new_booking(env):
    service, store, _, scheduled, state = env
    state["cfg"] = replace(state["cfg"], max_posts_per_day=1)
    scheduled.add(text="Existing reservation", fire_at=time.time() + 60)
    ok, result = await asyncio.to_thread(service.schedule, text="New reservation", fire_at=time.time() + 120)
    assert not ok and "cap" in result["error"]
    assert len(store.list_operations(tenant_id="default")) == 1
    assert state["calls"] == []


async def test_reply_service_binds_stored_draft_and_atomic_limits(env, tmp_path, monkeypatch):
    from kazma_core.x_api import reply_store, stance

    service, _, _, _, state = env
    replies = reply_store.XReplyStore(tmp_path / "replies.db")
    cfg = replace(stance.get_reply_config(), max_replies_per_day=5,
                  max_replies_per_target_per_day=1, cooldown_per_thread_s=0, summoners=())
    monkeypatch.setattr(stance, "get_reply_config", lambda: cfg)
    monkeypatch.setattr(reply_store, "get_reply_store", lambda: replies)
    for sid, text in (("first", "First reply"), ("second", "Second reply")):
        replies.claim(summon_id=sid, parent_id=sid, target_handle="same", summoner="stranger")
        from kazma_core.x_api.approval import capture_basis
        replies.mark_awaiting(sid, draft=text, subject_id="card", decision={"approval_basis": capture_basis(cfg)})
        assert replies.claim_approval(sid, expected_updated_at=replies.get(sid).updated_at, expected_revision=replies.get(sid).revision)
    outcomes = await asyncio.gather(*(service.publish(text=text, reply_to_id=sid, idempotency_key=sid,
                                      origin="reply", metadata={"summon_id": sid})
                                     for sid, text in (("first", "First reply"), ("second", "Second reply"))))
    assert sum(ok for ok, _ in outcomes) == 1
    assert len(state["calls"]) == 1


async def test_confirm_db_failure_repairs_from_correlated_receipt_without_resend(env, tmp_path, monkeypatch):
    from kazma_core.x_api import audit
    from kazma_core.x_api.operation_context import current_operation_id

    service, store, _, _, state = env
    receipts = audit.XAuditLog(tmp_path / "audit.db")
    monkeypatch.setattr(audit, "get_x_audit", lambda: receipts)
    class Client:
        async def create_tweet(self, text, *, reply_to_id=""):
            state["calls"].append(text)
            receipts.log(action="post", method="POST", endpoint="/2/tweets", http_status=201,
                         operation_id=current_operation_id(), request_body={"text": text}, response_body={"data": {"id": "901"}})
            return {"id": "901"}
    monkeypatch.setattr(service, "_client", lambda cfg: Client())
    confirm = store.confirm
    monkeypatch.setattr(store, "confirm", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("write refused")))
    ok, result = await service.publish(text="Remote confirmed", idempotency_key="audit-repair")
    assert ok and result["repair_needed"]
    assert store.get(result["operation_id"], tenant_id="default")["state"] == "sending"
    monkeypatch.setattr(store, "confirm", confirm)
    assert await asyncio.to_thread(service._reconcile_receipts) == 1
    assert store.get(result["operation_id"], tenant_id="default")["state"] == "published"
    await service.publish(text="Remote confirmed", idempotency_key="audit-repair")
    assert len(state["calls"]) == 1


async def test_similar_text_or_wrong_payload_audit_does_not_prove_unknown_send(env, tmp_path, monkeypatch):
    from kazma_core.x_api import audit
    from kazma_core.x_api.client import XApiError

    service, store, _, _, state = env
    receipts = audit.XAuditLog(tmp_path / "audit.db")
    monkeypatch.setattr(audit, "get_x_audit", lambda: receipts)
    state["error"] = XApiError("lost", outcome="unknown")
    _, result = await service.publish(text="Original", idempotency_key="unconfirmed")
    for operation_id, text in (("", "Original"), (result["operation_id"], "Wrong payload")):
        receipts.log(action="post", method="POST", endpoint="/2/tweets", http_status=201,
                     operation_id=operation_id, request_body={"text": text}, response_body={"data": {"id": "901"}})
    assert await asyncio.to_thread(service._reconcile_receipts) == 0
    assert store.get(result["operation_id"], tenant_id="default")["state"] == "outcome_unknown"
