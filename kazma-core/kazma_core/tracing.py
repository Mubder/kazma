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


class TraceStore:
    """In-memory ring buffer of recent traces with WebSocket broadcasting.

    Feeds the real-time observability dashboard without needing
    Langfuse or OpenTelemetry. Default capacity: 500 entries.
    Broadcasts new entries to connected WebSocket clients.
    """

    def __init__(self, max_entries: int = 500) -> None:
        self._traces: deque[TraceEntry] = deque(maxlen=max_entries)
        self._total_cost: float = 0.0
        self._total_tokens: int = 0
        self._total_llm_calls: int = 0
        self._total_tool_calls: int = 0
        self._start_time: float = time.time()
        self._ws_clients: set[Any] = set()

    def register_ws(self, websocket: Any) -> None:
        self._ws_clients.add(websocket)

    def unregister_ws(self, websocket: Any) -> None:
        self._ws_clients.discard(websocket)

    async def _broadcast(self, entry: TraceEntry) -> None:
        """Broadcast a new trace entry to all connected WebSocket clients."""
        import json
        import time as t

        payload = json.dumps(
            {
                "type": "trace",
                "data": {
                    "timestamp": t.strftime("%H:%M:%S", t.localtime(entry.timestamp)),
                    "trace_type": entry.trace_type,
                    "label": entry.label,
                    "status": entry.status,
                    "duration_ms": f"{entry.duration_ms:.0f}",
                    "tokens": entry.tokens,
                    "cost": f"${entry.cost:.4f}",
                },
                "metrics": {
                    "total_cost": f"${self._total_cost:.4f}",
                    "total_tokens": f"{self._total_tokens:,}",
                    "total_llm_calls": self._total_llm_calls,
                    "total_tool_calls": self._total_tool_calls,
                    "total_traces": len(self._traces),
                },
            }
        )
        dead = set()
        for ws in self._ws_clients:
            try:
                await ws.send_text(payload)
            except Exception as _e:
                logger.debug("WS client send failed, removing: %s", _e)
                dead.add(ws)
        self._ws_clients -= dead

    def add(self, entry: TraceEntry) -> None:
        self._traces.append(entry)
        self._total_cost += entry.cost
        self._total_tokens += entry.tokens
        if entry.trace_type == "llm":
            self._total_llm_calls += 1
        elif entry.trace_type == "tool":
            self._total_tool_calls += 1
        # Fire-and-forget broadcast (safe in both sync and async contexts)
        try:
            import asyncio

            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self._broadcast(entry))
        except RuntimeError:
            pass  # No running event loop (sync test context)

    def recent(self, limit: int = 50) -> list[TraceEntry]:
        return list(self._traces)[-limit:]

    def stats(self) -> dict[str, Any]:
        return {
            "total_cost": round(self._total_cost, 4),
            "total_tokens": self._total_tokens,
            "total_llm_calls": self._total_llm_calls,
            "total_tool_calls": self._total_tool_calls,
            "total_traces": len(self._traces),
            "uptime_seconds": time.time() - self._start_time,
        }


# Global trace store singleton
_trace_store = TraceStore()


def get_trace_store() -> TraceStore:
    return _trace_store


# ─── Tracing Backend ──────────────────────────────────────────────────


class TracingBackend(StrEnum):
    LANGFUSE = "langfuse"
    CONSOLE = "console"  # fallback for testing / no-dashboard mode


from kazma_core.config_schema import TracingConfig


class KazmaTracer:
    """Traces all agent operations.

    Supports two backends:
    - langfuse: Full-featured dashboard at localhost:3000 (primary)
    - console: Stdout logging for testing / no-dashboard environments

    All traces are also written to the in-memory TraceStore for the
    local dashboard at /dashboard.
    """

    def __init__(self, config: TracingConfig | None = None, *, backend: str | None = None) -> None:
        """Initialize the tracer.

        Args:
            config: A TracingConfig. If None, a default is created.
            backend: Convenience override for the backend name (e.g. "console",
                "langfuse"). Takes precedence over config.backend.
        """
        self.config = config or TracingConfig()
        if backend is not None:
            self.config = self.config.model_copy(update={"backend": backend})
        self.backend = TracingBackend(self.config.backend)
        self._client: Any = None
        self._init_backend()

    def _init_backend(self) -> None:
        """Initialize the tracing backend."""
        if self.backend == TracingBackend.LANGFUSE:
            self._init_langfuse()
        else:
            logger.info("Tracing backend: console (stdout logging only)")

    def _init_langfuse(self) -> None:
        """Initialize Langfuse client from config."""
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
    ) -> None:
        """Trace an LLM API call.

        Args:
            model: Model identifier (e.g. "gpt-4", "claude-3-opus").
            prompt: The input prompt (truncated to 500 chars for storage).
            response: The model response (truncated to 500 chars for storage).
            tokens: Total tokens consumed (prompt + completion).
            cost: Dollar cost of this call.
            duration_ms: Wall-clock duration in milliseconds.
        """
        # Write to local trace store
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
            )
        )

        # Send to configured backend
        metadata = {
            "model": model,
            "tokens": tokens,
            "cost_usd": cost,
            "duration_ms": duration_ms,
            "prompt_preview": prompt[:500],
            "response_preview": response[:500],
        }

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
        """Send LLM trace to Langfuse."""
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
        """Trace a tool execution.

        Args:
            tool_name: Name of the tool (e.g. "web_search", "file_read").
            input_data: Tool input parameters.
            output_data: Tool output / result.
            duration_ms: Wall-clock duration in milliseconds.
            success: Whether the tool executed successfully.
        """
        # Write to local trace store
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

        # Send to configured backend
        metadata = {
            "tool": tool_name,
            "success": success,
            "duration_ms": duration_ms,
            "input_keys": list(input_data.keys()),
            "output_keys": list(output_data.keys()),
        }

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
        """Send tool trace to Langfuse."""
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
        """Trace an agent state transition.

        Args:
            from_state: Previous state name (e.g. "idle", "thinking").
            to_state: New state name (e.g. "tool_calling", "responding").
            checkpoint_id: LangGraph checkpoint ID for this transition.
        """
        # Write to local trace store
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
        """Send state transition to Langfuse."""
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
        """Trace a context compaction event.

        Args:
            tokens_before: Token count before compaction.
            tokens_after: Token count after compaction.
            summary: Brief description of what was compacted.
        """
        reduction_pct = (1 - tokens_after / tokens_before) * 100 if tokens_before > 0 else 0

        # Write to local trace store
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
        """Send compaction trace to Langfuse."""
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
        """Flush pending traces to the backend."""
        if self.backend == TracingBackend.LANGFUSE and self._client:
            try:
                self._client.flush()
            except Exception as e:
                logger.warning("Langfuse flush failed: %s", e)

    def shutdown(self) -> None:
        """Gracefully shutdown the tracer."""
        self.flush()
        logger.info("Tracer shut down (backend=%s)", self.backend.value)


def create_tracer(config: TracingConfig | None = None, *, backend: str | None = None) -> KazmaTracer:
    """Factory to create a KazmaTracer with the configured backend.

    Args:
        config: TracingConfig object (from kazma.yaml tracing section).
                If None, uses default TracingConfig with env var fallbacks.
        backend: Convenience override (e.g. "console", "langfuse").
    """
    return KazmaTracer(config=config, backend=backend)
