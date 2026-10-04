"""Durable notices are transactional, leased and require real transport ACK."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from kazma_core.x_api.reply_store import XReplyStore


def held(store):
    store.claim(summon_id="123", parent_id="456", target_handle="target", summoner="owner")
    store.mark_awaiting("123", draft="Review the complete candidate", subject_id="card")


def test_notice_and_revision_commit_together(tmp_path):
    store = XReplyStore(tmp_path / "reply.db")
    held(store)
    record = store.get("123")
    notice = store.claim_notification(owner="a")
    assert record.draft_text in notice["message"]
    assert record.approval_token in notice["message"]
    assert not store.claim_notification(owner="b")


def test_two_connections_have_one_delivery_lease(tmp_path):
    path = tmp_path / "reply.db"
    first, second = XReplyStore(path), XReplyStore(path)
    held(first)
    with ThreadPoolExecutor(2) as pool:
        rows = list(pool.map(lambda s: s.claim_notification(owner=str(id(s))), (first, second)))
    assert sum(row is not None for row in rows) == 1


@pytest.mark.asyncio
async def test_failed_delivery_survives_restart_without_resending_publication(tmp_path, monkeypatch):
    from kazma_core.observability import ops_alerts
    from kazma_core.x_api.notifications import _drain_store

    path = tmp_path / "reply.db"
    store = XReplyStore(path)
    held(store)
    async def failed(text):
        return False
    monkeypatch.setattr(ops_alerts, "deliver_acknowledged", failed)
    assert await _drain_store(store) == 0
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT attempts, delivered_at, last_error FROM x_notification_outbox").fetchone()
        assert row[0] == 1 and row[1] is None and row[2]
        conn.execute("UPDATE x_notification_outbox SET next_attempt = 0")
    async def delivered(text):
        return True
    monkeypatch.setattr(ops_alerts, "deliver_acknowledged", delivered)
    assert await _drain_store(XReplyStore(path)) == 1
    assert await _drain_store(XReplyStore(path)) == 0
    assert store.get("123").status == "awaiting_approval"


@pytest.mark.asyncio
async def test_dispatch_intent_without_confirmed_route_is_not_delivery(monkeypatch):
    from kazma_core.observability import ops_alerts

    async def deliberately_nowhere(text, outcome):
        return True
    monkeypatch.setattr(ops_alerts, "_deliver", deliberately_nowhere)
    assert not await ops_alerts.deliver_acknowledged("Notice")


def test_notification_failure_rolls_back_held_transition(tmp_path, monkeypatch):
    from kazma_core.x_api import notifications

    store = XReplyStore(tmp_path / "reply.db")
    store.claim(summon_id="123", parent_id="456", target_handle="target", summoner="owner")
    def refused(*a, **k):
        raise sqlite3.OperationalError("database refused notice")
    monkeypatch.setattr(notifications, "enqueue", refused)
    with pytest.raises(sqlite3.OperationalError):
        store.mark_awaiting("123", draft="candidate", subject_id="card")
    assert store.get("123").status == "drafting"


@pytest.mark.asyncio
async def test_denied_revision_does_not_send_an_obsolete_approval_notice(tmp_path, monkeypatch):
    from kazma_core.observability import ops_alerts
    from kazma_core.x_api.notifications import _drain_store

    store = XReplyStore(tmp_path / "reply.db")
    held(store)
    record = store.get("123")
    assert store.deny_awaiting("123", expected_updated_at=record.updated_at, expected_revision=record.revision)
    async def should_not_deliver(text):
        raise AssertionError("A denied candidate must not advertise approval")
    monkeypatch.setattr(ops_alerts, "deliver_acknowledged", should_not_deliver)
    assert await _drain_store(store) == 0
