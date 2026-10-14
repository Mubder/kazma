"""A step is one step on every surface; a rewind is a rewind; Delete and Forget reach the steps
(Time Travel TT-01, TT-03, TT-05, TT-G2).

Audit 2026-10-13:

- TT-01: snapshots were keyed ``(thread_id, iteration)`` and ``iteration``
  restarts every turn. In a two-turn chat the page listed two steps of four,
  "iteration 1" was turn 2's step on the web and turn 1's on the chat apps,
  and retention kept the highest ITERATION numbers, deleting a short later
  turn's step as it was written.
- TT-03: a web restore of two turns was undone by the next message (the
  session transcript, 2+ turns longer than the checkpoint, was fed back to
  the model), the scratchpad's merge reducer kept the later turns' entries,
  and no open tab was told.
- TT-05: chat delete, forget and "Don't remember" left every snapshot of the
  chat; the chat apps' ``/replay clear`` cleared SQLite while the agent's
  in-memory copy kept serving the steps; the web chat's ``/replay clear``
  cleared nothing.

Each runs on the real graph and saver of ``tests/_tt_graph.py``; the negative
controls show the old keying, transcript and copies.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests import _tt_graph as tt

REPO = Path(__file__).resolve().parents[1]


def _params():
    return [pytest.param("sqlite", id="sqlite"),
            pytest.param("postgres", id="postgres", marks=pytest.mark.postgres)]


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


def _thread() -> str:
    import uuid

    return f"tt-{uuid.uuid4().hex[:10]}"


# ── TT-01: identity ───────────────────────────────────────────────────────


@pytest.mark.parametrize("saver", _params())
def test_every_step_is_listed_once_and_restores_the_same_state_everywhere(saver, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_core.time_travel import SnapshotRecorder, process_recorder, set_process_recorder
    from kazma_ui import thread_ownership
    from kazma_ui.replay_routes import create_replay_router

    rec = _recorder(tmp_path)
    thread_id = _thread()

    async def two_turns():
        async with tt.open_saver(saver, tmp_path) as cp:
            graph = tt.build_graph(rec, [], cp)
            await tt.run_turn(graph, thread_id, "read:alpha")
            await tt.decide(graph, thread_id, approved=True)
            await tt.run_turn(graph, thread_id, "read:beta")
            await tt.decide(graph, thread_id, approved=True)

    tt.run(two_turns())
    steps = rec.list_snapshots(thread_id)
    # Two turns, two supervisor steps each; iterations restart every turn.
    assert [(s.turn_index, s.iteration) for s in steps] == [(1, 1), (1, 2), (2, 1), (2, 2)]
    # Negative control: the old (thread, iteration) key keeps two of four.
    assert len({(s.thread_id, s.iteration): s for s in steps}) == 2

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(thread_ownership, "require_thread_owned", AsyncMock(return_value=None))
        app = FastAPI()
        app.include_router(create_replay_router(recorder=rec, graph=object()))
        listed = TestClient(app).get(f"/api/replay/snapshots/{thread_id}").json()
    assert [t["turn_index"] for t in listed["turns"]] == [1, 2]
    assert [s["step"] for t in listed["turns"] for s in t["steps"]] == [1, 2, 1, 2]
    assert [s["id"] for s in listed["snapshots"]] == [s.id for s in steps]

    # The same id is the same state: the page's recorder, the process's (the
    # chat apps), and a fresh one after a restart.
    turn1_step1 = steps[0].id
    web = rec.get(turn1_step1).state_json
    set_process_recorder(None)
    gateway = process_recorder().get(turn1_step1).state_json
    rec.close()
    after_restart = SnapshotRecorder(db_path=str(tmp_path / "snapshots.db")).get(turn1_step1).state_json
    assert web == gateway == after_restart
    assert json.loads(web)["active_goal"] == "read:alpha"


def test_retention_keeps_the_newest_steps_by_capture_order(tmp_path):
    rec = _recorder(tmp_path, max_snapshots=3)
    for it in range(1, 6):  # a long turn
        rec.capture({"thread_id": "T", "iteration": it, "created_at": "turn-1", "messages": []})
    newest = rec.capture({"thread_id": "T", "iteration": 1, "created_at": "turn-2", "messages": []})
    kept = rec.list_snapshots("T")
    assert newest.id in {s.id for s in kept} and len(kept) == 3
    assert [s.iteration for s in kept] == [4, 5, 1]
    # Negative control: the old rule (highest iteration numbers) drops it.
    with closing(sqlite3.connect(str(tmp_path / "snapshots.db"))) as conn:
        conn.execute("INSERT INTO snapshots (id, thread_id, iteration, state_json, timestamp, seq) "
                     "VALUES ('old-a', 'T', 9, '{}', 'x', 1)")
        old_kept = [r[0] for r in conn.execute(
            "SELECT id FROM snapshots WHERE thread_id = 'T' ORDER BY iteration DESC LIMIT 3")]
    assert newest.id not in old_kept


def test_an_older_snapshots_db_gains_its_identity(tmp_path):
    """A file written before the identity columns: its rows are read, in
    their capture order, with their turn and message count."""
    path = tmp_path / "snapshots.db"
    with closing(sqlite3.connect(str(path))) as conn:
        conn.execute("CREATE TABLE snapshots (id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, iteration INTEGER NOT NULL, "
                     "state_json TEXT NOT NULL, timestamp TEXT NOT NULL, model_used TEXT DEFAULT '')")
        for i, (turn, it) in enumerate([("t1", 1), ("t1", 2), ("t2", 1)]):
            state = {"created_at": turn, "messages": [{"role": "user", "content": "q"}] * (1 if turn == "t1" else 2)}
            conn.execute("INSERT INTO snapshots VALUES (?, 'T', ?, ?, ?, '')",
                         (f"legacy-{i}", it, json.dumps(state), f"2026-01-0{i + 1}"))
        conn.commit()
    rec = _recorder(tmp_path)
    steps = rec.list_snapshots("T")
    assert [(s.id, s.turn_key, s.turn_index, s.message_count) for s in steps] == [
        ("legacy-0", "t1", 1, 1), ("legacy-1", "t1", 1, 1), ("legacy-2", "t2", 2, 2)]


# ── TT-03: a rewind is a rewind ─────────────────────────────────────────


def _transcript(rows):
    return [{"role": r, "content": c} for r, c in rows]


@pytest.mark.parametrize("saver", _params())
def test_a_restore_two_turns_back_is_what_the_next_message_sends(saver, tmp_path):
    """Three web turns; restore to the end of turn 1; the next message's
    model input (build_turn_messages, as the SSE route calls it, with the
    chat's transcript as fallback history) holds turn 1 only."""
    from kazma_core.agent.turn_input import build_turn_messages
    from kazma_ui.session_manager import ChatSession, get_session_manager
    from kazma_ui.thread_rewind import rewind_thread

    rec = _recorder(tmp_path)
    thread_id = _thread()
    store = get_session_manager()
    store.put(ChatSession(session_id=thread_id, thread_id=thread_id, title="t", messages=[]))

    async def run():
        async with tt.open_saver(saver, tmp_path) as cp:
            graph = tt.build_graph(rec, [], cp)
            for q in ("Q1", "Q2", "Q3 secret"):
                await tt.run_turn(graph, thread_id, q)
                with store.transact(thread_id) as sess:
                    sess.messages += _transcript([("user", q), ("assistant", f"Answer to {q}")])
            end_of_turn_1 = [s for s in rec.list_snapshots(thread_id) if s.turn_index == 1][-1]
            out = await rewind_thread(graph, thread_id, end_of_turn_1.id, actor="t", surface="web", recorder=rec)
            assert out["transcript_rows_removed"] == 4
            session = store.get(thread_id)
            assert [m["content"] for m in session.messages] == ["Q1", "Answer to Q1"]
            sent = await build_turn_messages(graph, tt.config(thread_id), user_text="Q4",
                                             fallback_history=session.messages)
            texts = [m.get("content") for m in sent]
            assert "Answer to Q2" not in texts and "Answer to Q3 secret" not in texts
            assert "Q1" in texts and texts[-1] == "Q4"
            # Negative control: the transcript as the old route left it.
            old_history = _transcript([("user", "Q1"), ("assistant", "Answer to Q1"),
                                       ("user", "Q2"), ("assistant", "Answer to Q2"),
                                       ("user", "Q3 secret"), ("assistant", "Answer to Q3 secret")])
            stale = await build_turn_messages(graph, tt.config(thread_id), user_text="Q4",
                                              fallback_history=old_history)
            assert "Answer to Q3 secret" in [m.get("content") for m in stale]
            # The steps after the restored one are gone too.
            assert max(s.turn_index for s in rec.list_snapshots(thread_id)) == 1

    tt.run(run())


@pytest.mark.parametrize("saver", _params())
def test_a_restore_replaces_the_scratchpad(saver, tmp_path):
    from kazma_core.time_travel import checkpoint_payload_from_snapshot
    from kazma_ui.thread_rewind import rewind_thread

    rec = _recorder(tmp_path)
    thread_id = _thread()

    async def run():
        async with tt.open_saver(saver, tmp_path) as cp:
            graph = tt.build_graph(rec, [], cp)
            await tt.run_turn(graph, thread_id, "read:alpha")
            await tt.decide(graph, thread_id, approved=True)
            turn1 = rec.list_snapshots(thread_id)[0]
            await tt.run_turn(graph, thread_id, "read:beta")
            await tt.decide(graph, thread_id, approved=True)
            await rewind_thread(graph, thread_id, turn1.id, actor="t", surface="web", recorder=rec)
            pad = (await graph.aget_state(tt.config(thread_id))).values.get("scratchpad") or {}
            assert sorted(pad) == ["note alpha"]
            # Negative control: the old payload merges into the later pad.
            await graph.aupdate_state(tt.config(thread_id), {"scratchpad": {"note beta": "beta"}})
            await graph.aupdate_state(tt.config(thread_id),
                                      checkpoint_payload_from_snapshot(rec.get(turn1.id).get_state(), thread_id))
            pad = (await graph.aget_state(tt.config(thread_id))).values.get("scratchpad") or {}
            assert sorted(pad) == ["note alpha", "note beta"]

    tt.run(run())


def test_a_restore_tells_every_open_tab(tmp_path, monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver

    from kazma_ui import delivery
    from kazma_ui.thread_rewind import rewind_thread

    frames = []

    class _Broker:
        async def emit(self, thread_id, event):
            frames.append((thread_id, event))
            return event

    monkeypatch.setattr(delivery, "get_turn_broker", lambda: _Broker())
    rec = _recorder(tmp_path)
    thread_id = _thread()

    async def run():
        graph = tt.build_graph(rec, [], InMemorySaver())
        await tt.run_turn(graph, thread_id, "hello")
        step = rec.list_snapshots(thread_id)[0]
        await rewind_thread(graph, thread_id, step.id, actor="t", surface="web", recorder=rec)
        return step

    step = tt.run(run())
    assert frames and frames[0][0] == thread_id
    assert frames[0][1]["type"] == "thread_rewound" and frames[0][1]["data"]["snapshot_id"] == step.id
    assert "thread_rewound" in delivery.REPLAY_SKIP_TYPES


def test_the_transcript_cut_keeps_an_unanswered_question_and_skips_instant_turns():
    from kazma_ui.thread_rewind import _transcript_cut

    rows = _transcript([("user", "Q1"), ("assistant", "A1"), ("user", "/yolo on"), ("assistant", "YOLO on"),
                        ("user", "Q2"), ("assistant", "A2"), ("user", "Q3"), ("assistant", "A3")])
    answered = _transcript([("user", "Q1"), ("assistant", "A1"), ("user", "Q2"), ("assistant", "A2")])
    assert _transcript_cut(rows, answered) == 6
    mid_turn = _transcript([("user", "Q1"), ("assistant", "A1"), ("user", "Q2")])
    assert _transcript_cut(rows, mid_turn) == 5
    assert _transcript_cut(rows, []) == 0


def test_the_pages_say_what_a_restore_takes_back_and_what_it_does_not():
    """Both languages: the later turns are taken back (chat, memory, steps),
    the world is not (files, commands, messages sent)."""
    from kazma_ui.i18n import t

    for lang in ("en", "ar"):
        about = t("replay.about_restore", lang)
        confirm = t("replay.confirm_restore", lang, turn=1, step=1)
        assert about != "replay.about_restore" and confirm != "replay.confirm_restore"
    assert "taken back" in t("replay.about_restore", "en") and "not undone" in t("replay.confirm_restore", "en")
    assert "تُسترجع" in t("replay.about_restore", "ar") and "لا يُتراجع" in t("replay.confirm_restore", "ar")
    assert "Later turns will be lost" not in t("replay.confirm_restore", "en")
    assert "never happened" not in t("replay.about_restore", "en")


# ── TT-05: Delete, Forget and Clear reach the steps ─────────────────────


def _seed_steps(rec, thread_id):
    for turn in (1, 2):
        for it in (1, 2):
            rec.capture({"thread_id": thread_id, "iteration": it, "created_at": f"t{turn}",
                         "messages": [{"role": "user", "content": f"secret {turn}"}] * turn})


def _held(path: Path, word: str) -> int:
    with closing(sqlite3.connect(str(path))) as conn:
        return conn.execute("SELECT count(*) FROM snapshots WHERE state_json LIKE ?", (f"%{word}%",)).fetchone()[0]


def test_deleting_a_chat_deletes_its_steps(tmp_path):
    from kazma_core.time_travel import process_recorder
    from kazma_ui.session_manager import ChatSession, get_session_manager
    from kazma_ui.sse_chat import create_sse_chat_router

    rec = process_recorder()
    get_session_manager().put(ChatSession(session_id="sess-1", thread_id="thr-1", title="x", messages=[]))
    _seed_steps(rec, "thr-1")
    _seed_steps(rec, "sess-1")  # an older build's key
    _seed_steps(rec, "other")
    router = create_sse_chat_router(graph=MagicMock())
    delete = next(r.endpoint for r in router.routes
                  if getattr(r, "path", "") == "/api/chat/sessions/{session_id}" and "DELETE" in r.methods)
    assert tt.run(delete("sess-1"))["status"] == "ok"
    assert rec.list_snapshots("thr-1") == [] and rec.list_snapshots("sess-1") == []
    assert len(rec.list_snapshots("other")) == 4


def test_the_delete_dialog_says_the_steps_go_and_memory_stays():
    """The disposition (TT-05, §15I) is stated where the choice is made, in
    both languages, and the script's fallback says the same."""
    from kazma_ui.i18n.catalog.chat import TRANSLATIONS

    body = TRANSLATIONS["chat.delete_chat_body"]
    assert "Time Travel steps are deleted with it" in body["en"] and "stays in its memory" in body["en"]
    assert "خطواتها المحفوظة في السفر عبر الزمن" in body["ar"] and "يبقى في ذاكرته" in body["ar"]
    script = (REPO / "kazma-ui" / "kazma_ui" / "static" / "js" / "chat.js").read_text(encoding="utf-8")
    assert body["en"] in script


def test_forgetting_a_turn_deletes_the_steps_that_hold_it(tmp_path, monkeypatch):
    from kazma_core.memory import forget

    rec = _held_memory(tmp_path, monkeypatch)
    eid = _remember_turn(tmp_path, "thr-f", 2)
    out = forget.forget_episode(eid, tenant_id="default")
    assert out["ok"] and out["snapshots_deleted"] == 2
    assert [s.turn_index for s in rec.list_snapshots("thr-f")] == [1, 1]
    assert _held(tmp_path / "snapshots.db", "secret 2") == 0


def test_forgetting_the_chat_deletes_every_step(tmp_path, monkeypatch):
    from kazma_core.memory import forget

    rec = _held_memory(tmp_path, monkeypatch)
    _remember_turn(tmp_path, "thr-f", 1)
    assert forget.forget_chat("thr-f", tenant_id="default")["ok"]
    assert rec.list_snapshots("thr-f") == []


def test_without_the_fan_out_a_forgotten_turn_stays_in_the_steps(tmp_path, monkeypatch):
    """Negative control: the forget of before (no snapshot step)."""
    from kazma_core.memory import forget

    _held_memory(tmp_path, monkeypatch)
    monkeypatch.setattr(forget, "_forget_snapshots", lambda keys, turn: 0)
    eid = _remember_turn(tmp_path, "thr-f", 2)
    forget.forget_episode(eid, tenant_id="default")
    assert _held(tmp_path / "snapshots.db", "secret 2") == 2


def _held_memory(tmp_path, monkeypatch):
    from kazma_core.memory import dual_write, forget
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema
    from kazma_core.time_travel import process_recorder

    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    monkeypatch.setattr(forget, "chat_keys", lambda key: [key] if key else [])
    with closing(sqlite3.connect(str(tmp_path / "memory_state.db"))) as db:
        ensure_primary_schema(db)
    with closing(sqlite3.connect(str(tmp_path / "memory_ops.db"))) as ops:
        ensure_ops_schema(ops)
    dual_write._reset_mirror()
    rec = process_recorder()
    _seed_steps(rec, "thr-f")
    return rec


def _remember_turn(tmp_path, thread_id: str, turn: int) -> str:
    from kazma_core.memory.dual_write import mirror_episode

    mirror_episode(session_id=thread_id, turn_number=turn, user_text=f"secret {turn}", assistant_text="ok")
    with closing(sqlite3.connect(str(tmp_path / "memory_state.db"))) as db:
        return db.execute("SELECT id FROM episodes WHERE session_id = ? AND turn_number = ?",
                          (thread_id, turn)).fetchone()[0]


@pytest.mark.parametrize("ledger", ["asked", "ignored"])
def test_the_retroactive_scan_reextracts_nothing_forgotten(tmp_path, monkeypatch, ledger):
    """``scripts/scan_old_sessions.py`` reads the steps: after a Forget it
    finds no forgotten turn there, and a forgotten turn that a copy still
    held is refused by the forget ledger under every chat key (Memory M08).
    ``ignored`` is the negative control: without the ledger check the scan
    would extract the forgotten turn again."""
    import importlib.util

    from kazma_core.memory import forget

    rec = _held_memory(tmp_path, monkeypatch)
    eid = _remember_turn(tmp_path, "thr-f", 2)
    assert forget.forget_episode(eid, tenant_id="default")["ok"]
    spec = importlib.util.spec_from_file_location("_scan_old_sessions_tt", REPO / "scripts" / "scan_old_sessions.py")
    scan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scan)

    steps = scan.load_snapshots_sessions(str(tmp_path / "snapshots.db"))
    held = [m.get("content") for s in steps if s["session_id"] == "thr-f" for m in s["messages"]]
    assert held and "secret 2" not in held, held
    assert rec.list_snapshots("thr-f")

    extracted: list[int] = []
    monkeypatch.setattr(scan, "run_heuristic_extraction", lambda *a, turn, **k: extracted.append(turn) or 0)
    if ledger == "ignored":
        monkeypatch.setattr(forget, "refuses_write", lambda conn, **kw: False)
    copy = {"session_id": "thr-f", "messages": [
        {"role": "user", "content": "secret 1"}, {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "secret 2"}, {"role": "assistant", "content": "ok"},
    ]}
    with closing(sqlite3.connect(str(tmp_path / "memory_state.db"))) as p, \
            closing(sqlite3.connect(str(tmp_path / "memory_ops.db"))) as o:
        tt.run(scan.scan_session(p, o, copy))
    assert extracted == ([1] if ledger == "asked" else [1, 2])


def test_replay_clear_on_every_surface_leaves_no_surface_a_step(tmp_path, monkeypatch):
    """The chat apps' /replay clear, the web chat's, and the page's Delete:
    afterwards no recorder -- the page's, the process's, a fresh one -- lists
    a step (the agent's in-memory copy used to keep serving them)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_core.time_travel import SnapshotRecorder, process_recorder
    from kazma_gateway.slash_commands import resolve_slash_command
    from kazma_ui import thread_ownership
    from kazma_ui.replay_routes import create_replay_router
    from kazma_ui.sse_chat._time_travel import time_travel_reply

    rec = process_recorder()
    page = SnapshotRecorder(db_path=rec.db_path)
    monkeypatch.setattr(thread_ownership, "require_thread_owned", AsyncMock(return_value=None))

    def everywhere(thread_id):
        return {len(r.list_snapshots(thread_id)) for r in (rec, page, SnapshotRecorder(db_path=rec.db_path))}

    _seed_steps(rec, "gw")
    assert "Deleted 4 saved steps" in resolve_slash_command("/replay clear", context={"thread_id": "gw"})
    assert everywhere("gw") == {0}
    _seed_steps(rec, "web")
    assert time_travel_reply("/replay clear", "web", rec, "en") == "Deleted 4 saved steps of this chat."
    assert everywhere("web") == {0}
    _seed_steps(rec, "page")
    app = FastAPI()
    app.include_router(create_replay_router(recorder=page, graph=object()))
    assert TestClient(app).delete("/api/replay/threads/page").json()["cleared"] == 4
    assert everywhere("page") == {0}


def test_snapshots_are_neither_backed_up_nor_carried(tmp_path):
    from kazma_core.backup.universal import selectable_databases
    from kazma_core.migration import exporter, importer
    from kazma_core.store_registry import NOT_BACKED_UP, STORES, carried_in_bundle

    assert STORES["snapshots.db"].migration == "machine" and STORES["snapshots.db"].reason
    assert "snapshots.db" in NOT_BACKED_UP
    assert "snapshots.db" not in {arc for _, arc in exporter._DATA_DBS} | set(exporter._DATA_DIR_DBS)
    assert "snapshots.db" not in importer._BUNDLE_DB_TO_DEST_RESOLVER
    assert not carried_in_bundle("snapshots.db")
    for name in ("snapshots.db", "chat_sessions.db"):
        sqlite3.connect(str(tmp_path / name)).close()
    picked = {p.name for p in selectable_databases(tmp_path)}
    assert "chat_sessions.db" in picked and "snapshots.db" not in picked
    # The audit trail travels.
    assert STORES["time_travel_audit.db"].migration == "bundle"
    assert "time_travel_audit.db" in set(exporter._DATA_DIR_DBS)


def test_the_memory_forget_contract_names_the_steps():
    from kazma_core.memory import memory_stores

    entry = next(e for e in memory_stores.EXTERNAL_STORES if "snapshots.db" in e.name)
    assert entry.erase == "forget" and "forget_turn_snapshots" in entry.forget


# ── TT-G2: one multi-surface, restart-spanning run ───────────────────────


@pytest.mark.parametrize("saver", _params())
def test_one_step_one_state_on_every_surface_across_a_restart(saver, tmp_path, monkeypatch):
    """Turns on the real graph; the page lists them; the chat apps resolve a
    step by its short id and restore it; after a 'restart' (a new recorder
    and saver) the same id is the same state, the next message sends the
    rewound transcript, and a deleted chat cannot be listed or restored."""
    from kazma_core.agent.turn_input import build_turn_messages
    from kazma_core.time_travel import SnapshotRecorder, process_recorder
    from kazma_ui.session_manager import ChatSession, get_session_manager
    from kazma_ui.sse_chat import create_sse_chat_router
    from kazma_ui.thread_rewind import RewindRefused, rewind_thread

    rec = process_recorder()
    thread_id = _thread()
    store = get_session_manager()
    store.put(ChatSession(session_id=thread_id, thread_id=thread_id, title="t", messages=[]))

    async def first_life():
        async with tt.open_saver(saver, tmp_path) as cp:
            graph = tt.build_graph(rec, [], cp)
            for q in ("Q1", "Q2", "Q3"):
                await tt.run_turn(graph, thread_id, q)
                with store.transact(thread_id) as sess:
                    sess.messages += _transcript([("user", q), ("assistant", f"Answer to {q}")])
            target = [s for s in rec.list_snapshots(thread_id) if s.turn_index == 2][-1]
            # The chat apps name it by its first characters.
            assert process_recorder().resolve_ref(thread_id, target.id[:8]) == target.id
            await rewind_thread(graph, thread_id, target.id, actor="telegram:1", surface="telegram")
            return target.id, (await graph.aget_state(tt.config(thread_id))).values["messages"]

    target_id, restored = tt.run(first_life())
    rec.close()

    async def second_life():
        async with tt.open_saver(saver, tmp_path) as cp:
            fresh = SnapshotRecorder(db_path=str(tmp_path / "snapshots.db"))
            graph = tt.build_graph(fresh, [], cp)
            assert fresh.get(target_id).get_state()["messages"][:4] == restored[:4]
            snap = await graph.aget_state(tt.config(thread_id))
            assert [m.get("content") for m in snap.values["messages"]] == [m.get("content") for m in restored]
            sent = await build_turn_messages(graph, tt.config(thread_id), user_text="Q4",
                                             fallback_history=store.get(thread_id).messages)
            assert "Answer to Q3" not in [m.get("content") for m in sent]
            # Deleted: nothing to list, nothing to restore.
            router = create_sse_chat_router(graph=MagicMock())
            delete = next(r.endpoint for r in router.routes
                          if getattr(r, "path", "") == "/api/chat/sessions/{session_id}" and "DELETE" in r.methods)
            await delete(thread_id)
            assert fresh.list_snapshots(thread_id) == []
            with pytest.raises(RewindRefused) as refused:
                await rewind_thread(graph, thread_id, target_id, actor="t", surface="web", recorder=fresh)
            assert refused.value.code == "not_found"

    tt.run(second_life())


def test_the_audit_trail_has_a_row_per_restore_fork_and_refusal(tmp_path):
    from langgraph.checkpoint.memory import InMemorySaver

    from kazma_core.time_travel_audit import _recent_audit_rows
    from kazma_ui.active_turns import register_turn, unregister_turn
    from kazma_ui.thread_rewind import RewindRefused, fork_thread, rewind_thread

    rec = _recorder(tmp_path)
    thread_id = _thread()

    async def run():
        graph = tt.build_graph(rec, [], InMemorySaver())
        await tt.run_turn(graph, thread_id, "hello")
        step = rec.list_snapshots(thread_id)[0]
        await rewind_thread(graph, thread_id, step.id, actor="web:admin:1", surface="web", recorder=rec)
        forked = await fork_thread(graph, thread_id, step.id, actor="web:admin:1", surface="web", recorder=rec)
        running = SimpleNamespace(done=lambda: False)
        register_turn(thread_id, running)
        try:
            with pytest.raises(RewindRefused):
                await rewind_thread(graph, thread_id, step.id, actor="web:admin:1", surface="web", recorder=rec)
        finally:
            unregister_turn(thread_id, running)
        return step, forked

    step, forked = tt.run(run())
    rows = _recent_audit_rows(thread_id)
    assert [(r["action"], r["outcome"]) for r in reversed(rows)] == [
        ("restore", "ok"), ("fork", "ok"), ("restore", "turn_running")]
    restore = rows[-1]
    assert restore["actor"] == "web:admin:1" and restore["snapshot_id"] == step.id
    assert restore["prior_checkpoint_id"]
    assert rows[1]["target_thread_id"] == forked["new_thread_id"]
