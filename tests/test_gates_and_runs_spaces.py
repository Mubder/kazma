"""Spaces S1-21: approvals and running work carry their space and asker, and
every surface keeps to its own.

* Every run adapter stamps the space and the person a run belongs to, from
  the kind's own record (a class gate over the adapters' sources, and each
  adapter's stamp driven on its own records).
* The registry's listing keeps a surface to the space bound now; Kazma's own
  reader, the supervisor, reads every space's.
* The supervisor opens another space's incident and sends nothing about it
  to the install's channels (O1): a space's X post stuck in ``sending`` no
  longer pages the owner with its words (the S1-17 review's case).
* A decision on another space's incident answers as an unknown one; in a
  shared space a run is its asker's or the space owner's to act on.
* The automatic first steps are the owner's, for the owner's work.
* An approval gate names whose work asked.

Each rule has its control: the code as it was, or the rule taken out.
"""

from __future__ import annotations

import ast
import asyncio
import json
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from kazma_core.runs import contract, registry, supervisor
from kazma_core.runs import spaces as run_spaces
from kazma_core.runs.contract import ActionResult, KindSpec, Run, RunState
from kazma_core.runs.incidents import IncidentStore, get_incident_store, reset_incident_store
from kazma_core.runs.kinds import KINDS
from kazma_core.spaces import identity, scope

from tests._space_matrix import Matrix

REPO = Path(__file__).resolve().parents[1]
ADAPTER_SOURCES = (
    *sorted((REPO / "kazma-core" / "kazma_core" / "runs" / "adapters").glob("*.py")),
    REPO / "kazma-ui" / "kazma_ui" / "runs_chat_turn.py",
)
#: Adapters whose runs are the install's own work, with why.
INSTALL_ONLY = {
    "maintenance.py": "a maintenance sweep is the install's own work: no space's content",
}

T0 = 1_800_000_000.0


@pytest.fixture
def matrix(monkeypatch):
    with Matrix() as made:
        yield made


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(scope, "_enforced", True)


# ── every adapter names its space ────────────────────────────────────────


def _runs_without_space(source: str) -> list[int]:
    """The lines of ``Run(...)`` calls in *source* that pass no ``space=``."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Run"
                and not any(k.arg == "space" for k in node.keywords)):
            found.append(node.lineno)
    return found


def test_every_adapter_names_the_space_of_its_runs() -> None:
    problems = []
    for path in ADAPTER_SOURCES:
        if path.name in ("__init__.py", "_common.py") or path.name in INSTALL_ONLY:
            continue
        lines = _runs_without_space(path.read_text(encoding="utf-8"))
        problems += [f"{path.relative_to(REPO)}:{line}" for line in lines]
    assert problems == [], "each run names its space (Spaces S1-21): " + ", ".join(problems)
    names = {p.name for p in ADAPTER_SOURCES}
    assert all(name in names for name in INSTALL_ONLY)


def test_control_a_run_built_without_its_space_is_found() -> None:
    planted = "def f():\n    return Run(run_id='x', kind='k', native_id='n', title='t', state=s, native_state='r')\n"
    assert _runs_without_space(planted) == [2]
    assert _runs_without_space(planted.replace("native_state='r')", "native_state='r', space='a')")) == []


def test_the_swarm_adapter_stamps_space_and_asker() -> None:
    from kazma_core.runs.adapters.swarm import ADAPTER
    from kazma_core.swarm.task import SwarmTask, TaskStatus

    task = SwarmTask(prompt="p", status=TaskStatus.FAILED,
                     metadata={"tenant_id": "matrix-b", "asker": "acct-b"})
    run = ADAPTER._run(task, False, True)
    assert (run.space, run.asker) == ("matrix-b", "acct-b")


def test_the_swarm_adapter_reads_every_space_when_enforced(tmp_path, enforced) -> None:
    """With nothing bound the task store refuses a list once Spaces is
    enforced (S1-15): the supervisor read no swarm task at all."""
    from kazma_core.runs.adapters.swarm import _SwarmAdapter
    from kazma_core.swarm.task import SwarmTask, TaskStatus
    from kazma_core.swarm.task_store import TaskStore

    store = TaskStore(str(tmp_path / "tasks.db"))
    for space in ("matrix-a", "matrix-b"):
        store.persist_task(SwarmTask(prompt=space, status=TaskStatus.PENDING, metadata={"tenant_id": space}))
    found = _SwarmAdapter._stored(store, 0.0)
    assert sorted(t.metadata["tenant_id"] for t in found) == ["matrix-a", "matrix-b"]


def test_control_the_old_swarm_read_is_refused_with_nothing_bound(tmp_path, enforced) -> None:
    from kazma_core.spaces.scope import NoSpaceBound
    from kazma_core.swarm.task_store import TaskStore

    store = TaskStore(str(tmp_path / "tasks.db"))
    with pytest.raises(NoSpaceBound):
        store.list_tasks(status="pending", page_size=40)


def test_the_cron_document_and_session_adapters_stamp_space_and_asker() -> None:
    from kazma_core.runs.adapters.cron import ADAPTER as CRON
    from kazma_core.runs.adapters.documents import ADAPTER as DOCS
    from kazma_core.runs.adapters.research import SESSIONS

    job = SimpleNamespace(job_id="j", status="failed", fire_due=None, last_fire_at=None, finished_at=T0,
                          lease_until=None, delivery_error="", last_outcome="x", prompt="p", version=1,
                          delivery_target="", thread_id="", tenant_id="matrix-b", creator="acct-b")
    run = CRON._run(job, "failed", T0, None)
    assert (run.space, run.asker) == ("matrix-b", "acct-b")

    state = SimpleNamespace(value="dead_letter")
    doc_job = SimpleNamespace(id="d", state=state, lease_owner=None, lease_expires_at=None, stage="",
                              cancel_requested=False, created_at=T0, heartbeat_at=None, updated_at=T0,
                              retry_at=None, version=1, error_message="", error_code="", tenant_id="matrix-b",
                              document_id="doc", actor_id="acct-b")
    run = DOCS._run(doc_job, "t", None, T0)
    assert (run.space, run.asker) == ("matrix-b", "acct-b")

    row = {"id": "s", "topic": "t", "status": "error", "stage": "x", "sources": 0, "created_at": T0,
           "updated_at": T0, "error": "", "tenant_id": "matrix-b", "asker": "acct-b"}
    run = SESSIONS._run(row, {}, False)
    assert (run.space, run.asker) == ("matrix-b", "acct-b")


def test_a_crawl_is_its_folder_s_or_its_library_s_space(monkeypatch) -> None:
    from kazma_core.paths import data_dir
    from kazma_core.runs.adapters.research import _CrawlSpaces

    folder = data_dir() / "spaces" / "matrix-b" / "files"
    folder.mkdir(parents=True, exist_ok=True)
    spaces = _CrawlSpaces()
    assert spaces.of({"policy": {"workspace": str(folder)}}) == "matrix-b"
    assert spaces.of({"policy": {"workspace": str(data_dir().parent)}}) == ""

    class _Store:
        @staticmethod
        def library_space(library_id: str) -> str | None:
            return {"lib-b": "matrix-b"}.get(library_id)

    monkeypatch.setattr("kazma_core.stores.knowledge.get_knowledge_store", lambda: _Store())
    assert spaces.of({"library_id": "lib-b"}) == "matrix-b"
    assert spaces.of({"library_id": "gone"}) == ""


def test_a_memory_task_is_its_payload_s_space() -> None:
    from kazma_core.runs.adapters.memory_queue import _task_space

    assert _task_space("post_turn_memory", json.dumps({"tenant_id": "matrix-b"})) == "matrix-b"
    assert _task_space("native_backup", json.dumps({"tenant_id": "matrix-b"})) == ""
    assert _task_space("post_turn_memory", "not json") == ""


def test_a_chat_turn_is_its_thread_s_space(matrix) -> None:
    from kazma_core.spaces import threads

    from kazma_ui.runs_chat_turn import _home

    threads.record_thread("t-run-b", Matrix.SPACE_B, matrix.account_b)
    assert _home("t-run-b") == (Matrix.SPACE_B, matrix.account_b)
    assert _home("t-never-recorded") == ("", "")


# ── the listing ─────────────────────────────────────────────────────────


class FakeAdapter:
    STATE_MAP = {"running": RunState.RUNNING, "failed": RunState.FAILED}

    def __init__(self) -> None:
        self.runs: list[Run] = []
        self.acted: list[tuple[str, str]] = []

    async def list_runs(self, now, *, window_s):
        return list(self.runs)

    async def act(self, native_id, action, *, actor):
        self.acted.append((native_id, action))
        return ActionResult(True, "done", "done")


@pytest.fixture
def fake(tmp_path, monkeypatch):
    adapter = FakeAdapter()
    for kind in list(KINDS):
        monkeypatch.delitem(KINDS, kind)
    monkeypatch.setitem(KINDS, "fake", KindSpec(kind="fake", adapter="x:y", label="Fake job", label_ar="عمل",
                                                source="test", stall_after_s=600.0, dead_after_s=120.0))
    monkeypatch.setitem(registry._ADAPTERS, "fake", adapter)
    reset_incident_store(IncidentStore(tmp_path / "incidents.db"))
    yield adapter
    reset_incident_store(None)


def _run(ident: str, space: str, asker: str = "", *, state=RunState.FAILED, title: str = "") -> Run:
    return Run(run_id=f"fake:{ident}", kind="fake", native_id=ident, title=title or f"job {ident}", state=state,
               native_state="failed" if state == RunState.FAILED else "running", started_at=T0,
               progress_at=T0, ended_at=T0 if state == RunState.FAILED else None, version="1",
               actions=("cancel",), space=space, asker=asker)


def _listed(**kwargs) -> list[str]:
    return [r.native_id for r in asyncio.run(registry.list_runs(**kwargs)).runs]


def test_the_listing_keeps_to_the_bound_space(fake, matrix, enforced) -> None:
    fake.runs = [_run("a", Matrix.SPACE_A), _run("b", Matrix.SPACE_B), _run("install", "")]
    with matrix.as_a():
        assert _listed() == ["a"]
    with scope.bind_scope(scope.OWNER_SPACE_ID, scope.OWNER_ACCOUNT_ID):
        assert _listed() == ["install"]  # the install's own work is the owner's
    assert _listed() == []  # nothing bound: no one's
    assert sorted(_listed(everyone=True)) == ["a", "b", "install"]  # Kazma's own reader


def test_control_without_the_filter_a_member_sees_another_space_s_runs(fake, matrix, enforced, monkeypatch) -> None:
    fake.runs = [_run("a", Matrix.SPACE_A), _run("b", Matrix.SPACE_B)]
    monkeypatch.setattr(registry, "visible_here", lambda space: True)
    with matrix.as_a():
        assert sorted(_listed()) == ["a", "b"]


def test_an_install_without_spaces_lists_everything(fake) -> None:
    fake.runs = [_run("a", "matrix-a"), _run("b", "matrix-b"), _run("install", "")]
    assert sorted(_listed()) == ["a", "b", "install"]
    assert run_spaces.runs_refusal("refused") is None  # one operator: nothing refused


# ── the supervisor and the owner's channels ─────────────────────────────


class _XStore:
    """The X publication store, holding one stuck post in each space."""

    def __init__(self, marks) -> None:
        self.rows = {
            "matrix-a": {"id": "op-a", "state": "sending", "text": f"post {marks.a}", "lease_until": T0 - 3600,
                         "sent_at": T0 - 7200, "created_at": T0 - 7200, "updated_at": T0 - 3600, "attempts": 1,
                         "version": 3, "origin": "schedule", "reason": "", "outcome": ""},
            "matrix-b": {"id": "op-b", "state": "sending", "text": f"post {marks.b}", "lease_until": T0 - 3600,
                         "sent_at": T0 - 7200, "created_at": T0 - 7200, "updated_at": T0 - 3600, "attempts": 1,
                         "version": 3, "origin": "schedule", "reason": "", "outcome": ""},
        }

    def operation_tenants(self):
        return list(self.rows)

    def list_operations(self, *, tenant_id, state, limit):
        row = self.rows.get(tenant_id)
        return [row] if row is not None and row["state"] == state else []


@pytest.fixture
def x_world(tmp_path, monkeypatch, matrix):
    """The real X run kind and adapter over a store holding a post stuck in
    ``sending`` in each space, the real supervisor, a private incident store,
    recorded sends and alerts. Spaces enforced; the supervisor binds nothing."""
    from kazma_core.runs.adapters import x_posts

    marks = matrix.plant("xpost")
    store = _XStore(marks)
    spec = KINDS["x_post"]
    for kind in list(KINDS):
        monkeypatch.delitem(KINDS, kind)
    monkeypatch.setitem(KINDS, "x_post", spec)
    monkeypatch.setitem(registry._ADAPTERS, "x_post", x_posts.ADAPTER)
    monkeypatch.setattr(x_posts, "_store_and_tenants", lambda: (store, store.operation_tenants()))
    monkeypatch.setattr(scope, "_enforced", True)
    reset_incident_store(IncidentStore(tmp_path / "incidents.db"))
    sent: list[dict] = []
    alerts: list[dict] = []

    async def fake_send(target, text, *, backend="telegram", **kw):
        sent.append({"target": target, "text": text})
        return f"sent:{target}"

    def fake_alert(key, title, detail="", **kw):
        alerts.append({"key": key, "title": title, "detail": detail})
        if kw.get("on_delivery"):
            receipt = threading.Thread(target=kw["on_delivery"], args=(True,))
            receipt.start()
            receipt.join()
        return True

    monkeypatch.setattr("kazma_core.runs.notify._send", fake_send)
    monkeypatch.setattr("kazma_core.observability.ops_alerts.alert", fake_alert)
    monkeypatch.setattr(supervisor, "_watch_started", T0 - 3600)
    yield SimpleNamespace(marks=marks, sent=sent, alerts=alerts)
    reset_incident_store(None)


def _words(world) -> str:
    return json.dumps([*world.sent, *world.alerts])


def test_a_stuck_x_post_of_another_space_pages_nobody(x_world) -> None:
    asyncio.run(supervisor.tick(now=T0))
    rows = {row["space"]: row for row in get_incident_store().recent(limit=10)}
    # Both incidents are opened, each carrying its space ...
    assert set(rows) == {"matrix-a", "matrix-b"}
    assert rows["matrix-b"]["notify"] == "suppressed"
    assert rows["matrix-b"]["notify_detail"].startswith("kept in its space")
    # ... and nothing of either member's post reached the owner's channels.
    assert x_world.marks.b not in _words(x_world) and x_world.marks.a not in _words(x_world)
    assert x_world.sent == [] and x_world.alerts == []


def test_control_today_s_supervisor_pages_the_owner_with_the_post(x_world, monkeypatch) -> None:
    monkeypatch.setattr(supervisor, "kept_in_its_space", lambda space: False)
    monkeypatch.setattr("kazma_core.runs.notify.kept_in_its_space", lambda space: False)
    asyncio.run(supervisor.tick(now=T0))
    assert x_world.marks.b in _words(x_world)


def test_the_owner_s_own_stuck_post_still_pages_the_owner(x_world, monkeypatch) -> None:
    from kazma_core.runs.adapters import x_posts

    store = _XStore(x_world.marks)
    store.rows = {"default": {**store.rows["matrix-a"], "id": "op-owner"}}
    monkeypatch.setattr(x_posts, "_store_and_tenants", lambda: (store, store.operation_tenants()))
    asyncio.run(supervisor.tick(now=T0))
    assert x_world.marks.a in _words(x_world)


def test_a_card_left_pending_from_before_spaces_is_not_sent(fake, matrix, enforced, monkeypatch) -> None:
    from kazma_core.runs import notify

    sent: list[str] = []

    async def fake_send(target, text, *, backend="telegram", **kw):
        sent.append(text)
        return f"sent:{target}"

    monkeypatch.setattr(notify, "_send", fake_send)
    monkeypatch.setattr("kazma_core.observability.ops_alerts.alert",
                        lambda *a, **k: sent.append(str(a)) or True)
    row, _ = get_incident_store().open_incident(
        _run("b", Matrix.SPACE_B, title="job secret-b"), "failed",
        origin={"platform": "telegram", "target": "telegram:1", "thread_id": ""})
    assert row["notify"] == "pending"
    assert asyncio.run(notify.deliver(row)) == "refused"
    assert sent == [] and get_incident_store().get(row["id"])["notify"] == "refused"


def test_the_bell_shows_the_bound_space_s_incidents(fake, matrix, enforced) -> None:
    from kazma_core.runs.notify import bell_alerts

    store = get_incident_store()
    store.open_incident(_run("a", Matrix.SPACE_A, title="job alpha"), "failed", origin={})
    store.open_incident(_run("b", Matrix.SPACE_B, title="job bravo"), "failed", origin={})
    with matrix.as_a():
        assert [a["reason"] for a in bell_alerts()] == ["job alpha"]
    assert bell_alerts() == []


# ── deciding ────────────────────────────────────────────────────────────


def _decide(incident_id: str, action: str = "cancel"):
    from kazma_core.runs.decide import decide

    return asyncio.run(decide(incident_id, action, actor="web:test", platform="web"))


def test_a_decision_on_another_space_s_incident_is_unknown(fake, matrix, enforced) -> None:
    fake.runs = [_run("b", Matrix.SPACE_B, matrix.account_b)]
    row, _ = get_incident_store().open_incident(fake.runs[0], "failed", origin={})
    with matrix.as_a():
        outcome = _decide(row["id"])
    assert outcome.code == "unknown_incident" and outcome.incident is None
    assert fake.acted == [] and get_incident_store().get(row["id"])["state"] == "open"


def test_control_without_the_space_checks_a_member_acts_on_another_space(fake, matrix, enforced, monkeypatch) -> None:
    fake.runs = [_run("b", Matrix.SPACE_B, matrix.account_b)]
    row, _ = get_incident_store().open_incident(fake.runs[0], "failed", origin={})
    monkeypatch.setattr(run_spaces, "visible_here", lambda space: True)
    monkeypatch.setattr(registry, "visible_here", lambda space: True)
    monkeypatch.setattr(registry, "may_act", lambda space, asker: True)
    with matrix.as_a():
        outcome = _decide(row["id"])
    assert outcome.ok and fake.acted == [("b", "cancel")]


def test_in_a_shared_space_the_asker_or_the_owner_acts(fake, matrix, enforced) -> None:
    shared = identity.create_space("Shared", created_by=matrix.account_a, space_id="matrix-shared")["id"]
    identity.add_member(shared, matrix.account_b)
    fake.runs = [_run("by-a", shared, matrix.account_a), _run("by-b", shared, matrix.account_b)]
    store = get_incident_store()
    by_a, _ = store.open_incident(fake.runs[0], "failed", origin={})
    by_b, _ = store.open_incident(fake.runs[1], "failed", origin={})
    with scope.bind_scope(shared, matrix.account_b):
        refused = _decide(by_a["id"])
    assert refused.code == "not_yours" and fake.acted == []
    with scope.bind_scope(shared, matrix.account_a):  # the owner of the space
        assert _decide(by_b["id"]).ok
    assert fake.acted == [("by-b", "cancel")]


def test_runbooks_take_no_step_on_another_space_s_run(enforced) -> None:
    from kazma_core.runs.runbooks import refusal

    assert "another space" in refusal(_run("b", "matrix-b"), "cancel")
    assert refusal(_run("o", ""), "cancel") == ""
    assert refusal(_run("o", scope.OWNER_SPACE_ID), "cancel") == ""


def test_a_document_job_is_acted_on_as_the_person_acting(monkeypatch, matrix, enforced) -> None:
    from kazma_core.documents import ingestion, principal
    from kazma_core.runs.adapters.documents import ADAPTER

    calls: list[str] = []
    service = SimpleNamespace(cancel_job=lambda **kw: calls.append(kw["actor_id"]) or {"state": "cancelled"},
                              retry_job=None)
    monkeypatch.setattr(ingestion, "peek_ingestion_service", lambda: service)
    monkeypatch.setattr(principal, "owner_actor", lambda service, tenant: "the-owner")
    with matrix.as_a():
        assert asyncio.run(ADAPTER.act(f"{Matrix.SPACE_A}/j1", "cancel", actor="web:a")).ok
    assert calls == [matrix.account_a]
    # Not enforced, the owner's account as before.
    monkeypatch.setattr(scope, "_enforced", False)
    asyncio.run(ADAPTER.act("default/j2", "cancel", actor="web"))
    assert calls[-1] == "the-owner"


# ── incidents and gates carry space and asker ───────────────────────────


def test_an_older_incident_file_gets_its_space_columns(tmp_path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE run_incidents (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, kind TEXT NOT NULL, "
                 "condition TEXT NOT NULL, state TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, "
                 "opened_at REAL NOT NULL, updated_at REAL NOT NULL, title TEXT NOT NULL DEFAULT '', "
                 "detail TEXT NOT NULL DEFAULT '', run_state TEXT NOT NULL DEFAULT '', run_version TEXT NOT NULL "
                 "DEFAULT '', effects TEXT NOT NULL DEFAULT 'none', actions TEXT NOT NULL DEFAULT '[]', origin TEXT "
                 "NOT NULL DEFAULT '{}', link TEXT NOT NULL DEFAULT '', notify TEXT NOT NULL DEFAULT 'pending', "
                 "notify_detail TEXT NOT NULL DEFAULT '', decided_by TEXT NOT NULL DEFAULT '', decided_platform "
                 "TEXT NOT NULL DEFAULT '', decided_action TEXT NOT NULL DEFAULT '', decided_at REAL, outcome_code "
                 "TEXT NOT NULL DEFAULT '', outcome_message TEXT NOT NULL DEFAULT '', auto_attempts INTEGER NOT "
                 "NULL DEFAULT 0, lineage TEXT NOT NULL DEFAULT '', diagnosis TEXT NOT NULL DEFAULT '', "
                 "diagnosis_state TEXT NOT NULL DEFAULT '', diagnosed_at REAL)")
    conn.execute("INSERT INTO run_incidents (id, run_id, kind, condition, state, opened_at, updated_at) "
                 "VALUES ('inc_old', 'fake:o', 'fake', 'failed', 'open', 1, 1)")
    conn.commit()
    conn.close()
    store = IncidentStore(path)
    row, created = store.open_incident(_run("b", "matrix-b", "acct-b"), "failed", origin={})
    assert created and (row["space"], row["asker"]) == ("matrix-b", "acct-b")
    # A row from before is the owner's space's (N19).
    assert [r["id"] for r in store.recent(space=scope.OWNER_SPACE_ID)] == ["inc_old"]
    assert [r["id"] for r in store.recent(space="matrix-b")] == [row["id"]]


def test_a_gate_names_whose_work_asked(tmp_path, monkeypatch, matrix) -> None:
    from kazma_core.safety import hitl_gates
    from kazma_core.safety.hitl_gates import GateRow, gate_for, register_gate

    monkeypatch.setattr(hitl_gates, "_db_path_override", str(tmp_path / "gates.db"))
    monkeypatch.setattr(hitl_gates, "_schema_ready", False)
    monkeypatch.setattr(scope, "_enforced", True)
    with matrix.as_a():
        register_gate(GateRow(gate_id="g-a", thread_id="t-a", tool="file_write"))
    assert (gate_for("g-a").tenant_id, gate_for("g-a").asker) == (Matrix.SPACE_A, matrix.account_a)
    # A writer's own tenant is kept.
    with matrix.as_a():
        register_gate(GateRow(gate_id="g-b", thread_id="t-b", tool="x", tenant_id="named"))
    assert gate_for("g-b").tenant_id == "named"
    # Not enforced, the tenant is the writer's as before; the asker is stamped.
    monkeypatch.setattr(scope, "_enforced", False)
    with matrix.as_a():
        register_gate(GateRow(gate_id="g-c", thread_id="t-c", tool="x"))
    assert (gate_for("g-c").tenant_id, gate_for("g-c").asker) == ("", matrix.account_a)


def test_run_records_keep_the_asker_on_the_server() -> None:
    from kazma_core.runs.incidents import public

    run = _run("x", "matrix-a", "acct-secret")
    assert "asker" not in run.to_dict() and run.to_dict()["space"] == "matrix-a"
    assert "asker" not in public({"origin": {}, "asker": "acct-secret", "decided_action": ""})
    assert contract.Run.__dataclass_fields__["space"].default == ""
