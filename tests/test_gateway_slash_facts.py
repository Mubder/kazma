"""A chat app's /status, /cost, /context and /memory report what they measured.

Until 2026-10-01 the gateway filled the slash commands' context with
constants. /status said the gateway was running, with the asking platform as
its only adapter, a queue of 0 and one thread, whatever was true. /cost said
``$0.0000 (0 tokens)`` in every chat. /context measured the ``/context``
message itself against a fixed 128,000, so every chat sat near 0 %.
``_build_slash_ctx`` (``kazma_gateway/agent_handler/commands.py``) now reads
each fact from where it lives.

The gate at the bottom fails on a key a command reads that the builder never
fills, or fills with a constant; its negative control is the old builder's
shape.
"""

from __future__ import annotations

import ast
import asyncio
import operator
import sqlite3
from pathlib import Path
from typing import Annotated, Any, TypedDict

import pytest

from kazma_gateway.agent_handler.commands import _build_slash_ctx
from kazma_gateway.gateway import BaseAdapter, GatewayManager, IncomingMessage, OutboundMessage
from kazma_gateway.receive_log import ReceiveLog
from kazma_gateway.slash_commands import resolve_slash_command

ROOT = Path(__file__).resolve().parents[1]
SLASH = ROOT / "kazma-gateway" / "kazma_gateway" / "slash_commands.py"
BUILDER = ROOT / "kazma-gateway" / "kazma_gateway" / "agent_handler" / "commands.py"


def _msg(text: str) -> IncomingMessage:
    return IncomingMessage(
        platform="telegram", sender_id="telegram:42", text=text, context_metadata={"chat_id": "42"}
    )


async def _answer(text: str, thread_id: str = "gw-telegram-42", **sources: Any) -> str:
    """What the chat app would be sent: the builder, then the resolver in a
    thread, as the gateway's handler runs them."""
    ctx = await _build_slash_ctx(thread_id, _msg(text), **sources)
    reply = await asyncio.to_thread(resolve_slash_command, text, context=ctx)
    assert reply is not None
    return reply


# ── /cost: the per-call ledger ──────────────────────────────────────────


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    from kazma_core.observability import llm_ledger

    monkeypatch.setattr(llm_ledger, "_conn", None)
    conn = llm_ledger._get_conn(str(tmp_path / "llm_calls.db"))  # the ledger writes here now
    yield llm_ledger
    conn.close()


def test_thread_usage_sums_one_chats_calls(ledger):
    ledger.record_llm_call(thread_id="chat-a", prompt_tokens=1200, completion_tokens=300, cost_usd=0.02)
    ledger.record_llm_call(thread_id="chat-a", prompt_tokens=900, completion_tokens=100, cost_usd=0.01)
    ledger.record_llm_call(thread_id="chat-b", prompt_tokens=5000, completion_tokens=5000, cost_usd=1.0)

    assert ledger.thread_usage("chat-a") == {"calls": 2, "tokens": 2500, "cost": pytest.approx(0.03)}
    assert ledger.thread_usage("chat-c") == {"calls": 0, "tokens": 0, "cost": 0.0}


def test_an_unreadable_ledger_is_unknown_not_zero(ledger, monkeypatch):
    def _broken(*_a: Any, **_k: Any) -> sqlite3.Connection:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(ledger, "_get_conn", _broken)
    assert ledger.thread_usage("chat-a") is None


async def test_cost_reports_the_chats_ledger(ledger):
    ledger.record_llm_call(thread_id="gw-telegram-42", prompt_tokens=2000, completion_tokens=481, cost_usd=0.0234)
    ledger.record_llm_call(thread_id="gw-telegram-42", prompt_tokens=10, completion_tokens=0, cost_usd=0.0)
    ledger.record_llm_call(thread_id="someone-else", prompt_tokens=9999, completion_tokens=1, cost_usd=5.0)

    reply = await _answer("/cost")
    assert reply == "💰 Session cost: `$0.0234` (2,491 tokens, 2 model calls)"


async def test_cost_says_when_the_ledger_is_off(ledger, monkeypatch):
    from kazma_core.agent import nonstop

    off = nonstop.NonStopConfig()
    off.ledger_enabled = False
    monkeypatch.setattr(nonstop, "get_nonstop_config", lambda *a, **k: off)
    assert "Cost tracking is off" in await _answer("/cost")


async def test_cost_says_when_the_ledger_is_unreadable(ledger, monkeypatch):
    monkeypatch.setattr(ledger, "thread_usage", lambda _thread: None)
    assert "unknown (the cost ledger could not be read)" in await _answer("/cost")


async def test_cost_says_when_no_price_is_known(ledger):
    ledger.record_llm_call(thread_id="gw-telegram-42", prompt_tokens=800, completion_tokens=200, cost_usd=0.0)
    reply = await _answer("/cost")
    assert "`$0.0000` (1,000 tokens, 1 model call)" in reply
    assert "no price is recorded" in reply


# ── /status: the gateway manager ────────────────────────────────────────


class _Adapter(BaseAdapter):
    """A platform adapter whose connection says what the test tells it."""

    def __init__(self, name: str, *, connected: bool, problem: str = "") -> None:
        super().__init__()
        self.name = name
        self._receive = ReceiveLog()
        if connected:
            self._receive.connected_now(new_session=True)
        elif problem:
            self._receive.problem(problem)

    async def listen(self, queue: asyncio.Queue[IncomingMessage], shutdown_event: asyncio.Event) -> None:
        await shutdown_event.wait()

    async def send(self, outbound: OutboundMessage) -> bool:
        return True


def _manager() -> GatewayManager:
    manager = GatewayManager()
    manager.add_adapter(_Adapter("telegram", connected=True))
    manager.add_adapter(
        _Adapter("slack", connected=False, problem="Slack refused the Socket Mode connection (invalid_auth)")
    )
    return manager


async def test_status_reports_a_stopped_gateway_and_its_queue():
    """Nothing started: the old context said running, queue 0, one thread."""
    manager = _manager()
    for n in range(3):
        manager.queue.put_nowait(_msg(f"message {n}"))

    reply = await _answer("/status", manager=manager)

    assert "○ Gateway: **stopped**" in reply
    assert "• Telegram: `down` — not started" in reply
    assert "• Slack: `down` — Slack refused the Socket Mode connection (invalid_auth)" in reply
    assert "• Queue depth: `3`" in reply
    assert "• Messages in progress: `0`" in reply


async def test_status_counts_the_other_messages_in_progress():
    """Through the real consumer: two turns block, then /status is asked. Its
    own message is one of the handler tasks and is not counted."""
    manager = _manager()
    release = asyncio.Event()
    replies: list[str] = []

    async def handler(msg: IncomingMessage) -> None:
        if msg.text == "/status":
            ctx = await _build_slash_ctx("gw-telegram-42", msg, manager=manager)
            replies.append(await asyncio.to_thread(resolve_slash_command, msg.text, context=ctx))
            return
        await release.wait()

    manager.on_message(handler)
    await manager.start()
    try:
        await manager.queue.put(_msg("a long question"))
        await manager.queue.put(_msg("another question"))
        for _ in range(200):
            if manager.messages_in_progress() == 2:
                break
            await asyncio.sleep(0.01)
        assert manager.messages_in_progress() == 2

        await manager.queue.put(_msg("/status"))
        for _ in range(300):
            if replies:
                break
            await asyncio.sleep(0.01)
    finally:
        release.set()
        await manager.stop()

    (reply,) = replies
    assert "● Gateway: **running**" in reply
    assert "• Telegram: `connected`" in reply
    assert "• Messages in progress: `2`" in reply


async def test_status_without_the_gateway_shows_unknown():
    reply = await _answer("/status")
    assert "? Gateway: **unknown**" in reply
    assert "• Chat apps: `?`" in reply
    assert "• Queue depth: `?`" in reply


# ── /context: the chat's saved conversation ─────────────────────────────


class _State(TypedDict):
    messages: Annotated[list, operator.add]


def _graph():
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph

    from kazma_core.checkpoint_serde import kazma_checkpoint_serde

    def reply(state: _State) -> dict[str, Any]:
        return {"messages": [{"role": "assistant", "content": "An answer with some length. " * 40}]}

    builder = StateGraph(_State)
    builder.add_node("reply", reply)
    builder.add_edge(START, "reply")
    builder.add_edge("reply", END)
    return builder.compile(checkpointer=MemorySaver(serde=kazma_checkpoint_serde()))


async def test_context_measures_the_saved_conversation(monkeypatch):
    graph = _graph()
    config = {"configurable": {"thread_id": "gw-telegram-42", "checkpoint_ns": ""}}
    for turn in range(3):
        await graph.ainvoke({"messages": [{"role": "user", "content": f"Question {turn}? " * 30}]}, config)

    import kazma_core.token_counter as token_counter

    monkeypatch.setattr(token_counter, "resolve_context_window", lambda *_a, **_k: 64_000)
    reply = await _answer("/context", graph=graph, config=config)

    line = next(ln for ln in reply.splitlines() if ln.startswith("Tokens:"))
    tokens = int(line.split()[1].replace(",", ""))
    assert tokens > 1_000, line  # the old count measured "/context": 2 tokens
    assert "/ 64,000" in line  # the model's window, not a fixed 128,000

    detailed = await _answer("/context details", graph=graph, config=config)
    assert "Role breakdown" in detailed and "assistant=" in detailed


async def test_context_without_the_checkpoint_says_so():
    assert "could not be read" in await _answer("/context")


# ── /memory: whose memory ───────────────────────────────────────────────


def test_memory_off_without_a_tenant_changes_nothing():
    """A tenant that could not be resolved used to fall back to "default",
    writing the forget ledger under another tenant."""
    reply = resolve_slash_command("/memory off", context={"thread_id": "gw-telegram-42"})
    assert "nothing was read or changed" in reply


# ── The gate: every fact a command shows is filled, and not by a constant ──


def _ctx_keys_read(tree: ast.AST) -> set[str]:
    keys: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "ctx"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            keys.add(node.args[0].value)
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, ast.Load)
            and isinstance(node.value, ast.Name)
            and node.value.id == "ctx"
            and isinstance(node.slice, ast.Constant)
        ):
            keys.add(node.slice.value)
    return keys


def _ctx_keys_filled(builder: ast.AST) -> dict[str, list[ast.expr]]:
    filled: dict[str, list[ast.expr]] = {}
    for node in ast.walk(builder):
        if isinstance(node, ast.AnnAssign | ast.Assign) and isinstance(node.value, ast.Dict):
            targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
            if any(isinstance(t, ast.Name) and t.id == "ctx" for t in targets):
                for key, value in zip(node.value.keys, node.value.values, strict=True):
                    if isinstance(key, ast.Constant):
                        filled.setdefault(key.value, []).append(value)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "ctx"
                    and isinstance(target.slice, ast.Constant)
                ):
                    filled.setdefault(target.slice.value, []).append(node.value)
    return filled


def _builder_function(source: str) -> ast.AST:
    tree = ast.parse(source)
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == "_build_slash_ctx"
    )


def unmeasured_facts(commands_source: str, builder_source: str) -> list[str]:
    """Keys the commands read that the builder never fills, or fills with a
    literal: each is a number a user is shown that nothing measured."""
    read = _ctx_keys_read(ast.parse(commands_source))
    filled = _ctx_keys_filled(_builder_function(builder_source))
    problems = []
    for key in sorted(read):
        if key not in filled:
            problems.append(f"{key}: read by a command, filled by nothing")
        elif any(isinstance(value, ast.Constant) for value in filled[key]):
            problems.append(f"{key}: filled with a constant")
    return problems


def test_every_fact_a_command_shows_is_read_from_its_source():
    problems = unmeasured_facts(SLASH.read_text(encoding="utf-8"), BUILDER.read_text(encoding="utf-8"))
    assert not problems, (
        "A chat-app slash command reads a context key that _build_slash_ctx "
        "(agent_handler/commands.py) does not fill from a real source -- the "
        "user is shown a number nothing measured:\n  " + "\n  ".join(problems)
    )


def test_the_gate_catches_the_old_builder():
    """Negative control: the builder as it was before 2026-10-01."""
    old_builder = '''
async def _build_slash_ctx(thread_id, msg, state, store):
    ctx = {"thread_id": thread_id, "platform": msg.platform}
    ctx["token_count"] = sum(len(str(m)) // 4 for m in state["messages"])
    ctx["total_tokens"] = 0
    ctx["total_cost"] = 0.0
    ctx["started"] = True
    ctx["adapters"] = msg.platform
    ctx["queue_depth"] = 0
    ctx["active_threads"] = 1
    return ctx
'''
    old_commands = '''
def _cmd_context(ctx):
    return ctx.get("token_count", 0) / ctx.get("max_tokens", 128000)
def _cmd_cost(ctx):
    return ctx.get("total_tokens", 0), ctx["total_cost"]
def _cmd_status(ctx):
    return ctx.get("started"), ctx.get("adapters"), ctx.get("queue_depth"), ctx.get("active_threads")
'''
    assert unmeasured_facts(old_commands, old_builder) == [
        "active_threads: filled with a constant",
        "max_tokens: read by a command, filled by nothing",
        "queue_depth: filled with a constant",
        "started: filled with a constant",
        "total_cost: filled with a constant",
        "total_tokens: filled with a constant",
    ]
