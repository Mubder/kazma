"""One process of ``tests/test_pg_sweeps_armed.py`` (S4-01 step 5).

    python -m tests._sweeps_armed_child seed  <dsn> <data>
    python -m tests._sweeps_armed_child sweep <dsn> <data> <armed|unarmed|blinded> <out.json>

``seed`` fills a fresh database and data folder as a start leaves them, with
rows of three spaces in the stores the sweeps keep on Postgres, then puts the
second lock's rules in place. ``sweep`` runs one pass of every maintenance
sweep the way the server's cadence does (``worker_bootstrap._run_sweeps``:
each in a thread, nothing bound), Spaces enforced, the lock armed or not,
and writes what came of it: each sweep's outcome, the warnings, and every
Kazma table's rows less their times. ``blinded`` is armed with the swarm
task retention's install declaration taken away, the control.

The network and the real embedder are closed here as in a test
(``tests._egress_guard``), alerts are off, and no Docker is on the path.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import pytest

#: The owner's space and two others, ids as Kazma makes them.
SPACES = ("default", "s0a0a0a0a0a0a", "s0b0b0b0b0b0b")
#: A time in a row or a message, and a measured duration: never the same twice.
_TIME = re.compile(r"\d{4}-\d\d-\d\dT[\d:.+]+|\d{9,}(?:\.\d+)?|\d+\.\d+s|0x[0-9a-f]+")
#: This process's own patches: it ends when its one job does.
_PATCH = pytest.MonkeyPatch()
_SECONDS = re.compile(r'(\\"seconds\\": )[\d.]+')


def _run(coroutine: Any) -> Any:
    import selectors

    factory = (lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())) if sys.platform == "win32" else None
    with asyncio.Runner(loop_factory=factory) as runner:
        return runner.run(coroutine)


def seed(dsn: str, data: str) -> None:
    """Every store's schema, rows of three spaces, then the rules."""
    psycopg = pytest.importorskip("psycopg")  # there: the test that runs this skips without it
    from kazma_core.db import row_security
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema
    from kazma_core.paths import memory_ops_db, primary_memory_db
    from kazma_core.spaces import identity, scope, threads
    from kazma_core.spaces.row_work import statements_of
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, bind_scope
    from kazma_core.swarm.task import SwarmTask, TaskResult, TaskStatus
    from kazma_core.swarm.task_store import TaskStore
    from kazma_ui.session_manager import ChatSession, SessionManager

    from tests.test_pg_app_role import _every_store_set_up

    _PATCH.setattr(scope, "_enforced", True)
    _every_store_set_up(dsn)
    threads.ensure_store()
    for space in SPACES[1:]:
        identity.create_space(f"Space {space[-4:]}", created_by=OWNER_ACCOUNT_ID, space_id=space)

    # Memory: the local stores, three memories of each space (the mirror sync pushes them).
    mem = sqlite3.connect(str(primary_memory_db()))
    mem.row_factory = sqlite3.Row
    ensure_primary_schema(mem)
    for space in SPACES:
        for i in range(3):
            mem.execute("INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
                        "tier, created_at, metadata_json) VALUES (?, ?, ?, ?, ?, 'Noted.', 'episodic', ?, '{}')",
                        (f"e_{space[-4:]}_{i}", space, f"chat-{space[-4:]}-0", i + 1,
                         f"remember the {space} thing {i}", time.time() - 86400 * (i + 1)))
    mem.commit()
    mem.close()
    ops = sqlite3.connect(str(memory_ops_db()))
    ensure_ops_schema(ops)
    ops.close()

    # Chats: two of each space, each on a thread recorded there.
    chats = SessionManager(db_path=str(Path(data) / "chat_sessions.db"))
    for space in SPACES:
        for i in range(2):
            thread = f"th-{space[-4:]}-{i}"
            threads.record_thread(thread, space, OWNER_ACCOUNT_ID)
            with bind_scope(space, OWNER_ACCOUNT_ID):
                chats.put(ChatSession(session_id=f"chat-{space[-4:]}-{i}", tenant_id=space, thread_id=thread,
                                      title=f"t{i}", messages=[{"role": "user", "content": f"hello {i}"},
                                                               {"role": "assistant", "content": f"noted {i}"}]))
    chats.close()

    # Swarm: in each space a task past its keep (sorted as a row written long ago is), a new one, a paused one.
    tasks = TaskStore(str(Path(data) / "swarm_tasks.db"))
    for space in SPACES:
        for days, status in ((400, TaskStatus.COMPLETED), (0, TaskStatus.COMPLETED), (0, TaskStatus.PAUSED)):
            task = SwarmTask(prompt=f"p {space}", workers=["w"], status=status,
                             metadata={"tenant_id": space, "asker": OWNER_ACCOUNT_ID})
            when = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - days * 86400))
            task.created_at = when
            task.completed_at = when if status == TaskStatus.COMPLETED else None
            task.result = TaskResult(task_id=task.id, status="success" if status == TaskStatus.COMPLETED else "paused")
            with statements_of(space):
                assert tasks.persist_task(task)
            if days:
                with psycopg.connect(dsn, autocommit=True) as raw:
                    raw.execute("UPDATE kazma_swarm_tasks SET sort_at = %s WHERE id = %s", (when, task.id))
    tasks.close()

    # Checkpoints: a chat of each space, sixty days idle, more steps than an idle chat keeps.
    async def checkpoints() -> None:
        from typing import TypedDict

        from kazma_core.checkpoints_pg import close_postgres_checkpointer, open_postgres_checkpointer
        from langgraph.graph import END, START, StateGraph

        class State(TypedDict):
            n: int

        saver = await open_postgres_checkpointer(dsn, max_size=2)
        real_ns = time.time_ns
        try:
            with pytest.MonkeyPatch.context() as clock:
                clock.setattr(time, "time_ns", lambda: real_ns() - 60 * 86400 * 10**9)  # their ids say sixty days ago
                graph = StateGraph(State)
                graph.add_node("step", lambda s: {"n": s["n"] + 1})
                graph.add_edge(START, "step")
                graph.add_edge("step", END)
                compiled = graph.compile(checkpointer=saver)
                for space in SPACES:
                    with bind_scope(space, OWNER_ACCOUNT_ID):
                        for _ in range(6):
                            await compiled.ainvoke({"n": 0}, {"configurable": {"thread_id": f"th-{space[-4:]}-0"}})
        finally:
            await close_postgres_checkpointer(saver)

    _run(checkpoints())
    done = row_security.apply(dsn, vector_table="")
    assert done.complete, (done.refused, done.left, done.unresolved)


def sweep(dsn: str, data: str, how: str, out: str) -> None:
    """One pass of every maintenance sweep, as the cadence runs it."""
    import tests._egress_guard as guard

    guard.install()
    from kazma_core.db import row_security
    from kazma_core.memory import state_backend
    from kazma_core.memory import worker_bootstrap as wb
    from kazma_core.spaces import scope

    _PATCH.setattr(scope, "_enforced", True)
    if how in ("armed", "blinded"):
        done = row_security.apply(dsn, tables=list(row_security.rules("")), vector_table="")
        assert done.complete, (done.refused, done.left, done.unresolved)
        row_security._arm(True)
    if how == "blinded":  # the control: the swarm task retention with no install declaration
        from kazma_core.swarm.task_store import TaskStore

        _PATCH.setattr(TaskStore, "prune_tasks", TaskStore.prune_tasks.__wrapped__)
    # The mirror is this copy's own database.
    _PATCH.setattr(state_backend, "_cfg", lambda: {"state": {"provider": "postgres", "url": dsn, "role": "mirror"}})
    seen: list[list[str]] = []

    class _Warnings(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.levelno >= logging.WARNING and record.name.startswith("kazma"):
                said = _TIME.sub("#", record.getMessage().replace(data, "<data>"))
                seen.append([record.levelname, record.name, said[:240]])

    logging.getLogger().addHandler(_Warnings())
    logging.getLogger().setLevel(logging.INFO)
    asyncio.run(wb._run_sweeps(wb._MAINTENANCE_SWEEPS))
    outcomes = {label: {"failures": s.get("consecutive_failures") or 0,
                        "waiting": bool(s.get("waiting_for_database")),
                        "error": (s.get("last_error") or "").split(":")[0]}
                for label, s in wb._SWEEP_STATE.items()}
    Path(out).write_text(json.dumps({"outcomes": outcomes, "warnings": sorted(seen), "tables": tables(dsn)},
                                    indent=1, sort_keys=True), encoding="utf-8")


def tables(dsn: str) -> dict[str, dict[str, Any]]:
    """Each Kazma table's rows, less the columns and values that hold a time."""
    psycopg = pytest.importorskip("psycopg")  # there: the test that runs this skips without it
    from kazma_core.db.pg_backup import KAZMA_PG_TABLES

    out: dict[str, dict[str, Any]] = {}
    with psycopg.connect(dsn, autocommit=True) as conn:
        for table in sorted(KAZMA_PG_TABLES):
            if conn.execute("SELECT to_regclass(%s)", (f"public.{table}",)).fetchone()[0] is None:
                continue
            cols = [r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = %s AND data_type NOT LIKE 'timestamp%%' AND column_name NOT LIKE '%%\\_at' "
                "AND column_name NOT IN ('valid_from', 'timestamp', 'ts', 'heartbeat_epoch', 'expires', 'updated') "
                "ORDER BY ordinal_position", (table,)).fetchall()]
            listed = ", ".join('"' + c.replace('"', '""') + '"' for c in cols)
            rows = conn.execute(f'SELECT {listed} FROM public."{table}"').fetchall()  # noqa: S608  # nosec B608 - Kazma's own table names
            texts = sorted(_SECONDS.sub(r"\1#", _TIME.sub("#", json.dumps(dict(zip(cols, r)), default=str,
                                                                         sort_keys=True)))
                           for r in rows)
            out[table] = {"rows": len(rows), "hash": hashlib.sha256("\n".join(texts).encode()).hexdigest()[:16]}
    return out


if __name__ == "__main__":
    if sys.argv[1] == "seed":
        seed(sys.argv[2], sys.argv[3])
    else:
        sweep(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
