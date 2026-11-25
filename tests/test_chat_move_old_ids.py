"""Spaces S2-12: what Kazma kept by a chat's old ids, when the chat moves.

* Taken out of a shared space: that space keeps none of it -- the chat's
  drafts, plan, confirmations and decided gates (``chat_moves.MOVED_COLUMNS``),
  the finished swarm tasks its maker started from it -- and the reminders and
  tasks the mover booked in it go with them (``chat_moves.follow_jobs``).
* Shared: all of it stays in the person's own space.
* Either way the old thread's grants, YOLO, path grants, ``/long``, steers,
  receipts and frames end with the chat, and a swarm task that is not over or
  plan mode refuses the move.

A gate: every table that names a thread beside a space is declared.
"""

from __future__ import annotations

import asyncio
import secrets
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from kazma_core import paths
from kazma_core.spaces import chat_moves, scope, space_data

from tests._chat_move_world import World, handed_over, world  # noqa: F401  (world: a fixture)


@pytest.fixture(autouse=True)
def _gates_in_the_data_folder():
    """The gate registry where an install keeps it (each test has its own
    folder's otherwise)."""
    from kazma_core.safety import hitl_gates

    hitl_gates.set_db_path_for_tests(None)
    yield


def _rows(store: str, table: str, where: str, *params: Any) -> list[tuple]:
    path = dict(space_data.store_paths()).get(store)
    if path is None:
        return []
    with closing(sqlite3.connect(str(path))) as conn:
        return conn.execute(f"SELECT * FROM {table} WHERE {where}", params).fetchall()


def _count(thread: str) -> dict[str, int]:
    return {table: len(_rows(store, table, "thread_id = ?", thread))
            for store, table in chat_moves.MOVED_COLUMNS}


def _plant(w: World, space: str, thread: str) -> str:
    """One row of the chat in each table of MOVED_COLUMNS: its commitment."""
    from kazma_core.agent.artifacts import get_artifact_store
    from kazma_core.agent.task_ledger import TaskLedger, get_ledger_store
    from kazma_core.safety import hitl_gates
    from kazma_core.safety.commitment.store import Commitment, create_commitment

    with scope.bind_scope(space, w.ids["reem"]):
        get_artifact_store().put_scratchpad(space, thread, "notes", "kept by the chat")
        get_ledger_store().save(TaskLedger(thread_id=thread, goal="the trip", tenant_id=space))
        held = create_commitment(Commitment(thread_id=thread, act="remind", status="confirmed", tool_name="t"))
        gate = hitl_gates.register_gate(hitl_gates.GateRow(
            gate_id=f"g-{thread}", thread_id=thread, tool="file_write", tenant_id=space))
        hitl_gates.settle_gate(gate.gate_id, "approve")
    assert _count(thread) == dict.fromkeys((table for _s, table in chat_moves.MOVED_COLUMNS), 1)
    return held


def _open_gate(w: World, space: str, thread: str) -> None:
    from kazma_core.safety import hitl_gates

    with scope.bind_scope(space, w.ids["reem"]):
        hitl_gates.register_gate(hitl_gates.GateRow(
            gate_id=f"open-{thread}", thread_id=thread, tool="shell_exec", tenant_id=space))


# ── the rows: gone from the shared space it was taken out of, else here ───


def test_a_chat_taken_out_leaves_none_of_its_rows(world) -> None:
    w, i = world, world.ids
    session, thread = w.chat("reem", i["family"], mark="out")
    _other, other_thread = w.chat("reem", i["family"], mark="another chat")
    held = _plant(w, i["family"], thread)
    _plant(w, i["family"], other_thread)
    assert _rows("memory_ops.db", "commitment_events", "commitment_id = ?", held)
    w.moved("reem", i["family"], session, i["mine"])
    assert _count(thread) == dict.fromkeys((t for _s, t in chat_moves.MOVED_COLUMNS), 0)
    assert _rows("memory_ops.db", "commitment_events", "commitment_id = ?", held) == []
    assert _count(other_thread) == dict.fromkeys((t for _s, t in chat_moves.MOVED_COLUMNS), 1), "another chat's stay"


def test_control_a_take_out_that_removes_none_leaves_them(world, monkeypatch) -> None:
    w, i = world, world.ids
    session, thread = w.chat("reem", i["family"], mark="out")
    _plant(w, i["family"], thread)
    monkeypatch.setattr(chat_moves, "remove_what_it_left", lambda *_a, **_k: 0)
    w.moved("reem", i["family"], session, i["mine"])
    assert set(_count(thread).values()) == {1}


def test_a_shared_chats_rows_stay_in_its_own_space(world) -> None:
    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="shared")
    _plant(w, i["mine"], thread)
    shared = w.moved("reem", i["mine"], session, i["family"])
    assert set(_count(thread).values()) == {1}
    for store, table in chat_moves.MOVED_COLUMNS:
        column = chat_moves.MOVED_COLUMNS[(store, table)]
        assert _rows(store, table, f"thread_id = ? AND {column} = ?", thread, i["mine"])
    there = w.row(i["family"], "reem", shared).thread_id
    assert set(_count(there).values()) == {0}, "and nothing of it is the copy's"


def test_another_spaces_rows_and_an_open_gate_are_never_removed(world) -> None:
    w, i = world, world.ids
    _session, thread = w.chat("reem", i["family"], mark="open")
    _open_gate(w, i["family"], thread)
    with scope.bind_scope(i["omars"], i["omar"]):
        from kazma_core.agent.artifacts import get_artifact_store

        get_artifact_store().put_scratchpad(i["omars"], thread, "notes", "another space's")
    assert chat_moves.remove_what_it_left(thread, space_id=i["family"]) == 0
    assert len(_rows("hitl_gates.db", "hitl_gates", "gate_id = ?", f"open-{thread}")) == 1
    assert len(_rows("agent_artifacts.db", "agent_artifacts", "thread_id = ?", thread)) == 1


# ── reminders and tasks: theirs ───────────────────────────────────────────


def _book(space: str, thread: str, who: str) -> str:
    from kazma_core.cron.store import ScheduledJob, SQLiteCronStore

    async def insert() -> str:
        store = SQLiteCronStore(str(paths.data_dir() / "cron.db"))
        try:
            await store.init()
            job = ScheduledJob(
                job_id=f"j-{secrets.token_hex(6)}", timing="daily 09:00", prompt="water the plants",
                platform="web", thread_id=thread, tenant_id=space, creator=who, kind="reminder",
                next_run=(datetime.now(UTC) + timedelta(days=1)).isoformat())
            await store.insert(job)
            return job.job_id
        finally:
            await store.close()

    return asyncio.run(insert())


def _job(job_id: str) -> tuple[str, str]:
    with closing(sqlite3.connect(str(paths.data_dir() / "cron.db"))) as conn:
        return conn.execute("SELECT tenant_id, thread_id FROM cron_jobs WHERE job_id = ?", (job_id,)).fetchone()


def _waiting_delivery(job_id: str, space: str) -> None:
    with closing(sqlite3.connect(str(paths.data_dir() / "cron.db"))) as conn, conn:
        conn.execute("INSERT INTO cron_deliveries (job_id, tenant_id, fire_key, body, created_at) "
                     "VALUES (?, ?, ?, 'water the plants', 1)", (job_id, space, f"f-{job_id}"))


def test_the_movers_own_reminders_come_with_them_on_a_take_out(world) -> None:
    w, i = world, world.ids
    session, thread = w.chat("reem", i["family"], mark="reminders")
    mine, omars = _book(i["family"], thread, i["reem"]), _book(i["family"], thread, i["omar"])
    elsewhere = _book(i["family"], "t-another-chat", i["reem"])
    taken = w.moved("reem", i["family"], session, i["mine"])
    there = w.row(i["mine"], "reem", taken).thread_id
    assert _job(mine) == (i["mine"], there), "hers comes with her, under the copy's thread"
    assert _job(omars) == (i["family"], thread), "another member's stays"
    assert _job(elsewhere) == (i["family"], "t-another-chat")
    with closing(sqlite3.connect(str(paths.data_dir() / "cron.db"))) as conn:
        events = conn.execute("SELECT event, actor, source FROM cron_events WHERE job_id = ? AND event = 'moved'",
                              (mine,)).fetchall()
    assert events == [("moved", i["reem"], "chat_move")]


def test_what_waits_to_be_delivered_comes_with_the_job(world) -> None:
    w, i = world, world.ids
    _session, thread = w.chat("reem", i["family"], mark="delivery")
    job = _book(i["family"], thread, i["reem"])
    _waiting_delivery(job, i["family"])
    moved = {"space_id": i["family"], "by": i["reem"], "to_space": i["mine"], "to_thread": "t-new"}
    assert chat_moves.follow_jobs(thread, **moved) == 1
    with closing(sqlite3.connect(str(paths.data_dir() / "cron.db"))) as conn:
        assert conn.execute("SELECT tenant_id FROM cron_deliveries WHERE job_id = ?",
                            (job,)).fetchall() == [(i["mine"],)]
    assert chat_moves.follow_jobs(thread, **moved) == 0, "run again, nothing more"


def test_on_a_share_reminders_stay_in_their_own_space(world) -> None:
    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="reminders")
    job = _book(i["mine"], thread, i["reem"])
    w.moved("reem", i["mine"], session, i["family"])
    assert _job(job) == (i["mine"], thread)


# ── swarm tasks, plan mode ────────────────────────────────────────────────


@pytest.fixture
def swarm(monkeypatch, tmp_path):
    from kazma_core.swarm import engine as engine_mod
    from kazma_core.swarm.task import SwarmTask, TaskStatus
    from kazma_core.swarm.task_store import TaskStore

    store = TaskStore(str(tmp_path / "swarm_tasks.db"))
    monkeypatch.setattr(engine_mod, "peek_task_store", lambda: store)

    def task(space: str, thread: str, who: str, status: str) -> str:
        made = SwarmTask(prompt="look it up", workers=["probe"], status=TaskStatus(status), metadata={
            "tenant_id": space, "asker": who, "source_thread_id": thread})
        with scope.bind_scope(space, who):
            assert store.persist_task(made)
        return made.id

    def held(task_id: str) -> bool:
        return store.get_task(task_id) is not None

    yield task, held
    store.close()


@pytest.mark.parametrize("status", ["pending", "running", "paused"])
def test_a_swarm_task_that_is_not_over_holds_the_move(world, swarm, status) -> None:
    w, i = world, world.ids
    task, _held = swarm
    session, thread = w.chat("reem", i["mine"], mark="swarm")
    task(i["mine"], thread, i["reem"], status)
    refused = w.move("reem", i["mine"], session, i["family"])
    assert refused.status_code == 409 and refused.json()["error"] == "swarm_running", refused.text
    assert session in w.listed("reem", i["mine"]) and chat_moves.moves_under_way() == {}


def test_a_finished_task_and_another_chats_do_not(world, swarm) -> None:
    w, i = world, world.ids
    task, _held = swarm
    session, thread = w.chat("reem", i["mine"], mark="swarm")
    task(i["mine"], thread, i["reem"], "completed")
    task(i["mine"], "t-another", i["reem"], "running")
    w.moved("reem", i["mine"], session, i["family"])


def test_the_finished_tasks_its_maker_started_go_with_a_take_out(world, swarm) -> None:
    w, i = world, world.ids
    task, held = swarm
    session, thread = w.chat("reem", i["family"], mark="swarm")
    mine, omars = (task(i["family"], thread, i[who], "completed") for who in ("reem", "omar"))
    elsewhere = task(i["family"], "t-another", i["reem"], "completed")
    w.moved("reem", i["family"], session, i["mine"])
    assert not held(mine)
    assert held(omars) and held(elsewhere)


def test_on_a_share_they_stay(world, swarm) -> None:
    w, i = world, world.ids
    task, held = swarm
    session, thread = w.chat("reem", i["mine"], mark="swarm")
    mine = task(i["mine"], thread, i["reem"], "completed")
    w.moved("reem", i["mine"], session, i["family"])
    assert held(mine)


def test_a_task_store_that_cannot_be_read_refuses_the_move(world, monkeypatch) -> None:
    from kazma_core.swarm import chat_tasks

    w, i = world, world.ids
    session, _thread = w.chat("reem", i["mine"], mark="swarm")

    def unreadable(_thread: str) -> str:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(chat_tasks, "open_task_of_chat", unreadable)
    assert w.move("reem", i["mine"], session, i["family"]).status_code == 503
    assert session in w.listed("reem", i["mine"]) and chat_moves.moves_under_way() == {}


def test_plan_mode_holds_the_move_until_it_is_over(world) -> None:
    from kazma_core.agent.plan_mode import consume_pending_execute, disable_plan_mode, enable_plan_mode

    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="plan")
    enable_plan_mode(thread, actor="reem")
    refused = w.move("reem", i["mine"], session, i["family"])
    assert refused.status_code == 409 and refused.json()["error"] == "plan_waiting", refused.text
    disable_plan_mode(thread, actor="reem", pending_execute=True)
    refused = w.move("reem", i["mine"], session, i["family"])
    assert refused.json()["error"] == "plan_waiting", "an approved plan waits to run"
    assert consume_pending_execute(thread) is True
    w.moved("reem", i["mine"], session, i["family"])


# ── what was kept for the running of the old thread ends ──────────────────


def _plant_chat_state(w: World, space: str, thread: str, tmp_path: Path) -> None:
    from kazma_core.agent.long_task import enable_long_task
    from kazma_core.agent.steer import push_soft_steer
    from kazma_core.safety.hitl_grants import grant_tool
    from kazma_core.safety.task_grants import grant_task
    from kazma_core.safety.yolo import enable_yolo
    from kazma_core.workspace.path_grants import grant_session_path
    from kazma_ui.delivery import get_turn_broker

    with scope.bind_scope(space, w.ids["reem"]):
        grant_tool(thread, "file_write", actor="reem")
        grant_task(thread, actor="reem")
        enable_yolo(thread, actor="reem", force=True)
        grant_session_path(thread, str(tmp_path), actor="reem")
        enable_long_task(thread, actor="reem")
    push_soft_steer(thread, "shorter please")
    get_turn_broker()._journal.append(thread, {"type": "token", "data": {"content": "x"}})


def _state_keys(thread: str) -> list[str]:
    from kazma_core.config_store import get_config_store

    return sorted(key for group in get_config_store().get_all().values() for key in group
                  if space_data._is_chat_state(key, {thread}))


@pytest.mark.parametrize("out", [False, True], ids=["shared", "taken-out"])
def test_its_grants_yolo_steers_and_frames_end_with_it(world, tmp_path, out) -> None:
    from kazma_core.agent.steer import drain_soft_steers
    from kazma_ui.delivery import get_turn_broker

    w, i = world, world.ids
    frm, to = (i["family"], i["mine"]) if out else (i["mine"], i["family"])
    session, thread = w.chat("reem", frm, mark="state")
    _other, other_thread = w.chat("reem", frm, mark="other")
    _plant_chat_state(w, frm, thread, tmp_path)
    _plant_chat_state(w, frm, other_thread, tmp_path)
    kinds = {key.split(".")[0] for key in _state_keys(thread)}
    assert kinds >= {"hitl_grant", "task_grant", "yolo", "path_grant", "long_task"}, kinds
    moved = w.row(to, "reem", w.moved("reem", frm, session, to))
    assert _state_keys(thread) == [] and _state_keys(moved.thread_id) == []
    assert drain_soft_steers(thread) == [] and get_turn_broker().resume(thread, 0)[0] == []
    assert _state_keys(other_thread) and drain_soft_steers(other_thread), "another chat's stay"


def _receipt(thread: str, effect: str, *, completed: bool) -> None:
    from kazma_core.agent.effect_journal import _EffectJournal

    journal = _EffectJournal(paths.data_dir() / "tool_effects.db")
    journal.begin(effect, "hash", thread, "file_write")
    if completed:
        journal.finish(effect, {"content": "done"})


def test_its_completed_receipts_end_and_one_whose_outcome_is_unknown_stays(world) -> None:
    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="receipts")
    _receipt(thread, f"done-{thread}", completed=True)
    _receipt(thread, f"unknown-{thread}", completed=False)
    _receipt("t-another", f"other-{thread}", completed=True)
    w.moved("reem", i["mine"], session, i["family"])
    with closing(sqlite3.connect(str(paths.data_dir() / "tool_effects.db"))) as conn:
        left = {r[0] for r in conn.execute("SELECT effect_id FROM tool_effects WHERE effect_id LIKE ?",
                                           (f"%{thread}",))}
    assert left == {f"unknown-{thread}", f"other-{thread}"}


def test_a_step_a_stop_cut_off_is_done_at_settle(world, tmp_path) -> None:
    """Every step after the delete may be the one a stop cut off."""
    w, i = world, world.ids
    _session, thread, entry = handed_over(w, "stopped", "stopped", out=True)
    _plant(w, i["family"], thread)
    _plant_chat_state(w, i["family"], thread, tmp_path)
    job = _book(i["family"], thread, i["reem"])
    w.restart()
    assert [s["outcome"] for s in w.settle()] == ["finished"]
    assert set(_count(thread).values()) == {0} and _state_keys(thread) == []
    assert _job(job) == (i["mine"], entry["new_thread"])


# ── the gate ──────────────────────────────────────────────────────────────


#: The columns that name a chat: its thread, or its session (the chat store's
#: rows and spool are kept by session).
_CHAT_COLUMNS = {"thread_id", "session_id", "thread_ids"}


def _threaded_tables(stores: list[tuple[str, Path]]) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for store, path in stores:
        with closing(sqlite3.connect(str(path))) as conn:
            for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
                columns = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
                if columns & _CHAT_COLUMNS and columns & set(space_data._SPACE_COLUMNS):
                    found.add((store, table))
    return found


def _tables(path: Path) -> set[str]:
    with closing(sqlite3.connect(str(path))) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _declared() -> set[tuple[str, str]]:
    return (set(chat_moves.MOVED_COLUMNS) | set(chat_moves.FOLLOWS)
            | set(chat_moves.MOVED_BY_THEIR_CODE) | set(chat_moves.STAYS))


def test_every_table_naming_a_thread_beside_a_space_moves_or_stays(monkeypatch, tmp_path) -> None:
    """Every table of a row store with a thread column and a space column is
    in ``MOVED_COLUMNS`` (removed from a shared space its chat is taken out
    of), ``FOLLOWS`` (its maker's, goes with them), ``MOVED_BY_THEIR_CODE``
    or ``STAYS`` (with why): a new one fails here."""
    from tests._space_world import plant_world

    gen = plant_world(monkeypatch, tmp_path)
    next(gen)
    try:
        stores = space_data.store_paths()
        found = _threaded_tables(stores)
        assert found - _declared() == set(), "a table names a thread beside a space and is not declared"
        by_name = dict(stores)
        # The other way: one declared and there names a thread beside a space.
        there = {(store, table) for store, table in _declared() if store in by_name and table in _tables(by_name[store])}
        assert there - found == set(), "a declared table names no thread beside a space"
        for (store, table), column in {**chat_moves.MOVED_COLUMNS, **chat_moves.FOLLOWS}.items():
            assert store in by_name, (store, table)
            with closing(sqlite3.connect(str(by_name[store]))) as conn:
                columns = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
            assert {column, "thread_id"} <= columns, (store, table)
    finally:
        gen.close()


def test_control_an_undeclared_table_fails_the_gate(tmp_path) -> None:
    planted = tmp_path / "planted.db"
    with closing(sqlite3.connect(planted)) as conn:
        conn.execute("CREATE TABLE notes (thread_id TEXT, tenant_id TEXT, body TEXT)")
    assert _threaded_tables([("planted.db", planted)]) - _declared() == {("planted.db", "notes")}
