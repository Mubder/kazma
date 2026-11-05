"""One database-away state for the process (plan A3).

`kazma_core.db.availability` is the one place that decides "Postgres is away":
a failure that is an outage starts it, one prober thread signs in until the
database answers, one WARNING says it, one page goes out after two minutes and
one more when it answers again (only if the first one reached somebody).

Held here:

* what counts as an outage (psycopg's connection classes, the pool's timeout,
  Kazma's own ``PostgresUnreachable``) and what does not (a query cancelled, a
  closed pool, SQLite);
* the state machine: one prober per outage, one WARNING, one away page at
  ``PAGE_AFTER_S``, one back page and only after a delivered away page, a
  success older than the outage ignored;
* ``wait_while_away``, the pool-chatter filter, the snapshot the guard reads;
* every background loop that sleeps is aware of the state or declared with why
  (the class gate, with a planted loop as its negative control);
* the memory queue, the maintenance sweeps and cron hold their work instead of
  spending attempts on it (each with the old behaviour as its control);
* boot waits for the settings store without a limit while the guard watches,
  and tells the guard what it waits for;
* the guard reads that boot status and the app's database snapshot.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import logging
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from kazma_core.db import availability

REPO = Path(__file__).resolve().parents[1]


# ── fixtures ──────────────────────────────────────────────────────────────


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture()
def db(monkeypatch):
    """The availability module on a fake clock, with its prober and pager
    recorded instead of run."""
    clock = _Clock()
    monkeypatch.setattr(availability, "_clock", clock)
    monkeypatch.setattr(availability, "_wall", lambda: 1_700_000_000.0 + clock.t)
    spawned: list[tuple[int, int]] = []
    monkeypatch.setattr(availability, "_spawn_prober", lambda oid, gen: spawned.append((oid, gen)))
    alerts: list[dict] = []

    def _record(key, title, detail, **kw):
        alerts.append({"key": key, "title": title, "detail": detail, **kw})

    monkeypatch.setattr(availability, "_alert", _record)
    probe = {"answer": (False, "connection refused")}
    monkeypatch.setattr(availability, "_probe", lambda: probe["answer"])
    availability.reset()
    yield SimpleNamespace(clock=clock, spawned=spawned, alerts=alerts, probe=probe)
    availability.reset()


def _psycopg_error(cls_name: str = "OperationalError", sqlstate: str | None = None,
                   module: str = "psycopg") -> Exception:
    cls = type(cls_name, (Exception,), {"__module__": module})
    exc = cls("boom")
    exc.sqlstate = sqlstate
    return exc


def _outage() -> Exception:
    return _psycopg_error(sqlstate="08006")


def _keys(alerts):
    return [a["key"] for a in alerts]


# ── what an outage is ─────────────────────────────────────────────────────


@pytest.mark.parametrize("exc", [
    _psycopg_error(sqlstate="08006"),
    _psycopg_error(sqlstate="08001"),
    _psycopg_error(sqlstate="57P01"),
    _psycopg_error(sqlstate="3D000"),
    _psycopg_error(sqlstate="28P01"),
    _psycopg_error(sqlstate=None),
    _psycopg_error("InterfaceError", sqlstate=None),
    _psycopg_error("PoolTimeout", module="psycopg_pool"),
    _psycopg_error("TooManyRequests", module="psycopg_pool"),
    type("PostgresUnreachable", (RuntimeError,), {"__module__": "kazma_core.db.postgres_pool"})("away"),
], ids=lambda e: f"{type(e).__module__}.{type(e).__name__}:{getattr(e, 'sqlstate', None)}")
def test_an_outage_is_an_outage(exc):
    assert availability.is_outage(exc)


@pytest.mark.parametrize("exc", [
    _psycopg_error(sqlstate="57014"),          # a query cancelled
    _psycopg_error(sqlstate="23505"),          # a unique violation
    _psycopg_error("PoolClosed", module="psycopg_pool"),
    _psycopg_error("ProgrammingError", sqlstate=None),
    sqlite3.OperationalError("database is locked"),
    ValueError("not a database"),
    None,
], ids=lambda e: f"{type(e).__name__}:{getattr(e, 'sqlstate', None)}")
def test_other_failures_are_not_an_outage(exc):
    assert not availability.is_outage(exc)


def test_an_outage_is_found_through_its_cause():
    try:
        try:
            raise _outage()
        except Exception as inner:
            raise RuntimeError("settings read failed") from inner
    except RuntimeError as outer:
        assert availability.is_outage(outer)


def test_a_cause_cycle_ends():
    a = RuntimeError("a")
    b = RuntimeError("b")
    a.__cause__ = b
    b.__cause__ = a
    assert not availability.is_outage(a)


def test_kazma_database_is_matched_by_server(monkeypatch):
    pytest.importorskip("psycopg")
    monkeypatch.setattr("kazma_core.db.backend.get_database_url",
                        lambda: "postgresql://kazma:pw@127.0.0.1:5432/kazma")
    assert availability.is_kazma_database("postgres://other@localhost/kazma")
    assert not availability.is_kazma_database("postgresql://kazma@localhost:6543/kazma")
    assert not availability.is_kazma_database("postgresql://kazma@db.example.com/kazma")
    assert not availability.is_kazma_database(None)
    monkeypatch.setattr("kazma_core.db.backend.get_database_url", lambda: None)
    assert not availability.is_kazma_database("postgresql://kazma@localhost/kazma")


# ── the state machine ─────────────────────────────────────────────────────


def test_a_failure_that_is_not_an_outage_changes_nothing(db):
    assert availability.note_failure("x", ValueError("no")) is False
    assert not availability.is_away()
    assert db.spawned == []
    assert availability.snapshot() == {"state": "answering"}


def test_a_reason_alone_is_an_outage(db):
    assert availability.note_failure("boot", reason="refused") is True
    assert availability.is_away()
    assert availability.cached_reason() == "refused"


def test_one_prober_per_outage_however_many_notice_it(db):
    for kind in ("memory_queue", "maintenance", "cron", "memory_queue"):
        assert availability.note_failure(kind, _outage())
    assert len(db.spawned) == 1
    assert sorted(availability.snapshot()["noticed_by"]) == ["cron", "maintenance", "memory_queue"]


def test_one_warning_one_page_after_two_minutes(db, caplog):
    caplog.set_level(logging.DEBUG, logger="kazma_core.db.availability")
    availability.note_failure("memory_queue", _outage())
    outage_id = db.spawned[0][0]
    availability._probe_once(outage_id)
    availability._probe_once(outage_id)
    warnings = [r for r in caplog.records
                if r.name == "kazma_core.db.availability" and r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "not answering" in warnings[0].getMessage()
    assert db.alerts == []  # not before PAGE_AFTER_S

    db.clock.advance(availability.PAGE_AFTER_S - 1)
    availability._probe_once(outage_id)
    assert db.alerts == []

    db.clock.advance(2)
    availability._probe_once(outage_id)
    availability._probe_once(outage_id)
    assert _keys(db.alerts) == ["database.away"]
    assert db.alerts[0]["severity"] == "error"
    snap = availability.snapshot()
    assert snap["state"] == "away" and snap["confirmed"] and snap["page_requested"]
    assert not snap["paged"]  # nobody said it arrived yet


def test_no_page_for_an_outage_nobody_confirmed(db):
    """A failure the prober never confirmed (it answered at once) pages nothing."""
    availability.note_failure("x", _outage())
    db.clock.advance(availability.PAGE_AFTER_S * 3)
    db.probe["answer"] = (True, None)
    availability._probe_once(db.spawned[0][0])
    assert db.alerts == []
    assert not availability.is_away()


@pytest.mark.parametrize("answer", [(True, None), (None, None)],
                         ids=["answers", "no database configured"])
def test_a_probe_that_answers_ends_the_outage(db, answer):
    availability.note_failure("x", _outage())
    db.probe["answer"] = answer
    availability._probe_once(db.spawned[0][0])
    assert not availability.is_away()
    assert availability.cached_reason() is None


def _page_then_back(db, *, delivered: bool | None, back_first: bool = False):
    availability.note_failure("x", _outage())
    oid = db.spawned[0][0]
    availability._probe_once(oid)
    db.clock.advance(availability.PAGE_AFTER_S + 1)
    availability._probe_once(oid)
    away = db.alerts[0]
    assert away["key"] == "database.away"
    if not back_first and delivered is not None:
        away["on_delivery"](delivered)
    db.probe["answer"] = (True, None)
    db.clock.advance(30)
    availability._probe_once(oid)
    if back_first and delivered is not None:
        away["on_delivery"](delivered)


def test_back_page_after_a_delivered_away_page(db):
    _page_then_back(db, delivered=True)
    assert _keys(db.alerts) == ["database.away", "database.back"]
    assert db.alerts[1]["severity"] == "success"
    assert "Back after" in db.alerts[1]["detail"]
    assert availability.snapshot()["state"] == "answering"
    assert availability.snapshot()["last_outage_s"] > availability.PAGE_AFTER_S


def test_back_page_when_the_away_page_lands_after_the_database_came_back(db):
    _page_then_back(db, delivered=True, back_first=True)
    assert _keys(db.alerts) == ["database.away", "database.back"]


@pytest.mark.parametrize("delivered", [False, None], ids=["refused", "never answered"])
def test_no_back_page_when_the_away_page_reached_nobody(db, delivered):
    _page_then_back(db, delivered=delivered)
    assert _keys(db.alerts) == ["database.away"]


def test_back_page_only_once(db):
    _page_then_back(db, delivered=True, back_first=True)
    db.alerts[0]["on_delivery"](True)  # a second delivery report
    assert _keys(db.alerts) == ["database.away", "database.back"]


def test_a_success_older_than_the_outage_is_ignored(db):
    before = availability.now()
    db.clock.advance(5)
    availability.note_failure("x", _outage())
    availability.note_success("settings", since=before)
    assert availability.is_away()
    availability.note_success("settings", since=availability.now())
    assert not availability.is_away()


def test_a_success_while_answering_does_nothing(db):
    availability.note_success("settings")
    assert availability.snapshot() == {"state": "answering"}


def test_a_stale_prober_is_started_again_once_per_period(db):
    availability.note_failure("x", _outage())
    assert len(db.spawned) == 1
    db.clock.advance(availability.PROBER_STALE_S - 1)
    assert availability.is_away()
    assert len(db.spawned) == 1
    db.clock.advance(2)
    assert availability.is_away()
    assert availability.is_away()
    assert len(db.spawned) == 2
    # The new prober's generation retires the old one.
    assert db.spawned[1][1] != db.spawned[0][1]


def test_a_new_outage_after_one_ended_is_its_own(db):
    availability.note_failure("x", _outage())
    availability._probe_once(db.spawned[0][0])
    db.probe["answer"] = (True, None)
    availability._probe_once(db.spawned[0][0])
    db.probe["answer"] = (False, "refused")
    availability.note_failure("x", _outage())
    assert len(db.spawned) == 2
    assert db.spawned[1][0] != db.spawned[0][0]
    # A probe for the old outage changes nothing about the new one.
    availability._probe_once(db.spawned[0][0])
    assert not availability.snapshot()["confirmed"]


def test_snapshot_says_what_the_guard_reads(db):
    availability.note_failure("cron", _outage())
    availability._probe_once(db.spawned[0][0])
    db.clock.advance(10)
    snap = availability.snapshot()
    for key in ("away_s", "probe_age_s", "probe_stale_after_s", "since", "cause",
                "confirmed", "noticed_by", "page_at_s", "paged", "page_requested"):
        assert key in snap, key
    assert snap["probe_stale_after_s"] == availability.PROBER_STALE_S
    assert snap["page_at_s"] == availability.PAGE_AFTER_S
    assert snap["cause"] == "connection refused"
    json.dumps(snap)


@pytest.mark.parametrize("seconds, text", [
    (5, "5 s"), (60, "1 min"), (125, "2 min 5 s"), (3600, "1 h"), (3720, "1 h 2 min"),
])
def test_durations_read_plainly(seconds, text):
    assert availability._duration(seconds) == text


# ── wait_while_away ───────────────────────────────────────────────────────


async def test_wait_while_away_returns_at_once_when_answering(db, monkeypatch):
    calls = []

    async def _sleep(seconds):
        calls.append(seconds)
        return False

    monkeypatch.setattr("kazma_core.shutdown.sleep_or_shutdown", _sleep)
    assert await availability.wait_while_away() is False
    assert calls == []


async def test_wait_while_away_waits_until_the_database_answers(db, monkeypatch):
    availability.note_failure("x", _outage())
    calls = []

    async def _sleep(seconds):
        calls.append(seconds)
        if len(calls) == 3:
            availability.note_success("probe")
        return False

    monkeypatch.setattr("kazma_core.shutdown.sleep_or_shutdown", _sleep)
    assert await availability.wait_while_away(step_s=7) is False
    assert calls == [7, 7, 7]


async def test_wait_while_away_ends_on_shutdown(db, monkeypatch):
    availability.note_failure("x", _outage())

    async def _sleep(seconds):
        return True

    monkeypatch.setattr("kazma_core.shutdown.sleep_or_shutdown", _sleep)
    assert await availability.wait_while_away() is True
    assert availability.is_away()


# ── the pool's chatter ────────────────────────────────────────────────────


def _record(msg: str) -> logging.LogRecord:
    return logging.LogRecord("psycopg.pool", logging.WARNING, __file__, 1, msg, None, None)


def test_pool_chatter_is_kept_out_only_while_away(db):
    chatter = _record("error connecting in 'pool-1': connection refused")
    other = _record("pool 'pool-1' is closed")
    flt = availability._FILTER
    assert flt.filter(chatter) and flt.filter(other)
    availability.note_failure("x", _outage())
    assert not flt.filter(chatter)
    assert flt.filter(other)
    assert flt.filter(logging.LogRecord("psycopg.pool", logging.WARNING, __file__, 1, None, None, None))


def test_the_filter_is_on_the_pool_logger():
    availability._install_log_filter()
    availability._install_log_filter()
    filters = logging.getLogger("psycopg.pool").filters
    assert filters.count(availability._FILTER) == 1


# ── every background loop that sleeps knows about the outage ─────────────
#
# A loop that runs while Postgres is away either waits for it (its own body
# names ``availability.wait_while_away(`` or ``availability.is_away(``) or is
# declared here with why it keeps running. Found from the source the way the
# loops are written: an ``async def`` whose own ``while`` loop sleeps.

AWARE = {
    "kazma-core/kazma_core/backup/restore_drill.py::drill_scheduler",
    "kazma-core/kazma_core/documents/ingestion.py::DocumentIngestionService._sweep_stranded_intakes",
    "kazma-core/kazma_core/documents/retention.py::start_document_maintenance_loop._loop",
    "kazma-core/kazma_core/mcp/reconnect.py::MCPReconnector.run",
    "kazma-core/kazma_core/memory/worker_bootstrap.py::_start_macro_sleep_scheduler._loop",
    "kazma-core/kazma_core/memory/worker_bootstrap.py::_start_backup_export_scheduler._loop",
    "kazma-core/kazma_core/memory/worker_bootstrap.py::_start_session_purge_scheduler._loop",
    "kazma-core/kazma_core/memory/worker_bootstrap.py::_start_reconsolidation_scheduler._loop",
    "kazma-core/kazma_core/observability/daily_digest.py::digest_scheduler",
    "kazma-core/kazma_core/observability/firing_ledger.py::ledger_scheduler",
    "kazma-core/kazma_core/runs/supervisor.py::_loop",
    "kazma-core/kazma_core/swarm/engine.py::SwarmEngine.start_maintenance_loop._loop",
    "kazma-core/kazma_core/time_travel.py::start_snapshot_maintenance_loop._loop",
    "kazma-core/kazma_core/web_acquire/canary.py::_loop",
    "kazma-core/kazma_core/web_acquire/monitor.py::_loop",
    "kazma-core/kazma_core/x_api/mentions_fire.py::_loop",
    "kazma-core/kazma_core/x_api/scheduled_fire.py::_loop",
    "kazma-ui/kazma_ui/app.py::KazmaAppBuilder._start_background_loops._heartbeat_loop",
    "kazma-ui/kazma_ui/hitl_timeout.py::_watchdog_loop",
    "kazma-ui/kazma_ui/turn_liveness.py::_loop",
}

_KEEPS_RUNNING = "keeps running on purpose: "
_NO_DATABASE = "touches no database: "
_ONE_REQUEST = "waits inside one request or job, which its caller bounds"

DECLARED = {
    "kazma-core/kazma_core/memory/worker_bootstrap.py::_start_commitment_gc_scheduler._loop":
        _KEEPS_RUNNING + "most of its sweeps are SQLite, the supervisor watch must not stop with "
        "the database, and a sweep that fails while away is recorded as waiting_for_database",
    "kazma-core/kazma_core/observability/alerts.py::_health_check_loop":
        _KEEPS_RUNNING + "it is how an outage is seen",
    "kazma-core/kazma_core/memory/task_queue.py::_MemoryWorker._run_lease_renewal":
        _NO_DATABASE + "renews leases in memory_ops.db (SQLite)",
    "kazma-core/kazma_core/observability/loop_stall.py::start_stall_watchdog._heartbeat":
        _NO_DATABASE + "a timestamp for the stall watchdog",
    "kazma-gateway/kazma_gateway/adapters/discord.py::DiscordAdapter._heartbeat":
        _NO_DATABASE + "Discord's websocket heartbeat",
    "kazma-gateway/kazma_gateway/adapters/slack.py::SlackAdapter._listen_socket_mode":
        _NO_DATABASE + "Slack's socket",
    "kazma-gateway/kazma_gateway/adapters/slack.py::SlackAdapter._listen_polling":
        _NO_DATABASE + "Slack's HTTP polling",
    "kazma-gateway/kazma_gateway/adapters/telegram_send.py::send_chunks_with_retry":
        _NO_DATABASE + "Telegram's send retries",
    "kazma-core/kazma_core/agent/tool_builtins/system.py::register_system_tools.check_swarm_task":
        _ONE_REQUEST,
    "kazma-core/kazma_core/documents/worker.py::DocumentWorker._heartbeat": _ONE_REQUEST,
    "kazma-core/kazma_core/lifecycle_notifier.py::_settled": _ONE_REQUEST,
    "kazma-core/kazma_core/llm_provider.py::LLMProvider.chat_stream": _ONE_REQUEST,
    "kazma-core/kazma_core/safety/bus_bridge.py::request_approval_via_registry": _ONE_REQUEST,
    "kazma-core/kazma_core/stores/knowledge_ingest.py::_fetch_with_retries": _ONE_REQUEST,
    "kazma-core/kazma_core/stores/knowledge_ingest.py::_ingest_site_impl": _ONE_REQUEST,
    "kazma-core/kazma_core/swarm/durable.py::_heartbeat": _ONE_REQUEST,
    "kazma-core/kazma_core/telemetry.py::HardwareMonitor.stream": _ONE_REQUEST,
    "kazma-core/kazma_core/tools/web_research.py::crawl_site": _ONE_REQUEST,
    "kazma-core/kazma_core/web_acquire/origins.py::_OriginScheduler.request": _ONE_REQUEST,
    "kazma-core/kazma_core/web_acquire/process_worker.py::ProcessOwner.finish": _ONE_REQUEST,
    "kazma-gateway/kazma_gateway/agent_handler/commands.py::_try_research_command": _ONE_REQUEST,
    "kazma-gateway/kazma_gateway/agent_handler/graph.py::create_graph_handler._handler_body": _ONE_REQUEST,
    "kazma-gateway/kazma_gateway/chat_adapters.py::ChatAdapters.first_outcome": _ONE_REQUEST,
    "kazma-gateway/kazma_gateway/gateway.py::RateLimiter.acquire": _ONE_REQUEST,
    "kazma-skills/kazma_skills/native/document_platform/tools.py::_wait_terminal": _ONE_REQUEST,
    "kazma-ui/kazma_ui/routes_direct/misc.py::_await_segment_tail": _ONE_REQUEST,
    "kazma-ui/kazma_ui/sse_chat/__init__.py::create_sse_chat_router.steer_chat_turn": _ONE_REQUEST,
    "kazma-ui/kazma_ui/sse_chat/_streaming.py::_stream_langgraph_events": _ONE_REQUEST,
    "kazma-ui/kazma_ui/sse_chat/_streaming.py::_stream_langgraph_events._pump_watchdog": _ONE_REQUEST,
}

_AWARE_CALLS = ("availability.wait_while_away(", "availability.is_away(")
_NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _own_nodes(fn):
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        if isinstance(node, _NESTED):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _sleeps(loop) -> bool:
    stack = list(ast.iter_child_nodes(loop))
    while stack:
        node = stack.pop()
        if isinstance(node, _NESTED):
            continue
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            if name == "sleep_or_shutdown":
                return True
            if (name == "sleep" and isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                    and f.value.id in ("asyncio", "_asyncio", "_aio")):
                return True
        stack.extend(ast.iter_child_nodes(node))
    return False


def _sleeping_loops(path: str, source: str) -> dict[str, str]:
    """``{"path::Qualified.name": that function's own source}``."""
    found: dict[str, str] = {}
    tree = ast.parse(source)

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}.{child.name}" if prefix else child.name
                if isinstance(child, ast.AsyncFunctionDef) and any(
                        isinstance(n, ast.While) and _sleeps(n) for n in _own_nodes(child)):
                    found[f"{path}::{name}"] = ast.get_source_segment(source, child) or ""
                walk(child, name)
            else:
                walk(child, prefix)

    walk(tree, "")
    return found


def _product_loops() -> dict[str, str]:
    files = subprocess.run(["git", "ls-files", "kazma-*/kazma_*"], cwd=REPO,
                           capture_output=True, text=True, check=True).stdout.split()
    loops: dict[str, str] = {}
    for rel in files:
        if not rel.endswith(".py") or "/tests/" in rel:
            continue
        path = REPO / rel
        if not path.exists():
            continue
        loops.update(_sleeping_loops(rel, path.read_text(encoding="utf-8")))
    return loops


def _loop_findings(loops: dict[str, str]) -> list[str]:
    findings = []
    for name, source in sorted(loops.items()):
        if name in AWARE:
            if not any(call in source for call in _AWARE_CALLS):
                findings.append(f"{name}: declared aware but never asks availability")
        elif name not in DECLARED:
            findings.append(f"{name}: a background loop that neither waits for the database "
                            "nor is declared with why it keeps running")
    for name in sorted((AWARE | set(DECLARED)) - set(loops)):
        findings.append(f"{name}: declared, but no such sleeping loop exists any more")
    for name, why in DECLARED.items():
        if not why.strip():
            findings.append(f"{name}: declared without a reason")
    return findings


def test_every_background_loop_knows_about_the_database():
    findings = _loop_findings(_product_loops())
    assert not findings, "\n".join(findings)


def test_the_loop_gate_catches_a_planted_loop():
    planted = (
        "import asyncio\n"
        "async def sweeper():\n"
        "    while True:\n"
        "        await asyncio.sleep(30)\n"
        "        do_work()\n"
        "class Watcher:\n"
        "    async def run(self):\n"
        "        while True:\n"
        "            await sleep_or_shutdown(5)\n"
    )
    loops = _product_loops()
    loops.update(_sleeping_loops("kazma-core/kazma_core/planted.py", planted))
    findings = _loop_findings(loops)
    assert any("planted.py::sweeper" in f for f in findings)
    assert any("planted.py::Watcher.run" in f for f in findings)


def test_the_loop_gate_catches_an_aware_loop_that_stopped_asking():
    loops = _product_loops()
    name = "kazma-ui/kazma_ui/turn_liveness.py::_loop"
    loops[name] = loops[name].replace("availability.wait_while_away(", "availability.nothing(")
    assert any(name in f and "never asks" in f for f in _loop_findings(loops))


# ── the memory queue holds its tasks ──────────────────────────────────────


@pytest.fixture()
def queue(tmp_path, monkeypatch):
    from kazma_core.memory import task_queue as tq
    from kazma_core.memory.schema_v2 import ensure_ops_schema

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr(tq, "_worker", None)
    monkeypatch.setattr("kazma_core.observability.ops_alerts.alert", lambda *a, **k: True)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"), check_same_thread=False)
    ops.row_factory = sqlite3.Row
    ensure_ops_schema(ops)
    yield SimpleNamespace(tq=tq, ops=ops)
    ops.close()


def _claimed(queue):
    tid = queue.tq.enqueue_task("probe_hang", {}, max_attempts=3)
    assert tid
    worker = queue.tq._MemoryWorker()
    task = worker._claim_batch(lane="maintenance")[0]
    return worker, task


def _row(queue, task):
    return dict(queue.ops.execute("SELECT * FROM memory_task_queue WHERE id=?", (task["id"],)).fetchone())


def test_a_task_that_failed_on_the_outage_keeps_its_attempts(queue):
    worker, task = _claimed(queue)
    before = _row(queue, task)["attempts"]
    start = time.time()
    worker._ack(task, success=False, error="connection refused", outage=True)
    row = _row(queue, task)
    assert row["status"] == "pending"
    assert row["attempts"] == before
    assert row["error_log"].startswith("waits for the database")
    assert row["next_attempt_at"] >= start + queue.tq._OUTAGE_RETRY_S - 1


def test_a_task_that_failed_otherwise_spends_an_attempt(queue):
    """The control: the same failure without the outage costs an attempt."""
    worker, task = _claimed(queue)
    before = _row(queue, task)["attempts"]
    worker._ack(task, success=False, error="ValueError: bad payload")
    assert _row(queue, task)["attempts"] == before + 1


def test_the_queue_asks_the_one_state(db, queue):
    assert queue.tq._database_away(ValueError("no")) is False
    assert queue.tq._database_away(_outage()) is True
    assert queue.tq._database_away(None) is True  # the outage it just reported


# ── the maintenance sweeps wait, never count it against themselves ───────


async def test_a_sweep_that_failed_on_the_outage_is_waiting_not_failing(db):
    from kazma_core.memory import worker_bootstrap as wb

    label = "test-availability-outage-sweep"

    def _sweep():
        raise _outage()

    try:
        await wb._run_sweeps([(label, _sweep)])
        state = wb._SWEEP_STATE[label]
        assert state["waiting_for_database"] is True
        assert state["consecutive_failures"] == 0
        assert state["last_error"].startswith("waits for the database")
    finally:
        wb._SWEEP_STATE.pop(label, None)


async def test_a_sweep_that_failed_otherwise_counts(db):
    from kazma_core.memory import worker_bootstrap as wb

    label = "test-availability-plain-sweep"

    def _sweep():
        raise ValueError("a bug")

    try:
        await wb._run_sweeps([(label, _sweep)])
        state = wb._SWEEP_STATE[label]
        assert state["consecutive_failures"] == 1
        assert "waiting_for_database" not in state
    finally:
        wb._SWEEP_STATE.pop(label, None)


# ── cron holds a task fire, never a reminder ─────────────────────────────


class _DueStore:
    def __init__(self, jobs):
        self.jobs = jobs

    async def due_jobs(self, *, now, limit):
        return list(self.jobs)


async def _due_started(monkeypatch, away: bool) -> list[str]:
    from datetime import UTC, datetime

    from kazma_core.cron.scheduler import CronScheduler
    from kazma_core.cron.store import JobStatus, ScheduledJob

    now = datetime.now(UTC)
    jobs = [
        ScheduledJob(job_id=f"job-{kind}", timing="2030-01-01T09:00", prompt="p",
                     platform="telegram", thread_id="t1", status=JobStatus.PENDING,
                     next_run=now.isoformat(), kind=kind, delivery_target="telegram:42")
        for kind in ("reminder", "task")
    ]
    sched = CronScheduler(store=_DueStore(jobs), poll_interval=60.0)
    sched._running = True
    started: list[str] = []

    async def _nothing(*a, **k):
        return None

    async def _execute_bounded(job, sem):
        started.append(job.kind)

    monkeypatch.setattr(sched, "_recover", _nothing)
    monkeypatch.setattr(sched, "_drain_deliveries", _nothing)
    monkeypatch.setattr(sched, "_drain_alerts", _nothing)
    monkeypatch.setattr(sched, "_execute_bounded", _execute_bounded)
    monkeypatch.setattr("kazma_core.cron.settings.cron_settings",
                        lambda: SimpleNamespace(catchup_seconds=3600))
    if away:
        availability.note_failure("test", reason="down")
    await sched._poll_once()
    await asyncio.gather(*list(sched._exec_tasks))
    return sorted(started)


async def test_cron_holds_a_task_while_the_database_is_away(db, monkeypatch):
    assert await _due_started(monkeypatch, away=True) == ["reminder"]


async def test_cron_runs_both_while_the_database_answers(db, monkeypatch):
    """The control: the same pass with the database answering starts both."""
    assert await _due_started(monkeypatch, away=False) == ["reminder", "task"]


# ── boot waits for the settings store while the guard watches ────────────


def _boot_wait(monkeypatch, tmp_path, *, guarded: bool, failures: int, budget_s=None):
    from kazma_core import config_availability as ca

    status = tmp_path / "boot_status.json"
    if guarded:
        monkeypatch.setenv(ca.BOOT_STATUS_ENV, str(status))
    else:
        monkeypatch.delenv(ca.BOOT_STATUS_ENV, raising=False)
    clock = _Clock()
    seen: list[dict | None] = []
    calls = {"n": 0}

    def _open():
        calls["n"] += 1
        if calls["n"] <= failures:
            raise ca.ConfigStoreUnavailableError(reason="connection refused")
        return "store"

    def _sleep(seconds):
        seen.append(json.loads(status.read_text()) if status.exists() else None)
        clock.advance(seconds)

    kwargs = {"sleep": _sleep, "clock": clock}
    if budget_s is not None:
        kwargs["budget_s"] = budget_s
    return ca, status, seen, clock, (lambda: ca.wait_for_settings_store(_open, **kwargs))


def test_boot_tells_the_guard_it_waits_for_the_database(monkeypatch, tmp_path):
    ca, status, seen, clock, run = _boot_wait(monkeypatch, tmp_path, guarded=True, failures=3)
    assert run() == "store"
    assert [s["waiting_for"] for s in seen] == ["database"] * 3
    assert seen[0]["reason"] == "connection refused"
    assert json.loads(status.read_text())["waiting_for"] is None


def test_boot_under_the_guard_waits_past_the_old_limit(monkeypatch, tmp_path):
    ca, status, seen, clock, run = _boot_wait(monkeypatch, tmp_path, guarded=True,
                                              failures=int(ca_boot_wait() / 10) + 20)
    assert run() == "store"
    assert clock.t - 1000.0 > ca.BOOT_WAIT_S


def test_boot_without_the_guard_gives_up_at_its_limit(monkeypatch, tmp_path):
    """The control: with nobody watching, the old bounded wait still holds."""
    ca, status, seen, clock, run = _boot_wait(monkeypatch, tmp_path, guarded=False,
                                              failures=10_000)
    with pytest.raises(ca.ConfigStoreUnavailableError):
        run()
    assert clock.t - 1000.0 <= ca.BOOT_WAIT_S
    assert not status.exists()


def ca_boot_wait() -> float:
    from kazma_core import config_availability as ca

    return ca.BOOT_WAIT_S


def test_boot_status_is_written_only_for_a_guard(monkeypatch, tmp_path):
    from kazma_core import config_availability as ca

    monkeypatch.delenv(ca.BOOT_STATUS_ENV, raising=False)
    assert ca.write_boot_status("database") is False
    monkeypatch.setenv(ca.BOOT_STATUS_ENV, str(tmp_path / "s.json"))
    assert ca.write_boot_status("database", since=5.0, reason="x" * 500) is True
    data = json.loads((tmp_path / "s.json").read_text())
    assert data["waiting_for"] == "database" and data["since"] == 5.0
    assert len(data["reason"]) == 300


# ── the guard reads both ──────────────────────────────────────────────────


@pytest.fixture()
def guard(tmp_path, monkeypatch):
    name = "kazma_guard_dbavail_test"
    path = REPO / "scripts" / "service" / "kazma_guard.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        status = tmp_path / "boot_status.json"
        monkeypatch.setenv("KAZMA_GUARD_BOOT_STATUS", str(status))
        yield SimpleNamespace(g=module, status=status)
    finally:
        sys.modules.pop(name, None)


def test_the_guard_reads_a_fresh_boot_status(guard):
    now = 10_000.0
    guard.status.write_text(json.dumps({"waiting_for": "database", "at": now - 5}))
    assert guard.g.read_boot_status(now - 60, now=now)["waiting_for"] == "database"


@pytest.mark.parametrize("content, spawned_at", [
    (json.dumps({"waiting_for": "database", "at": 9_000.0}), 9_500.0),      # an earlier boot's
    (json.dumps({"waiting_for": "database", "at": 9_700.0}), 9_000.0),      # stale (> 150 s)
    ("{not json", 9_000.0),
    (json.dumps(["a list"]), 9_000.0),
    (json.dumps({"waiting_for": "database", "at": "soon"}), 9_000.0),
], ids=["before the spawn", "stale", "bad json", "not an object", "bad time"])
def test_the_guard_ignores_a_boot_status_it_cannot_trust(guard, content, spawned_at):
    guard.status.write_text(content)
    assert guard.g.read_boot_status(spawned_at, now=10_000.0) is None


def test_the_guard_reads_no_boot_status_when_there_is_none(guard):
    assert guard.g.read_boot_status(0.0, now=10_000.0) is None


def test_the_guard_trusts_a_watch_that_is_probing(guard):
    watching = {"state": "away", "confirmed": True, "probe_age_s": 10, "probe_stale_after_s": 75}
    assert guard.g.app_watches_database(watching)
    assert not guard.g.app_watches_database({**watching, "probe_age_s": 90})       # prober gone
    assert not guard.g.app_watches_database({**watching, "confirmed": False})
    assert not guard.g.app_watches_database({**watching, "probe_age_s": "x"})
    assert not guard.g.app_watches_database({"state": "answering"})
    assert not guard.g.app_watches_database(None)


def test_the_guard_leaves_the_page_to_the_app_while_it_can_send_it(guard):
    away = {"state": "away", "away_s": 60, "page_at_s": 120, "paged": False}
    assert guard.g.app_pages_database_outage(away)
    assert guard.g.app_pages_database_outage({**away, "paged": True, "away_s": 9999})
    # Past the page time plus the grace and nothing paged: the guard pages.
    late = {**away, "away_s": 120 + guard.g.APP_PAGE_GRACE_S + 1}
    assert not guard.g.app_pages_database_outage(late)
    assert not guard.g.app_pages_database_outage({"state": "answering"})


def test_the_guard_reads_the_snapshot_from_readiness(guard):
    snap = {"state": "away", "confirmed": True}
    raw = json.dumps({"status": "not_ready", "database_availability": snap, "restart_required": False})
    assert guard.g._readiness_fields(raw) == (False, snap)
    assert guard.g._readiness_fields(json.dumps({"restart_required": True})) == (True, None)
    assert guard.g._readiness_fields("<html>") == (False, None)
    assert guard.g._readiness_fields(json.dumps([1])) == (False, None)
    result = guard.g.ProbeResult(False, "not ready", database=snap)
    assert guard.g.database_state(result) == snap
    assert guard.g.database_state(guard.g.ProbeResult(True, "ok")) is None
