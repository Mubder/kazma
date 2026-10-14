"""Time Travel is bounded, observable and says what it does (TT-06 to TT-11, TT-G3).

Audit 2026-10-13:

- TT-06: every capture stored the whole state with no byte bound: 1,669,451
  bytes for an 81,380-byte final state, a 5 MB snapshot accepted, the global
  cap counted the in-memory dict only (memory 2, SQLite 6), the listing
  decoded every state to count messages, VACUUM ran daily for nothing.
- TT-07: the web fork wrote the raw graph messages (system prompt, tool
  results) into the chat row, titles were English only, the gateway fork's
  owner record and chat row were written on the loop with failures at DEBUG.
- TT-08: Settings' "effective" cap read an attribute the recorder did not
  have, so "Restart required" never cleared.
- TT-09: a JSON-escaped Windows path was never rewritten by a migration.
- TT-10: the docs and the page claimed what the code did not do.
- TT-11: a failed write was announced as a step; a corrupt store read as
  "nothing saved"; a prune that did not stick paged nobody.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import tracemalloc
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests import _tt_graph as tt

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from kazma_core import time_travel, time_travel_audit
    from kazma_core.safety import hitl_gates

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    hitl_gates.set_db_path_for_tests(str(tmp_path / "hitl_gates.db"))
    time_travel_audit.set_db_path_for_tests(str(tmp_path / "tt_audit.db"))
    time_travel.set_process_recorder(None)
    yield
    time_travel.set_process_recorder(None)
    hitl_gates.set_db_path_for_tests(None)
    time_travel_audit.set_db_path_for_tests(None)


def _recorder(tmp_path, **kw):
    from kazma_core.time_travel import SnapshotRecorder

    return SnapshotRecorder(db_path=str(tmp_path / "snapshots.db"), **kw)


def _db_totals(tmp_path) -> tuple[int, int]:
    with closing(sqlite3.connect(str(tmp_path / "snapshots.db"))) as conn:
        n, b = conn.execute("SELECT count(*), COALESCE(SUM(length(state_json)), 0) FROM snapshots").fetchone()
    return int(n), int(b)


# ── TT-06: byte budgets ────────────────────────────────────────────────────


def test_a_state_over_the_ceiling_is_stored_cut_and_cannot_be_restored(tmp_path):
    from langgraph.checkpoint.memory import InMemorySaver

    from kazma_core.time_travel import DEFAULT_MAX_SNAPSHOT_BYTES, TRUNCATION_MARK
    from kazma_ui.thread_rewind import RewindRefused, fork_thread, rewind_thread

    rec = _recorder(tmp_path)
    big = {"thread_id": "big", "iteration": 1, "created_at": "t",
           "messages": [{"role": "tool", "content": "y" * 5_000_000}]}
    assert len(json.dumps(big)) > 5_000_000  # what the old capture stored whole
    record = rec.capture(big)
    assert record.truncated and record.nbytes <= DEFAULT_MAX_SNAPSHOT_BYTES
    assert TRUNCATION_MARK in rec.get(record.id).get_state()
    assert rec.list_snapshots("big")[0].truncated

    async def run():
        graph = tt.build_graph(rec, [], InMemorySaver())
        for action in (rewind_thread, fork_thread):
            with pytest.raises(RewindRefused) as refused:
                await action(graph, "big", record.id, actor="t", surface="web", recorder=rec)
            assert refused.value.code == "snapshot_truncated"

    tt.run(run())


def test_the_budgets_are_enforced_in_sqlite(tmp_path):
    """Per thread and in total, by count and by bytes, in the database (the
    old global cap bounded the in-memory dict only: memory 2, SQLite 6)."""
    rec = _recorder(tmp_path, max_snapshots=50, max_global_snapshots=5, max_snapshot_bytes=8192,
                    max_thread_bytes=20_000, max_total_bytes=40_000)
    for i in range(12):
        rec.capture({"thread_id": f"t{i % 3}", "iteration": i, "created_at": f"turn{i}",
                     "messages": [{"role": "tool", "content": "x" * 3000}]})
    n, b = _db_totals(tmp_path)
    assert n <= 5 and b <= 40_000
    for thread in ("t0", "t1", "t2"):
        assert sum(s.nbytes for s in rec.list_snapshots(thread)) <= 20_000


def test_a_turn_keeps_its_first_and_newest_steps(tmp_path):
    rec = _recorder(tmp_path, max_per_turn=4)
    for it in range(1, 11):
        rec.capture({"thread_id": "T", "iteration": it, "created_at": "one turn", "messages": []})
    assert [s.iteration for s in rec.list_snapshots("T")] == [1, 8, 9, 10]


def test_captures_thin_inside_a_long_turn():
    from kazma_core.time_travel import CAPTURE_ALL_UNTIL, CAPTURE_EVERY, _capture_due

    due = [it for it in range(1, 2001) if _capture_due(it, "tool_worker")]
    assert due[:CAPTURE_ALL_UNTIL] == list(range(1, CAPTURE_ALL_UNTIL + 1))
    assert all(it % CAPTURE_EVERY == 0 for it in due[CAPTURE_ALL_UNTIL:])
    assert len(due) == CAPTURE_ALL_UNTIL + (2000 - CAPTURE_ALL_UNTIL) // CAPTURE_EVERY
    assert _capture_due(1999, "respond")  # the turn's last step


def test_the_listing_reads_no_state(tmp_path, monkeypatch):
    """Message counts come from the row: the listing never decodes a state."""
    from kazma_core import time_travel

    rec = _recorder(tmp_path)
    rec.capture({"thread_id": "T", "iteration": 1, "created_at": "x",
                 "messages": [{"role": "user", "content": "q"}] * 3})
    monkeypatch.setattr(time_travel.SnapshotRecord, "get_state",
                        lambda self: (_ for _ in ()).throw(AssertionError("decoded a state")))
    listed = rec.list_snapshots("T")
    assert listed[0].message_count == 3 and listed[0].state_json == ""


def test_vacuum_runs_only_when_the_free_pages_justify_it(tmp_path):
    from kazma_core.time_travel import maintain_snapshots

    rec = _recorder(tmp_path)
    rec.capture({"thread_id": "T", "iteration": 1, "created_at": "x", "messages": []})
    rec.close()
    assert maintain_snapshots(str(tmp_path / "snapshots.db"), 30)["vacuum"].startswith("skipped")


async def _mission(recorder, thread_id: str, iterations: int, chunk: int):
    """A mission-length turn through the product's capture hook."""
    from kazma_core.time_travel import capture_after_supervisor

    messages: list[dict] = [{"role": "user", "content": "go"}]
    for it in range(1, iterations + 1):
        messages.append({"role": "tool", "tool_call_id": f"c{it}", "content": "z" * chunk})
        state = {"thread_id": thread_id, "iteration": it - 1, "created_at": f"{thread_id}-turn",
                 "messages": list(messages)}
        nxt = "respond" if it == iterations else "tool_worker"
        await capture_after_supervisor(recorder, state, {"iteration": it, "next_node": nxt})


def test_a_mission_length_turn_and_many_threads_stay_within_the_budgets(tmp_path):
    """TT-G3's measurement: a 2,000-iteration turn and 40 threads, with the
    stored bytes and the peak Python allocation against the declared budgets.
    ``KAZMA_TEST_TT_SOAK_SCALE`` multiplies the threads and the message size
    for a longer run (the result is printed)."""
    from kazma_core.time_travel import (
        DEFAULT_MAX_SNAPSHOT_BYTES,
        DEFAULT_MAX_THREAD_BYTES,
        DEFAULT_MAX_TOTAL_BYTES,
    )

    scale = max(1, int(os.environ.get("KAZMA_TEST_TT_SOAK_SCALE", "1")))
    rec = _recorder(tmp_path)
    started = time.perf_counter()
    tracemalloc.start()

    async def run():
        # The other chats first: at a larger scale they fill the install's
        # row cap, and eviction is oldest first, so a mission written first
        # would be evicted whole (correctly) and leave nothing to measure.
        for i in range(40 * scale):
            await _mission(rec, f"thread-{i}", 60, 400)
        await _mission(rec, "mission", 2000, 200 * scale)

    tt.run(run())
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rows, stored = _db_totals(tmp_path)
    mission = rec.list_snapshots("mission")
    size = (tmp_path / "snapshots.db").stat().st_size
    print(f"TT-G3 scale={scale} rows={rows} stored={stored} file={size} peak_alloc={peak} "
          f"mission_steps={len(mission)} seconds={time.perf_counter() - started:.1f}")
    assert stored <= DEFAULT_MAX_TOTAL_BYTES
    assert sum(s.nbytes for s in mission) <= DEFAULT_MAX_THREAD_BYTES
    assert max(s.nbytes for s in mission) <= DEFAULT_MAX_SNAPSHOT_BYTES
    assert len(mission) <= 30 and mission[-1].iteration == 2000
    held = sum(s.nbytes for s in mission)
    if held + max(s.nbytes for s in mission) <= DEFAULT_MAX_THREAD_BYTES:
        # Room to spare: the per-turn rule decides, and keeps the first step.
        # Past the chat's byte budget, oldest first takes it (larger scales).
        assert mission[0].iteration == 1
    # RAM: no cache of states; a few states in flight at most.
    assert peak < 6 * DEFAULT_MAX_SNAPSHOT_BYTES + 32 * 1024 * 1024


def test_without_the_budgets_the_store_grows_with_the_square_of_a_turn(tmp_path):
    """Negative control: every step captured, no byte budget (the old capture)."""
    rec = _recorder(tmp_path, max_snapshots=10_000, max_global_snapshots=10_000,
                    max_snapshot_bytes=1 << 40, max_thread_bytes=1 << 40, max_total_bytes=1 << 40,
                    max_per_turn=10_000)
    msgs: list[dict] = []
    for it in range(1, 41):
        msgs.append({"role": "tool", "content": "x" * 2000})
        rec.capture({"thread_id": "T", "iteration": it, "created_at": "t", "messages": list(msgs)})
    _rows, stored = _db_totals(tmp_path)
    final = rec.list_snapshots("T")[-1].nbytes
    assert stored > 15 * final


# ── TT-07: one fork routine ────────────────────────────────────────────────


def test_the_fork_route_writes_the_questions_and_answers_with_a_catalog_title(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langgraph.checkpoint.memory import InMemorySaver

    from kazma_ui import thread_ownership
    from kazma_ui.i18n import set_request_language
    from kazma_ui.replay_routes import create_replay_router
    from kazma_ui.session_manager import ChatSession, get_session_manager

    rec = _recorder(tmp_path)
    graph = tt.build_graph(rec, [], InMemorySaver())
    store = get_session_manager()
    store.put(ChatSession(session_id="src", thread_id="src", title="Trip plan", messages=[]))

    async def setup():
        await tt.run_turn(graph, "src", "read:alpha")
        await tt.decide(graph, "src", approved=True)

    tt.run(setup())
    step = rec.list_snapshots("src")[-1]
    raw_roles = {m["role"] for m in rec.get(step.id).get_state()["messages"]}
    assert {"tool", "assistant", "user"} <= raw_roles  # what the old fork stored
    monkeypatch.setattr(thread_ownership, "require_thread_owned", AsyncMock(return_value=None))
    app = FastAPI()
    app.include_router(create_replay_router(recorder=rec, graph=graph))
    for lang, title in (("en", "Fork of Trip plan (turn 1)"), ("ar", "تفريع من Trip plan (الدور 1)")):
        token = set_request_language(lang)
        try:
            out = TestClient(app).post("/api/replay/fork", json={"thread_id": "src", "snapshot_id": step.id}).json()
        finally:
            from kazma_ui.i18n import _REQUEST_LANG

            _REQUEST_LANG.reset(token)
        assert out["ok"] and out["chat_saved"]
        chat = store.get(out["session_id"])
        assert chat.title == title
        assert {m["role"] for m in chat.messages} == {"user", "assistant"}
        assert all(m["content"] for m in chat.messages)


def test_a_fork_whose_owner_cannot_be_recorded_writes_nothing(tmp_path, monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver

    from kazma_core.sessions import directory
    from kazma_ui.thread_rewind import RewindRefused, fork_thread

    rec = _recorder(tmp_path)
    graph = tt.build_graph(rec, [], InMemorySaver())
    monkeypatch.setattr(directory, "record_thread_owner", lambda *a: False)

    async def run():
        await tt.run_turn(graph, "src", "hello")
        step = rec.list_snapshots("src")[0]
        with pytest.raises(RewindRefused) as refused:
            await fork_thread(graph, "src", step.id, actor="telegram:1", surface="telegram",
                              new_thread_id="gw-telegram-1-abc", owner="telegram:1")
        assert refused.value.code == "owner_unavailable"
        snap = await graph.aget_state(tt.config("gw-telegram-1-abc"))
        assert not (snap.values or {}).get("messages")

    tt.run(run())


def test_a_failed_owner_record_is_reported_not_swallowed(monkeypatch, caplog):
    import logging

    from kazma_core import config_store
    from kazma_core.sessions.directory import record_thread_owner

    class _Broken:
        def set_if_absent(self, *a, **k):
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(config_store, "get_config_store", lambda: _Broken())
    with caplog.at_level(logging.WARNING, logger="kazma_core.sessions.directory"):
        assert record_thread_owner("t", "telegram:1") is False
    assert any("NOT recorded" in r.message for r in caplog.records)


# ── TT-08: Settings reads the live recorder ─────────────────────────────


def _settings_client(tmp_path, agent):
    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient

    from kazma_core.config_store import get_config_store
    from kazma_ui.settings import create_settings_router

    (tmp_path / "templates").mkdir(exist_ok=True)
    (tmp_path / "templates" / "settings.html").write_text("ok")
    app = FastAPI()
    app.include_router(create_settings_router(agent, get_config_store(), Jinja2Templates(directory=str(tmp_path / "templates"))))
    return TestClient(app)


def test_settings_reports_the_live_recorders_cap(tmp_path):
    from kazma_core.config_store import get_config_store
    from kazma_core.time_travel import create_recorder

    get_config_store().set("time_travel.max_snapshots", 120, category="time_travel")
    try:
        rec = create_recorder(db_path=str(tmp_path / "snapshots.db"))
        agent = SimpleNamespace(snapshot_recorder=rec, config=SimpleNamespace(raw={}))
        got = _settings_client(tmp_path, agent).get("/api/settings/time_travel").json()
        assert got["store"]["max_snapshots"] == 120 and got["effective"] == 120
        assert got["limits"]["max_snapshots"] == 120 and got["effective_retention_days"] == 30
        # Negative control: a recorder without the public property (the old one).
        old = SimpleNamespace(_max_snapshots=120)
        got = _settings_client(tmp_path, SimpleNamespace(snapshot_recorder=old,
                                                         config=SimpleNamespace(raw={}))).get(
            "/api/settings/time_travel").json()
        assert got["effective"] == 50 != got["store"]["max_snapshots"]
    finally:
        get_config_store().delete("time_travel.max_snapshots")


# ── TT-09: JSON-escaped paths ─────────────────────────────────────────────


def _old_rewrite_text(text, path_map):
    """The rewrite before 2026-10-14 (the negative control)."""
    from kazma_core.migration.path_rewrite import _boundary_aware_replace

    out, total = text, 0
    for source, target in path_map:
        fs_source, fs_target = source.replace("\\", "/"), target.replace("\\", "/")
        if fs_source in out:
            out, n = _boundary_aware_replace(out, fs_source, fs_target)
            total += n
        bs_source = source.replace("/", "\\")
        if bs_source != fs_source and bs_source in out:
            out, n = _boundary_aware_replace(out, bs_source, target.replace("/", "\\"))
            total += n
    return out, total


@pytest.mark.parametrize("direction", ["windows_to_linux", "linux_to_windows"])
def test_json_columns_are_rewritten_both_ways_and_still_parse(tmp_path, direction):
    from kazma_core.migration.path_rewrite import PathMap, rewrite_paths_in_sqlite

    win, lin = r"C:\Users\owner\kazma", "/home/owner/kazma"
    src, dst = (win, lin) if direction == "windows_to_linux" else (lin, win)
    sep = "\\" if src == win else "/"
    pm = PathMap()
    pm.add(src, dst)
    state = {"workspace_root": src + sep + "ws", "messages": [
        {"role": "tool", "content": f"wrote {src}{sep}ws{sep}notes.md"}]}
    db = tmp_path / "bundle" / "chat_sessions.db"
    db.parent.mkdir()
    with closing(sqlite3.connect(str(db))) as conn:
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, messages TEXT)")
        conn.execute("INSERT INTO sessions VALUES ('a', ?)", (json.dumps(state),))
        conn.commit()
    changed = rewrite_paths_in_sqlite(str(db), [("sessions", "messages")], pm)
    assert changed == 1
    with closing(sqlite3.connect(str(db))) as conn:
        out = json.loads(conn.execute("SELECT messages FROM sessions").fetchone()[0])
    assert out["workspace_root"].startswith(dst.replace("\\", "/") if dst == lin else "C:")
    assert src not in json.dumps(out) and src.replace("\\", "\\\\") not in json.dumps(out)
    if dst == lin:
        assert out["workspace_root"] == "/home/owner/kazma/ws"
        assert out["messages"][0]["content"] == "wrote /home/owner/kazma/ws/notes.md"
    if direction == "windows_to_linux":
        # Negative control: the old rewrite matched nothing in the JSON text.
        assert _old_rewrite_text(json.dumps(state), pm)[1] == 0


def test_a_rewrite_that_would_break_the_json_is_not_written(tmp_path, monkeypatch):
    from kazma_core.migration import path_rewrite

    db = tmp_path / "x.db"
    with closing(sqlite3.connect(str(db))) as conn:
        conn.execute("CREATE TABLE t (id TEXT PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO t VALUES ('a', ?)", (json.dumps({"p": "/old/root"}),))
        conn.commit()
    monkeypatch.setattr(path_rewrite, "rewrite_text", lambda text, pm: (text + "{", 1))
    pm = path_rewrite.PathMap()
    pm.add("/old/root", "/new/root")
    assert path_rewrite.rewrite_paths_in_sqlite(str(db), [("t", "v")], pm) == 0


# ── TT-10: the docs and the page say what the code does ───────────────────


def test_the_docs_claims_match_the_code():
    page = (REPO / "docs/docs/guide/time-travel-and-replay.md").read_text(encoding="utf-8")
    assert "**In any chat** (web, Telegram, Discord, Slack)" not in page
    assert "| `/replay <N>` | **Restore**" not in page
    assert "`/replay <step>`" in page and "not backed up" in page
    assert "approval_pending" in page and "turn_running" in page
    agents = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    assert "LRU-capped per-thread at 50" not in agents
    routes = (REPO / "kazma-ui/kazma_ui/replay_routes.py").read_text(encoding="utf-8")
    for named in re.findall(r"``(tests/[\w/]+\.py)``", routes):
        assert (REPO / named).exists(), named


def test_the_web_chat_reply_follows_the_page_language(tmp_path):
    from kazma_ui.sse_chat._time_travel import time_travel_reply

    rec = _recorder(tmp_path)
    for it in (1, 2, 3):
        rec.capture({"thread_id": "T", "iteration": it, "created_at": "x", "messages": []})
    ar = time_travel_reply("/replay", "T", rec, "ar")
    en = time_travel_reply("/replay", "T", rec, "en")
    assert "لهذه المحادثة 3 خطوات محفوظة" in ar and "This chat has 3 saved steps" in en
    assert "step(s)" not in en


# ── TT-11: failures are said, never announced as success ─────────────────


def test_a_failed_write_is_not_announced(tmp_path, monkeypatch):
    from kazma_core import metrics
    from kazma_core.time_travel import capture_after_supervisor

    seen: list[str] = []
    monkeypatch.setattr(metrics, "record_time_travel_capture", lambda result: seen.append(result))
    rec = _recorder(tmp_path)

    def _locked(record):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(rec._get_store(), "save", _locked)
    result = tt.run(capture_after_supervisor(
        rec, {"thread_id": "T", "iteration": 0, "created_at": "x", "messages": []},
        {"iteration": 1, "next_node": "respond"}))
    assert "snapshot_id" not in result and seen == ["failed"]


def test_a_corrupt_store_is_said_on_every_surface(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_core.time_travel import SnapshotRecorder, SnapshotStoreError
    from kazma_ui import thread_ownership
    from kazma_ui.replay_routes import create_replay_router
    from kazma_ui.sse_chat._time_travel import time_travel_reply

    db = tmp_path / "corrupt.db"
    db.write_bytes(b"this is not a sqlite database " * 200)
    rec = SnapshotRecorder(db_path=str(db))
    monkeypatch.setattr(thread_ownership, "require_thread_owned", AsyncMock(return_value=None))
    app = FastAPI()
    app.include_router(create_replay_router(recorder=rec, graph=object()))
    client = TestClient(app)
    for path in ("/api/replay/threads", "/api/replay/snapshots/T", "/api/replay/snapshots/T/abc"):
        got = client.get(path)
        assert got.status_code == 503 and got.json()["error"] == "store_unavailable", (path, got.text)
    with pytest.raises(SnapshotStoreError):
        time_travel_reply("/replay", "T", rec, "en")


def test_a_prune_that_does_not_stick_pages_the_operator(tmp_path, monkeypatch):
    from kazma_core import time_travel
    from kazma_core.observability import ops_alerts

    alerts: list[str] = []
    monkeypatch.setattr(ops_alerts, "alert", lambda key, *a, **k: alerts.append(key) or True)
    rec = _recorder(tmp_path)
    rec.capture({"thread_id": "T", "iteration": 1, "created_at": "x", "messages": []})
    rec.close()
    with closing(sqlite3.connect(str(tmp_path / "snapshots.db"))) as conn:
        conn.execute("UPDATE snapshots SET timestamp = '2000-01-01T00:00:00'")
        conn.commit()

    real_connect = sqlite3.connect

    class _NoCommit:
        def __init__(self, conn):
            self._c = conn

        def __getattr__(self, name):
            return getattr(self._c, name)

        def commit(self):
            return None  # the 2026-09-14 shape: the delete rolled back

    monkeypatch.setattr(time_travel.sqlite3, "connect",
                        lambda path, *a, **k: _NoCommit(real_connect(path, *a, **k))
                        if "mode=ro" not in str(path) else real_connect(path, *a, **k))
    out = time_travel.maintain_snapshots(str(tmp_path / "snapshots.db"), 30)
    assert out["prune"].startswith("did not stick")
    assert alerts == ["time_travel.prune_not_sticking"]


def test_the_daily_maintenance_has_a_ledger_signature():
    from kazma_core.observability import firing_ledger as fl

    sig = next(s for s in fl.FIRING_SIGNATURES if s.mechanism == "time travel maintenance")
    assert sig.kind == fl.ROUTINE and re.search(sig.pattern, "[TimeTravel] maintenance: deleted=3 remaining_old=0")


# ── TT-07: the time-travel modules reach no loop-stall helper ────────────


def test_the_owner_record_and_the_fork_chat_are_loop_stall_helpers():
    from tests.test_static_gates import _CROSS_MODULE_AREAS, _LOOP_STALL_HELPERS, _loop_stall_helper_calls
    import ast

    assert {"record_thread_owner", "_save_fork_chat", "forget_thread_snapshots"} <= _LOOP_STALL_HELPERS
    assert "time_travel" in _CROSS_MODULE_AREAS
    # Negative control: the gateway fork as it was (both writes on the loop).
    old = ("async def _handle_fork(thread_id, sender):\n"
           "    record_thread_owner(thread_id, sender)\n"
           "    _save_fork_chat(thread_id, 'x', [])\n")
    assert [n for _, n in _loop_stall_helper_calls(ast.parse(old))] == ["record_thread_owner", "_save_fork_chat"]
