"""Universal Scheduled Tasks API (/api/scheduled/*) on the REAL scheduler and store.

The audit (SCH-S02 and section 7, stage 5) found the defects of this API sat
in a test gap: this file used an in-memory fake scheduler and seeded only
pending rows. It now runs the real ``CronScheduler`` + ``SQLiteCronStore``
behind the real router (a header middleware stands in for the auth
middleware's tenant binding, as the audit's witnesses did), and covers:

* SCH-S02 -- the edit round trip leaves a long prompt byte-identical, a batch
  job's wrapper intact; a stale form is refused by version.
* SCH-S03 -- every route keeps to the caller's tenant (another tenant's id is
  an unknown id), and with platform users every route refuses (decision D1).
* SCH-S06 / E14 -- past, zero, huge and oversized inputs answer 4xx with a
  reason (never 500 or 404); the job cap refuses with a code.
* SCH-S07 / E12 -- actions on running and finished jobs through the routes.
* SCH-S10 -- no response carries the chat address.
* SCH-S12 -- the declared public origin passes the same-origin rule, and
  every change records its actor and source.
"""

from __future__ import annotations

import importlib
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
_TEMPLATE_DIR = ROOT / "kazma-ui" / "kazma_ui" / "templates"
_CSRF = {"X-Requested-With": "XMLHttpRequest"}
_TARGET = "telegram:1804015016"


def _sm_mod():
    """The send_message MODULE — package attr of that name is the function."""
    return importlib.import_module("kazma_core.tools.send_message")


def _row(db: Path, job_id: str) -> dict:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return dict(conn.execute("SELECT * FROM cron_jobs WHERE job_id = ?", (job_id,)).fetchone())
    finally:
        conn.close()


@pytest.fixture()
def real_cron(tmp_path, monkeypatch):
    """The real CronScheduler + SQLiteCronStore behind the real router."""
    import kazma_core.cron.scheduler as sm
    from kazma_core.tenant_context import reset_current_tenant_id, set_current_tenant_id

    from kazma_ui.scheduled_api import create_scheduled_router

    db = tmp_path / "cron.db"
    store = sm.SQLiteCronStore(str(db))
    sched = sm.CronScheduler(store=store, graph_builder=None)  # never started: no poll loop
    monkeypatch.setattr(sm, "get_cron_scheduler", lambda: sched)
    monkeypatch.setattr(_sm_mod(), "operator_telegram_target", lambda: _TARGET)
    monkeypatch.setattr(_sm_mod(), "resolve_delivery_target", lambda: _TARGET)

    @asynccontextmanager
    async def lifespan(_app):
        await store.init()
        yield
        await store.close()

    app = FastAPI(lifespan=lifespan)

    @app.middleware("http")
    async def _tenant(request: Request, call_next):  # stands in for the auth middleware
        tid = request.headers.get("x-test-tenant")
        tok = set_current_tenant_id(tid) if tid else None
        try:
            return await call_next(request)
        finally:
            if tok is not None:
                reset_current_tenant_id(tok)

    class _Agent:
        config: dict = {}

    app.include_router(create_scheduled_router(_Agent(), Jinja2Templates(directory=str(_TEMPLATE_DIR))))
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, db=db, sched=sched, store=store)


def _create(c, prompt="do it", timing="1h", headers=None, **extra):
    r = c.post("/api/scheduled/cron", json={"timing": timing, "prompt": prompt, **extra},
               headers={**_CSRF, **(headers or {})})
    assert r.status_code == 200, r.text
    return r.json()


def _task(c, job_id, headers=None):
    return next(t for t in c.get("/api/scheduled/tasks", headers=headers or {}).json()["tasks"] if t["id"] == job_id)


# ── the basics, on the real store ────────────────────────────────────────


def test_cron_create_edit_delete(real_cron) -> None:
    c = real_cron.client
    body = _create(c)
    job_id = body["job_id"]
    assert body["destination"] == "telegram" and "delivery_target" not in body
    assert _row(real_cron.db, job_id)["delivery_target"] == _TARGET
    edited = c.put(f"/api/scheduled/cron/{job_id}", json={"prompt": "edited prompt"}, headers=_CSRF)
    assert edited.status_code == 200 and edited.json()["status"] == "rescheduled"
    deleted = c.delete(f"/api/scheduled/cron/{job_id}", headers=_CSRF)
    assert deleted.status_code == 200 and deleted.json()["status"] == "cancelled"


def test_list_tasks_extracts_wrapped_arabic_tweet(real_cron) -> None:
    c = real_cron.client
    prompt = (
        "Rescheduled batch job 2/8 — POST ONE TWEET ONLY. Call x_post with "
        'EXACTLY this text: "كاظمه لا تجيب فقط — بل تنفّذ وفق جدول."'
    )
    job_id = _create(c, prompt=prompt)["job_id"]
    conn = sqlite3.connect(str(real_cron.db))
    conn.execute("UPDATE cron_jobs SET status = 'done', last_result = ? WHERE job_id = ?",
                 ("Status for batch job 2/8: 1. **x_post attempted** with the exact locked text: > "
                  "كاظمه لا تجيب فقط — بل تنفّذ وفق جدول.", job_id))
    conn.commit()
    conn.close()
    task = _task(c, job_id)
    assert task["summary"].startswith("كاظمه")
    assert "POST ONE TWEET" not in task["summary"]
    assert task["kicker"] == "Batch 2/8"
    assert task["dir"] == "rtl"
    assert task["outcome"] == "x_post attempted"
    assert task["class"] == "done" and task["actions"] == ["restore"]
    assert "prompt" not in task  # a display summary, never the prompt


def test_scheduled_page_shows_error_not_last_result_as_red() -> None:
    html = (_TEMPLATE_DIR / "scheduled.html").read_text(encoding="utf-8")
    js = (ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "scheduled.js").read_text(encoding="utf-8")
    assert "errorText(task)" in html
    assert "outcomeText(task)" in html
    assert 'x-text="reason(task)"' not in html
    assert "extractPostBody" in js
    assert ":dir=" in html
    assert "auditDetailText" in js
    assert "auditDetailUrl" in js
    assert "request_body" not in html
    assert "response_body" not in html
    assert "x-text=\"auditDetail()\"" not in html
    assert "JSON.stringify(e" not in js
    assert "JSON.stringify(this.audit" not in js


def test_csrf_blocks_mutation_without_header(real_cron) -> None:
    resp = real_cron.client.post("/api/scheduled/cron", json={"timing": "1h", "prompt": "x"})
    assert resp.status_code == 403


def test_cron_create_fails_closed_without_delivery_target(real_cron, monkeypatch) -> None:
    """Web cron create used to hardcode delivery_target="" — fire then drop."""
    monkeypatch.setattr(_sm_mod(), "resolve_delivery_target", lambda: "")
    resp = real_cron.client.post("/api/scheduled/cron", json={"timing": "1h", "prompt": "x"}, headers=_CSRF)
    assert resp.status_code == 400 and "delivery" in resp.json()["error"].lower()
    assert real_cron.client.get("/api/scheduled/tasks").json()["tasks"] == []


# ── SCH-S02: the edit round trip ─────────────────────────────────────────


def test_a_timing_edit_leaves_the_prompt_byte_identical(real_cron) -> None:
    """List, open edit (GET the whole prompt), save only the timing: the
    stored prompt is untouched -- the page used to send the 200-character
    summary back as the prompt."""
    c = real_cron.client
    long_prompt = "Check the deploy dashboard and report every failing check. " + "x" * 600 + " END-MARKER"
    wrapped = 'Rescheduled batch job 2/8 - POST ONE TWEET ONLY. Call x_post with EXACTLY this text: "Hello from Kazma"'
    for prompt in (long_prompt, wrapped):
        job_id = _create(c, prompt=prompt)["job_id"]
        task = _task(c, job_id)
        job = c.get(f"/api/scheduled/cron/{job_id}").json()["job"]
        assert job["prompt"] == prompt and job["version"] == task["version"]
        r = c.put(f"/api/scheduled/cron/{job_id}", json={"timing": "2h", "expected_version": job["version"]},
                  headers=_CSRF)
        assert r.status_code == 200 and r.json()["status"] == "rescheduled"
        assert _row(real_cron.db, job_id)["prompt"] == prompt
        assert _row(real_cron.db, job_id)["timing"] == "2h"


def test_a_stale_form_is_refused_by_version(real_cron) -> None:
    c = real_cron.client
    job_id = _create(c, prompt="first")["job_id"]
    version = c.get(f"/api/scheduled/cron/{job_id}").json()["job"]["version"]
    assert c.put(f"/api/scheduled/cron/{job_id}", json={"prompt": "second", "expected_version": version},
                 headers=_CSRF).status_code == 200
    stale = c.put(f"/api/scheduled/cron/{job_id}", json={"prompt": "stale", "expected_version": version},
                  headers=_CSRF)
    assert stale.status_code == 409 and stale.json()["code"] == "conflict"
    assert _row(real_cron.db, job_id)["prompt"] == "second"


def test_negative_control_the_old_round_trip_truncates(real_cron) -> None:
    """Sending the list's summary back as the prompt -- the old openEdit /
    saveCron pair -- changes the stored prompt; the round trip above does not."""
    c = real_cron.client
    long_prompt = "Report every failing check. " + "y" * 700
    job_id = _create(c, prompt=long_prompt)["job_id"]
    summary = _task(c, job_id)["summary"]
    c.put(f"/api/scheduled/cron/{job_id}", json={"timing": "2h", "prompt": summary}, headers=_CSRF)
    assert _row(real_cron.db, job_id)["prompt"] != long_prompt


# ── SCH-S03: tenants, and the single-operator rule (D1) ─────────────────


def test_every_route_keeps_to_the_callers_tenant(real_cron, monkeypatch) -> None:
    """In production mode, tenant B with tenant A's ids changes nothing and
    reads nothing: every answer is exactly an unknown id's."""
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    c = real_cron.client
    a = {"x-test-tenant": "tenant-a"}
    b = {"x-test-tenant": "tenant-b"}
    job_id = _create(c, prompt="tenant A reminder", headers=a)["job_id"]
    assert _row(real_cron.db, job_id)["tenant_id"] == "tenant-a"
    assert job_id not in [t["id"] for t in c.get("/api/scheduled/tasks", headers=b).json()["tasks"]]
    unknown = "cron-000000000000"
    for method, path, body in (
        ("GET", "/api/scheduled/cron/{}", None),
        ("PUT", "/api/scheduled/cron/{}", {"prompt": "tenant B instruction"}),
        ("DELETE", "/api/scheduled/cron/{}", None),
        ("POST", "/api/scheduled/cron/{}/restore", {"timing": "2h"}),
    ):
        theirs = c.request(method, path.format(job_id), json=body, headers={**_CSRF, **b})
        nobodys = c.request(method, path.format(unknown), json=body, headers={**_CSRF, **b})
        assert (theirs.status_code, theirs.json().get("code")) == (nobodys.status_code, nobodys.json().get("code"))
        assert theirs.status_code == 404
    row = _row(real_cron.db, job_id)
    assert row["prompt"] == "tenant A reminder" and row["status"] == "pending"


async def test_the_tools_keep_to_the_callers_tenant(real_cron, monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    c = real_cron.client
    job_id = _create(c, prompt="tenant A reminder", headers={"x-test-tenant": "tenant-a"})["job_id"]
    tools = importlib.import_module("kazma_skills.native.task_scheduler_cron.tools")
    from kazma_core.tenant_context import reset_current_tenant_id, set_current_tenant_id

    async def as_b(fn, *args, **kw):
        tok = set_current_tenant_id("tenant-b")
        try:
            return await fn(*args, **kw)
        finally:
            reset_current_tenant_id(tok)

    from functools import partial

    assert '"not_found"' in c.portal.call(partial(as_b, tools.edit_scheduled, job_id, prompt="tenant B"))
    assert '"not_found"' in c.portal.call(as_b, tools.cancel_scheduled, job_id)
    assert job_id not in c.portal.call(as_b, tools.list_scheduled)
    row = _row(real_cron.db, job_id)
    assert row["prompt"] == "tenant A reminder" and row["status"] == "pending"


def test_with_platform_users_every_route_refuses(real_cron, monkeypatch) -> None:
    import kazma_core.security.platform_rbac as rbac

    monkeypatch.setattr(rbac, "_load_users_from_store", lambda: [{"username": "a"}])
    c = real_cron.client
    for method, path, body in (
        ("GET", "/api/scheduled/tasks", None),
        ("GET", "/api/scheduled/cron/cron-x", None),
        ("POST", "/api/scheduled/cron", {"timing": "1h", "prompt": "x"}),
        ("PUT", "/api/scheduled/cron/cron-x", {"prompt": "x"}),
        ("DELETE", "/api/scheduled/cron/cron-x", None),
        ("POST", "/api/scheduled/cron/cron-x/restore", {"timing": "1h"}),
        ("POST", "/api/scheduled/x", {"text": "x", "when": "1h"}),
    ):
        r = c.request(method, path, json=body, headers=_CSRF)
        assert r.status_code == 409 and r.json()["code"] == "scheduled_single_operator", (method, path)


def test_the_page_says_why_in_its_language(monkeypatch) -> None:
    """The page renders, with the refusal flag for its banner (a catalog key
    in both languages)."""
    import kazma_core.security.platform_rbac as rbac
    from fastapi.responses import HTMLResponse

    from kazma_ui.i18n.catalog.scheduled import TRANSLATIONS
    from kazma_ui.scheduled_api import create_scheduled_router

    seen: dict = {}

    class _Templates:
        def TemplateResponse(self, request, name, context):  # noqa: N802 — Jinja2Templates' API
            seen.update(context)
            return HTMLResponse("ok")

    class _Agent:
        config: dict = {}

    app = FastAPI()
    app.include_router(create_scheduled_router(_Agent(), _Templates()))
    monkeypatch.setattr(rbac, "_load_users_from_store", lambda: [{"username": "a"}])
    with TestClient(app) as c:
        assert c.get("/scheduled").status_code == 200
    assert seen["single_operator_refusal"] is True
    monkeypatch.setattr(rbac, "_load_users_from_store", lambda: [])
    with TestClient(app) as c:
        c.get("/scheduled")
    assert seen["single_operator_refusal"] is False
    html = (_TEMPLATE_DIR / "scheduled.html").read_text(encoding="utf-8")
    assert "{% if single_operator_refusal %}" in html and "t('scheduled.single_operator')" in html
    assert TRANSLATIONS["scheduled.single_operator"]["ar"] and TRANSLATIONS["scheduled.single_operator"]["en"]


async def test_with_platform_users_every_tool_refuses(real_cron, monkeypatch) -> None:
    import json

    import kazma_core.security.platform_rbac as rbac

    monkeypatch.setattr(rbac, "_load_users_from_store", lambda: [{"username": "a"}])
    tools = importlib.import_module("kazma_skills.native.task_scheduler_cron.tools")
    for out in (await tools.schedule_task("1h", "x"), await tools.list_scheduled(),
                await tools.cancel_scheduled("cron-x"), await tools.edit_scheduled("cron-x", prompt="y")):
        assert json.loads(out)["code"] == "scheduled_single_operator"


def test_negative_control_the_old_store_ignored_the_tenant(real_cron, monkeypatch) -> None:
    """The old store methods took only the id: a statement without the tenant
    predicate changes tenant A's row from tenant B's request."""
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    c = real_cron.client
    job_id = _create(c, prompt="tenant A reminder", headers={"x-test-tenant": "tenant-a"})["job_id"]
    conn = sqlite3.connect(str(real_cron.db))
    conn.execute("UPDATE cron_jobs SET prompt = 'tenant B instruction' WHERE job_id = ?", (job_id,))  # old shape
    conn.commit()
    conn.close()
    assert _row(real_cron.db, job_id)["prompt"] == "tenant B instruction"


# ── SCH-S06 / E14: bounds and status codes ───────────────────────────────


@pytest.mark.parametrize(("timing", "prompt", "status"), [
    ("2020-01-01T00:00:00", "past", 400),
    ("0m", "now", 400),
    ("99999999999h", "x", 400),
    ("nonsense", "x", 400),
    ("1h", "p" * 8001, 422),
    ("x" * 101, "p", 422),
])
def test_bad_inputs_are_refused_with_a_reason(real_cron, timing, prompt, status) -> None:
    r = real_cron.client.post("/api/scheduled/cron", json={"timing": timing, "prompt": prompt}, headers=_CSRF)
    assert r.status_code == status, r.text
    assert real_cron.client.get("/api/scheduled/tasks").json()["tasks"] == []


def test_a_bad_edit_timing_is_400_not_404(real_cron) -> None:
    c = real_cron.client
    job_id = _create(c)["job_id"]
    r = c.put(f"/api/scheduled/cron/{job_id}", json={"timing": "nonsense"}, headers=_CSRF)
    assert r.status_code == 400 and r.json()["status"] == "error"


def test_the_job_cap_refuses_with_a_code(real_cron, monkeypatch) -> None:
    from kazma_core.config_store import get_config_store

    get_config_store().set("cron.max_jobs_per_tenant", 2)
    try:
        c = real_cron.client
        _create(c)
        _create(c)
        r = c.post("/api/scheduled/cron", json={"timing": "1h", "prompt": "third"}, headers=_CSRF)
        assert r.status_code == 409 and r.json()["code"] == "tenant_job_limit"
    finally:
        get_config_store().delete("cron.max_jobs_per_tenant")


def test_mutations_carry_the_scheduled_rate_bucket() -> None:
    from kazma_ui.scheduled_api import create_scheduled_router

    class _Agent:
        config: dict = {}

    router = create_scheduled_router(_Agent(), Jinja2Templates(directory=str(_TEMPLATE_DIR)))
    for route in router.routes:
        if set(getattr(route, "methods", ())) & {"POST", "PUT", "DELETE"}:
            calls = [getattr(d.call, "__qualname__", "") for d in route.dependant.dependencies]
            assert any("rate_limit" in q for q in calls), route.path


# ── SCH-S07 / E12: running and finished jobs through the routes ─────────


def test_running_and_finished_jobs_answer_with_reasons(real_cron) -> None:
    c = real_cron.client
    job_id = _create(c, prompt="r")["job_id"]
    conn = sqlite3.connect(str(real_cron.db))
    conn.execute("UPDATE cron_jobs SET status='running' WHERE job_id=?", (job_id,))
    conn.commit()
    conn.close()
    assert _task(c, job_id)["actions"] == ["edit", "cancel"]
    edit = c.put(f"/api/scheduled/cron/{job_id}", json={"timing": "5m"}, headers=_CSRF)
    assert edit.status_code == 202 and edit.json()["status"] == "edit_pending" and edit.json()["message"]
    d = c.delete(f"/api/scheduled/cron/{job_id}", headers=_CSRF)
    assert d.status_code == 202 and d.json()["status"] == "cancel_pending"

    jid = _create(c, prompt="cancel me")["job_id"]
    assert c.delete(f"/api/scheduled/cron/{jid}", headers=_CSRF).status_code == 200
    r = c.put(f"/api/scheduled/cron/{jid}", json={"timing": "5m"}, headers=_CSRF)
    assert r.status_code == 409 and r.json()["code"] == "not_editable" and r.json()["error"]
    assert _row(real_cron.db, jid)["status"] == "cancelled"  # an edit never brings it back
    again = c.post(f"/api/scheduled/cron/{jid}/restore", json={"timing": "5m"}, headers=_CSRF)
    assert again.status_code == 200 and _row(real_cron.db, jid)["status"] == "pending"


# ── SCH-S10: no chat address in any answer ───────────────────────────────


async def test_no_answer_carries_the_chat_address(real_cron) -> None:
    import re

    c = real_cron.client
    tools = importlib.import_module("kazma_skills.native.task_scheduler_cron.tools")
    job_id = _create(c)["job_id"]
    from functools import partial

    outputs = [
        c.portal.call(tools.schedule_task, "1h", "ping"),
        c.portal.call(tools.list_scheduled),
        c.portal.call(partial(tools.edit_scheduled, job_id, prompt="pong")),
        c.portal.call(tools.cancel_scheduled, job_id),
        c.get("/api/scheduled/tasks").text,
        c.get(f"/api/scheduled/cron/{job_id}").text,
    ]
    for out in outputs:
        assert not re.search(r"\b\w+:\d{3,}\b", out), out


def test_negative_control_the_old_result_named_the_address() -> None:
    import json
    import re

    old = json.dumps({"job_id": "cron-1", "delivery_target": _TARGET})
    assert re.search(r"\b\w+:\d{3,}\b", old)


# ── SCH-S12: the declared origin, and the actor on every change ─────────


def test_the_declared_public_origin_passes(real_cron, monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_PUBLIC_URL", "https://kazma.example")
    r = real_cron.client.post(
        "/api/scheduled/cron", json={"timing": "1h", "prompt": "p"},
        headers={**_CSRF, "Origin": "https://kazma.example", "Host": "127.0.0.1:9090"})
    assert r.status_code == 200, r.text
    evil = real_cron.client.post(
        "/api/scheduled/cron", json={"timing": "1h", "prompt": "p"},
        headers={**_CSRF, "Origin": "https://evil.example", "Host": "127.0.0.1:9090"})
    assert evil.status_code == 403


def test_every_change_records_its_actor_and_source(real_cron) -> None:
    c = real_cron.client
    job_id = _create(c)["job_id"]
    c.put(f"/api/scheduled/cron/{job_id}", json={"prompt": "edited"}, headers=_CSRF)
    c.delete(f"/api/scheduled/cron/{job_id}", headers=_CSRF)
    row = _row(real_cron.db, job_id)
    assert row["created_by"] and row["updated_by"] and row["source"] == "web"
    conn = sqlite3.connect(str(real_cron.db))
    events = conn.execute("SELECT event, actor, source FROM cron_events WHERE job_id = ? ORDER BY id",
                          (job_id,)).fetchall()
    conn.close()
    assert [e[0] for e in events] == ["created", "edit", "cancel"]
    assert all(actor.startswith("web:") and source == "web" for _e, actor, source in events)


# ── X rows: operation truth; the parallel reschedule path is gone ────────


@pytest.fixture()
def x_env(tmp_path, monkeypatch):
    import kazma_core.x_api.config as config_mod
    import kazma_core.x_api.ledger as ledger_mod
    import kazma_core.x_api.policy as policy_mod
    import kazma_core.x_api.publication_store as pub
    import kazma_core.x_api.reply_store as reply_mod
    import kazma_core.x_api.schedule as schedule_mod
    from kazma_core.x_api.config import XConfig, XCredentials
    from kazma_core.x_api.ledger import XPostLedger
    from kazma_core.x_api.schedule import XScheduledStore

    ledger = XPostLedger(tmp_path / "x_posts.db")
    cfg = XConfig(enabled=True, handle="@kazma", account_id="123",
                  credentials=XCredentials("k", "ks", "t", "ts"), max_posts_per_day=8,
                  max_posts_per_month=80, max_mentions=2, max_cashtags=1, max_hashtags=4,
                  max_chars=280, duplicate_window_days=30, kill_switch=False)
    monkeypatch.setattr(config_mod, "get_x_config", lambda: cfg)
    monkeypatch.setattr(ledger_mod, "get_ledger", lambda: ledger)
    monkeypatch.setattr(policy_mod, "get_ledger", lambda: ledger)
    monkeypatch.setattr(reply_mod, "_store", reply_mod.XReplyStore(tmp_path / "x_reply.db"))
    monkeypatch.setattr(schedule_mod, "get_x_scheduled_store", lambda: XScheduledStore(tmp_path / "x_sched.db"))
    monkeypatch.delenv("KAZMA_X_SCHEDULE", raising=False)
    monkeypatch.delenv("KAZMA_X_POST", raising=False)
    pub._set_publication_store_for_tests(pub._PublicationStore(tmp_path / "x_pub.db"))
    try:
        yield cfg
    finally:
        pub._set_publication_store_for_tests(None)


def test_x_rows_are_the_operation_truth(real_cron, x_env) -> None:
    c = real_cron.client
    booked = c.post("/api/scheduled/x", json={"text": "booked from the web", "when": "1h"}, headers=_CSRF)
    assert booked.status_code == 200 and booked.json()["scheduled"] is True
    task = next(t for t in c.get("/api/scheduled/tasks").json()["tasks"] if t["source"] == "x")
    assert task["status"] == "scheduled" and task["class"] == "upcoming"
    assert task["actions"] == ["edit", "cancel"] and task["operation_id"] and task["version"]


def test_the_pages_parallel_x_reschedule_and_delete_are_gone(real_cron) -> None:
    """PUT/DELETE /api/scheduled/x/{id} skipped the account binding X Studio's
    queue actions hold (SCH-S04); the page uses the queue actions."""
    c = real_cron.client
    assert c.put("/api/scheduled/x/1", json={"when": "2h"}, headers=_CSRF).status_code in (404, 405)
    assert c.delete("/api/scheduled/x/1", headers=_CSRF).status_code in (404, 405)
    js = (ROOT / "kazma-ui/kazma_ui/static/js/scheduled.js").read_text(encoding="utf-8")
    assert "'/api/x/queue/'" in js and "/reschedule" in js and "/cancel" in js
    assert "'/api/scheduled/x/'" not in js


def test_x_booking_cannot_report_scheduled_over_a_cancelled_row(real_cron, x_env) -> None:
    """The page's booking path answers with the stored truth too (SCH-E08)."""
    from kazma_core.x_api.publication_service import update_schedule

    c = real_cron.client
    first = c.post("/api/scheduled/x", json={"text": "launch note", "when": "1h"}, headers=_CSRF).json()
    assert first["scheduled"] is True
    update_schedule(first["operation_id"], expected_version=first["version"], action="cancel")
    again = c.post("/api/scheduled/x", json={"text": "launch note", "when": "2h"}, headers=_CSRF).json()
    # A fresh booking (no fixed key here): scheduled, and its own operation.
    assert again["operation_id"] != first["operation_id"] and again["state"] == "scheduled"
