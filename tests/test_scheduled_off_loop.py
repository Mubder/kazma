"""Scheduled reads settings, RBAC and SQLite off the event loop (Scheduled audit SCH-E10, S11).

The witnesses: the cron engine read ``cron.timezone`` on the loop (schedule,
list, finalize), and the Scheduled routes read the RBAC store, the zone (once
per job) and the delivery address on the loop. The zone, the tenant rule and
the address are now resolved once per request or fire, in a thread, and the
fire's graph is built in a thread. The static gate holds the area
(``tests/test_static_gates.py``, area "scheduled"); this file checks the
behaviour, and that the gate sees the planted shape.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
CSRF = {"X-Requested-With": "XMLHttpRequest"}


def _on_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


async def test_the_engine_reads_the_zone_off_the_loop(tmp_path, monkeypatch):
    import kazma_core.config_store as cs

    from kazma_core.cron.scheduler import CronScheduler, SQLiteCronStore

    seen: list[str] = []
    real = cs.get_config_store

    class Recorder:
        def get(self, key, default=None):
            if _on_loop():
                seen.append(key)
            return real().get(key, default)

        def __getattr__(self, name):
            return getattr(real(), name)

    store = SQLiteCronStore(db_path=str(tmp_path / "cron.db"))
    await store.init()
    monkeypatch.setattr(cs, "get_config_store", lambda: Recorder())
    try:
        sched = CronScheduler(store=store)
        await sched.schedule(timing="daily at 9am", prompt="x", delivery_target="telegram:42", tenant_id="default")
        await sched.list_jobs()
        await sched.reschedule((await store.list_all())[0].job_id, timing="2h")
    finally:
        await store.close()
    assert seen == [], f"settings read on the loop: {seen}"


def test_the_routes_read_settings_rbac_and_the_address_off_the_loop(tmp_path, monkeypatch):
    import kazma_core.cron.scheduler as sm
    import kazma_core.security.platform_rbac as rbac

    from kazma_ui.scheduled_api import create_scheduled_router

    sm_mod = importlib.import_module("kazma_core.tools.send_message")
    seen: dict[str, list[bool]] = {"rbac": [], "tz": [], "target": []}
    orig_tz = sm.resolve_cron_timezone_name

    def _tz():
        seen["tz"].append(_on_loop())
        return orig_tz()

    def _rbac():
        seen["rbac"].append(_on_loop())
        return False

    def _target():
        seen["target"].append(_on_loop())
        return "telegram:1001"

    monkeypatch.setattr(sm, "resolve_cron_timezone_name", _tz)
    monkeypatch.setattr(rbac, "multi_user_enabled", _rbac)
    monkeypatch.setattr(sm_mod, "resolve_delivery_target", _target)
    store = sm.SQLiteCronStore(str(tmp_path / "cron.db"))
    sched = sm.CronScheduler(store=store)
    monkeypatch.setattr(sm, "get_cron_scheduler", lambda: sched)

    @asynccontextmanager
    async def lifespan(_app):
        await store.init()
        yield
        await store.close()

    class _Agent:
        config: dict = {}

    app = FastAPI(lifespan=lifespan)
    app.include_router(create_scheduled_router(_Agent(), Jinja2Templates(
        directory=str(ROOT / "kazma-ui/kazma_ui/templates"))))
    with TestClient(app) as c:
        job = c.post("/api/scheduled/cron", json={"timing": "1h", "prompt": "p"}, headers=CSRF).json()
        c.post("/api/scheduled/cron", json={"timing": "2h", "prompt": "q"}, headers=CSRF)
        assert c.get("/api/scheduled/tasks").status_code == 200
        c.put(f"/api/scheduled/cron/{job['job_id']}", json={"timing": "3h"}, headers=CSRF)
    assert all(seen.values()), seen  # each was read
    assert not any(any(v) for v in seen.values()), seen  # and never on the loop
    # Once per request, not once per job.
    assert len(seen["tz"]) <= 4


def test_the_gate_sees_a_planted_scheduled_caller():
    """Negative control: an async Scheduled function reaching
    multi_user_or_production through another module's helper is flagged by
    the cross-module inference the area gate uses."""
    from tests.test_static_gates import _bound_stall_names, _loop_stall_helper_calls, _qualified_stall_helpers

    trees = {
        "kazma_core.tenant_isolation": ast.parse(
            "def multi_user_or_production():\n    return multi_user_enabled()\n"),
        "kazma_core.cron.planted": ast.parse(
            "from kazma_core.tenant_isolation import multi_user_or_production\n"
            "def _scope():\n    return multi_user_or_production()\n"
            "async def list_jobs():\n    return _scope()\n"),
    }
    stalls = _qualified_stall_helpers(trees)
    extra = _bound_stall_names("kazma_core.cron.planted", trees["kazma_core.cron.planted"], stalls)
    assert _loop_stall_helper_calls(trees["kazma_core.cron.planted"], extra) == [(5, "_scope")]
