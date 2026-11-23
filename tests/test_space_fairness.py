"""Fairness between spaces and between the people of a space (Spaces S4-04).

One space cannot starve another, and in a shared space one person cannot
take the space's whole share. Tested on the real queues, each with one queue
for all -- the order and the slots as they were -- as the control:

* **turns** (``turn_segment_budget`` through the real ``run_agent_turn`` and
  graph): a turn beyond its person's share, or its space's, waits and is
  refused with an honest end, its graph never run; another space's turn
  runs; an approval's resume is let in; a sub-agent's turn takes no slot.
  Every caller of the budget checks ``segment.refused`` (a gate; a planted
  caller that runs the graph anyway is the control).
* **scheduled jobs** (the real scheduler's poll on a real store): another
  space's due job fires while the first space's backlog waits its turn; the
  due list's window takes turns by space and person.
* **document jobs** (the real SQLite claim): within a space, the person with
  the fewest running goes first.
* **the memory lane** (the real claim on a real ``memory_ops.db``): spaces
  take turns (N37).
* **rate windows** (the real dependency): a person's two sessions share one
  window; a space's people together get ``SPACE_WINDOW_FACTOR`` people's.
* **crawls, research runs, swarm tasks**: a person's share of the space's.
* **the People page** lists every limit, in both languages.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from kazma_core.spaces import fairness, identity, people, scope, space_limits
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

from tests._space_matrix import Matrix

A, B = Matrix.SPACE_A, Matrix.SPACE_B
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def matrix(monkeypatch):
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    with Matrix() as made:
        yield made


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(scope, "_enforced", True)


@pytest.fixture
def shared(matrix, enforced):
    """Space A shared by its maker (A) and person B; B also has space B."""
    identity.add_member(A, matrix.account_b)
    return matrix


def _limits(space: str, **values: int) -> None:
    people.set_space_limits(space, values, by=OWNER_ACCOUNT_ID)


def _one_queue_for_all(monkeypatch, cap: int) -> None:
    """The control: every space and person in one queue of *cap* slots --
    no share of a space or a person, as before S4-04 (and S2-08)."""
    monkeypatch.setattr(fairness, "shares", lambda kind, space_id: (cap, cap))
    for slots in (fairness.turn_slots, fairness.scheduled_slots):
        real = slots._try

        def one_queue(space: str, person: str, cap_: tuple[int, int], force: bool, real=real) -> str | None:
            return real("one queue", "", cap_, force)

        monkeypatch.setattr(slots, "_try", one_queue)
        monkeypatch.setattr(slots, "_release", lambda space, person, s=slots, r=slots._release: r("one queue", ""))


async def _until(check: Any, seconds: float = 10.0) -> None:
    for _ in range(int(seconds / 0.01)):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("never happened")


# ── a person's share ────────────────────────────────────────────────────


def test_a_persons_share_is_half_of_a_shared_space(matrix, enforced) -> None:
    reg = identity.read_registry()
    assert fairness.person_share(8, A, reg) == 8  # one member: the whole of it
    identity.add_member(A, matrix.account_b)
    reg = identity.read_registry()
    assert [fairness.person_share(n, A, reg) for n in (1, 2, 5, 8)] == [1, 1, 3, 4]
    _limits(A, turns=6)
    assert fairness.shares("turns", A) == (6, 3)


def test_nothing_is_counted_while_spaces_is_not_enforced(matrix) -> None:
    async def take() -> Any:
        return await fairness.turn_slots.take(A, matrix.account_a, wait_s=0)

    assert fairness.shares("turns", A) is None
    assert asyncio.run(take()) is None and fairness.turn_slots.running(A) == 0


def _thread_hops(monkeypatch, matrix: Matrix) -> list[str]:
    """Every slot a turn and a due job take, through the real turn runner:
    the calls handed to a thread on the way (S4-04 review: one per turn and
    per due job with Spaces off)."""
    hops: list[str] = []
    real = asyncio.to_thread

    async def counting(func: Any, /, *args: Any, **kwargs: Any) -> Any:
        hops.append(getattr(func, "__name__", repr(func)))
        return await real(func, *args, **kwargs)

    held = _Held()
    held.go.set()
    graph = _turn_graph(held)

    async def scenario() -> None:
        monkeypatch.setattr(asyncio, "to_thread", counting)
        try:
            with matrix.as_a():
                for slots in (fairness.turn_slots, fairness.scheduled_slots):
                    release = await slots.take(A, matrix.account_a)
                    if release is not None:
                        release()
                await _turn(graph, "hops-1", "a question")
        finally:
            monkeypatch.setattr(asyncio, "to_thread", real)
    assert fairness.turn_slots.running(A) == 0 and fairness.scheduled_slots.running(A) == 0

    asyncio.run(scenario())
    return [h for h in hops if h == "shares"]


def test_with_spaces_off_a_slot_costs_no_thread_hop(matrix, monkeypatch) -> None:
    assert _thread_hops(monkeypatch, matrix) == []


def test_control_with_spaces_on_each_slot_reads_its_shares_in_a_thread(matrix, enforced, monkeypatch) -> None:
    assert _thread_hops(monkeypatch, matrix) == ["shares", "shares", "shares"]


# ── turns, through the real run_agent_turn and graph ────────────────────


class _Held:
    """A scripted supervisor that holds each turn until the test lets it go."""

    def __init__(self) -> None:
        self.go = asyncio.Event()
        self.ran: list[str] = []

    async def think(self, seconds: float) -> None:
        self.ran.append("ran")
        await self.go.wait()


def _turn_graph(held: _Held) -> Any:
    from tests.test_turn_budget import Script, build_graph

    script = Script(answer_s=1.0)
    script.think = held.think  # type: ignore[method-assign]
    return build_graph(script)


async def _turn(graph: Any, thread: str, text: str) -> Any:
    from kazma_core.agent.turn import run_agent_turn

    from tests.test_turn_budget import _turn_input

    return await run_agent_turn(graph=graph, thread_id=thread, state=_turn_input(thread, text), persist=False)


def _run_turns(shared: Matrix, monkeypatch) -> dict[str, Any]:
    """In shared space A (turns 2, so a person's share is 1): A's first turn
    holds; A's second is refused for A's share; B's turn in A runs; with A
    at its share, B's turn in space B still runs."""
    monkeypatch.setattr(fairness, "TURN_WAIT_S", 0.3)
    _limits(A, turns=2)
    held = _Held()
    graph = _turn_graph(held)
    out: dict[str, Any] = {}

    async def scenario() -> None:
        with shared.as_a():
            first = asyncio.create_task(_turn(graph, "t-a1", "first question"))
        await _until(lambda: held.ran == ["ran"])
        with shared.as_a():
            out["second"] = await _turn(graph, "t-a2", "second question")
        with scope.bind_scope(A, shared.account_b):
            third = asyncio.create_task(_turn(graph, "t-b-in-a", "a question in A"))
        await _until(lambda: held.ran == ["ran", "ran"])
        with scope.bind_scope(A, shared.account_b):
            out["a_full"] = await _turn(graph, "t-b-in-a-2", "another")
        with shared.as_b():
            other = asyncio.create_task(_turn(graph, "t-b", "a question in B"))
        await _until(lambda: len(held.ran) == 3, 3.0)
        out["running_a"] = fairness.turn_slots.running(A)
        held.go.set()
        out["done"] = [await first, await third, await other]
        out["ran"] = list(held.ran)
        out["graph"] = graph

    asyncio.run(scenario())
    return out


def test_a_turn_beyond_its_share_is_refused_and_another_spaces_turn_runs(shared, monkeypatch) -> None:
    out = _run_turns(shared, monkeypatch)
    second = out["second"]
    # A refusal, not a failure (the S4-04 review): answered, never counted as failed.
    assert not second.turn_failed and second.error is None
    assert "your share of this space's" in second.text
    assert not out["a_full"].turn_failed and "This space already runs 2 turns" in out["a_full"].text
    assert out["running_a"] == 2 and len(out["ran"]) == 3  # the refused turns' graphs never ran
    assert all(not r.turn_failed for r in out["done"]), out["done"]
    assert fairness.turn_slots.running(A) == 0 and fairness.turn_slots.running(B) == 0

    async def stored() -> Any:
        return await out["graph"].aget_state({"configurable": {"thread_id": "t-a2", "checkpoint_ns": ""}})

    values = asyncio.run(stored()).values
    messages = values["messages"]
    assert values["turn_failed"] is False and not values.get("error_message")
    assert [m["role"] for m in messages] == ["user", "assistant"]  # the question and the honest end
    assert messages[0]["content"] == "second question" and "your share" in messages[1]["content"]


def test_control_one_queue_for_all_lets_one_space_starve_another(shared, monkeypatch) -> None:
    _one_queue_for_all(monkeypatch, cap=2)
    monkeypatch.setattr(fairness, "TURN_WAIT_S", 0.3)
    held = _Held()
    graph = _turn_graph(held)

    async def scenario() -> Any:
        with shared.as_a():
            first = asyncio.create_task(_turn(graph, "c-a1", "one"))
            second = asyncio.create_task(_turn(graph, "c-a2", "two"))
        await _until(lambda: held.ran == ["ran", "ran"])
        with shared.as_b():
            starved = await _turn(graph, "c-b", "my turn")
        held.go.set()
        await first
        await second
        return starved

    starved = asyncio.run(scenario())
    assert "This space already runs 2 turns" in starved.text  # B waited behind A's turns and was refused


def test_a_refused_turn_is_answered_in_the_persons_language(shared, monkeypatch) -> None:
    monkeypatch.setattr(fairness, "TURN_WAIT_S", 0.1)
    _limits(A, turns=1)
    held = _Held()
    graph = _turn_graph(held)

    async def scenario() -> Any:
        with shared.as_a():
            first = asyncio.create_task(_turn(graph, "ar-1", "سؤال أول"))
        await _until(lambda: held.ran == ["ran"])
        with shared.as_a():
            refused = await _turn(graph, "ar-2", "عندي سؤال ثاني عن الطقس اليوم")
        held.go.set()
        await first
        return refused

    refused = asyncio.run(scenario())
    assert not refused.turn_failed and "حصتها" in refused.text


def _refused_turn_line(shared: Matrix, monkeypatch, caplog) -> str:
    """The line ``close_turn`` writes for a turn refused for its share --
    the one the daily digest and the weekly report read."""
    from collections import OrderedDict

    from kazma_ui import turn_runtime

    # The line is written once per turn per process: a table of its own.
    monkeypatch.setattr(turn_runtime, "_finished_logged", OrderedDict())
    monkeypatch.setattr(fairness, "TURN_WAIT_S", 0.1)
    _limits(A, turns=1)
    held = _Held()
    graph = _turn_graph(held)

    async def scenario() -> Any:
        with shared.as_a():
            first = asyncio.create_task(_turn(graph, "line-1", "first"))
        await _until(lambda: held.ran == ["ran"])
        with shared.as_a():
            await _turn(graph, "line-2", "refused question")
        held.go.set()
        await first
        return await graph.aget_state({"configurable": {"thread_id": "line-2", "checkpoint_ns": ""}})

    snap = asyncio.run(scenario())
    with caplog.at_level("INFO", logger="kazma_ui.turn_runtime"):
        turn_runtime._note_finished_turn(snap, "line-2")
    lines = [r.getMessage() for r in caplog.records if "Turn finished" in r.getMessage()]
    assert len(lines) == 1, lines
    return lines[0]


def test_a_refused_turn_is_not_counted_as_failed(shared, monkeypatch, caplog) -> None:
    assert "failed=no" in _refused_turn_line(shared, monkeypatch, caplog)


def test_control_a_refusal_written_as_a_failure_is_counted_failed(shared, monkeypatch, caplog) -> None:
    from kazma_core.agent import turn_budget

    real = turn_budget._reply_update

    def as_before(values: Any, input_state: Any, text: str, *, failed: bool = True) -> Any:
        return real(values, input_state, text)  # every honest end failed, refusals too

    monkeypatch.setattr(turn_budget, "_reply_update", as_before)
    assert "failed=yes" in _refused_turn_line(shared, monkeypatch, caplog)


def test_an_approvals_resume_is_let_in_beyond_the_share(shared, monkeypatch) -> None:
    from kazma_core.agent.turn_budget import turn_segment_budget
    from langgraph.types import Command

    monkeypatch.setattr(fairness, "TURN_WAIT_S", 0.0)
    _limits(A, turns=1)
    graph = SimpleNamespace(checkpointer=None)

    async def scenario() -> tuple[bool, bool, int]:
        with shared.as_a():
            async with turn_segment_budget(graph, {}, thread_id="r-1", input_state={"messages": []}) as running:
                async with turn_segment_budget(graph, {}, thread_id="r-1",
                                               input_state=Command(resume={"approved": True})) as resumed:
                    return running.refused, resumed.refused, fairness.turn_slots.running(A)

    first, resume, held = asyncio.run(scenario())
    assert (first, resume) == (False, False)
    # The resume ran inside the turn: it is part of it, not a second slot.
    assert held == 1


def test_a_sub_agents_turn_takes_no_slot_of_its_own(shared, monkeypatch) -> None:
    from kazma_core.agent.turn_budget import turn_segment_budget

    monkeypatch.setattr(fairness, "TURN_WAIT_S", 0.0)
    _limits(A, turns=1)
    graph = SimpleNamespace(checkpointer=None)

    async def scenario() -> tuple[bool, bool]:
        with shared.as_a():
            async with turn_segment_budget(graph, {}, thread_id="p-1", input_state={"messages": []}) as parent:

                async def sub_agent() -> bool:
                    async with turn_segment_budget(graph, {}, thread_id="p-1-sub",
                                                   input_state={"messages": []}) as child:
                        return child.refused

                return parent.refused, await asyncio.create_task(sub_agent())

    assert asyncio.run(scenario()) == (False, False)
    assert fairness.turn_slots.running(A) == 0


def _segment_callers(sources: dict[str, str]) -> list[str]:
    """Each ``async with turn_segment_budget(...) as X:`` whose body never
    reads ``X.refused``."""
    missing = []
    for rel, text in sources.items():
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, ast.AsyncWith):
                continue
            for item in node.items:
                call = item.context_expr
                name = getattr(getattr(call, "func", None), "id", None) or getattr(
                    getattr(call, "func", None), "attr", None)
                if name != "turn_segment_budget" or not isinstance(item.optional_vars, ast.Name):
                    continue
                var = item.optional_vars.id
                checked = any(isinstance(n, ast.Attribute) and n.attr == "refused"
                              and isinstance(n.value, ast.Name) and n.value.id == var
                              for stmt in node.body for n in ast.walk(stmt))
                if not checked:
                    missing.append(f"{rel}:{node.lineno}")
    return missing


def _product_sources() -> dict[str, str]:
    found: dict[str, str] = {}
    for package in ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway",
                    "kazma-cli/kazma_cli"):
        for path in sorted((REPO / package).rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "turn_segment_budget" in text:
                found[path.relative_to(REPO).as_posix()] = text
    return found


def test_every_caller_of_the_budget_checks_a_refused_turn() -> None:
    sources = _product_sources()
    assert len([s for s in sources.values() if "async with turn_segment_budget(" in s]) >= 3
    assert _segment_callers(sources) == []


def test_control_a_caller_that_runs_the_graph_anyway_is_found() -> None:
    planted = {"planted.py": (
        "async def run(graph, cfg, state):\n"
        "    async with turn_segment_budget(graph, cfg, input_state=state) as segment:\n"
        "        return await graph.ainvoke(state, cfg)\n")}
    assert _segment_callers(planted) == ["planted.py:2"]


# ── scheduled jobs, through the real scheduler and store ────────────────


async def _scheduled_world(tmp: Path, shared: Matrix) -> Any:
    from kazma_core.cron.scheduler import JobStatus, ScheduledJob, SQLiteCronStore

    store = SQLiteCronStore(db_path=str(tmp / "cron.db"))
    await store.init()
    now = datetime.now(UTC)
    for i in range(5):  # A's backlog, due first (task fires: a reminder takes no slot)
        await store.insert(ScheduledJob(
            job_id=f"a-{i}", timing="2026-11-23T09:00", prompt=f"A {i}", platform="telegram",
            thread_id=f"ta{i}", status=JobStatus.PENDING, kind="task",
            next_run=(now - timedelta(seconds=60 - i)).isoformat(), created_at=now.isoformat(),
            delivery_target="telegram:1", tenant_id=A, creator=shared.account_a))
    await store.insert(ScheduledJob(
        job_id="b-0", timing="2026-11-23T09:00", prompt="B", platform="telegram", thread_id="tb",
        status=JobStatus.PENDING, kind="task", next_run=(now - timedelta(seconds=1)).isoformat(),
        created_at=now.isoformat(), delivery_target="telegram:1", tenant_id=B, creator=shared.account_b))
    return store


def _fires(shared: Matrix, tmp: Path, monkeypatch) -> list[str]:
    """One pass of the real scheduler (two of its fires at once, A's share
    one): the jobs that reached their fire while the first ones run."""
    from kazma_core.cron.scheduler import CronScheduler

    fired: list[str] = []
    go = asyncio.Event()

    async def execute(self: Any, job: Any) -> None:
        fired.append(job.job_id)
        await go.wait()

    monkeypatch.setattr(CronScheduler, "_execute", execute)

    async def scenario() -> None:
        store = await _scheduled_world(tmp, shared)
        sched = CronScheduler(store=store, poll_interval=0.2, max_concurrent=2)
        sched._running = True
        try:
            await sched._poll_once()
            await _until(lambda: len(fired) >= 2, 3.0)
            await asyncio.sleep(0.3)
        finally:
            go.set()
            await asyncio.gather(*list(sched._exec_tasks), return_exceptions=True)
            await store.close()

    asyncio.run(scenario())
    return fired


def test_another_spaces_due_job_fires_while_a_backlog_waits(shared, tmp_path, monkeypatch) -> None:
    _limits(A, scheduled=1)
    fired = _fires(shared, tmp_path, monkeypatch)
    assert fired[:2] == ["a-0", "b-0"], fired


def test_control_one_queue_for_all_holds_the_other_space_back(shared, tmp_path, monkeypatch) -> None:
    from kazma_core.cron import store as cron_store

    _one_queue_for_all(monkeypatch, cap=10**6)
    real = cron_store.SQLiteCronStore.due_jobs

    async def oldest_first(self: Any, *, now: Any, limit: int = 100, fair: bool = False) -> Any:
        return await real(self, now=now, limit=limit, fair=False)

    monkeypatch.setattr(cron_store.SQLiteCronStore, "due_jobs", oldest_first)
    fired = _fires(shared, tmp_path, monkeypatch)
    assert fired[:2] == ["a-0", "a-1"], fired  # B's job waits behind A's backlog


def test_the_due_window_takes_turns_by_space_and_person(shared, tmp_path) -> None:
    from kazma_core.cron.scheduler import JobStatus, ScheduledJob

    async def scenario() -> tuple[list[str], list[str]]:
        store = await _scheduled_world(tmp_path, shared)
        now = datetime.now(UTC)
        # Person B's own job in shared space A, due after A's backlog.
        await store.insert(ScheduledJob(
            job_id="a-by-b", timing="2026-11-23T09:00", prompt="mine", platform="telegram", thread_id="tx",
            status=JobStatus.PENDING, kind="reminder", next_run=(now - timedelta(seconds=2)).isoformat(),
            created_at=now.isoformat(), delivery_target="telegram:1", tenant_id=A, creator=shared.account_b))
        try:
            fair = [j.job_id for j in await store.due_jobs(now=now, limit=3, fair=True)]
            plain = [j.job_id for j in await store.due_jobs(now=now, limit=3)]
        finally:
            await store.close()
        return fair, plain

    fair, plain = asyncio.run(scenario())
    assert fair == ["a-0", "b-0", "a-by-b"], fair
    assert plain == ["a-0", "a-1", "a-2"], plain  # control: oldest first, A's backlog fills the window


# ── document jobs, through the real claim ───────────────────────────────


def _document_queue(tmp_path: Path) -> Any:
    from kazma_core.documents.jobs import DocumentJobRepository
    from kazma_core.documents.models import DocumentJobState
    from kazma_core.documents.repository import DocumentRepository

    meta = DocumentRepository(tmp_path / "fair.db", tenant_quota_bytes=10_000_000)
    jobs = DocumentJobRepository(meta, jitter=lambda _b: 0, retry_base_seconds=5)

    def enqueue(actor: str, key: str) -> Any:
        document = meta.create_document(tenant_id=A, owner_id=actor, title="Document")
        payload = f"{actor}-{key}".encode()
        digest = hashlib.sha256(payload).hexdigest()
        blob = meta.register_blob(tenant_id=A, sha256=digest, byte_size=len(payload), storage_kind="originals")
        version = meta.create_version(tenant_id=A, document_id=document.id, actor_id=actor,
                                      source_blob_id=blob.id, source_sha256=digest,
                                      original_filename="d.pdf", mime_type="application/pdf")
        job = jobs.enqueue(tenant_id=A, workspace_id="w", document_id=document.id, version_id=version.id,
                           idempotency_key=key, actor_id=actor)
        for target in (DocumentJobState.QUARANTINED, DocumentJobState.VALIDATING,
                       DocumentJobState.READY_TO_PARSE):
            job = jobs.transition(tenant_id=A, job_id=job.id, expected_state=job.state,
                                  expected_version=job.version, new_state=target)
        return job

    return meta, jobs, enqueue


def test_within_a_space_the_person_with_fewest_running_goes_first(enforced, tmp_path) -> None:
    meta, jobs, enqueue = _document_queue(tmp_path)
    try:
        backlog = [enqueue("ali", f"ali-{n}") for n in range(4)]
        reems = enqueue("reem", "reem-0")
        claimed = [jobs.claim_next(owner=f"w{n}", lease_seconds=600).id for n in range(2)]
    finally:
        meta.close()
    assert claimed == [backlog[0].id, reems.id]


def test_control_one_queue_per_space_serves_one_persons_backlog_first(tmp_path) -> None:
    from kazma_core.documents.jobs import _ACTIVE_STATE_VALUES

    meta, jobs, enqueue = _document_queue(tmp_path)
    try:
        backlog = [enqueue("ali", f"ali-{n}") for n in range(4)]
        enqueue("reem", "reem-0")
        jobs.claim_next(owner="w0", lease_seconds=600)
        active = ",".join(f"'{s}'" for s in _ACTIVE_STATE_VALUES)
        old = jobs._conn.execute(
            f"WITH running AS (SELECT tenant_id, COUNT(*) AS n FROM document_jobs WHERE state IN ({active}) "
            "AND lease_owner IS NOT NULL GROUP BY tenant_id) SELECT j.id FROM document_jobs j "
            "LEFT JOIN running r ON r.tenant_id = j.tenant_id WHERE j.state = 'ready_to_parse' "
            "ORDER BY COALESCE(r.n, 0), COALESCE(j.retry_at, j.created_at), j.id LIMIT 1").fetchone()
    finally:
        meta.close()
    assert old["id"] == backlog[1].id  # the order as it was: Ali's backlog before Reem


# ── the memory lane, through the real claim ─────────────────────────────


def _memory_lane(tmp_path: Path, monkeypatch) -> list[str]:
    from kazma_core import paths
    from kazma_core.memory import task_queue

    monkeypatch.setattr(paths, "memory_ops_db", lambda: tmp_path / "memory_ops.db")
    for n in range(4):
        assert task_queue.enqueue_task("micro_consolidation", {"episode_id": f"a{n}"}, space=A)
    assert task_queue.enqueue_task("micro_consolidation", {"episode_id": "b0"}, space=B)
    claimed = task_queue._MemoryWorker()._claim_batch(2, "memory")
    return [json.loads(row["payload_json"])["episode_id"] for row in claimed]


def test_the_memory_lane_takes_turns_by_space(enforced, tmp_path, monkeypatch) -> None:
    assert _memory_lane(tmp_path, monkeypatch) == ["a0", "b0"]


def test_control_one_queue_for_all_claims_one_spaces_backlog_first(tmp_path, monkeypatch) -> None:
    assert _memory_lane(tmp_path, monkeypatch) == ["a0", "a1"]  # Spaces not enforced: oldest first


def test_a_task_names_the_space_it_was_queued_in(enforced, tmp_path, monkeypatch) -> None:
    from kazma_core import paths
    from kazma_core.memory import task_queue

    monkeypatch.setattr(paths, "memory_ops_db", lambda: tmp_path / "memory_ops.db")
    task_queue.enqueue_task("entity_merge", {"merge_id": "m", "tenant_id": B})
    with scope.bind_scope(A, "someone"):
        task_queue.enqueue_task("topic_summary_rebuild", {"summary_id": "s"})
    rows = sqlite3.connect(tmp_path / "memory_ops.db").execute(
        "SELECT task_type, claim_space FROM memory_task_queue ORDER BY created_at").fetchall()
    assert rows == [("entity_merge", B), ("topic_summary_rebuild", A)]


# ── rate windows ────────────────────────────────────────────────────────


def _rate_app(monkeypatch) -> Any:
    from fastapi import Depends, FastAPI, Request
    from kazma_ui import rate_limit

    monkeypatch.setattr(rate_limit, "_enabled", lambda: True)
    monkeypatch.setattr(rate_limit, "_per_minute", lambda bucket, default: default)
    rate_limit._windows.clear()
    app = FastAPI()

    @app.middleware("http")
    async def bind(request: Request, call_next: Any) -> Any:
        request.scope["kazma_asker"] = request.headers.get("x-person", "")
        request.scope["kazma_space"] = request.headers.get("x-space", "")
        return await call_next(request)

    @app.get("/paid", dependencies=[Depends(rate_limit.rate_limit("fairness_test", 2))])
    def paid() -> dict[str, bool]:
        return {"ok": True}

    return app


def _ask(client: Any, person: str, session: str, space: str = A) -> int:
    return client.get("/paid", headers={"x-person": person, "x-space": space},
                      cookies={"kazma-session": session}).status_code


def test_a_person_has_one_window_and_a_space_two_peoples_worth(enforced, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    with TestClient(_rate_app(monkeypatch)) as client:
        # Two browsers of one person share one window.
        assert [_ask(client, "ali", "s1"), _ask(client, "ali", "s2"), _ask(client, "ali", "s3")] == [200, 200, 429]
        assert [_ask(client, "reem", "s4"), _ask(client, "reem", "s5")] == [200, 200]
        refused = client.get("/paid", headers={"x-person": "sam", "x-space": A}, cookies={"kazma-session": "s6"})
        assert refused.status_code == 429 and "This space reached its rate limit" in refused.text
        assert _ask(client, "sam", "s7", space=B) == 200  # another space is not held back


def test_control_keyed_by_session_each_browser_gets_a_whole_window(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    with TestClient(_rate_app(monkeypatch)) as client:  # Spaces not enforced: as before
        codes = [_ask(client, "ali", f"s{n}") for n in range(1, 7)]
    assert codes == [200] * 6


# ── crawls, research runs and swarm tasks: a person's share ─────────────


@pytest.fixture
def journal(tmp_path, monkeypatch):
    from kazma_core import config_store
    from kazma_core.web_acquire import crawl_jobs

    store = config_store.ConfigStore(str(tmp_path / "journal.db"))
    monkeypatch.setattr(crawl_jobs, "config_store",
                        SimpleNamespace(get_config_store=lambda: store, ConfigStore=config_store.ConfigStore))
    yield store
    store.close()


def _crawl(space: str, person: str, n: int) -> Any:
    from kazma_core.web_acquire import crawl_jobs

    with scope.bind_scope(space, person):
        return crawl_jobs.start(f"lib-{n}", f"https://c{n}.example.test/",
                                {"mode": "local_only", "scope": "tree", "max_pages": 10})


def test_a_person_holds_half_a_shared_spaces_crawls(shared, journal) -> None:
    from kazma_core.web_acquire.result import AcquisitionError

    _limits(A, crawls=2)
    running = [_crawl(A, shared.account_a, 1)]
    with pytest.raises(AcquisitionError, match="your share of this space's"):
        _crawl(A, shared.account_a, 2)
    running.append(_crawl(A, shared.account_b, 3))  # the other person still crawls
    for job in running:
        job.finish("completed")


def test_control_a_space_of_one_member_gives_its_person_the_whole_share(matrix, enforced, journal) -> None:
    _limits(A, crawls=2)
    running = [_crawl(A, matrix.account_a, 1), _crawl(A, matrix.account_a, 2)]
    for job in running:
        job.finish("completed")


def test_a_person_holds_half_a_shared_spaces_research_runs(shared) -> None:
    from kazma_core.web_acquire import research_runtime
    from kazma_core.web_acquire.result import AcquisitionError

    _limits(A, research=2)

    async def scenario() -> None:
        with scope.bind_scope(A, shared.account_a):
            mine = await research_runtime._take_space_share()
            with pytest.raises(AcquisitionError, match="your share"):
                await research_runtime._take_space_share()
        with scope.bind_scope(A, shared.account_b):
            theirs = await research_runtime._take_space_share()
        assert research_runtime.research_running_in(A) == 2
        mine()
        theirs()

    asyncio.run(scenario())
    assert research_runtime.research_running_in(A) == 0 and not research_runtime._person_runs


def test_a_person_holds_half_a_shared_spaces_swarm_tasks(shared) -> None:
    from kazma_core.swarm import SwarmConfig, SwarmEngine
    from kazma_core.swarm.task import SwarmTask

    _limits(A, swarm=4)  # a person's share: 2
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))

    def task(person: str) -> SwarmTask:
        made = SwarmTask(prompt="x", workers=["w"])
        made.metadata = {"tenant_id": A, "asker": person}
        engine._active_tasks[made.id] = made
        return made

    task(shared.account_a)
    task(shared.account_a)
    theirs = task(shared.account_b)
    mine = task(shared.account_a)
    assert engine._running_ahead_of(mine) == (3, 2)
    assert "your share" in (engine._space_share_refusal(mine, engine._running_ahead_of(mine)) or "")
    assert engine._space_share_refusal(theirs, engine._running_ahead_of(theirs)) is None


# ── the People page ─────────────────────────────────────────────────────


def test_the_people_page_lists_every_limit_in_both_languages() -> None:
    from kazma_ui.i18n.catalog.settings import TRANSLATIONS as ENTRIES

    script = (REPO / "kazma-ui/kazma_ui/static/js/settings_ops.js").read_text(encoding="utf-8")
    listed = re.search(r"spaceLimitNames:\s*\[([^\]]*)\]", script)
    assert listed is not None
    names = tuple(re.findall(r"'([a-z_]+)'", listed.group(1)))
    assert names == space_limits.LIMIT_NAMES
    for name in names:
        entry = ENTRIES[f"settings.space_limit.{name}"]
        assert entry["en"] and entry["ar"]


def _fires_when_b_arrives_later(shared: Matrix, tmp: Path, monkeypatch) -> list[str]:
    """A's backlog is due and started in one pass; B's job comes due after,
    and the next pass starts it: the jobs that reached their fire before
    any ended."""
    from kazma_core.cron.scheduler import CronScheduler, JobStatus, ScheduledJob, SQLiteCronStore

    fired: list[str] = []
    go = asyncio.Event()

    async def execute(self: Any, job: Any) -> None:
        fired.append(job.job_id)
        await go.wait()

    monkeypatch.setattr(CronScheduler, "_execute", execute)

    async def scenario() -> None:
        store = SQLiteCronStore(db_path=str(tmp / "cron.db"))
        await store.init()
        now = datetime.now(UTC)
        for i in range(5):
            await store.insert(ScheduledJob(
                job_id=f"a-{i}", timing="2026-11-23T09:00", prompt=f"A {i}", platform="telegram",
                thread_id=f"ta{i}", status=JobStatus.PENDING, kind="task",
                next_run=(now - timedelta(seconds=60 - i)).isoformat(), created_at=now.isoformat(),
                delivery_target="telegram:1", tenant_id=A, creator=shared.account_a))
        sched = CronScheduler(store=store, poll_interval=0.2, max_concurrent=2)
        sched._running = True
        try:
            await sched._poll_once()
            await asyncio.sleep(0.3)
            await store.insert(ScheduledJob(
                job_id="b-0", timing="2026-11-23T09:00", prompt="B", platform="telegram", thread_id="tb",
                status=JobStatus.PENDING, kind="task", next_run=datetime.now(UTC).isoformat(),
                created_at=now.isoformat(), delivery_target="telegram:1", tenant_id=B,
                creator=shared.account_b))
            await sched._poll_once()
            await asyncio.sleep(0.5)
        finally:
            go.set()
            await asyncio.gather(*list(sched._exec_tasks), return_exceptions=True)
            await store.close()

    asyncio.run(scenario())
    return fired


def test_a_job_due_after_another_spaces_backlog_started_fires_at_once(shared, tmp_path, monkeypatch) -> None:
    _limits(A, scheduled=1)
    fired = _fires_when_b_arrives_later(shared, tmp_path, monkeypatch)
    assert fired[:2] == ["a-0", "b-0"], fired


def test_control_without_the_slots_it_waits_behind_the_backlog(shared, tmp_path, monkeypatch) -> None:
    # The due list still takes turns; only the slots are one queue for all.
    _one_queue_for_all(monkeypatch, cap=10**6)
    fired = _fires_when_b_arrives_later(shared, tmp_path, monkeypatch)
    assert fired[:2] == ["a-0", "a-1"] and "b-0" not in fired[:2], fired


def _reminder_behind_a_task(shared: Matrix, tmp: Path, monkeypatch) -> list[str]:
    """Person B's task fire in shared space A holds B's one scheduled slot
    (the space's share 2, a person's 1) while B's reminder comes due: the
    jobs that reached their fire before the task ended."""
    from kazma_core.cron.scheduler import CronScheduler, JobStatus, ScheduledJob, SQLiteCronStore

    fired: list[str] = []
    go = asyncio.Event()

    async def execute(self: Any, job: Any) -> None:
        fired.append(job.job_id)
        if job.kind == "task":
            await go.wait()

    monkeypatch.setattr(CronScheduler, "_execute", execute)

    async def scenario() -> None:
        store = SQLiteCronStore(db_path=str(tmp / "cron.db"))
        await store.init()
        now = datetime.now(UTC)
        await store.insert(ScheduledJob(
            job_id="task-b", timing="2026-11-23T09:00", prompt="a long task", platform="telegram",
            thread_id="tt", status=JobStatus.PENDING, kind="task",
            next_run=(now - timedelta(seconds=5)).isoformat(), created_at=now.isoformat(),
            delivery_target="telegram:1", tenant_id=A, creator=shared.account_b))
        sched = CronScheduler(store=store, poll_interval=0.2, max_concurrent=4)
        sched._running = True
        try:
            await sched._poll_once()
            await _until(lambda: "task-b" in fired, 3.0)
            await store.insert(ScheduledJob(
                job_id="remind-b", timing="2026-11-23T09:00", prompt="remind me at 9:00", platform="telegram",
                thread_id="tr", status=JobStatus.PENDING, kind="reminder",
                next_run=datetime.now(UTC).isoformat(), created_at=now.isoformat(),
                delivery_target="telegram:1", tenant_id=A, creator=shared.account_b))
            await sched._poll_once()
            await asyncio.sleep(0.6)  # three polls' worth: the slot's wait is one
        finally:
            go.set()
            await asyncio.gather(*list(sched._exec_tasks), return_exceptions=True)
            await store.close()

    asyncio.run(scenario())
    return fired


def test_a_reminder_never_waits_behind_its_persons_running_task(shared, tmp_path, monkeypatch) -> None:
    assert fairness.shares("scheduled", A) == (2, 1)
    fired = _reminder_behind_a_task(shared, tmp_path, monkeypatch)
    assert fired == ["task-b", "remind-b"], fired


def test_control_a_reminder_that_takes_a_slot_waits_behind_the_task(shared, tmp_path, monkeypatch) -> None:
    from kazma_core.cron.scheduler import CronScheduler

    # Today's rule before the fix: every due job takes a slot, reminders too.
    monkeypatch.setattr(CronScheduler, "_takes_a_slot", staticmethod(lambda job: True))
    fired = _reminder_behind_a_task(shared, tmp_path, monkeypatch)
    assert fired == ["task-b"], fired  # "remind me at 9:00" waits for the task to end
