"""Token-streaming adapter for the supervisor LLM path.

Kazma's LLM clients are custom httpx classes, not LangChain ``BaseChatModel``,
so LangGraph ``astream_events`` never emits ``on_chat_model_stream``. This
module is the industry-standard bridge:

1. Providers implement ``chat_stream()`` (async generator of ``StreamDelta``).
2. ``invoke_llm_chat()`` consumes that stream and injects synthetic
   ``on_chat_model_stream`` events into a per-thread queue.
3. SSE / WS already map that event to ``token`` / ``llm_delta`` frames.

LiteLLM egress: optional proxy for the generic ``LLMProvider`` only
(``kazma_core.llm_gateway``). Point ``KAZMA_LITELLM_URL`` at a proxy.
Local Ollama/LM Studio stay direct unless ``KAZMA_LITELLM_LOCAL=1``.
Native 4-branch providers stay direct (AGENTS.md §1). Kill-switch
``KAZMA_LITELLM=0``.

Kill-switch: ``KAZMA_LLM_STREAM=0`` falls back to blocking ``chat()``.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "StreamDelta",
    "bridged_event_stream",
    "emit_token_delta",
    "invoke_llm_chat",
    "register_delta_queue",
    "stream_enabled",
    "unregister_delta_queue",
]

logger = logging.getLogger(__name__)

_SENTINEL = object()


@dataclass
class StreamDelta:
    """One chunk from a streaming LLM completion.

    Token text is in ``content``. The generator MUST yield a final delta
    with ``response`` set (the assembled ``LLMResponse``) so callers can
    keep the existing ``chat()`` contract.
    """

    content: str = ""
    response: Any = None
    finish_reason: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


# thread_id → asyncio.Queue that SSE/WS already consume as astream_events.
_delta_queues: dict[str, asyncio.Queue[Any]] = {}


def stream_enabled() -> bool:
    """True unless the operator killed streaming (``KAZMA_LLM_STREAM=0``)."""
    raw = (os.environ.get("KAZMA_LLM_STREAM") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def register_delta_queue(thread_id: str, queue: asyncio.Queue[Any]) -> str:
    """Bind *queue* as the token sink for *thread_id*. Returns the id."""
    tid = (thread_id or "").strip()
    if tid:
        _delta_queues[tid] = queue
    return tid


def unregister_delta_queue(thread_id: str) -> None:
    """Drop the token sink for *thread_id* (idempotent)."""
    tid = (thread_id or "").strip()
    if tid:
        _delta_queues.pop(tid, None)


def emit_token_delta(content: str, *, thread_id: str | None = None) -> None:
    """Inject a synthetic ``on_chat_model_stream`` event for SSE/WS.

    Looks up the queue by ``thread_id`` or the HITL ``get_current_thread_id()``
    ContextVar (set on every chat transport). Never raises.
    """
    text = content if isinstance(content, str) else str(content or "")
    if not text:
        return
    tid = (thread_id or "").strip()
    if not tid:
        try:
            from kazma_core.safety.hitl import get_current_thread_id

            tid = (get_current_thread_id() or "").strip()
        except Exception:
            tid = ""
    if not tid:
        return
    queue = _delta_queues.get(tid)
    if queue is None:
        return
    event = {
        "event": "on_chat_model_stream",
        "name": "llm",
        "data": {"chunk": {"content": text}},
    }
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        # Bounded SSE queue: drop tokens rather than block the LLM read.
        # Post-stream backfill still paints the final answer.
        logger.debug("[llm_stream] delta queue full — dropping token thread=%s", tid[:12])
    except Exception:
        logger.debug("[llm_stream] emit failed thread=%s", tid[:12], exc_info=True)


def _has_chat_stream(client: Any) -> bool:
    """True when *client*'s class implements an async-generator ``chat_stream``.

    ``MagicMock`` instances auto-create ``chat_stream`` on the instance; we
    inspect the **class** so supervisor tests that stub ``.chat`` keep working.
    """
    attr = getattr(type(client), "chat_stream", None)
    if attr is None:
        return False
    fn = inspect.unwrap(attr)
    return inspect.isasyncgenfunction(fn)


async def invoke_llm_chat(
    client: Any, *args: Any, emit_deltas: bool = True, **kwargs: Any
) -> Any:
    """Call ``chat_stream`` when available, else ``chat()``.

    Token deltas are pushed onto the per-thread queue (if any). Used by the
    supervisor, nudge recovery, respond-node synthesis, and failover so every
    user-visible LLM call can stream.

    ``emit_deltas=False`` (S3-2) is for RECOVERY attempts after an earlier
    attempt of the same user-visible call already streamed partial deltas:
    the consumer appends, so re-streaming from token 0 paints the incident's
    duplicated prefix. A quiet attempt delivers its text via the final
    response + turn_complete backfill (replace semantics).
    """
    if stream_enabled() and _has_chat_stream(client):
        response: Any = None
        try:
            async for delta in client.chat_stream(*args, **kwargs):
                if getattr(delta, "content", None) and emit_deltas:
                    emit_token_delta(delta.content)
                if getattr(delta, "response", None) is not None:
                    response = delta.response
        except TypeError:
            # Subclass chat_stream signature mismatch — fall back.
            logger.debug("[llm_stream] chat_stream TypeError — falling back to chat()")
            return await client.chat(*args, **kwargs)
        if response is None:
            from kazma_core.llm_provider import LLMError

            raise LLMError(
                "LLM stream ended without a final response",
                transient=True,
            )
        return response
    return await client.chat(*args, **kwargs)


async def bridged_event_stream(
    thread_id: str,
    source: AsyncIterator[dict[str, Any]],
) -> AsyncIterator[dict[str, Any]]:
    """Merge LangGraph ``astream_events`` with injected token deltas.

    Token emits from ``invoke_llm_chat`` land on the same queue the
    consumer reads, so EventBridge / SSE see ``on_chat_model_stream``.
    """
    queue: asyncio.Queue[Any] = asyncio.Queue()
    register_delta_queue(thread_id, queue)

    async def _pump() -> None:
        try:
            async for ev in source:
                await queue.put(ev)
        except Exception as exc:  # noqa: BLE001 — forwarded to consumer
            await queue.put(exc)
        finally:
            await queue.put(_SENTINEL)

    pump = asyncio.create_task(_pump())
    try:
        while True:
            item = await queue.get()
            if item is _SENTINEL:
                break
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        unregister_delta_queue(thread_id)
        if not pump.done():
            pump.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await pump
