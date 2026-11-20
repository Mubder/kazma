"""Kazma Tracer — Observability layer using Langfuse (primary) or OpenTelemetry (fallback).

Every LLM call, tool execution, state transition, and context compaction
is traced and visible on a local dashboard.
"""

from __future__ import annotations

import logging
import os
import time
from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from kazma_core.config_schema import TracingConfig
from kazma_core.tracing.events import EventBridge, TelemetryEvent
from kazma_core.tracing.langfuse_enable import langfuse_keys, resolve_langfuse_enabled
from kazma_core.background import spawn_background

logger = logging.getLogger(__name__)


# ─── In-memory trace store (feeds the dashboard) ─────────────────────


@dataclass
class TraceEntry:
    """A single trace entry for dashboard display."""

    timestamp: float
    trace_type: str  # llm, tool, state, compaction
    label: str
    status: str  # success, error, warning
    duration_ms: float
    tokens: int = 0
    cost: float = 0.0
    details: str = ""
    #: False for a model call whose price Kazma does not know: its cost is
    #: counted as 0, and the dashboard says the total is unknown, not $0.
    priced: bool = True
    #: The space and person the call or step was for (Spaces S1-22),
    #: stamped by :meth:`TraceStore.add` from the scope bound where it was
    #: made. Empty: Kazma's own work, or nothing bound -- the owner's space's.
    space: str = ""
    asker: str = ""


def _space_of(space: str) -> str:
    """The space a trace is shown in: its own, else the owner's."""
    from kazma_core.spaces.scope import OWNER_SPACE_ID

    return space or OWNER_SPACE_ID


class _Totals:
    """Running totals of one view (the install, or one space)."""

    __slots__ = ("cost", "tokens", "llm_calls", "unpriced_llm_calls", "tool_calls")

    def __init__(self) -> None:
        self.cost = 0.0
        self.tokens = 0
        self.llm_calls = 0
        self.unpriced_llm_calls = 0
        self.tool_calls = 0

    def add(self, entry: TraceEntry) -> None:
        self.cost += entry.cost
        self.tokens += entry.tokens
        if entry.trace_type == "llm":
            self.llm_calls += 1
            if not entry.priced:
                self.unpriced_llm_calls += 1
        elif entry.trace_type == "tool":
            self.tool_calls += 1


class TraceStore:
    """In-memory ring buffer of recent traces with WebSocket broadcasting.

    Feeds the real-time observability dashboard without needing
    Langfuse or OpenTelemetry. Default capacity: 500 entries.
    Broadcasts new entries to connected WebSocket clients.

    Each entry carries its space (Spaces S1-22). A reader passes the space
    it shows (:meth:`recent`, :meth:`stats`; None: every one, as before
    Spaces), and a socket registered for a space is sent that space's
    entries and totals only: a trace's label and details are a turn's, never
    another space's to see.
    """

    def __init__(self, max_entries: int = 500) -> None:
        self._traces: deque[TraceEntry] = deque(maxlen=max_entries)
        self._all = _Totals()
        self._by_space: dict[str, _Totals] = {}
        self._start_time: float = time.time()
        #: socket -> the space it shows (None: every one).
        self._ws_clients: dict[Any, str | None] = {}

    def register_ws(self, websocket: Any, space: str | None = None) -> None:
        self._ws_clients[websocket] = space

    def unregister_ws(self, websocket: Any) -> None:
        self._ws_clients.pop(websocket, None)

    def _metrics(self, space: str | None) -> dict[str, Any]:
        stats = self.stats(space)
        return {
            "total_cost": round(float(stats["total_cost"]), 6),
            "total_tokens": stats["total_tokens"],
            "total_llm_calls": stats["total_llm_calls"],
            "unpriced_llm_calls": stats["unpriced_llm_calls"],
            "total_tool_calls": stats["total_tool_calls"],
            "total_traces": stats["total_traces"],
            "uptime_seconds": stats["uptime_seconds"],
        }

    async def _broadcast(self, entry: TraceEntry) -> None:
        """Broadcast a new trace entry to the connected sockets that show
        its space."""
        import json
        import time as t

        data = {
            "timestamp": t.strftime("%H:%M:%S", t.localtime(entry.timestamp)),
            "trace_type": entry.trace_type,
            "label": entry.label,
            "status": entry.status,
            "duration_ms": f"{entry.duration_ms:.0f}",
            "tokens": entry.tokens,
            "cost": f"${entry.cost:.4f}",
        }
        dead = set()
        for ws, shown in list(self._ws_clients.items()):
            if shown is not None and _space_of(entry.space) != shown:
                continue
            payload = json.dumps({"type": "trace", "data": data, "metrics": self._metrics(shown)})
            try:
                await ws.send_text(payload)
            except Exception as _e:
                logger.debug("WS client send failed, removing: %s", _e)
                dead.add(ws)
        for ws in dead:
            self._ws_clients.pop(ws, None)

    def add(self, entry: TraceEntry) -> None:
        if not entry.space and not entry.asker:
            from kazma_core.spaces.scope import current_scope

            bound = current_scope()
            if bound is not None:
                entry.space, entry.asker = bound.space_id, bound.asker
        self._traces.append(entry)
        self._all.add(entry)
        self._by_space.setdefault(_space_of(entry.space), _Totals()).add(entry)
        try:
            import asyncio

            loop = asyncio.get_event_loop()
            if loop.is_running():
                spawn_background(self._broadcast(entry), name="trace-broadcast")
        except RuntimeError:
            pass

    def recent(self, limit: int = 50, space: str | None = None) -> list[TraceEntry]:
        """The newest *limit* entries; of *space* only when it is given."""
        if space is None:
            return list(self._traces)[-limit:]
        return [e for e in self._traces if _space_of(e.space) == space][-limit:]

    def stats(self, space: str | None = None) -> dict[str, Any]:
        """The running totals; of *space* only when it is given."""
        totals = self._all if space is None else self._by_space.get(space, _Totals())
        kept = len(self._traces) if space is None else sum(1 for e in self._traces if _space_of(e.space) == space)
        return {
            "total_cost": round(totals.cost, 4),
            "total_tokens": totals.tokens,
            "total_llm_calls": totals.llm_calls,
            "unpriced_llm_calls": totals.unpriced_llm_calls,
            "total_tool_calls": totals.tool_calls,
            "total_traces": kept,
            "uptime_seconds": time.time() - self._start_time,
        }



def shown_space() -> str | None:
    """The space a reader of the trace feed shows (Spaces S1-22): the one
    bound now once Spaces is enforced ("" with none bound: no space's), every
    one (None) before. No I/O."""
    from kazma_core.spaces.scope import current_scope, spaces_enforced

    if not spaces_enforced():
        return None
    bound = current_scope()
    return bound.space_id if bound is not None else ""


def view(store: TraceStore, limit: int, space: str | None) -> tuple[list[TraceEntry], dict[str, Any]]:
    """The newest *limit* entries and the totals a reader shows: *space*'s,
    or -- with None, as before Spaces -- every one, asked the old way."""
    if space is None:
        return store.recent(limit), store.stats()
    return store.recent(limit, space), store.stats(space)


# Global trace store singleton
_trace_store = TraceStore()


def get_trace_store() -> TraceStore:
    return _trace_store


# ─── Tracing Backend ──────────────────────────────────────────────────


class TracingBackend(StrEnum):
    LANGFUSE = "langfuse"
    CONSOLE = "console"  # fallback for testing / no-dashboard mode


__all__ = [
    "KazmaTracer",
    "TraceEntry",
    "TraceStore",
    "TracingBackend",
    "create_tracer",
    "get_trace_store",
    "shown_space",
    "view",
    "TelemetryEvent",
    "EventBridge",
    "langfuse_keys",
    "resolve_langfuse_enabled",
]


class KazmaTracer:
    """Traces all agent operations."""

    def __init__(self, config: TracingConfig | None = None, *, backend: str | None = None) -> None:
        self.config = config or TracingConfig()
        if backend is not None:
            self.config = self.config.model_copy(update={"backend": backend})
        self.backend = TracingBackend(self.config.backend)
        self._client: Any = None
        self._init_backend()

    def _init_backend(self) -> None:
        if self.backend == TracingBackend.LANGFUSE:
            self._init_langfuse()
        else:
            logger.info("Tracing backend: console (stdout logging only)")

    def _init_langfuse(self) -> None:
        try:
            from langfuse import Langfuse

            public_key = self.config.langfuse_public_key or os.getenv("LANGFUSE_PUBLIC_KEY", "")
            secret_key = self.config.langfuse_secret_key or os.getenv("LANGFUSE_SECRET_KEY", "")
            host = self.config.langfuse_host or os.getenv("LANGFUSE_HOST", "http://localhost:3000")

            self._client = Langfuse(
                public_key=public_key,
                secret_key=secret_key,
                host=host,
            )
            logger.info("Langfuse tracing initialized")
        except ImportError:
            logger.warning("langfuse not installed, falling back to console tracing")
            self.backend = TracingBackend.CONSOLE
        except Exception as e:
            logger.warning("Langfuse init failed (%s), falling back to console", e)
            self.backend = TracingBackend.CONSOLE

    def trace_llm_call(
        self,
        model: str,
        prompt: str,
        response: str,
        tokens: int,
        cost: float,
        duration_ms: float = 0.0,
        *,
        priced: bool = True,
    ) -> None:
        _trace_store.add(
            TraceEntry(
                timestamp=time.time(),
                trace_type="llm",
                label=model,
                status="success",
                duration_ms=duration_ms,
                tokens=tokens,
                cost=cost,
                details=prompt[:200],
                priced=priced,
            )
        )

        if self.backend == TracingBackend.LANGFUSE and self._client:
            self._trace_llm_langfuse(model, tokens, cost, duration_ms, prompt, response)
        else:
            logger.info(
                "LLM call: model=%s tokens=%d cost=$%.4f duration=%.0fms",
                model,
                tokens,
                cost,
                duration_ms,
            )

    def _trace_llm_langfuse(
        self,
        model: str,
        tokens: int,
        cost: float,
        duration_ms: float,
        prompt: str,
        response: str,
    ) -> None:
        try:
            generation = self._client.generation(
                name="llm_call",
                model=model,
                usage={
                    "totalTokens": tokens,
                    "inputTokens": len(prompt.split()) * 2,
                    "outputTokens": len(response.split()) * 2,
                },
                metadata={
                    "cost_usd": cost,
                    "duration_ms": duration_ms,
                },
            )
            generation.end(
                output=response[:2000],
                usage={"totalTokens": tokens},
            )
        except Exception as e:
            logger.warning("Langfuse LLM trace failed: %s", e)

    def trace_tool_execution(
        self,
        tool_name: str,
        input_data: dict[str, Any],
        output_data: dict[str, Any],
        duration_ms: float,
        success: bool = True,
    ) -> None:
        status = "success" if success else "error"
        _trace_store.add(
            TraceEntry(
                timestamp=time.time(),
                trace_type="tool",
                label=tool_name,
                status=status,
                duration_ms=duration_ms,
                details=str(list(input_data.keys()))[:200],
            )
        )

        if self.backend == TracingBackend.LANGFUSE and self._client:
            self._trace_tool_langfuse(tool_name, input_data, output_data, duration_ms, success)
        else:
            status_label = "OK" if success else "FAIL"
            logger.info(
                "Tool %s [%s]: duration=%.0fms",
                tool_name,
                status_label,
                duration_ms,
            )

    def _trace_tool_langfuse(
        self,
        tool_name: str,
        input_data: dict[str, Any],
        output_data: dict[str, Any],
        duration_ms: float,
        success: bool,
    ) -> None:
        try:
            span = self._client.span(
                name=f"tool:{tool_name}",
                input=input_data,
                output=output_data,
                metadata={
                    "duration_ms": duration_ms,
                    "success": success,
                },
            )
            span.end()
        except Exception as e:
            logger.warning("Langfuse tool trace failed: %s", e)

    def trace_state_transition(
        self,
        from_state: str,
        to_state: str,
        checkpoint_id: str,
    ) -> None:
        _trace_store.add(
            TraceEntry(
                timestamp=time.time(),
                trace_type="state",
                label=f"{from_state} → {to_state}",
                status="success",
                duration_ms=0,
                details=f"checkpoint={checkpoint_id[:12]}",
            )
        )

        if self.backend == TracingBackend.LANGFUSE and self._client:
            self._trace_transition_langfuse(from_state, to_state, checkpoint_id)
        else:
            logger.info(
                "State transition: %s → %s (checkpoint=%s)",
                from_state,
                to_state,
                checkpoint_id[:12],
            )

    def _trace_transition_langfuse(
        self,
        from_state: str,
        to_state: str,
        checkpoint_id: str,
    ) -> None:
        try:
            span = self._client.span(
                name="state_transition",
                input={"from_state": from_state},
                output={"to_state": to_state},
                metadata={"checkpoint_id": checkpoint_id},
            )
            span.end()
        except Exception as e:
            logger.warning("Langfuse transition trace failed: %s", e)

    def trace_compaction(
        self,
        tokens_before: int,
        tokens_after: int,
        summary: str,
    ) -> None:
        reduction_pct = (1 - tokens_after / tokens_before) * 100 if tokens_before > 0 else 0

        _trace_store.add(
            TraceEntry(
                timestamp=time.time(),
                trace_type="compaction",
                label=f"{tokens_before} → {tokens_after}",
                status="success",
                duration_ms=0,
                details=f"{reduction_pct:.0f}% reduction",
            )
        )

        if self.backend == TracingBackend.LANGFUSE and self._client:
            self._trace_compaction_langfuse(tokens_before, tokens_after, summary, reduction_pct)
        else:
            logger.info(
                "Compaction: %d → %d tokens (%.0f%% reduction): %s",
                tokens_before,
                tokens_after,
                reduction_pct,
                summary[:100],
            )

    def _trace_compaction_langfuse(
        self,
        tokens_before: int,
        tokens_after: int,
        summary: str,
        reduction_pct: float,
    ) -> None:
        try:
            span = self._client.span(
                name="context_compaction",
                input={"tokens_before": tokens_before},
                output={"tokens_after": tokens_after, "summary": summary},
                metadata={
                    "reduction_pct": reduction_pct,
                    "tokens_saved": tokens_before - tokens_after,
                },
            )
            span.end()
        except Exception as e:
            logger.warning("Langfuse compaction trace failed: %s", e)

    def flush(self) -> None:
        if self.backend == TracingBackend.LANGFUSE and self._client:
            try:
                self._client.flush()
            except Exception as e:
                logger.warning("Langfuse flush failed: %s", e)

    def shutdown(self) -> None:
        self.flush()
        logger.info("Tracer shut down (backend=%s)", self.backend.value)


def create_tracer(config: TracingConfig | None = None, *, backend: str | None = None) -> KazmaTracer:
    return KazmaTracer(config=config, backend=backend)
