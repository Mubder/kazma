"""The armed run is armed (S4-01 step 5 review M2).

CI's Postgres job runs every Postgres test twice: not armed, then with
``KAZMA_TEST_SECOND_LOCK=armed`` on a fresh database
(``tests/_second_lock_armed.py``). An armed run that is not armed -- the
plugin not loaded, the variable misspelt, a module that disarmed and never
re-armed -- passes every test the unarmed run passes and reports the same
count, so it proves nothing about the lock. Each run checks itself here: it
is armed exactly when it says so, and the review's three planted reads -- from
space A, a store read and a raw SELECT of space B's swarm task, and a
statement that says no space -- read across and run unarmed (Spaces is not
enforced, so nothing but the lock could hide the row), and armed read nothing
and are refused. The test's own read, declared, sees the row in both: the
escape works, which is why a declaration is read (``tests/_as_request.py``).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from tests._as_request import in_space, own, tests_own

try:
    import psycopg
except ImportError:  # CI's light job has no psycopg: collected and skipped, so the counts agree
    psycopg = None

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(psycopg is None, reason="psycopg is not installed")]

#: What this run says it is: the armed plugin's own reading of the variable.
ARMED_RUN = (os.environ.get("KAZMA_TEST_SECOND_LOCK") or "").strip().lower() == "armed"


@pytest.fixture
def b_task(tmp_path: Any) -> Iterator[SimpleNamespace]:
    """A swarm task of space B, written in B as the turn that started it."""
    dsn = os.environ.get("KAZMA_DATABASE_URL") or ""
    if not dsn or os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
        pytest.skip("needs a throwaway Postgres (scripts/postgres_suite.py)")
    if os.environ.get("KAZMA_DB_BACKEND") != "postgres":
        pytest.skip("the swarm's tasks are on Postgres only with KAZMA_DB_BACKEND=postgres")
    from kazma_core.swarm import SwarmTask, TaskStatus
    from kazma_core.swarm.task_store import TaskStore

    a, b = (f"s{uuid.uuid4().hex[:12]}" for _ in range(2))
    task = SwarmTask(prompt="B's task", id=f"task-{uuid.uuid4().hex[:12]}", status=TaskStatus.RUNNING,
                     metadata={"tenant_id": b})
    with in_space(b, "omar"):  # a tool's own store, made in a turn
        store = TaskStore(str(tmp_path / "none.db"))
        assert store.persist_task(task)
    try:
        yield SimpleNamespace(store=store, a=a, b=b, task=task)
    finally:
        from kazma_core.db.postgres_pool import get_postgres_pool

        with tests_own():
            get_postgres_pool().execute("DELETE FROM kazma_swarm_tasks WHERE id = %s", (task.id,))


def _ids(rows: list[dict[str, Any]]) -> list[str]:
    return [str(r["id"]) for r in rows]


def test_the_run_is_armed_exactly_when_it_says_so() -> None:
    from kazma_core.db import row_security

    assert row_security.armed() is ARMED_RUN, (
        f"KAZMA_TEST_SECOND_LOCK says {'armed' if ARMED_RUN else 'not armed'}, the lock is "
        f"{'armed' if row_security.armed() else 'not armed'}: this run does not prove what it reports"
    )


def test_a_store_read_of_another_spaces_task(b_task: SimpleNamespace) -> None:
    with in_space(b_task.a, "reem"):
        got = b_task.store.get_task(b_task.task.id)
    if ARMED_RUN:
        assert got is None, "armed: B's task is out of A's door's sight"
    else:
        assert got is not None, "the control, unarmed and Spaces not enforced: nothing hides it"


def test_a_raw_select_of_another_spaces_row(b_task: SimpleNamespace) -> None:
    from kazma_core.db.postgres_pool import get_postgres_pool

    with in_space(b_task.a, "reem"):
        rows = get_postgres_pool().execute("SELECT id FROM kazma_swarm_tasks WHERE id = %s", (b_task.task.id,))
    assert _ids(rows) == ([] if ARMED_RUN else [b_task.task.id])


def test_a_statement_that_says_no_space(b_task: SimpleNamespace) -> None:
    from kazma_core.db.postgres_pool import get_postgres_pool
    from kazma_core.spaces.scope import NoSpaceBound

    statement = ("SELECT id FROM kazma_swarm_tasks WHERE id = %s", (b_task.task.id,))
    if ARMED_RUN:
        with pytest.raises(NoSpaceBound):
            get_postgres_pool().execute(*statement)
    else:
        assert _ids(get_postgres_pool().execute(*statement)) == [b_task.task.id]


def test_the_tests_own_read_sees_the_row_either_way(b_task: SimpleNamespace) -> None:
    from kazma_core.db.postgres_pool import get_postgres_pool

    assert own(b_task.store).get_task(b_task.task.id) is not None
    with tests_own():
        rows = get_postgres_pool().execute("SELECT id FROM kazma_swarm_tasks WHERE id = %s", (b_task.task.id,))
    assert _ids(rows) == [b_task.task.id]
