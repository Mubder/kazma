"""Health carries bounded counters, tenant isolation and transition-only alerts."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    from kazma_core.x_api import health
    from kazma_core.x_api.reply_store import reset_reply_store

    monkeypatch.setattr(health, "_cycles", {})
    reset_reply_store(tmp_path / "replies.db")
    return health


def test_health_no_text_and_no_cross_tenant_counts(env):
    from kazma_core.tenant_context import tenant_scope
    from kazma_core.x_api.reply_store import get_reply_store

    with tenant_scope("tenant-a"):
        get_reply_store().claim(summon_id="456", parent_id="123", parent_text="Private source text",
                              target_handle="private", summoner="owner")
        get_reply_store().record_decision("456", draft="Private candidate", subject_id="coffee", decision={
            "usage": {"calls": 5, "output_tokens": 86, "missing_usage": 1},
            "checks": [{"check": "target", "verdict": "unknown", "reason": "Private reason"}]})
        snapshot = env.health_snapshot()
        assert snapshot["replies"]["model_calls"] == 5 and snapshot["replies"]["output_tokens"] == 86
        assert snapshot["replies"]["verification_verdicts"] == {"unknown": 1}
        assert "Private" not in json.dumps(snapshot)
    with tenant_scope("tenant-b"):
        assert env.health_snapshot()["replies"]["states"] == {}


def test_incident_and_recovery_enqueue_once_and_supersede_old_notice(env):
    from kazma_core.x_api.publication_store import get_publication_store

    for _ in range(6):
        env.record_cycle("scheduler", success=False, interval=30)
    store = get_publication_store()
    assert store.summary(tenant_id="default")["pending_notifications"] == 1
    failed = store.claim_notification(owner="worker")
    assert store.notification_current(failed)
    for _ in range(4):
        env.record_cycle("scheduler", success=True, interval=30)
    assert store.summary(tenant_id="default")["pending_notifications"] == 2
    assert not store.notification_current(failed)
    assert env.health_snapshot()["loops"]["scheduler"]["successful_cycles"] == 4


def test_staleness_uses_monotonic_even_when_wall_clock_changes(env, monkeypatch):
    from types import SimpleNamespace

    env.record_cycle("mentions", success=True, interval=60)
    previous = env._cycles["mentions"]["last_monotonic"]
    monkeypatch.setattr(env.time, "monotonic", lambda: previous + 121)
    monkeypatch.setattr(env.time, "time", lambda: 1)
    result = env._loop_health("mentions", SimpleNamespace(done=lambda: False))
    assert result["stale"]
    assert "last_monotonic" not in result


async def test_health_route_suppresses_config_read_side_effects(env, monkeypatch):
    import kazma_core.diagnostic_scope as diagnostics
    import kazma_ui.x_api as api

    def snapshot():
        assert diagnostics.active_diagnostic() == "/api/x/health"
        assert diagnostics.writes_suppressed()
        return {"scope": "read-only"}

    monkeypatch.setattr(env, "health_snapshot", snapshot)
    assert (await api._x_health()).status_code == 200
    assert diagnostics.active_diagnostic() is None


def _pending():
    from kazma_core.x_api.publication_store import get_publication_store

    return get_publication_store().summary(tenant_id="default")["pending_notifications"]


def test_a_loop_paged_before_a_restart_says_it_recovered_after_it(env, monkeypatch):
    """The failing card promises a recovery card; a restart kept it only in
    ``_cycles``, so the new process never sent it."""
    from kazma_core.observability import outage_pages

    for _ in range(3):
        env.record_cycle("mentions", success=False, interval=60, error="timeout")
    assert _pending() == 1
    monkeypatch.setattr(env, "_cycles", {})  # a new process
    for _ in range(3):
        env.record_cycle("mentions", success=False, interval=60, error="timeout")
    assert _pending() == 1, "still failing after the restart pages nothing new"
    env.record_cycle("mentions", success=True, interval=60)
    assert _pending() == 2
    assert outage_pages.outage_page_owed("x.loop:mentions") is None
    monkeypatch.setattr(env, "_cycles", {})
    env.record_cycle("mentions", success=True, interval=60)
    assert _pending() == 2, "a settled note is not sent again"


def test_without_the_mark_the_restart_swallows_the_recovery(env, monkeypatch):
    """Negative control: the process global alone, as before."""
    from kazma_core.observability import outage_pages

    monkeypatch.setattr(outage_pages, "outage_page_owed", lambda key: None)
    for _ in range(3):
        env.record_cycle("mentions", success=False, interval=60, error="timeout")
    monkeypatch.setattr(env, "_cycles", {})
    env.record_cycle("mentions", success=True, interval=60)
    assert _pending() == 1
