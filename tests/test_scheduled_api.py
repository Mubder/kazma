"""Universal Scheduled Tasks API (/api/scheduled/*) — cron + X aggregator + CRUD.

Covers the aggregator list, cron create/edit/delete, X book/reschedule/delete,
tenant-agnostic happy paths, and the same-origin CSRF guard on mutations.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from kazma_core.x_api.schedule import XScheduledStore

_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "kazma-ui" / "kazma_ui" / "templates"
_CSRF = {"X-Requested-With": "XMLHttpRequest"}


class _FakeScheduler:
    """In-memory stand-in for CronScheduler (avoids aiosqlite loop binding)."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self._n = 0

    async def schedule(self, *, timing, prompt, platform="web", thread_id="", delivery_target=""):
        from kazma_core.cron.scheduler import parse_timing

        next_run = parse_timing(timing).isoformat()
        self._n += 1
        job_id = f"cron-{self._n:04d}"
        self.jobs[job_id] = {
            "job_id": job_id, "timing": timing, "prompt": prompt,
            "platform": platform, "thread_id": thread_id, "status": "pending",
            "created_at": "", "next_run": next_run, "last_result": None,
            "tenant_id": "default", "delivery_target": delivery_target,
        }
        return {"job_id": job_id, "timing": timing, "next_run": next_run, "status": "scheduled"}

    async def list_jobs(self):
        return list(self.jobs.values())

    async def reschedule(self, job_id, *, timing=None, prompt=None):
        if job_id not in self.jobs:
            return {"status": "not_found", "job_id": job_id}
        job = self.jobs[job_id]
        if timing:
            job["timing"] = timing
        if prompt:
            job["prompt"] = prompt
        return {"status": "rescheduled", "job_id": job_id, "next_run": job.get("next_run", "")}

    async def cancel(self, job_id):
        if job_id in self.jobs and self.jobs[job_id]["status"] == "pending":
            self.jobs[job_id]["status"] = "cancelled"
            return {"status": "cancelled", "job_id": job_id}
        return {"status": "not_found", "job_id": job_id}


@pytest.fixture()
def cron_scheduler(monkeypatch: pytest.MonkeyPatch):
    import kazma_core.cron.scheduler as sched_mod

    fake = _FakeScheduler()
    monkeypatch.setattr(sched_mod, "get_cron_scheduler", lambda: fake)
    return fake


@pytest.fixture()
def x_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import kazma_core.x_api.schedule as schedule_mod

    store = XScheduledStore(tmp_path / "x_scheduled.db")
    monkeypatch.setattr(schedule_mod, "get_x_scheduled_store", lambda: store)
    return store


@pytest.fixture()
def client(cron_scheduler, x_store):
    from kazma_ui.scheduled_api import create_scheduled_router

    app = FastAPI()
    templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

    class _FakeAgent:
        config = {}

    app.include_router(create_scheduled_router(_FakeAgent(), templates))
    with TestClient(app) as c:
        yield c


def test_list_tasks_aggregates_cron_and_x(client, cron_scheduler, x_store) -> None:
    import asyncio

    asyncio.run(cron_scheduler.schedule(timing="1h", prompt="A cron task"))
    x_store.add(text="A scheduled tweet", fire_at=9999999999.0)

    resp = client.get("/api/scheduled/tasks")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    sources = {t["source"] for t in data["tasks"]}
    assert sources == {"cron", "x"}


def test_cron_create_edit_delete(client) -> None:
    created = client.post("/api/scheduled/cron", json={"timing": "1h", "prompt": "do it"}, headers=_CSRF)
    assert created.status_code == 200, created.text
    job_id = created.json()["job_id"]

    edited = client.put(f"/api/scheduled/cron/{job_id}", json={"prompt": "edited prompt"}, headers=_CSRF)
    assert edited.status_code == 200, edited.text
    assert edited.json()["status"] == "rescheduled"

    deleted = client.delete(f"/api/scheduled/cron/{job_id}", headers=_CSRF)
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "cancelled"


def test_csrf_blocks_mutation_without_header(client) -> None:
    resp = client.post("/api/scheduled/cron", json={"timing": "1h", "prompt": "x"})
    assert resp.status_code == 403


def test_cron_create_rejects_bad_timing(client) -> None:
    resp = client.post("/api/scheduled/cron", json={"timing": "nonsense", "prompt": "x"}, headers=_CSRF)
    assert resp.status_code == 400


def test_x_reschedule_and_delete(client, x_store) -> None:
    import time as _time

    pid = x_store.add(text="move me", fire_at=_time.time() + 3600)

    moved = client.put(f"/api/scheduled/x/{pid}", json={"when": "2h"}, headers=_CSRF)
    assert moved.status_code == 200, moved.text
    assert moved.json()["ok"] is True

    deleted = client.delete(f"/api/scheduled/x/{pid}", headers=_CSRF)
    assert deleted.status_code == 200
    assert deleted.json()["cancelled"] is True

    # Deleting again reports not pending.
    again = client.delete(f"/api/scheduled/x/{pid}", headers=_CSRF)
    assert again.status_code == 400


def test_x_book_via_api(client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import kazma_core.x_api.config as config_mod
    import kazma_core.x_api.ledger as ledger_mod
    import kazma_core.x_api.policy as policy_mod
    import kazma_core.x_api.schedule as schedule_mod
    from kazma_core.x_api.config import XConfig, XCredentials
    from kazma_core.x_api.ledger import XPostLedger

    ledger = XPostLedger(tmp_path / "x_posts.db")
    store = XScheduledStore(tmp_path / "x_sched_book.db")
    cfg = XConfig(
        enabled=True, handle="@kazma",
        credentials=XCredentials("k", "ks", "t", "ts"),
        max_posts_per_day=8, max_posts_per_month=80, max_mentions=2,
        max_cashtags=1, max_hashtags=4, max_chars=280,
        duplicate_window_days=30, kill_switch=False,
    )
    monkeypatch.setattr(config_mod, "get_x_config", lambda: cfg)
    monkeypatch.setattr(ledger_mod, "get_ledger", lambda: ledger)
    monkeypatch.setattr(policy_mod, "get_ledger", lambda: ledger)
    monkeypatch.setattr(schedule_mod, "get_x_scheduled_store", lambda: store)
    monkeypatch.delenv("KAZMA_X_SCHEDULE", raising=False)
    monkeypatch.delenv("KAZMA_X_POST", raising=False)

    resp = client.post(
        "/api/scheduled/x",
        json={"text": "booked from the web", "when": "1h"},
        headers=_CSRF,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True and data["scheduled"] is True
    assert store.count_pending() == 1
