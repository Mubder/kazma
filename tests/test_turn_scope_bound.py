"""Every turn runs in its thread's space, as its asker (Spaces S1-07).

A chat thread is recorded once with its space and the account that opened it
(``kazma_core.spaces.threads``). Every place that runs a turn runs the graph
inside ``turn_segment_budget``, which binds the scope from that record: the
web, the chat apps, a scheduled fire, a resume after a restart, an approval's
timeout. On an install that enforces Spaces a turn asked for in another
space than its thread's is refused: a turn that paused in one space can only
be resumed there.

Until 2026-11 a resume with nothing bound ran as ``default`` -- the owner's
data -- whatever thread it was. That is the negative control.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest
from kazma_core.agent.turn_budget import turn_segment_budget
from kazma_core.spaces import scope, threads
from kazma_core.spaces.scope import (
    OWNER_ACCOUNT_ID,
    OWNER_SPACE_ID,
    NoSpaceBound,
    Scope,
    bind_scope,
)
from kazma_core.tenant_context import get_current_tenant_id, tenant_scope

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _own_record_store(tmp_path, monkeypatch):
    monkeypatch.setattr(threads, "thread_scopes_db", lambda: str(tmp_path / "thread_scopes.db"))
    monkeypatch.setattr(threads, "_schema_ready", set())


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(scope, "_enforced", True)


async def _run_turn(thread_id: str) -> tuple[Scope | None, str | None, str]:
    """One graph run on *thread_id*: what the code inside the run sees."""
    async with turn_segment_budget(None, {"configurable": {"thread_id": thread_id}}):
        seen = await asyncio.to_thread(
            lambda: (scope.current_scope(), get_current_tenant_id(), scope.require_space_id())
        )
    return seen


def _turn(thread_id: str) -> tuple[Scope | None, str | None, str]:
    return asyncio.run(_run_turn(thread_id))


# ── The record ───────────────────────────────────────────────────────────


def test_a_thread_is_recorded_once_and_the_first_record_stands() -> None:
    first = threads.record_thread("t1", "family", "layla")
    again = threads.record_thread("t1", "other", "omar")
    assert again == first
    assert threads.record_of("t1").space_id == "family"
    assert threads.record_of("t1").opener == "layla"
    assert threads.record_of("nope") is None


@pytest.mark.parametrize("space_id, opener", [("Bad Id", "layla"), ("family", ""), ("", "layla")])
def test_a_record_needs_a_real_space_and_account(space_id, opener) -> None:
    with pytest.raises(ValueError):
        threads.record_thread("t1", space_id, opener)
    assert threads.record_of("t1") is None


# ── One operator: as before, and the thread is recorded ──────────────────


def test_one_operator_an_unbound_turn_runs_as_before_and_is_recorded() -> None:
    """A chat-app turn binds no tenant today. It still does not: the vault
    tells "no tenant" from ``default``. The turn knows its space and asker."""
    bound, tenant, space_id = _turn("tg-1")
    assert bound == Scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID)
    assert tenant is None, "the tenant ContextVar is left as the entry point left it"
    assert space_id == "default"
    record = threads.record_of("tg-1")
    assert (record.space_id, record.opener) == (OWNER_SPACE_ID, OWNER_ACCOUNT_ID)
    assert scope.current_scope() is None, "nothing stays bound after the turn"


def test_one_operator_a_tenant_bound_turn_keeps_its_tenant() -> None:
    async def _run():
        with tenant_scope("acme"):
            return await _run_turn("cron-1")

    bound, tenant, space_id = asyncio.run(_run())
    assert (bound.space_id, tenant, space_id) == ("acme", "acme", "acme")
    assert threads.record_of("cron-1").space_id == "acme"


def test_a_web_turn_runs_as_its_asker_and_records_the_opener() -> None:
    async def _run():
        with bind_scope("family", "layla"):
            return await _run_turn("web-1")

    bound, tenant, space_id = asyncio.run(_run())
    assert bound == Scope("family", "layla")
    assert tenant == "family" and space_id == "family"
    record = threads.record_of("web-1")
    assert (record.space_id, record.opener) == ("family", "layla")


def test_another_member_of_the_space_runs_as_themselves() -> None:
    threads.record_thread("web-1", "family", "layla")

    async def _run():
        with bind_scope("family", "omar"):
            return await _run_turn("web-1")

    bound, _tenant, _space = asyncio.run(_run())
    assert bound == Scope("family", "omar"), "the asker is who asked, not who opened the chat"
    assert threads.record_of("web-1").opener == "layla"


# ── Enforced ─────────────────────────────────────────────────────────────


def test_enforced_a_resume_with_nothing_bound_runs_in_the_threads_space(enforced) -> None:
    """A watchdog or a resume at start binds nothing: the turn runs in its
    thread's space, as the account that opened it."""
    threads.record_thread("web-1", "family", "layla")
    bound, tenant, space_id = _turn("web-1")
    assert bound == Scope("family", "layla")
    assert tenant == "family" and space_id == "family"


def test_enforced_a_resume_in_another_space_is_refused(enforced) -> None:
    threads.record_thread("web-1", "family", "layla")

    async def _run():
        with bind_scope("omar-own", "omar"):
            return await _run_turn("web-1")

    with pytest.raises(threads.ThreadInAnotherSpace):
        asyncio.run(_run())
    assert threads.record_of("web-1").space_id == "family", "the record is unchanged"


def test_enforced_a_new_thread_with_nothing_bound_is_refused(enforced) -> None:
    with pytest.raises(NoSpaceBound):
        _turn("orphan-1")
    assert threads.record_of("orphan-1") is None


def test_not_enforced_a_turn_in_another_space_runs_as_before_and_is_said(caplog) -> None:
    threads.record_thread("web-1", "family", "layla")

    async def _run():
        with bind_scope("acme", "omar"):
            return await _run_turn("web-1")

    with caplog.at_level("WARNING", logger="kazma_core.spaces.threads"):
        bound, _tenant, _space = asyncio.run(_run())
    assert bound == Scope("acme", "omar")
    assert any("refuses this" in r.getMessage() for r in caplog.records)


def test_control_a_resume_with_no_record_ran_as_default(enforced) -> None:
    """The turn as it ran before the record: nothing bound means the owner's
    space, for any thread. With the record it runs in the thread's own."""
    threads.record_thread("web-1", "family", "layla")

    def _old_space_of_a_resume() -> str:
        return get_current_tenant_id() or "default"

    assert _old_space_of_a_resume() == OWNER_SPACE_ID
    assert _turn("web-1")[2] == "family"


# ── Every place that runs a graph binds the scope ────────────────────────

_PACKAGES = ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway",
             "kazma-cli/kazma_cli", "kazma-tui/kazma_tui", "kazma-skills/kazma_skills")

#: Graph runs that are not a turn of a chat thread, each with why its scope
#: comes from somewhere else. A site that no longer exists must leave.
NOT_A_THREADS_TURN: dict[tuple[str, str], str] = {
    ("kazma-core/kazma_core/agent/turn.py", "_ainvoke"): (
        "the one pass-through to graph.ainvoke; every caller sits inside "
        "turn_segment_budget (tests/test_turn_budget.py holds them to it)"
    ),
    ("kazma-ui/kazma_ui/turn_runtime.py", "astream_events"): (
        "the one pass-through to graph.astream_events; its consumer, the SSE "
        "pump, reads it inside turn_segment_budget (tests/test_turn_budget.py)"
    ),
}

_GRAPH_RUNS = ("ainvoke", "astream_events", "astream")


def _graph_runs_outside_the_budget(source: str, name: str) -> list[str]:
    """Calls of ``<graph>.ainvoke/astream_events/astream`` in *source* that
    are not inside an ``async with turn_segment_budget(...)`` block."""
    tree = ast.parse(source)
    found: list[str] = []

    def _walk(node: ast.AST, inside: bool, func: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func = node.name
        if isinstance(node, ast.AsyncWith) and any(
            isinstance(item.context_expr, ast.Call)
            and getattr(item.context_expr.func, "id", getattr(item.context_expr.func, "attr", ""))
            == "turn_segment_budget"
            for item in node.items
        ):
            inside = True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _GRAPH_RUNS
            and "graph" in ast.unparse(node.func.value).lower()
            and not inside
        ):
            found.append(f"{name}:{func}:{node.lineno}")
        for child in ast.iter_child_nodes(node):
            _walk(child, inside, func)

    _walk(tree, False, "<module>")
    return found


def test_every_graph_run_binds_the_threads_scope() -> None:
    """Class gate: every product call that runs a graph sits inside
    ``turn_segment_budget`` -- where the thread's scope is bound -- or is
    declared with why it is not a thread's turn."""
    offenders: list[str] = []
    seen_declared: set[tuple[str, str]] = set()
    for package in _PACKAGES:
        for path in sorted((REPO / package).rglob("*.py")):
            rel = path.relative_to(REPO).as_posix()
            for site in _graph_runs_outside_the_budget(path.read_text(encoding="utf-8"), rel):
                _file, func, _line = site.rsplit(":", 2)
                if (rel, func) in NOT_A_THREADS_TURN:
                    seen_declared.add((rel, func))
                    continue
                offenders.append(site)
    assert not offenders, (
        "graph runs outside turn_segment_budget (no thread scope is bound): "
        f"{offenders}"
    )
    stale = set(NOT_A_THREADS_TURN) - seen_declared
    assert not stale, f"declared sites that no longer run a graph: {sorted(stale)}"


def test_the_gate_sees_a_planted_unbound_graph_run() -> None:
    planted = (
        "async def resume(graph, config):\n"
        "    return await graph.ainvoke(None, config)\n"
    )
    assert _graph_runs_outside_the_budget(planted, "x.py") == ["x.py:resume:2"]
    bound = (
        "async def resume(graph, config):\n"
        "    async with turn_segment_budget(graph, config):\n"
        "        return await graph.ainvoke(None, config)\n"
    )
    assert _graph_runs_outside_the_budget(bound, "x.py") == []


def test_the_budget_block_is_where_the_scope_is_bound() -> None:
    """The gate above leans on this: ``turn_segment_budget`` itself asks the
    thread record and binds what it answers."""
    source = (REPO / "kazma-core/kazma_core/agent/turn_budget.py").read_text(encoding="utf-8")
    assert "scope_for_turn" in source and "_threads.bound(turn_scope)" in source
