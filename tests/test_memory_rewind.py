"""Rewinding a chat rewinds its memory (Memory audit M09, MEM-R10; TT-03).

``/undo``, ``/edit``, a time-travel restore and abort rewrote the chat's state
and left memory as it was: an undone answer stayed a memory with its facts,
and after ``/edit`` both answers were memories of the same turn. Each rewrite
now calls ``memory.forget.rewind_memory``: the turns it took back are
forgotten (journaled, ledger rows from the old questions, so a deferred write,
the deep pass and turn reconcile do not bring them back), and the deep pass
does not apply facts for a turn forgotten during its model call.

Here, through a real LangGraph graph: undo, edit, a restore and abort, each
followed by recall that does not return the rewound content and the earlier
turn kept; the old behaviour as the negative control; the deep pass; and a
gate on every product function that writes graph state.
"""

from __future__ import annotations

import ast
import asyncio
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, TypedDict

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from kazma_core.memory import forget

REPO = Path(__file__).resolve().parents[1]
THREAD = "gw-rewind-1"

Q1, A1 = "what is my favourite colour", "Teal, you said."
Q2, A2 = "which city is the quokkahaven office in", "The quokkahaven office is in Lisbon."


class _State(TypedDict, total=False):
    messages: list[dict[str, Any]]
    tenant_id: str
    thread_id: str


def _graph():
    g = StateGraph(_State)

    async def node(state: _State) -> _State:
        return {"messages": state.get("messages", [])}

    g.add_node("n", node)
    g.set_entry_point("n")
    g.add_edge("n", END)
    return g.compile(checkpointer=MemorySaver())


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory import dual_write
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    monkeypatch.setattr(forget, "chat_keys", lambda key: [key] if key else [])
    db = sqlite3.connect(str(tmp_path / "memory_state.db"), check_same_thread=False)
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    dual_write._reset_mirror()
    yield SimpleNamespace(db=db, ops=ops, dir=tmp_path)
    dual_write._reset_mirror()
    ops.close()
    db.close()


MESSAGES = [
    {"role": "user", "content": Q1}, {"role": "assistant", "content": A1},
    {"role": "user", "content": Q2}, {"role": "assistant", "content": A2},
]


def _remember_both_turns(mem):
    """What memory holds of the two turns: episodes, a fact, an agent note."""
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.dual_write import mirror_episode

    for n, (q, a) in enumerate([(Q1, A1), (Q2, A2)], start=1):
        assert mirror_episode(session_id=THREAD, turn_number=n, user_text=q, assistant_text=a,
                              created_at=time.time() - 3600)
    mirror_episode(session_id="memory_store", turn_number=0, user_text="quokkahaven office: Lisbon",
                   source="memory_store_tool", metadata={"chat": THREAD, "chat_turn": 2})
    mutate_belief(mem.db, "quokkahaven_office", "located_in", "Lisbon", ops_conn=mem.ops,
                  predicate_type="functional", source_session=THREAD, source_turn=2)
    mem.db.commit()


async def _seed(graph) -> dict:
    config = {"configurable": {"thread_id": THREAD, "checkpoint_ns": ""}}
    await graph.ainvoke({"messages": list(MESSAGES), "tenant_id": "default", "thread_id": THREAD}, config)
    return config


def _recall_holds_turn_two(mem) -> bool:
    from kazma_core.memory.recall import recall

    found = recall("quokkahaven office city", tenant_id="default", local_only=True, conn=mem.db)
    text = " ".join([h.content or "" for h in found.episodes] + [h.content or "" for h in found.beliefs])
    return "quokkahaven" in text.lower() or "lisbon" in text.lower()


def _turn_one_kept(mem) -> bool:
    row = mem.db.execute("SELECT tier FROM episodes WHERE session_id = ? AND turn_number = 1",
                         (THREAD,)).fetchone()
    return row is not None and row[0] != forget.FORGOTTEN_TIER


async def _undo(graph, config) -> None:
    from kazma_core.agent.rewind import drop_last_answer

    before = list((await graph.aget_state(config)).values["messages"])
    after = drop_last_answer(before)
    await graph.aupdate_state(config, {"messages": after})
    await forget.rewind_memory(THREAD, before, after)


def test_undo_takes_the_answer_back_from_memory(mem):
    _remember_both_turns(mem)
    assert _recall_holds_turn_two(mem)  # before
    graph = _graph()

    async def run():
        await _undo(graph, await _seed(graph))

    asyncio.run(run())
    assert not _recall_holds_turn_two(mem) and _turn_one_kept(mem)
    assert mem.db.execute("SELECT COUNT(*) FROM beliefs WHERE source_turn = 2 AND object != ''").fetchone()[0] == 0


def test_edit_forgets_the_replaced_answer_and_keeps_no_second_version(mem):
    from kazma_core.agent.rewind import replace_last_answer
    from kazma_core.memory.consolidator import user_turn_index

    _remember_both_turns(mem)
    graph = _graph()

    async def run():
        config = await _seed(graph)
        before = list((await graph.aget_state(config)).values["messages"])
        after, edited = replace_last_answer(before, "Actually it is in Porto.")
        await graph.aupdate_state(config, {"messages": after})
        await forget.rewind_memory(THREAD, before, after, from_turn=user_turn_index(after[: edited + 1]))

    asyncio.run(run())
    assert not _recall_holds_turn_two(mem) and _turn_one_kept(mem)
    live = mem.db.execute("SELECT COUNT(*) FROM episodes WHERE session_id = ? AND turn_number = 2 "
                          "AND tier != 'forgotten'", (THREAD,)).fetchone()[0]
    assert live == 0


def test_a_restore_takes_the_later_turns_back(mem):
    from kazma_core.time_travel import apply_snapshot_to_thread

    _remember_both_turns(mem)
    graph = _graph()

    async def run():
        await _seed(graph)
        snapshot = {"messages": MESSAGES[:2], "tenant_id": "default", "thread_id": THREAD}
        await apply_snapshot_to_thread(graph, snapshot, THREAD)

    asyncio.run(run())
    assert not _recall_holds_turn_two(mem) and _turn_one_kept(mem)


def test_a_fork_leaves_the_original_chats_memory(mem):
    from kazma_core.time_travel import apply_snapshot_to_thread

    _remember_both_turns(mem)
    graph = _graph()

    async def run():
        await _seed(graph)
        await apply_snapshot_to_thread(graph, {"messages": MESSAGES[:2], "tenant_id": "default"}, "fork-1")

    asyncio.run(run())
    assert _recall_holds_turn_two(mem)


def test_an_aborted_turn_is_never_written_later(mem):
    """Abort keeps the question, not its turn: a deferred write of the turn,
    or turn reconcile, is refused by the ledger."""
    from kazma_core.agent.steer import abort_marker
    from kazma_core.memory.dual_write import mirror_episode

    _remember_both_turns(mem)
    before = [*MESSAGES, {"role": "user", "content": "draft the quokkahaven memo"}]
    after = [*before, {"role": "system", "content": abort_marker()}]
    asyncio.run(forget.rewind_memory(THREAD, before, after))
    assert mirror_episode(session_id=THREAD, turn_number=3, user_text="draft the quokkahaven memo",
                          assistant_text="Here is the memo.") is None
    # Only the aborted turn: the answered ones stay.
    assert _turn_one_kept(mem) and _recall_holds_turn_two(mem)


def test_without_the_rewind_memory_keeps_the_undone_answer(mem):
    """Negative control: the rewrite alone, as before 2026-10-13."""
    from kazma_core.agent.rewind import drop_last_answer

    _remember_both_turns(mem)
    graph = _graph()

    async def run():
        config = await _seed(graph)
        before = list((await graph.aget_state(config)).values["messages"])
        await graph.aupdate_state(config, {"messages": drop_last_answer(before)})

    asyncio.run(run())
    assert _recall_holds_turn_two(mem)


def test_the_deep_pass_does_not_apply_a_turn_forgotten_during_its_model_call(mem):
    from kazma_core.memory.worker_bootstrap import _apply_if_still_remembered

    _remember_both_turns(mem)
    eid = mem.db.execute("SELECT id FROM episodes WHERE session_id = ? AND turn_number = 2",
                         (THREAD,)).fetchone()[0]
    forget.forget_episode(eid, tenant_id="default")
    raw = [{"subject": "quokkahaven_office", "predicate": "located_in", "predicate_type": "functional",
            "object": "Faro", "confidence": 0.9, "importance": 4}]
    out = _apply_if_still_remembered(raw, mem.db, mem.ops, stats={"applied": 0, "rejected": 0, "actions": []},
                                     episode_id=eid, session_id=THREAD, turn=2, tenant_id="default", now=1.0)
    assert out["applied"] == 0 and out.get("skipped_forgotten")
    assert mem.db.execute("SELECT COUNT(*) FROM beliefs WHERE object = 'Faro'").fetchone()[0] == 0


# ── The gate: every graph-state writer takes its turns back ───────────────

#: Functions that write graph state without rewinding a chat, and why.
NOT_A_REWIND = {
    "kazma-core/kazma_core/agent/supervisor_watchdog.py": "appends a reflection note to the running turn",
}


def _writers(sources: dict[str, str]) -> list[str]:
    """Innermost functions that call ``.aupdate_state(...)`` and neither call
    ``rewind_memory`` nor are declared."""
    out = []
    for rel, text in sources.items():
        tree = ast.parse(text)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            nested = {id(n) for n in ast.walk(fn) if n is not fn and isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
            own = [c for c in calls if not any(id(p) in nested for p in _parents(fn, c))]
            names = {c.func.attr for c in own}
            names |= {c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
            if "aupdate_state" in names and "rewind_memory" not in names and rel not in NOT_A_REWIND:
                out.append(f"{rel}:{fn.name}")
    return sorted(out)


def _parents(root: ast.AST, target: ast.AST) -> list[ast.AST]:
    """The nodes between *root* and *target* (to tell a nested function's call)."""
    path: list[ast.AST] = []

    def walk(node: ast.AST) -> bool:
        if node is target:
            return True
        for child in ast.iter_child_nodes(node):
            if walk(child):
                path.append(node)
                return True
        return False

    walk(root)
    return [p for p in path if p is not root]


def test_every_graph_state_writer_rewinds_memory():
    sources = {}
    for pkg in ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway"):
        for p in (REPO / pkg).rglob("*.py"):
            text = p.read_text(encoding="utf-8", errors="replace")
            if "aupdate_state(" in text:
                sources[p.relative_to(REPO).as_posix()] = text
    assert _writers(sources) == []


def test_the_old_undo_is_caught():
    """Negative control: the undo before 2026-10-13."""
    old = (
        "async def _handle_undo(thread_id, config):\n"
        "    snap = await graph.aget_state(config)\n"
        "    messages = list(snap.values['messages'])\n"
        "    messages.pop()\n"
        "    await graph.aupdate_state(config, {'messages': messages})\n"
    )
    assert _writers({"g.py": old}) == ["g.py:_handle_undo"]
