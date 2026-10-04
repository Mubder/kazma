"""Publication uncertainty and authority never grant permission to resend."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("failure, expected", [
    (httpx.ReadTimeout("response lost"), "unknown"),
    (httpx.WriteError("partial write"), "unknown"),
    (httpx.ConnectError("connection refused"), "not_sent"),
])
async def test_client_classifies_network_write_outcome(monkeypatch, failure, expected):
    from kazma_core.x_api.client import XApiError, XClient
    from kazma_core.x_api.config import XCredentials

    original = httpx.AsyncClient

    async def handle(request):
        raise failure

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    with pytest.raises(XApiError) as caught:
        await XClient(XCredentials("a", "b", "c", "d")).create_tweet("Test")
    assert caught.value.outcome == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("status, body, expected", [
    (201, {"data": {"text": "missing id"}}, "unknown"),
    (201, {"data": {"id": "not-a-tweet-id"}}, "unknown"),
    (503, {"detail": "gateway failed"}, "unknown"),
    (400, {"detail": "invalid request"}, "rejected"),
])
async def test_client_classifies_http_write_outcome(monkeypatch, status, body, expected):
    from kazma_core.x_api.client import XApiError, XClient
    from kazma_core.x_api.config import XCredentials

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body)), **kw,
    ))
    with pytest.raises(XApiError) as caught:
        await XClient(XCredentials("a", "b", "c", "d")).create_tweet("Test")
    assert caught.value.outcome == expected


def test_arbitrary_marker_cannot_open_thread():
    from kazma_core.x_api.stance import ReplyConfig

    cfg = ReplyConfig(
        enabled=True, mode="draft", summoners=("owner",), trigger="",
        max_replies_per_day=5, max_replies_per_target_per_day=1,
        cooldown_per_thread_s=1, min_target_followers=0, poll_interval_s=600,
        open_thread_marker="#Open", close_thread_marker="#Close",
    )
    assert not cfg.is_summoner("stranger", summon_text="@kazma #Open 😂")
    assert not cfg.is_summoner("stranger", parent_text="Someone else's #Open post")
    assert cfg.is_summoner("stranger", conversation_open=True)
    assert not cfg.is_summoner("stranger", conversation_open=True, conversation_closed=True)


def test_schedule_mutations_enforce_owner(tmp_path):
    from kazma_core.x_api.schedule import XScheduledStore

    store = XScheduledStore(tmp_path / "x_scheduled.db")
    ident = store.add(text="Private campaign", fire_at=time.time() + 3600, tenant_id="owner")
    assert store.get(ident, tenant_id="stranger") is None
    assert not store.cancel(ident, tenant_id="stranger")
    assert not store.set_fire_time(ident, time.time() + 7200, tenant_id="stranger")
    assert store.get(ident, tenant_id="owner").status == "pending"


@pytest.mark.asyncio
async def test_unknown_summon_cannot_retry_or_approve(tmp_path, monkeypatch):
    from kazma_core.x_api.reply import approve_summon, retry_summon
    from kazma_core.x_api.reply_store import reset_reply_store

    store = reset_reply_store(tmp_path / "x_replies.db")
    store.claim(summon_id="123", parent_id="456", target_handle="target", summoner="owner")
    store.mark_awaiting("123", draft="A held reply", subject_id="coffee")
    store.mark_publish_failure("123", "HTTP response lost", outcome="unknown")
    calls = []

    async def publish(**kw):
        calls.append(kw)
        return True, {"tweet_id": "789"}

    monkeypatch.setattr("kazma_core.x_api.booking.publish_x_post", publish)
    assert (await approve_summon("123")).action == "skipped"
    assert (await retry_summon("123")).action == "skipped"
    assert not store.release("123")
    assert calls == []


@pytest.mark.asyncio
async def test_concurrent_approval_claims_only_once(tmp_path, monkeypatch):
    from kazma_core.x_api.reply import approve_summon
    from kazma_core.x_api.reply_store import reset_reply_store

    store = reset_reply_store(tmp_path / "x_replies.db")
    store.claim(summon_id="123", parent_id="456", target_handle="target", summoner="owner")
    store.mark_awaiting("123", draft="A held reply", subject_id="coffee")
    calls = []

    async def publish(**kw):
        calls.append(kw)
        await asyncio.sleep(0.05)
        return True, {"tweet_id": "789"}

    monkeypatch.setattr("kazma_core.x_api.booking.publish_x_post", publish)
    monkeypatch.setattr("kazma_core.x_api.approval.binding_hold", lambda basis: "")
    token = store.get("123").approval_token
    results = await asyncio.gather(approve_summon("123", approval_token=token), approve_summon("123", approval_token=token))
    assert len(calls) == 1
    assert sum(result.action == "posted" for result in results) == 1


def test_missing_tenant_in_production_never_selects_default(monkeypatch):
    from kazma_core.tenant_context import tenant_scope
    from kazma_core.x_api.ownership import x_tenant_id

    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    with tenant_scope(""):
        with pytest.raises(PermissionError):
            x_tenant_id()


def test_tenant_cannot_read_or_change_another_reply_or_audit(tmp_path):
    from kazma_core.tenant_context import tenant_scope
    from kazma_core.x_api.audit import XAuditLog
    from kazma_core.x_api.ledger import XPostLedger
    from kazma_core.x_api.reply_store import XReplyStore

    replies = XReplyStore(tmp_path / "x_replies.db")
    ledger = XPostLedger(tmp_path / "x_posts.db")
    audit = XAuditLog(tmp_path / "x_audit.db")
    with tenant_scope("owner"):
        replies.claim(summon_id="123", parent_id="456", target_handle="target", summoner="operator")
        replies.mark_awaiting("123", draft="Private reply", subject_id="coffee")
        replies.set_since_id("123")
        ledger.record(tweet_id="789", text="Private campaign")
        audit.log(action="post", request_body={"text": "Private campaign"})
        replies.close_conversation("conv", closed_by="owner")
    with tenant_scope("stranger"):
        assert replies.get("123") is None
        assert replies.recent() == []
        assert not replies.release("123")
        assert not replies.seen("123")
        assert replies.get_since_id() == ""
        assert not replies.is_conversation_closed("conv")
        assert ledger.recent() == []
        assert ledger.count_since(0) == 0
        assert ledger.text_for_tweet("789") == ""
        assert not ledger.mark_deleted("789")
        assert audit.query() == []
    with tenant_scope("owner"):
        assert replies.get("123").draft_text == "Private reply"
        assert ledger.count_since(0) == 1
        assert len(audit.query()) == 1


def test_invalid_subject_config_cannot_expand_to_voice_mode():
    from kazma_core.config_store import get_config_store
    from kazma_core.x_api.stance import get_reply_config

    get_config_store().batch_set([
        ("connectors.x.reply.enabled", True, "connectors"),
        ("connectors.x.reply.mode", "auto", "connectors"),
        ("connectors.x.reply.subjects", [{"id": "broken"}], "connectors"),
    ])
    cfg = get_reply_config()
    assert cfg.config_errors
    assert not cfg.can_draft()


def test_archive_retains_idempotency_and_history(tmp_path):
    from kazma_core.x_api.reply_store import XReplyStore

    store = XReplyStore(tmp_path / "x_replies.db")
    store.claim(summon_id="123", parent_id="456", target_handle="target", summoner="owner")
    store.mark_awaiting("123", draft="Draft", subject_id="coffee")
    store.mark_publish_failure("123", "response lost", outcome="unknown")
    assert store.forget("123")
    assert store.get("123") is None
    assert store.get("123", include_archived=True).status == "outcome_unknown"
    assert store.seen("123")
    assert not store.release("123")
    assert not store.claim(summon_id="123", parent_id="456", target_handle="target", summoner="owner")


def test_interruption_cannot_turn_possible_send_into_retryable_failure(tmp_path):
    from kazma_core.x_api.reply_store import XReplyStore

    store = XReplyStore(tmp_path / "x_replies.db")
    store.claim(summon_id="123", parent_id="456", target_handle="target", summoner="owner")
    store.mark_awaiting("123", draft="Draft", subject_id="coffee")
    assert store.claim_approval("123", expected_updated_at=store.get("123").updated_at, expected_revision=store.get("123").revision)
    store.mark_interrupted("123", "process was interrupted")
    store.mark_failed("123", "HTTP error should not reopen this")
    assert store.get("123").status == "outcome_unknown"
    assert not store.release("123")


def test_two_store_instances_cannot_claim_same_scheduled_send(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from kazma_core.x_api.schedule import XScheduledStore

    path = tmp_path / "x_scheduled.db"
    first, second = XScheduledStore(path), XScheduledStore(path)
    ident = first.add(text="Only once", fire_at=time.time() - 1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda store: store.claim_send(ident, tenant_id="default"), (first, second)))
    assert sum(claim is not None for claim in claims) == 1
    assert not first.cancel(ident, tenant_id="default")
    assert not first.set_fire_time(ident, time.time() + 60, tenant_id="default")
    assert XScheduledStore(path).list_due() == []
