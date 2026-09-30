"""/health/ready answers inside the guard's probe, whatever its checks do.

The guard probes /health/ready with a 10 s timeout and restarts Kazma after
three probes that get no answer. The checks ran one after another, several
on the event loop: while Postgres was away on 2026-09-28 (a Docker Desktop
update) the settings, database, model-registry and provider checks each
waited on the pool, the probe timed out, and the guard restarted a Kazma
whose only problem was its database. The checks now run at once, each off
the loop and capped, so the probe gets an answer -- a 503 naming the
database -- and the guard can tell "down" from "its database is down".
"""

from __future__ import annotations

import asyncio
import time

import pytest

from kazma_ui import health

_CHECKS = ("check_config_store", "check_database", "check_swarm_engine",
           "check_model_registry", "check_agent_runner", "check_mcp",
           "check_cron", "check_schedulers", "check_llm_provider")


def _slow(name: str, seconds: float):
    component = name.removeprefix("check_")

    def check():
        time.sleep(seconds)
        return {"status": "ok", "component": component}

    return check


@pytest.fixture
def slow_checks(monkeypatch):
    def install(seconds: float, **overrides: float) -> None:
        for name in _CHECKS:
            monkeypatch.setattr(health, name, _slow(name, overrides.get(name, seconds)))
    return install


def _timed(coro) -> tuple[float, object]:
    """How long the route took to answer -- measured inside the loop, since
    asyncio.run then waits for a capped check's thread to finish, which a
    running server never does."""
    async def run() -> tuple[float, object]:
        start = time.monotonic()
        result = await coro
        return time.monotonic() - start, result

    return asyncio.run(run())


def test_the_checks_run_at_once(slow_checks):
    slow_checks(0.3)
    elapsed, resp = _timed(health._readiness())
    assert resp.status_code == 200
    # Nine checks of 0.3 s: one after another is 2.7 s.
    assert elapsed < 1.2, elapsed


def test_a_hung_database_is_a_quick_503_naming_it(slow_checks):
    # The pool waits 5 s per call while Postgres is away; the check is capped.
    slow_checks(0.05, check_database=5.0, check_config_store=5.0)
    elapsed, resp = _timed(health._readiness())
    assert resp.status_code == 503
    import json

    body = json.loads(resp.body)
    assert body["checks"]["database"]["status"] == "failed"
    assert "timed out" in body["checks"]["database"]["error"]
    # Well inside the guard's probe timeout (10 s).
    assert elapsed < 6.0, elapsed


def test_a_volatile_settings_store_says_only_a_restart_clears_it(slow_checks, monkeypatch):
    """A boot while the database was away keeps the in-memory settings store
    for the life of the process; the guard must restart it, not ride it out."""
    import json

    slow_checks(0.01)
    monkeypatch.setattr(health, "check_config_store", lambda: {
        "status": "failed", "component": "config_store", "error": "VOLATILE",
        "restart_required": True,
    })
    _elapsed, resp = _timed(health._readiness())
    assert resp.status_code == 503
    assert json.loads(resp.body)["restart_required"] is True

    # A database that is merely away is an outage to ride out: no flag.
    slow_checks(0.01)
    monkeypatch.setattr(health, "check_database", lambda: {
        "status": "failed", "component": "database", "error": "timed out (3s)",
    })
    _elapsed, resp = _timed(health._readiness())
    assert resp.status_code == 503
    assert "restart_required" not in json.loads(resp.body)


def test_one_check_that_raises_fails_only_itself(slow_checks, monkeypatch):
    slow_checks(0.01)

    def broken():
        raise RuntimeError("boom")

    monkeypatch.setattr(health, "check_mcp", broken)
    _elapsed, resp = _timed(health._readiness())
    import json

    body = json.loads(resp.body)
    assert body["checks"]["mcp"]["status"] == "failed"
    assert resp.status_code == 200  # mcp is not critical


def test_one_after_another_is_what_the_timing_catches(slow_checks, monkeypatch):
    """Negative control: the same checks gathered one after another."""
    slow_checks(0.3)

    async def sequential(*aws, **_kw):
        return [await aw for aw in aws]

    monkeypatch.setattr(health.asyncio, "gather", sequential)
    elapsed, _resp = _timed(health._readiness())
    assert elapsed > 2.0, elapsed
