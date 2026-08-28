"""Scheduled X posts — Kazma-side store, booking tools, deterministic fire loop.

X has no native scheduled-post API, so Kazma stores the draft and fires
POST /2/tweets at the appointed time. These tests pin:
  * the store roundtrip (add / list_due / mark_fired / cancel / count_pending)
  * booking policy (caps reservation, dedupe, past-time rejection)
  * the fire loop (success, 429 deferral, no auto-retry on ambiguous failure)
  * the kill-switch
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from kazma_core.x_api.schedule import (
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_FIRED,
    STATUS_PENDING,
    XScheduledStore,
    x_schedule_enabled,
)


@pytest.fixture()
def store(tmp_path: Path) -> XScheduledStore:
    return XScheduledStore(tmp_path / "x_scheduled.db")


def _future(seconds: float = 3600.0) -> float:
    return time.time() + seconds


# ── Store ─────────────────────────────────────────────────────────────


def test_store_add_and_list_due(store: XScheduledStore) -> None:
    pid = store.add(text="hello future", fire_at=_future(60))
    assert pid >= 1
    # Not due yet.
    assert store.list_due() == []
    # Force it due by querying with a later "now".
    due = store.list_due(now=time.time() + 120)
    assert len(due) == 1
    assert due[0].text == "hello future"
    assert due[0].status == STATUS_PENDING


def test_store_transitions(store: XScheduledStore) -> None:
    pid = store.add(text="t", fire_at=_future())
    store.mark_fired(pid, "1770")
    got = store.get(pid)
    assert got.status == STATUS_FIRED and got.tweet_id == "1770"

    pid2 = store.add(text="t2", fire_at=_future())
    assert store.cancel(pid2) is True
    assert store.get(pid2).status == STATUS_CANCELLED
    # Cancelling again (not pending) is a no-op.
    assert store.cancel(pid2) is False

    pid3 = store.add(text="t3", fire_at=_future())
    store.mark_failed(pid3, "boom")
    assert store.get(pid3).status == STATUS_FAILED


def test_store_count_pending_and_tenant_scope(store: XScheduledStore) -> None:
    store.add(text="a", fire_at=_future(), tenant_id="t1")
    store.add(text="b", fire_at=_future(), tenant_id="t1")
    store.add(text="c", fire_at=_future(), tenant_id="t2")
    assert store.count_pending() == 3
    assert store.count_pending(tenant_id="t1") == 2
    assert len(store.list_all(tenant_id="t1")) == 2


def test_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KAZMA_X_POST", raising=False)
    monkeypatch.delenv("KAZMA_X_SCHEDULE", raising=False)
    assert x_schedule_enabled() is True
    monkeypatch.setenv("KAZMA_X_SCHEDULE", "0")
    assert x_schedule_enabled() is False
    monkeypatch.delenv("KAZMA_X_SCHEDULE", raising=False)
    monkeypatch.setenv("KAZMA_X_POST", "0")
    assert x_schedule_enabled() is False


# ── Booking tool ──────────────────────────────────────────────────────


def _cfg(**overrides):
    from kazma_core.x_api.config import XConfig, XCredentials

    creds = XCredentials("k", "ks", "t", "ts")
    base = dict(
        enabled=True,
        handle="@kazma",
        credentials=creds,
        max_posts_per_day=8,
        max_posts_per_month=80,
        max_mentions=2,
        max_cashtags=1,
        max_hashtags=4,
        max_chars=280,
        duplicate_window_days=30,
        kill_switch=False,
    )
    base.update(overrides)
    return XConfig(**base)


@pytest.fixture()
def booking_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolated ledger + schedule store + valid config for booking tools.

    ``book_x_post`` imports its collaborators at call time from their source
    modules, so we patch those (plus ``policy.get_ledger``, which is bound at
    policy import time, and the tools-module refs used by list/cancel).
    """
    import kazma_core.x_api.config as config_mod
    import kazma_core.x_api.ledger as ledger_mod
    import kazma_core.x_api.policy as policy_mod
    import kazma_core.x_api.schedule as schedule_mod
    from kazma_core.x_api.ledger import XPostLedger
    from kazma_core.x_api.schedule import XScheduledStore
    from kazma_skills.native.x_publisher import tools as t

    ledger = XPostLedger(tmp_path / "x_posts.db")
    sched = XScheduledStore(tmp_path / "x_scheduled.db")
    cfg = _cfg()

    monkeypatch.setattr(config_mod, "get_x_config", lambda: cfg)
    monkeypatch.setattr(ledger_mod, "get_ledger", lambda: ledger)
    monkeypatch.setattr(policy_mod, "get_ledger", lambda: ledger)
    monkeypatch.setattr(schedule_mod, "get_x_scheduled_store", lambda: sched)
    # Tools-module refs used by x_list_scheduled / x_cancel_scheduled_post.
    monkeypatch.setattr(t, "get_x_scheduled_store", lambda: sched)
    monkeypatch.setattr(t, "get_x_config", lambda: cfg)
    monkeypatch.setattr(t, "get_ledger", lambda: ledger)
    monkeypatch.delenv("KAZMA_X_SCHEDULE", raising=False)
    monkeypatch.delenv("KAZMA_X_POST", raising=False)
    return t, ledger, sched


@pytest.mark.asyncio
async def test_booking_success_returns_id_and_fire_time(booking_env) -> None:
    t, ledger, sched = booking_env
    import json as _json

    out = _json.loads(await t.x_schedule_post("Kazma ships scheduled posts", "1h"))
    assert out["ok"] is True and out["scheduled"] is True
    assert out["id"] >= 1 and out["text"] == "Kazma ships scheduled posts"
    assert out["fire_at"]
    assert sched.count_pending() == 1


@pytest.mark.asyncio
async def test_booking_rejects_past_time(booking_env) -> None:
    t, _, _ = booking_env
    import json as _json
    from datetime import datetime, timedelta, timezone

    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    out = _json.loads(await t.x_schedule_post("hello", past))
    assert out["ok"] is False
    assert "past" in out["error"].lower()


@pytest.mark.asyncio
async def test_booking_enforces_policy(booking_env) -> None:
    t, _, _ = booking_env
    import json as _json

    out = _json.loads(await t.x_schedule_post("x" * 281, "1h"))
    assert out["ok"] is False and "cap" in out["error"].lower()


@pytest.mark.asyncio
async def test_booking_reserves_quota(booking_env, monkeypatch: pytest.MonkeyPatch) -> None:
    t, ledger, sched = booking_env
    import json as _json
    import kazma_core.x_api.config as config_mod

    # Tighten the daily cap to 1, then book one and try a second.
    monkeypatch.setattr(config_mod, "get_x_config", lambda: _cfg(max_posts_per_day=1))
    first = _json.loads(await t.x_schedule_post("first scheduled", "1h"))
    assert first["ok"] is True
    second = _json.loads(await t.x_schedule_post("a different legal tweet", "2h"))
    assert second["ok"] is False
    assert "daily cap" in second["error"].lower()


@pytest.mark.asyncio
async def test_booking_dedupe_pending(booking_env) -> None:
    t, _, sched = booking_env
    import json as _json

    first = _json.loads(await t.x_schedule_post("same draft twice", "1h"))
    assert first["ok"] is True
    dup = _json.loads(await t.x_schedule_post("same draft twice", "2h"))
    assert dup["ok"] is False
    assert "already scheduled" in dup["error"].lower()


@pytest.mark.asyncio
async def test_cancel_scheduled_post(booking_env) -> None:
    t, _, sched = booking_env
    import json as _json

    booked = _json.loads(await t.x_schedule_post("cancel me later", "1h"))
    pid = booked["id"]
    assert sched.count_pending() == 1

    out = _json.loads(await t.x_cancel_scheduled_post(pid))
    assert out["ok"] is True and out["cancelled"] is True
    # Quota released.
    assert sched.count_pending() == 0

    # Cancelling again reports it's not pending.
    again = _json.loads(await t.x_cancel_scheduled_post(pid))
    assert again["ok"] is False

    missing = _json.loads(await t.x_cancel_scheduled_post(99999))
    assert missing["ok"] is False


@pytest.mark.asyncio
async def test_booking_disabled_by_kill_switch(booking_env, monkeypatch: pytest.MonkeyPatch) -> None:
    t, _, _ = booking_env
    import json as _json

    monkeypatch.setenv("KAZMA_X_SCHEDULE", "0")
    out = _json.loads(await t.x_schedule_post("hello", "1h"))
    assert out["ok"] is False and "disabled" in out["error"].lower()


# ── Fire loop ─────────────────────────────────────────────────────────


class _FakeXClient:
    """Stands in for XClient inside scheduled_fire."""

    result: dict | None = None
    exc: Exception | None = None
    calls: list = []

    def __init__(self, credentials=None) -> None:
        pass

    async def create_tweet(self, text, *, reply_to_id=""):
        _FakeXClient.calls.append(text)
        if _FakeXClient.exc is not None:
            raise _FakeXClient.exc
        return _FakeXClient.result or {"id": "1770000000000000999"}


@pytest.fixture()
def fire_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import kazma_core.x_api.scheduled_fire as fire
    from kazma_core.x_api.ledger import XPostLedger

    ledger = XPostLedger(tmp_path / "x_posts.db")
    sched = XScheduledStore(tmp_path / "x_scheduled.db")
    monkeypatch.setattr(fire, "XClient", _FakeXClient)
    monkeypatch.setattr(fire, "get_x_config", lambda: _cfg())
    monkeypatch.setattr(fire, "get_ledger", lambda: ledger)
    monkeypatch.setattr(fire, "get_x_scheduled_store", lambda: sched)
    _FakeXClient.result = None
    _FakeXClient.exc = None
    _FakeXClient.calls = []
    return fire, ledger, sched


@pytest.mark.asyncio
async def test_fire_loop_posts_due_and_records(fire_env) -> None:
    fire, ledger, sched = fire_env
    pid = sched.add(text="fire me", fire_at=time.time() - 5)

    await fire._fire_due_posts()

    assert _FakeXClient.calls == ["fire me"]
    got = sched.get(pid)
    assert got.status == STATUS_FIRED
    assert got.tweet_id == "1770000000000000999"
    # Ledger quota/dedupe now counts the fired post.
    assert ledger.count_since(time.time() - 86400) == 1


@pytest.mark.asyncio
async def test_fire_loop_429_defers_not_fails(fire_env) -> None:
    from kazma_core.x_api.client import XApiError

    fire, _, sched = fire_env
    pid = sched.add(text="rate limited", fire_at=time.time() - 5)
    _FakeXClient.exc = XApiError("X rate limit (HTTP 429). (Retry-After 30)", status=429, transient=True)

    await fire._fire_due_posts()

    got = sched.get(pid)
    # Still pending, deferred into the future, attempt bumped.
    assert got.status == STATUS_PENDING
    assert got.attempts == 1
    assert got.fire_at > time.time()


@pytest.mark.asyncio
async def test_fire_loop_ambiguous_failure_marks_failed_no_retry(fire_env) -> None:
    from kazma_core.x_api.client import XApiError

    fire, _, sched = fire_env
    pid = sched.add(text="ambiguous", fire_at=time.time() - 5)
    _FakeXClient.exc = XApiError("X API timed out. Did not retry.", transient=True)

    await fire._fire_due_posts()
    await fire._fire_due_posts()  # a second poll must NOT retry

    got = sched.get(pid)
    assert got.status == STATUS_FAILED
    # Only one send attempt — the double-post guard.
    assert _FakeXClient.calls == ["ambiguous"]


@pytest.mark.asyncio
async def test_fire_loop_skips_when_connector_disabled(fire_env, monkeypatch: pytest.MonkeyPatch) -> None:
    fire, _, sched = fire_env
    pid = sched.add(text="wont fire", fire_at=time.time() - 5)
    monkeypatch.setattr(fire, "get_x_config", lambda: _cfg(enabled=False))

    await fire._fire_due_posts()

    got = sched.get(pid)
    assert got.status == STATUS_FAILED
    assert _FakeXClient.calls == []


@pytest.mark.asyncio
async def test_fire_loop_does_not_post_a_cancelled_item(fire_env) -> None:
    fire, _, sched = fire_env
    pid = sched.add(text="cancelled before fire", fire_at=time.time() - 5)
    assert sched.cancel(pid) is True

    await fire._fire_due_posts()

    # The cancel-race guard must prevent publication.
    assert _FakeXClient.calls == []
    assert sched.get(pid).status == STATUS_CANCELLED
