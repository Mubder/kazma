"""Scheduled X posts: rebooking and the fire cycle (Scheduled audit SCH-E08, E13).

* SCH-E08 -- booking a draft again under its fixed key ("proposal:<id>")
  replayed the first operation whatever had become of it, and answered
  ``scheduled: True`` with the NEW time over a cancelled row that would never
  fire. Now: a key whose booking ended books a new revision; one still booked
  at another time is a conflict naming the stored time; the reply always
  carries the stored time and state, and ``ok`` only when scheduled.
* SCH-E13 -- one raising row no longer stops the rows after it in the same
  cycle (it is held with a notice); a provably unsent post is retried, bounded;
  finished operations, delivered notices and done projections are retained by
  age; the reply's time is in the operator's zone.

X is never contacted: the client fails the test if reached, or is a fake.
"""

from __future__ import annotations

import time
from datetime import datetime

import pytest


@pytest.fixture
def x_env(tmp_path, monkeypatch):
    from kazma_core.x_api import config, ledger, publication_service, schedule
    from kazma_core.x_api.config import XConfig, XCredentials
    from kazma_core.x_api.publication_store import (
        _PublicationStore,
        _set_publication_store_for_tests,
        get_publication_store,
    )

    cfg = XConfig(enabled=True, credentials=XCredentials("k", "s", "t", "ts"), handle="kazma",
                  account_id="123", max_posts_per_day=50, max_posts_per_month=500,
                  max_mentions=2, max_cashtags=1, max_hashtags=4, max_chars=280,
                  duplicate_window_days=30, kill_switch=False)
    posts = ledger.XPostLedger(tmp_path / "posts.db")
    scheduled = schedule.XScheduledStore(tmp_path / "schedule.db")
    monkeypatch.setattr(config, "get_x_config", lambda: cfg)
    monkeypatch.setattr(ledger, "get_ledger", lambda: posts)
    monkeypatch.setattr(schedule, "get_x_scheduled_store", lambda: scheduled)

    def no_client(_cfg):
        raise AssertionError("this test must never reach X")

    monkeypatch.setattr(publication_service, "_client", no_client)
    _set_publication_store_for_tests(_PublicationStore(tmp_path / "x_publications.db"))
    try:
        yield publication_service, get_publication_store()
    finally:
        _set_publication_store_for_tests(None)


def _stored(store, ident):
    return store.get(ident, tenant_id="default")


def test_rebook_after_cancel_is_a_new_booking_at_the_new_time(x_env):
    service, store = x_env
    first_at = time.time() + 3600
    ok, first = service.schedule(text="Launch note", fire_at=first_at, idempotency_key="proposal:p-1")
    assert ok and service.cancel_schedule(first["id"])
    later = time.time() + 7200
    ok, again = service.schedule(text="Launch note", fire_at=later, idempotency_key="proposal:p-1")
    row = _stored(store, again["operation_id"])
    assert ok and again["scheduled"] is True and row["state"] == "scheduled"
    assert again["operation_id"] != first["operation_id"]
    assert abs(row["due_at"] - later) < 1
    assert datetime.fromisoformat(again["fire_at"]).timestamp() == pytest.approx(later, abs=1)
    assert _stored(store, first["operation_id"])["state"] == "cancelled"


def test_rebook_after_a_permanent_failure_is_a_new_booking(x_env):
    service, store = x_env
    ok, first = service.schedule(text="Release thread", fire_at=time.time() + 3600, idempotency_key="proposal:p-2")
    assert ok
    with store._connection(transaction=True) as conn:  # noqa: SLF001 — the state a refusal leaves
        conn.execute("UPDATE x_operations SET state = 'failed_permanent', outcome = 'rejected' WHERE id = ?",
                     (first["operation_id"],))
    ok, again = service.schedule(text="Release thread", fire_at=time.time() + 5400, idempotency_key="proposal:p-2")
    assert ok and again["state"] == "scheduled" and again["operation_id"] != first["operation_id"]


def test_rebook_while_booked_at_another_time_is_a_conflict(x_env):
    service, store = x_env
    first_at = time.time() + 3600
    ok, first = service.schedule(text="Pending note", fire_at=first_at, idempotency_key="proposal:p-3")
    assert ok
    ok, again = service.schedule(text="Pending note", fire_at=time.time() + 7200, idempotency_key="proposal:p-3")
    assert not ok and again["code"] == "booking_conflict"
    assert again["operation_id"] == first["operation_id"] and again["state"] == "scheduled"
    assert datetime.fromisoformat(again["fire_at"]).timestamp() == pytest.approx(first_at, abs=1)  # the STORED time
    ok, same = service.schedule(text="Pending note", fire_at=first_at, idempotency_key="proposal:p-3")
    assert ok and same["operation_id"] == first["operation_id"]  # the same booking asked again


def test_the_chat_tool_says_ok_only_when_scheduled(x_env, monkeypatch):
    import asyncio
    import json

    from kazma_core.x_api import booking
    from kazma_skills.native.x_publisher import tools

    service, store = x_env
    ok, first = service.schedule(text="Tool note", fire_at=time.time() + 3600, idempotency_key="proposal:p-4")

    def book(**kw):
        return service.schedule(text=kw["text"], fire_at=time.time() + 7200, idempotency_key=kw["idempotency_key"])

    monkeypatch.setattr(booking, "book_x_post", book)
    out = json.loads(asyncio.run(tools.x_schedule_post("Tool note", "2h", proposal_id="p-4")))
    assert out["ok"] is False and out["scheduled"] is False and out["state"] == "scheduled"
    assert out["code"] == "booking_conflict" and out["operation_id"] == first["operation_id"]


def test_negative_control_the_old_replay(x_env):
    """The store still replays a key (now marked); the service used to pass
    the replay through as a success at the requested time."""
    service, store = x_env
    ok, first = service.schedule(text="Old note", fire_at=time.time() + 3600, idempotency_key="proposal:p-5")
    assert service.cancel_schedule(first["id"])
    from kazma_core.x_api.publication_service import _config, _reserve

    row = _reserve(_config(), text="Old note", reply_to_id="", due_at=time.time() + 7200,
                   idempotency_key="proposal:p-5", origin="schedule")
    assert row["_replayed"] is True and row["state"] == "cancelled"


def test_the_reply_names_the_operators_zone(x_env, monkeypatch):
    import kazma_core.cron.scheduler as sched

    monkeypatch.setattr(sched, "resolve_cron_timezone_name", lambda: ("Asia/Kuwait", "env"))
    service, _store = x_env
    ok, out = service.schedule(text="Zoned note", fire_at=time.time() + 3600)
    assert ok and out["tz"] == "Asia/Kuwait" and out["fire_at"].endswith("+03:00")


# ── SCH-E13: the fire cycle ──────────────────────────────────────────────


async def test_one_raising_row_does_not_stop_the_rows_after_it(x_env, monkeypatch):
    service, store = x_env
    from kazma_core.x_api.publication_store import get_publication_store as _gps

    ok1, a = service.schedule(text="First due", fire_at=time.time() + 3600)
    ok2, b = service.schedule(text="Second due", fire_at=time.time() + 3600)
    assert ok1 and ok2
    with _gps()._connection(transaction=True) as conn:  # noqa: SLF001 — both are due now
        conn.execute("UPDATE x_operations SET due_at = ? WHERE state = 'scheduled'", (time.time() - 5,))
    seen: list[str] = []

    async def dispatch(ident, *, cfg=None):
        seen.append(ident)
        if ident == a["operation_id"]:
            raise RuntimeError("one bad row")
        return True, {}

    monkeypatch.setattr(service, "_dispatch_operation", dispatch)

    async def nothing():
        return 0

    import kazma_core.x_api.notifications as notifications

    monkeypatch.setattr(notifications, "drain_notifications", nothing)
    await service.fire_due_operations()
    assert set(seen) == {a["operation_id"], b["operation_id"]}  # the later row still dispatched
    held = _stored(store, a["operation_id"])
    assert held["state"] == "awaiting_approval" and "review it in X Studio" in held["reason"]


async def test_negative_control_without_isolation_the_cycle_stops(x_env, monkeypatch):
    """The old loop (no per-row guard) raised out of the cycle at the bad row."""
    service, store = x_env
    seen: list[str] = []

    async def dispatch(ident, *, cfg=None):
        seen.append(ident)
        raise RuntimeError("one bad row")

    async def old_cycle(rows):
        for row in rows:
            await dispatch(row["id"])

    with pytest.raises(RuntimeError):
        await old_cycle([{"id": "a"}, {"id": "b"}])
    assert seen == ["a"]


async def test_a_provably_unsent_post_is_retried_bounded(x_env, monkeypatch):
    from kazma_core.x_api.client import XApiError

    service, store = x_env
    ok, booked = service.schedule(text="Retry me", fire_at=time.time() + 3600)
    ident = booked["operation_id"]

    class _Refusing:
        async def create_tweet(self, text, reply_to_id=""):
            raise XApiError("connection refused", outcome="not_sent")

    monkeypatch.setattr(service, "_client", lambda cfg: _Refusing())
    states = []
    for _attempt in range(4):
        with store._connection(transaction=True) as conn:  # noqa: SLF001 — due now
            conn.execute("UPDATE x_operations SET due_at = ? WHERE id = ?", (time.time() - 1, ident))
        await service._dispatch_operation(ident)
        states.append(_stored(store, ident)["state"])
    assert states[:2] == ["deferred", "deferred"]  # retried a minute later
    assert states[2] == "failed_permanent"  # bounded: three attempts in all
    assert _stored(store, ident)["outcome"] == "not_sent"


def test_retention_prunes_finished_operations_by_age(x_env):
    service, store = x_env
    ok, old = service.schedule(text="Old cancelled", fire_at=time.time() + 3600)
    ok, live = service.schedule(text="Still booked", fire_at=time.time() + 3600)
    assert service.cancel_schedule(old["id"])
    long_ago = time.time() - 120 * 86400
    with store._connection(transaction=True) as conn:  # noqa: SLF001 — age the cancelled row
        conn.execute("UPDATE x_operations SET updated_at = ? WHERE id = ?", (long_ago, old["operation_id"]))
    pruned = store.prune_terminal(older_than_days=90)
    assert pruned["operations"] == 1
    assert _stored(store, old["operation_id"]) is None
    assert _stored(store, live["operation_id"])["state"] == "scheduled"
    assert store.events(old["operation_id"], tenant_id="default") == []
