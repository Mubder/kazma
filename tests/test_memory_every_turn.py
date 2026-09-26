"""Every conversation turn reaches long-term memory.

From 2026-08-08 to 2026-09-26 no web chat turn was written to memory: the
post-turn hand-over had moved out of the graph into the gateway handler, and
the web transports never called it. On the live install 1,004 of 1,174 chat
turns had no memory (877 web, 127 gateway). Three fixes, each held here:

* ``kazma_ui.turn_runtime.close_turn`` -- the closer every transport runs --
  hands a finished turn to memory, once (``consolidator.remember_turn``), and
  nothing else may; a gate holds the single call site.
* A turn's question is paired with ITS answer, and the episode's turn number
  is the conversation's turn index, so a question asked twice is two memories.
* ``memory.turn_reconcile`` writes an episode for every chat-store turn that
  has none, with the turn's own time -- the 7-week gap, and any future one.
"""

from __future__ import annotations

import ast
import asyncio
import json
import sqlite3
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from kazma_core.memory import consolidator, turn_reconcile
from kazma_core.memory.schema_v2 import ensure_primary_schema

REPO = Path(__file__).resolve().parents[1]


def _u(text, ts=None):
    return {"role": "user", "content": text, **({"ts": ts} if ts else {})}


def _a(text):
    return {"role": "assistant", "content": text}


# ── The pair and the turn number ──────────────────────────────────────────


def test_a_question_is_paired_with_its_own_answer():
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from kazma_core.memory.rehydrate import _legacy_turn_texts

    unanswered = [_u("first?"), _a("first answer"), _u("second?")]
    assert consolidator.extract_turn_texts(unanswered) == ("second?", "")
    assert _legacy_turn_texts(unanswered) == ("second?", "first answer")  # the old rule
    with_tools = [
        HumanMessage("look it up"),
        AIMessage("", tool_calls=[{"name": "web_search", "args": {"q": "x"}, "id": "c1"}]),
        ToolMessage("result", tool_call_id="c1"),
        AIMessage("Here is what I found."),
    ]
    assert consolidator.extract_turn_texts(with_tools) == ("look it up", "Here is what I found.")


def test_a_repeated_question_is_two_memories():
    """The turn number was the turn's iteration count: two one-iteration
    turns asking the same thing got the same episode id, and INSERT OR
    IGNORE kept only the first."""
    from kazma_core.agent.graph_respond import respond_node
    from kazma_core.memory.dual_write import _episode_id

    first = [_u("what is on today?"), _a("Dentist at 10.")]
    second = first + [_u("what is on today?"), _a("Nothing else.")]
    stamps = []
    for msgs in (first, second):
        out = asyncio.run(respond_node({"messages": msgs, "iteration": 0, "thread_id": "t1"}))
        stamps.append(out["_post_turn_memory"]["turn"])
    assert stamps == [1, 2]
    ids = {_episode_id("t1", n, "what is on today?") for n in stamps}
    assert len(ids) == 2
    old_iteration_turns = [1, 1]  # negative control: both turns took one iteration
    assert len({_episode_id("t1", n, "what is on today?") for n in old_iteration_turns}) == 1


# ── One hand-over, at the closer every transport runs ─────────────────────


class _Graph:
    def __init__(self, values, next_nodes=()):
        self.snap = SimpleNamespace(values=values, next=tuple(next_nodes), tasks=[])

    async def aget_state(self, config):
        return self.snap


@pytest.fixture()
def scheduled(monkeypatch, tmp_path):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    calls: list[dict] = []

    def record(messages, *, session_id=None, turn=None, tenant_id="default"):
        calls.append({"session": session_id, "turn": turn, "tenant": tenant_id,
                      "question": consolidator.extract_turn_texts(messages)[0]})

    monkeypatch.setattr(consolidator, "_schedule_post_turn_memory", record)
    return calls


def _finished(thread, *msgs, turn=None):
    return {
        "messages": list(msgs),
        "_post_turn_memory": {"session_id": thread,
                              "turn": consolidator.user_turn_index(msgs) if turn is None else turn,
                              "tenant_id": "default"},
    }


def _close(graph, thread, **kwargs):
    from kazma_ui.turn_runtime import close_turn

    return asyncio.run(close_turn(graph, {"configurable": {"thread_id": thread}},
                                  thread_id=thread, **kwargs))


def test_a_finished_web_turn_is_handed_to_memory_once(scheduled):
    thread = f"web-{uuid.uuid4().hex[:8]}"
    graph = _Graph(_finished(thread, _u("book the table"), _a("Booked for 8.")))
    _close(graph, thread, streamed_text="Booked for 8.")
    _close(graph, thread)  # a disconnect or late settle closes the same turn again
    assert scheduled == [{"session": thread, "turn": 1, "tenant": "default",
                          "question": "book the table"}]


def test_a_paused_unfinished_or_stale_turn_is_not(scheduled):
    thread = f"web-{uuid.uuid4().hex[:8]}"
    msgs = (_u("delete the logs"), _a("Waiting for approval."))
    _close(_Graph(_finished(thread, *msgs), next_nodes=("tool_worker",)), thread)  # paused
    _close(_Graph(_finished(thread, *msgs)), thread, interrupted=True)  # interrupted
    stale = _finished(thread, _u("old"), _a("old answer"), turn=1)
    stale["messages"] += [_u("new question")]  # this turn ended without respond_node
    _close(_Graph(stale), thread)
    assert scheduled == []


def _calls_to(source: str, name: str) -> int:
    return sum(
        1
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and (getattr(node.func, "id", None) == name or getattr(node.func, "attr", None) == name)
    )


def test_one_call_site_hands_turns_to_memory():
    """The web lost memory because a transport had to remember to call it.
    Now close_turn alone calls remember_turn, and remember_turn alone
    schedules the work."""
    sites: dict[str, list[str]] = {"remember_turn": [], "_schedule_post_turn_memory": []}
    for package in ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway",
                    "kazma-tui/kazma_tui", "kazma-cli/kazma_cli"):
        for path in sorted((REPO / package).rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            for name in sites:
                if _calls_to(source, name):
                    sites[name].append(path.relative_to(REPO).as_posix())
    assert sites == {
        "remember_turn": ["kazma-ui/kazma_ui/turn_runtime.py"],
        "_schedule_post_turn_memory": ["kazma-core/kazma_core/memory/consolidator.py"],
    }
    rogue = "def done(state):\n    schedule_post_turn_memory(state['messages'])\n"
    assert _calls_to(rogue, "schedule_post_turn_memory") == 1  # negative control


# ── The reconciler ────────────────────────────────────────────────────────


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory import dual_write

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    db = sqlite3.connect(str(tmp_path / "memory_state.db"))
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    dual_write._reset_mirror()
    yield SimpleNamespace(dir=tmp_path, db=db)
    dual_write._reset_mirror()
    db.close()


def _iso(days_ago: float) -> str:
    return datetime.fromtimestamp(time.time() - days_ago * 86400, UTC).isoformat()


def _save(mem, session_id, messages, *, thread_id="", tenant="default"):
    from kazma_ui.session_manager import ChatSession, SessionManager

    store = SessionManager(db_path=str(mem.dir / "chat_sessions.db"))
    try:
        store.put(ChatSession(session_id=session_id, tenant_id=tenant, thread_id=thread_id,
                              messages=list(messages)))
    finally:
        store.close()


def _episodes(db):
    return [dict(r) for r in db.execute(
        "SELECT session_id, turn_number, user_text, assistant_text, tier, created_at, "
        "tenant_id, metadata_json FROM episodes ORDER BY created_at")]


def _run(mem, budget=60.0, now=None):
    return turn_reconcile._reconcile(
        mem.db, ("", ""), time.monotonic() + budget, time.time() if now is None else now
    )


def _queued(mem, task_type):
    from kazma_core.paths import memory_ops_db

    conn = sqlite3.connect(memory_ops_db())
    try:
        return [json.loads(r[0]) for r in conn.execute(
            "SELECT payload_json FROM memory_task_queue WHERE task_type = ?", (task_type,))]
    finally:
        conn.close()


def _with_the_pool_full(fn):
    taken = 0
    while consolidator._v2_extract_sem.acquire(blocking=False):
        taken += 1
    try:
        async def in_a_turn():
            fn()

        asyncio.run(in_a_turn())
    finally:
        for _ in range(taken):
            consolidator._v2_extract_sem.release()


def test_a_turn_the_pool_cannot_take_is_remembered_from_the_queue(mem):
    """Every extraction thread busy: the turn goes to the durable queue, and
    the memory worker writes its episode AND its facts. It used to be skipped
    for good -- turn reconcile brought the episode back, never the facts
    (Stage 2, W2)."""
    from kazma_core.memory import worker_bootstrap

    msgs = [_u("Quick note: I live in Porto."), _a("Porto, lovely city.")]
    _with_the_pool_full(lambda: consolidator._schedule_post_turn_memory(
        msgs, session_id="sess-w2", turn=1, tenant_id="default"))
    assert _episodes(mem.db) == []  # nothing ran in the turn
    [payload] = _queued(mem, "post_turn_memory")
    assert payload["user_text"] == "Quick note: I live in Porto."
    assert payload["assistant_text"] == "Porto, lovely city."

    assert asyncio.run(worker_bootstrap._handle_post_turn_memory(payload)) is True

    [episode] = _episodes(mem.db)
    assert (episode["session_id"], episode["turn_number"]) == ("sess-w2", 1)
    assert episode["assistant_text"] == "Porto, lovely city."
    facts = mem.db.execute(
        "SELECT predicate, object, source_session, source_turn FROM beliefs").fetchall()
    assert [tuple(f) for f in facts] == [("lives_in", "Porto", "sess-w2", 1)]
    worker_bootstrap.register_v2_handlers()
    from kazma_core.memory.task_queue import _HANDLERS

    assert "post_turn_memory" in _HANDLERS


def test_a_turn_the_queue_refuses_is_reported(mem, monkeypatch, caplog):
    """Negative control: with the queue down too, the loss is a warning and a
    counted failure -- never the silent debug line it used to be."""
    import logging

    monkeypatch.setattr("kazma_core.memory.task_queue.enqueue_task", lambda *a, **k: None)
    before = consolidator.get_post_turn_metrics()["enqueue_fail"]
    with caplog.at_level(logging.WARNING, logger="kazma_core.memory.consolidator"):
        _with_the_pool_full(lambda: consolidator._schedule_post_turn_memory(
            [_u("I live in Braga.")], session_id="sess-w2b", turn=1, tenant_id="default"))
    assert any("memory queue refused" in r.getMessage() for r in caplog.records)
    assert consolidator.get_post_turn_metrics()["enqueue_fail"] == before + 1


def test_turns_missing_from_memory_are_written_with_their_own_time(mem):
    from kazma_core.memory.dual_write import mirror_episode

    _save(mem, "s-web", [
        _u("plan the trip to Salalah", _iso(40)), _a("Day 1: arrive, Day 2: beach."),
        _u("/new", _iso(40)), _a("New season."),
        _u("what did we pick for day 2?", _iso(39)), _a("The beach."),
        _u("and the hotel?", _iso(39)), _a("Hilton."),
    ], thread_id="th-web")
    # The live pipeline did write the first turn.
    mirror_episode(session_id="th-web", turn_number=1, user_text="plan the trip to Salalah",
                   assistant_text="Day 1: arrive, Day 2: beach.")
    before_beliefs = mem.db.execute("SELECT count(*) FROM beliefs").fetchone()[0]

    report = _run(mem)
    rows = _episodes(mem.db)
    assert report["turns_written"] == 2 and report["done"] is True
    written = [r for r in rows if json.loads(r["metadata_json"])["source"] == "turn_reconcile"]
    assert [(r["turn_number"], r["user_text"], r["assistant_text"]) for r in written] == [
        (3, "what did we pick for day 2?", "The beach."),
        (4, "and the hotel?", "Hilton."),
    ]
    assert all(r["session_id"] == "th-web" and r["tier"] == "episodic" for r in written)
    assert abs(written[0]["created_at"] - (time.time() - 39 * 86400)) < 5
    assert mem.db.execute("SELECT count(*) FROM beliefs").fetchone()[0] == before_beliefs
    assert _run(mem)["turns_written"] == 0  # idempotent


def test_a_question_asked_twice_needs_two_memories_and_stubs_count(mem):
    from kazma_core.memory.dual_write import mirror_episode

    _save(mem, "s-rep", [
        _u("status?", _iso(30)), _a("All green."),
        _u("status?", _iso(20)), _a("One job failed."),
        _u("the long report question " * 12, _iso(10)), _a("A long answer."),
    ])
    mirror_episode(session_id="s-rep", turn_number=1, user_text="status?", assistant_text="All green.")
    long_q = ("the long report question " * 12).strip()
    mem.db.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
        "summary_text, tier, structural_importance, created_at) VALUES "
        "('erased', 'default', 's-rep', 3, NULL, NULL, ?, 'archived', 1, 0)",
        (long_q[:200] + " — A long answer.",),
    )
    mem.db.commit()
    assert _run(mem)["turns_written"] == 1
    texts = [r["assistant_text"] for r in _episodes(mem.db) if r["user_text"] == "status?"]
    assert sorted(texts) == ["All green.", "One job failed."]


def test_a_turn_still_settling_is_left_to_the_live_pipeline(mem):
    _save(mem, "s-new", [_u("just now", _iso(0.001)), _a("Hi.")])
    _save(mem, "s-old", [_u("last week", _iso(7)), _a("Hello.")])
    report = _run(mem)
    assert report["turns_written"] == 1 and report["turns_waiting"] == 1
    later = _run(mem, now=time.time() + 3600)  # after it settled, from the start
    assert later["turns_written"] == 1
    assert sorted(r["user_text"] for r in _episodes(mem.db)) == ["just now", "last week"]


def test_the_cursor_resumes_and_never_passes_an_unsettled_session(mem, monkeypatch):
    monkeypatch.setattr(turn_reconcile, "_PAGE", 1)  # page boundaries everywhere
    for i in range(4):
        _save(mem, f"s{i}", [_u(f"question {i}", _iso(5)), _a(f"answer {i}")])
    nothing = turn_reconcile._reconcile(mem.db, ("", ""), time.monotonic() - 1, time.time())
    assert (nothing["turns_written"], nothing["after"]) == (0, ["", ""])
    # Saved just now, the sessions are still settling: read, never passed.
    fresh = _run(mem)
    assert fresh["turns_written"] == 4 and fresh["after"] == ["", ""]
    later = time.time() + 3600  # an hour on, all four have settled
    report = _run(mem, now=later)
    assert report["turns_written"] == 0 and report["done"] is True
    assert report["after"][1] == "s3"
    assert turn_reconcile._reconcile(mem.db, tuple(report["after"]), time.monotonic() + 60,
                                     later)["sessions"] == 0


def test_every_tenant_and_the_pass_state(mem):
    from kazma_core.config_store import get_config_store

    _save(mem, "s-a", [_u("alpha", _iso(3)), _a("A")], tenant="default")
    _save(mem, "s-b", [_u("beta", _iso(3)), _a("B")], tenant="acme")
    report = turn_reconcile.run_turn_reconcile_pass()
    assert report["turns_written"] == 2
    assert {(r["tenant_id"], r["user_text"]) for r in _episodes(mem.db)} == {
        ("default", "alpha"), ("acme", "beta")}
    state = get_config_store().get(turn_reconcile.STATE_KEY)
    assert state["last"]["turns_written"] == 2
    assert state["after"] == ["", ""]  # both changed a moment ago: read again next pass


def test_memory_health_says_when_it_is_catching_up():
    from kazma_core.memory.health import _findability_component

    row = _findability_component({
        "vectors": {"episodes": {"total": 3, "pending": 0}, "beliefs": {"total": 1, "pending": 0}},
        "erased": {},
        "turn_reconcile": {"done": False, "turns_written": 120},
    })
    assert row["status"] == "warn" and "120 in the last pass" in row["detail"]


@pytest.mark.postgres
def test_the_reconciler_reads_the_store_the_web_ui_writes(mem):
    token = f"zq{uuid.uuid4().hex[:10]}"
    session = f"rec-{uuid.uuid4().hex[:8]}"
    _save(mem, session, [_u(f"remember the {token} code", _iso(2)), _a("Noted.")])
    report = _run(mem)
    assert report["turns_written"] >= 1
    assert [r["user_text"] for r in _episodes(mem.db) if r["session_id"] == session] == [
        f"remember the {token} code"]


# ── Nothing in the product repoints live memory ───────────────────────────


def test_the_golden_eval_never_touches_live_memory(tmp_path, monkeypatch):
    """The Dashboard's golden eval ran inside the live server, rebound the
    memory path to a temp file and left the shared episode writer on it."""
    import tempfile

    import kazma_core.paths as paths
    from kazma_core.memory import dual_write
    from kazma_core.memory.eval_golden import run_golden_eval

    live = tmp_path / "memory_state.db"
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(live))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    db = sqlite3.connect(str(live))
    ensure_primary_schema(db)
    db.execute("INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, "
               "assistant_text, summary_text, tier, structural_importance, created_at) "
               "VALUES ('mine', 'default', 's', 1, 'my real memory', 'kept', '', 'episodic', 1, 0)")
    db.commit()
    db.close()
    path_fn, writer = paths.primary_memory_db, dual_write._mirror
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))  # where its temp file goes

    report = run_golden_eval()
    assert report["total"] > 0 and report["pass_rate"] >= 0.5
    assert paths.primary_memory_db is path_fn and dual_write._mirror is writer
    check = sqlite3.connect(str(live))
    try:
        assert check.execute("SELECT id, user_text FROM episodes").fetchall() == [
            ("mine", "my real memory")]
    finally:
        check.close()
    assert list(scratch.iterdir()) == []  # its temp database is gone


def test_the_golden_eval_refuses_the_live_database(tmp_path, monkeypatch):
    from kazma_core.memory.eval_golden import run_golden_eval

    live = tmp_path / "memory_state.db"
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(live))
    conn = sqlite3.connect(str(live))
    ensure_primary_schema(conn)
    try:
        with pytest.raises(ValueError, match="never runs on live memory"):
            run_golden_eval(conn=conn)
    finally:
        conn.close()


def _repoints_live_memory(source: str, *, writer_module: bool = False) -> list[str]:
    """Rebinding a ``kazma_core.paths`` function, or resetting the shared
    episode writer: either points every thread of the server elsewhere."""
    tree = ast.parse(source)
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update(a.asname or a.name for a in node.names if a.name == "kazma_core.paths")
        elif isinstance(node, ast.ImportFrom) and node.module == "kazma_core":
            aliases.update(a.asname or a.name for a in node.names if a.name == "paths")

    def is_paths(expr) -> bool:
        return (isinstance(expr, ast.Name) and expr.id in aliases) or ast.unparse(expr) == "kazma_core.paths"

    found = []
    for node in ast.walk(tree):
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AugAssign) else [])
        found += [ast.unparse(t) for t in targets if isinstance(t, ast.Attribute) and is_paths(t.value)]
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name == "setattr" and node.args and is_paths(node.args[0]):
                found.append(ast.unparse(node))
            if name == "_reset_mirror" and not writer_module:
                found.append(ast.unparse(node))
    return found


def test_no_product_code_repoints_live_memory():
    offenders = []
    for package in ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway",
                    "kazma-tui/kazma_tui", "kazma-cli/kazma_cli", "kazma-skills/kazma_skills"):
        for path in sorted((REPO / package).rglob("*.py")):
            rel = path.relative_to(REPO).as_posix()
            found = _repoints_live_memory(path.read_text(encoding="utf-8"),
                                          writer_module=rel.endswith("memory/dual_write.py"))
            offenders += [f"{rel}: {f}" for f in found]
    assert offenders == []
    old = ("import kazma_core.paths as paths\n"
           "paths.primary_memory_db = lambda: tmp_path\n"
           "from kazma_core.memory.dual_write import _reset_mirror\n_reset_mirror()\n")
    assert len(_repoints_live_memory(old)) == 2  # negative control: the old golden eval
