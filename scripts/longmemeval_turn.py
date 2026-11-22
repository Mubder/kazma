"""Kazma's own chat turn as a LongMemEval reader (the memory plan's W1c).

The other readers of ``scripts/longmemeval_bench.py`` are sent one prompt: a
history, a question, "Answer:". This one asks the question the way a person
asks Kazma: through the supervisor graph a chat turn runs
(``build_supervisor_graph``, ``run_agent_turn``), with Kazma's base prompt,
its recall block and rules (recall on the question's own history, seeded the
way the harness seeds it), the memory tools a chat turn reads with
(``memory_search``, ``memory_read`` to open a memory the block cut,
``memory_list``), the reasoning level the run names (the setting a chat turn
reads, ``agent.chat_reasoning``) and at most three tool rounds. Every model
call is counted: calls, tokens, the cache's share, reasoning, tool rounds,
time.

What is not the product's, and why:

* the clock is the question's (``question_date``, read in UTC as the
  harness reads every date): recall's time words, ``memory_list``'s and the
  base prompt's "TODAY" note (``cultural_context_enrichment.note_for_day``);
* the tools are the memory read tools only (a web search or a file would
  answer from somewhere else), and the rounds stop at three (the plan's
  rule; the product allows more);
* recall reads this machine's store only (``local_only``, as the harness's
  block does), and nothing is written after the turn (``persist=False``: no
  post-turn memory, no lesson);
* the model is the run's reader, built as the product builds a provider's
  client (``provider_adapters.build_client``), never from a stored key.

Everything here is process-wide (the memory database is the one
``paths.primary_memory_db`` names, the patches are module attributes), so one
turn runs at a time (``_ONE_TURN``). No dataset text is written anywhere but
the run's private data folder, and it is removed after each turn.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sqlite3
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

#: The memory tools a chat turn reads with. The write ones (store, forget,
#: invalidate) and the admin ones are not offered: a benchmark turn changes
#: nothing.
TOOLS = ("memory_search", "memory_read", "memory_list")
#: At most this many tool rounds (the plan's W1c): the supervisor's own
#: iteration budget, one more than the rounds, refuses a fourth round's calls
#: and answers from what it has.
TOOL_ROUNDS = 3
#: Bumped when what a turn is asked changes, so a cached answer is asked again.
TURN_VERSION = 1

_ONE_TURN = asyncio.Lock()


@dataclass
class TurnOutcome:
    """What one turn answered and what it cost."""

    text: str = ""
    error: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    #: The recall block the turn's first model call held ("" when none).
    block: str = ""


def _failure(exc: BaseException) -> str:
    """A refused call, said without the provider's own text: its kind and
    whether the supervisor could retry it (``LLMError``)."""
    kind = str(getattr(exc, "kind", "") or "")
    retry = "transient" if getattr(exc, "transient", False) else "permanent"
    return ":".join(part for part in (type(exc).__name__, kind, retry) if part)


class CountingClient:
    """The reader, counted: every model call's tokens, the cache's share,
    reasoning, the responses that asked for tools and refused calls. The
    supervisor streams when the client streams (``llm_stream.invoke_llm_chat``),
    so both ways in are counted, each call once."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.reset()

    def reset(self) -> None:
        self.usage: Counter[str] = Counter()
        self.errors: list[str] = []
        self.first_messages: list[dict[str, Any]] | None = None

    def __getattr__(self, name: str) -> Any:
        if name == "inner":
            raise AttributeError(name)
        return getattr(self.inner, name)

    def _seen(self, kwargs: dict[str, Any], args: tuple[Any, ...]) -> None:
        if self.first_messages is None:
            messages = kwargs.get("messages", args[0] if args else None)
            self.first_messages = [dict(m) for m in messages or []]

    def _note(self, response: Any) -> None:
        usage = getattr(response, "usage", None) or {}
        self.usage["calls"] += 1
        for key in ("prompt_tokens", "completion_tokens", "prompt_cache_hit_tokens"):
            if isinstance(usage.get(key), int):
                self.usage[key] += int(usage[key])
        details = usage.get("completion_tokens_details") or {}
        if isinstance(details, dict) and isinstance(details.get("reasoning_tokens"), int):
            self.usage["reasoning_tokens"] += int(details["reasoning_tokens"])
        if getattr(response, "tool_calls", None):
            self.usage["tool_requests"] += 1
        if getattr(response, "finish_reason", "") == "length":
            self.usage["cut_at_limit"] += 1

    async def chat(self, *args: Any, **kwargs: Any) -> Any:
        self._seen(kwargs, args)
        try:
            response = await self.inner.chat(*args, **kwargs)
        except Exception as exc:
            self.errors.append(_failure(exc))
            raise
        self._note(response)
        return response

    async def chat_stream(self, *args: Any, **kwargs: Any) -> Any:
        from kazma_core.llm_stream import StreamDelta, _has_chat_stream

        if not _has_chat_stream(self.inner):
            yield StreamDelta(response=await self.chat(*args, **kwargs))
            return
        self._seen(kwargs, args)
        try:
            async for delta in self.inner.chat_stream(*args, **kwargs):
                if getattr(delta, "response", None) is not None:
                    self._note(delta.response)
                yield delta
        except Exception as exc:
            self.errors.append(_failure(exc))
            raise


class MemoryTools:
    """Kazma's memory tools (``register_memory_tools``), offering and running
    only ``TOOLS``; each call counted by name."""

    def __init__(self) -> None:
        from kazma_core.agent.tool_builtins.memory import register_memory_tools
        from kazma_core.agent.tool_registry import LocalToolRegistry

        self.registry = LocalToolRegistry(include_builtins=False)
        register_memory_tools(self.registry)
        self.used: Counter[str] = Counter()

    def definitions(self) -> list[dict[str, Any]]:
        return [d for d in self.registry.get_tool_definitions() if d["function"]["name"] in TOOLS]

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name not in TOOLS:
            return {"content": f"Error: {tool_name} is not offered in this turn.", "is_error": True,
                    "refused": True}
        self.used[tool_name] += 1
        return await self.registry.execute(tool_name, arguments)


def reader_client(*, model: str, base_url: str, key: str) -> Any:
    """The reader as Kazma builds a provider's client (``build_client``, the
    DeepSeek preset): the key is passed in, never stored."""
    from kazma_core.llm_provider import LLMConfig
    from kazma_core.provider_adapters import build_client

    config = LLMConfig.from_dict({"base_url": base_url, "api_key": key, "model": model})
    return build_client("deepseek", config, {"preset": "deepseek"})


@contextlib.contextmanager
def _private_memory(seed: Callable[[sqlite3.Connection], Any]) -> Iterator[str]:
    """A memory database of the turn's own, seeded, as ``primary_memory_db``;
    removed afterwards."""
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    fd, path = tempfile.mkstemp(prefix="kazma-lme-turn-", suffix=".db")
    os.close(fd)
    held = os.environ.get("KAZMA_MEMORY_STATE_DB")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_primary_schema(conn)
        seed(conn)
        conn.commit()
    finally:
        conn.close()
    os.environ["KAZMA_MEMORY_STATE_DB"] = path
    try:
        yield path
    finally:
        if held is None:
            os.environ.pop("KAZMA_MEMORY_STATE_DB", None)
        else:
            os.environ["KAZMA_MEMORY_STATE_DB"] = held
        for suffix in ("", "-wal", "-shm"):
            with contextlib.suppress(OSError):
                os.remove(path + suffix)


@contextlib.contextmanager
def _at_the_questions_time(now: float, client: Any) -> Iterator[None]:
    """The question's clock for recall, ``memory_list`` and the base prompt's
    date note, the reader for every model call of the turn."""
    from unittest import mock
    from zoneinfo import ZoneInfo

    from kazma_core.cultural_context_enrichment import note_for_day
    from kazma_core.memory import memory_list as memory_list_module
    from kazma_core.memory import recall as recall_module

    product_recall = recall_module.recall
    product_list = memory_list_module.list_memories
    day = datetime.fromtimestamp(now, UTC).date()

    def recall_then(query: str, **kwargs: Any) -> Any:
        kwargs.setdefault("now", now)
        kwargs["local_only"] = True
        return product_recall(query, **kwargs)

    def list_then(**kwargs: Any) -> Any:
        kwargs.setdefault("now", now)
        return product_list(**kwargs)

    with mock.patch.object(recall_module, "recall", recall_then), \
            mock.patch.object(recall_module, "_memory_zone", lambda: ZoneInfo("UTC")), \
            mock.patch.object(memory_list_module, "list_memories", list_then), \
            mock.patch("kazma_core.cultural_context_enrichment.daily_prompt_note",
                       lambda: note_for_day(day, "UTC")), \
            mock.patch("kazma_core.runtime.live_llm.resolve_live_client",
                       lambda fallback, **_kw: (client, None)):
        yield


@contextlib.contextmanager
def _reasoning_setting(level: str) -> Iterator[None]:
    """``agent.chat_reasoning`` set to *level* for the turn (the setting a
    chat turn reads), and put back."""
    from kazma_core.agent.chat_reasoning import CHAT_REASONING_KEY
    from kazma_core.config_store import get_config_store

    store = get_config_store()
    missing = object()
    held = store.get(CHAT_REASONING_KEY, missing)
    store.set(CHAT_REASONING_KEY, level)
    try:
        yield
    finally:
        if held is missing:
            store.delete(CHAT_REASONING_KEY)
        else:
            store.set(CHAT_REASONING_KEY, held)


def _recall_block(messages: list[dict[str, Any]] | None) -> str:
    from kazma_core.turn_notes import note_kind

    for message in messages or []:
        if note_kind(message) == "memory_recall":
            return str(message.get("content") or "")
    return ""


async def run_turn(question: str, *, now: float, tenant: str, seed: Callable[[sqlite3.Connection], Any],
                   client: CountingClient, system_prompt: str, thread_id: str, reasoning: str = "low",
                   rounds: int = TOOL_ROUNDS, cost_breaker: Any = None, authority: Any = None,
                   tracer: Any = None) -> TurnOutcome:
    """Ask *question* through Kazma's chat turn on a memory holding what
    *seed* writes for *tenant*, at the time *now*."""
    from kazma_core.agent.graph_builder import build_supervisor_graph
    from kazma_core.agent.state import initial_supervisor_state
    from kazma_core.agent.turn import run_agent_turn
    from kazma_core.authority import create_authority
    from kazma_core.cost_breaker import create_cost_breaker
    from kazma_core.tenant_context import tenant_scope
    from kazma_core.tracing import KazmaTracer

    async with _ONE_TURN:
        client.reset()
        tools = MemoryTools()
        started = time.perf_counter()
        with _private_memory(seed), _at_the_questions_time(now, client), _reasoning_setting(reasoning):
            graph = build_supervisor_graph(
                llm=client, system_prompt=system_prompt, tool_definitions=tools.definitions(),
                tool_executor=tools, cost_breaker=cost_breaker or create_cost_breaker(),
                authority=authority or create_authority(model="benchmark", window=1_000_000),
                tracer=tracer or KazmaTracer(backend="console"),
            )
            state = await asyncio.to_thread(initial_supervisor_state, thread_id=thread_id, tenant_id=tenant)
            state["max_iterations"] = rounds + 1
            state["messages"] = [{"role": "user", "content": question}]
            with tenant_scope(tenant):
                result = await run_agent_turn(graph=graph, thread_id=thread_id, state=state, persist=False)
        usage: dict[str, Any] = {
            **{k: int(v) for k, v in client.usage.items()},
            "failed_calls": len(client.errors),
            "tools": dict(sorted(tools.used.items())),
            "seconds": round(time.perf_counter() - started, 2),
        }
        # A call the supervisor retried and got past is counted, not the
        # turn's failure; a turn that ended failed, or with nothing, says why
        # (the last refused call's kind when there was one) and is asked again.
        text = (result.text or "").strip()
        error = None
        if result.turn_failed or result.error or not text:
            error = client.errors[-1] if client.errors else ("turn_failed" if result.turn_failed else "no_answer")
        return TurnOutcome(text="" if error else text, error=error, usage=usage,
                           block=_recall_block(client.first_messages))
