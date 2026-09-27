"""The Postgres checkpointer is opened in one place and its pool is closed.

``KazmaAgent`` and the gateway's ``CheckpointManager`` each built their own
``AsyncPostgresSaver``; the agent's copy closed its pool with ``aclose()``,
which psycopg's ``AsyncConnectionPool`` does not have, so each model switch
(the graph is rebuilt) left a pool and its connection open for good. Found
2026-09-27 by the Postgres-coverage gate, which named both builders as never
run against a real Postgres.
"""

from __future__ import annotations

import ast
import asyncio
import operator
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, TypedDict

import pytest
from kazma_core import checkpoints_pg

REPO = Path(__file__).resolve().parents[1]
PRODUCT = [REPO / d for d in (
    "kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway",
    "kazma-cli/kazma_cli", "kazma-skills/kazma_skills", "kazma-tui/kazma_tui",
)]


class _Pool:
    """psycopg's AsyncConnectionPool as far as closing goes: ``close()``, no
    ``aclose()``."""

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def test_the_closer_closes_the_pool_and_only_once():
    pool = _Pool()
    saver = SimpleNamespace(conn=pool)
    assert asyncio.run(checkpoints_pg.close_postgres_checkpointer(saver)) is True
    assert pool.closed
    assert asyncio.run(checkpoints_pg.close_postgres_checkpointer(saver)) is False
    assert asyncio.run(checkpoints_pg.close_postgres_checkpointer(SimpleNamespace())) is False


def test_the_agent_closes_its_postgres_pool():
    """Through ``KazmaAgent._close_checkpointer``, the path a model switch
    takes. Negative control: the method the old code looked for is not
    there, so it closed nothing."""
    from kazma_core.agent_runner import KazmaAgent

    pool = _Pool()
    assert getattr(pool, "aclose", None) is None
    agent = KazmaAgent.__new__(KazmaAgent)
    agent._checkpointer_shared_path = None
    agent._checkpoint_conn = None
    agent._checkpointer = SimpleNamespace(conn=pool)

    asyncio.run(agent._close_checkpointer())

    assert pool.closed and agent._checkpointer is None


def _constructions(source: str, names: set[str]) -> list[int]:
    tree = ast.parse(source)
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if name in names:
                lines.append(node.lineno)
    return lines


def test_every_postgres_checkpointer_comes_from_the_one_opener():
    home = REPO / "kazma-core/kazma_core/checkpoints_pg.py"
    found = []
    for root in PRODUCT:
        for path in root.rglob("*.py"):
            if path == home:
                continue
            for line in _constructions(path.read_text(encoding="utf-8", errors="replace"),
                                       {"AsyncPostgresSaver", "PostgresSaver", "AsyncConnectionPool"}):
                found.append(f"{path.relative_to(REPO).as_posix()}:{line}")
    assert found == [], "open it with kazma_core.checkpoints_pg.open_postgres_checkpointer: " + ", ".join(found)
    assert _constructions(home.read_text(encoding="utf-8"), {"AsyncPostgresSaver"}), "the home lost it"
    # Negative control: a second builder is seen.
    assert _constructions("pool = AsyncConnectionPool(conninfo=dsn)\n", {"AsyncConnectionPool"}) == [1]


class _State(TypedDict):
    messages: Annotated[list, operator.add]


def _graph(saver):
    from langgraph.graph import END, START, StateGraph

    g = StateGraph(_State)
    g.add_node("step", lambda s: {"messages": [len(s.get("messages") or [])]})
    g.add_edge(START, "step")
    g.add_edge("step", END)
    return g.compile(checkpointer=saver)


@pytest.mark.postgres
def test_a_chat_outlives_its_checkpointer_on_a_real_postgres():
    """Open, run a turn, close (the pool is closed), open again on a new pool:
    the chat is where it was."""
    dsn = os.environ.get("KAZMA_DATABASE_URL") or ""
    if not dsn or os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
        pytest.skip("needs a real Postgres (KAZMA_DATABASE_URL + KAZMA_TEST_ALLOW_REAL_DB=1)")
    pytest.importorskip("langgraph.checkpoint.postgres.aio")
    thread = f"cpg-{uuid.uuid4().hex[:10]}"
    cfg = {"configurable": {"thread_id": thread}}

    async def scenario() -> None:
        saver = await checkpoints_pg.open_postgres_checkpointer(dsn, max_size=2)
        try:
            await _graph(saver).ainvoke({"messages": []}, cfg)
            await _graph(saver).ainvoke({"messages": []}, cfg)
        finally:
            assert await checkpoints_pg.close_postgres_checkpointer(saver) is True
        assert saver.conn.closed
        again = await checkpoints_pg.open_postgres_checkpointer(dsn, max_size=2)
        try:
            state = await _graph(again).aget_state(cfg)
            assert state.values["messages"] == [0, 1]
            async with again.conn.connection() as conn:
                for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                    await conn.execute(f"DELETE FROM {table} WHERE thread_id = %s", (thread,))
        finally:
            await checkpoints_pg.close_postgres_checkpointer(again)

    factory = None
    if sys.platform == "win32":
        import selectors

        factory = lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())  # noqa: E731
    with asyncio.Runner(loop_factory=factory) as runner:
        runner.run(scenario())
