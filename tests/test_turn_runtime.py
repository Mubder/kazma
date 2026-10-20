"""TurnRunner/TurnCloser: a headless graph finish always writes the session.

Reproduces 2026-08-31 turn b1cb7994e22a: HITL stub in SessionStore, full
report only in the checkpoint, because auto-deny called graph.ainvoke
with no persist.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from kazma_ui import reply_sink
from kazma_ui.turn_runtime import close_turn, invoke_turn, persist_reply

STUB = (
    "There's a live API endpoint GET /api/settings/agent/safety "
    "that reads the actual runtime ConfigStore."
)
FINAL = (
    "Everything checks out. The verification is complete — "
    "here's the full picture."
)

_UI = Path(__file__).resolve().parents[1] / "kazma-ui" / "kazma_ui"
_GW = Path(__file__).resolve().parents[1] / "kazma-gateway" / "kazma_gateway"
_CORE = Path(__file__).resolve().parents[1] / "kazma-core" / "kazma_core"
_ALLOWED_INVOKE = {
    "kazma-ui/kazma_ui/turn_runtime.py",
}
_ALLOWED_CORE_INVOKE = {
    "kazma-core/kazma_core/agent/turn.py",
}


class _Session:
    def __init__(self) -> None:
        self.session_id = "sess-1"
        self.thread_id = "thread-1"
        self.messages = [{"role": "user", "content": "recheck it again"}]


class _Transact:
    def __init__(self, store: _Store) -> None:
        self._store = store

    def __enter__(self) -> _Session:
        return self._store.session

    def __exit__(self, *exc: object) -> bool:
        return False


class _Store:
    def __init__(self) -> None:
        self.session = _Session()

    def transact(self, session_id: str) -> _Transact:
        return _Transact(self)

    def get(self, session_id: str) -> _Session:
        return self.session


class _Snap:
    def __init__(self, text: str, paused: bool) -> None:
        self.values = {
            "messages": [
                {"role": "user", "content": "recheck it again"},
                {"role": "assistant", "content": text},
            ]
        }
        if paused:
            interrupt = SimpleNamespace(
                value={"type": "hitl_approval", "tool": "browser_navigate"}
            )
            self.tasks = [SimpleNamespace(interrupts=[interrupt])]
            self.next = ("ToolWorker",)
        else:
            self.tasks = []
            self.next = ()


class _Graph:
    def __init__(self) -> None:
        self.snap = _Snap(STUB, paused=True)

    async def ainvoke(self, *a: object, **k: object) -> dict:
        self.snap = _Snap(FINAL, paused=False)
        return {"messages": [{"role": "assistant", "content": FINAL}]}

    async def aget_state(self, config: object) -> _Snap:
        return self.snap


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch):
    st = _Store()
    monkeypatch.setattr(reply_sink, "_store", lambda: st)
    reply_sink.reset_reply_turns()
    from kazma_ui.active_turns import reset_active_turns

    reset_active_turns()
    yield st
    reply_sink.reset_reply_turns()
    reset_active_turns()


def _assistant_rows(store: _Store) -> list[dict]:
    return [m for m in store.session.messages if m.get("role") == "assistant"]


def test_headless_auto_deny_persists_final_into_the_open_stub(store: _Store) -> None:
    """Last-night sequence: open HITL stub, headless ainvoke, reload sees FINAL."""
    turn = reply_sink.open_reply_turn("thread-1")
    assert persist_reply("sess-1", turn, STUB, interrupted=True, thread_id="thread-1")
    rows = _assistant_rows(store)
    assert len(rows) == 1
    assert rows[0]["content"] == STUB
    assert rows[0].get("open") is True

    graph = _Graph()
    config = {"configurable": {"thread_id": "thread-1", "checkpoint_ns": ""}}

    async def _run() -> None:
        await invoke_turn(
            graph,
            object(),
            config,
            session_id="sess-1",
            thread_id="thread-1",
            turn_id=turn,
        )

    asyncio.run(_run())

    rows = _assistant_rows(store)
    assert len(rows) == 1, f"must not grow a second bubble: {rows}"
    assert FINAL in rows[0]["content"]
    assert "open" not in rows[0]
    kinds = [p.get("type") for p in (rows[0].get("parts") or [])]
    assert "text" in kinds
    assert "reasoning" in kinds, "HITL stub must survive as reasoning, not vanish"


def test_close_turn_never_raises_without_a_session() -> None:
    async def _run() -> bool:
        return await close_turn(
            None, {"configurable": {"thread_id": "missing"}}, thread_id="missing"
        )

    assert asyncio.run(_run()) is False


def test_close_turn_writes_empty_notice_when_graph_produced_nothing(
    store: _Store,
) -> None:
    class _Empty:
        async def aget_state(self, config: object) -> _Snap:
            snap = _Snap("", paused=False)
            snap.values["messages"] = [{"role": "user", "content": "hi"}]
            return snap

    turn = reply_sink.open_reply_turn("thread-1")
    config = {"configurable": {"thread_id": "thread-1", "checkpoint_ns": ""}}

    async def _run() -> None:
        await close_turn(
            _Empty(),
            config,
            session_id="sess-1",
            thread_id="thread-1",
            turn_id=turn,
        )

    asyncio.run(_run())
    rows = _assistant_rows(store)
    assert len(rows) == 1
    assert "without producing a reply" in rows[0]["content"]


def _graph_invoke_calls(tree: ast.AST) -> list[int]:
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr in ("ainvoke", "astream_events"):
            hits.append(int(node.lineno))
    return hits


def test_ainvoke_gate_fails_on_synthetic_violation() -> None:
    """Negative control: a planted graph.ainvoke must be detected."""
    tree = ast.parse(
        "async def _auto_deny(graph, cmd, config):\n"
        "    await graph.ainvoke(cmd, config)\n"
    )
    assert _graph_invoke_calls(tree), "the gate must fail on a synthetic ainvoke"


def test_ui_and_gateway_ainvoke_only_in_turn_runtime() -> None:
    """UI/gateway production code may not call graph.ainvoke itself."""
    offenders: list[str] = []
    repo = Path(__file__).resolve().parents[1]
    for base in (_UI, _GW):
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(repo).as_posix()
            if rel in _ALLOWED_INVOKE:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for lineno in _graph_invoke_calls(tree):
                offenders.append(f"{rel}:{lineno}")
    assert not offenders, (
        "graph.ainvoke / astream_events in UI/gateway must go through "
        "kazma_ui.turn_runtime (HITL auto-deny silently dropped finals when "
        "it called ainvoke with no persist). Offenders:\n  "
        + "\n  ".join(offenders)
    )


def test_core_ainvoke_only_in_turn_py() -> None:
    """kazma_core production code may only call graph.ainvoke in turn.py."""
    offenders: list[str] = []
    repo = Path(__file__).resolve().parents[1]
    for path in _CORE.rglob("*.py"):
        if "__pycache__" in path.parts or "kazma_core_tests" in path.parts:
            continue
        rel = path.relative_to(repo).as_posix()
        if rel in _ALLOWED_CORE_INVOKE:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for lineno in _graph_invoke_calls(tree):
            offenders.append(f"{rel}:{lineno}")
    assert not offenders, (
        "graph.ainvoke in kazma_core must live in agent/turn.py so every "
        "mouth runs the closer. Offenders:\n  " + "\n  ".join(offenders)
    )


PREVIOUS = "Task completed. Kuwait Towers: three towers, 187 m, opened 1 March 1979."


class _CancelledAfterPrevious:
    """The checkpoint of a turn cancelled before it said anything: the
    previous turn's answer is the last assistant message."""

    async def aget_state(self, config: object) -> SimpleNamespace:
        return SimpleNamespace(
            values={"messages": [
                {"role": "user", "content": "Check swarm task task-88ae"},
                {"role": "assistant", "content": PREVIOUS},
                {"role": "user", "content": "Write a 2,000-word history of Kuwait"},
            ]},
            tasks=[], next=(),
        )


def _abort_closes(store: _Store) -> list[dict]:
    store.session.messages = [{"role": "user", "content": "Write a 2,000-word history of Kuwait"}]
    turn = reply_sink.open_reply_turn("thread-1")
    config = {"configurable": {"thread_id": "thread-1", "checkpoint_ns": ""}}
    asyncio.run(close_turn(_CancelledAfterPrevious(), config, session_id="sess-1",
                           thread_id="thread-1", turn_id=turn, interrupted=True))
    return _assistant_rows(store)


def test_an_aborted_turn_is_never_saved_with_the_previous_answer(store: _Store) -> None:
    """Live 2026-10-20: /abort 32 s into "Write a 2,000-word history of
    Kuwait" saved the swarm summary before it (1,928 characters) as that
    turn's reply."""
    rows = _abort_closes(store)
    assert all(PREVIOUS not in str(r.get("content")) for r in rows), rows


def test_negative_control_the_last_assistant_text_of_the_whole_chat(
    store: _Store, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kazma_ui.sse_chat import _helpers

    monkeypatch.setattr(_helpers, "_this_turn_assistant_text", _helpers._last_assistant_text)
    rows = _abort_closes(store)
    assert any(PREVIOUS in str(r.get("content")) for r in rows), rows


def _aborted_row(store: _Store, monkeypatch: pytest.MonkeyPatch, *, partial: str = "", close: bool = True):
    import kazma_ui.turn_runtime as rt

    monkeypatch.setattr(rt, "resolve_session_id", lambda thread_id, session_id="": "sess-1")
    turn = reply_sink.open_reply_turn("thread-1")
    # What the cancelled run writes on its way out: its row, left open.
    persist_reply("sess-1", turn, partial, interrupted=True, thread_id="thread-1")
    if close:
        asyncio.run(rt.close_aborted_turn("thread-1"))
    rows = [m for m in store.session.messages if m.get("turn_id") == turn]
    return rows[-1] if rows else {}


def test_an_aborted_turn_is_closed_with_its_cancellation(store: _Store, monkeypatch: pytest.MonkeyPatch) -> None:
    """Live 2026-10-20: /abort left the cancelled turn's row open, and the
    liveness watchdog paged "turn.stuck_open" ten minutes later."""
    from kazma_core.agent.steer import ABORTED_ANSWER

    row = _aborted_row(store, monkeypatch)
    assert row.get("lifecycle") == "closed", row
    assert row.get("content") == ABORTED_ANSWER
    assert "thread-1" not in reply_sink.open_turns_snapshot()


def test_an_aborted_partial_answer_is_kept(store: _Store, monkeypatch: pytest.MonkeyPatch) -> None:
    partial = "A History of Bahrain\n\nBahrain is an archipelago in the western Gulf, " * 3
    row = _aborted_row(store, monkeypatch, partial=partial)
    assert row.get("lifecycle") == "closed" and row.get("content") == partial.strip()


def test_negative_control_without_the_close_the_turn_stays_open(store: _Store, monkeypatch: pytest.MonkeyPatch) -> None:
    _aborted_row(store, monkeypatch, close=False)
    assert "thread-1" in reply_sink.open_turns_snapshot(), "the watchdog's stuck-open report"
