"""OpenTelemetry-compatible tracing for the swarm engine.

Emits spans for every task dispatch, worker execution, LLM call,
tool invocation, aggregation, synthesis, and handoff.  Spans are
exportable via :class:`InMemorySpanExporter` for testing.

The span model is intentionally stdlib-only (no ``opentelemetry``
package required) so it works without additional pip dependencies.
Spans carry OTel-compatible attributes (``trace_id``, ``span_id``,
``parent_span_id``, ``name``, ``start_time``, ``end_time``,
``attributes``, ``status``).
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


def _new_hex_id(length: int = 16) -> str:
    """Return a random hex string of *length* characters."""
    return uuid.uuid4().hex[:length]


def _utc_now_iso() -> str:
    """Return the current UTC time in ISO-8601 format."""
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Span model
# ---------------------------------------------------------------------------

@dataclass
class Span:
    """An OpenTelemetry-compatible tracing span.

    Attributes:
        trace_id:    Shared across all spans in a single trace (task).
        span_id:     Unique identifier for this span.
        parent_id:   ``None`` for root spans, otherwise the parent's
                     ``span_id``.
        name:        Human-readable span name (e.g. ``swarm.task.task-abc``).
        start_time:  ISO-8601 timestamp when the span started.
        end_time:    ISO-8601 timestamp when the span ended (``None``
                     while in-flight).
        attributes:  Key-value metadata attached to the span.
        status:      ``"ok"``, ``"error"``, or ``"unset"``.
        status_msg:  Optional error description.
    """

    trace_id: str
    span_id: str = field(default_factory=lambda: _new_hex_id())
    parent_id: str | None = None
    name: str = ""
    start_time: str = field(default_factory=_utc_now_iso)
    end_time: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "unset"
    status_msg: str = ""

    def set_attribute(self, key: str, value: Any) -> None:
        """Attach a key-value attribute to this span."""
        self.attributes[key] = value

    def set_status(self, status: str, message: str = "") -> None:
        """Set the span status (``"ok"``, ``"error"``, or ``"unset"``)."""
        self.status = status
        self.status_msg = message

    def end(self) -> None:
        """Mark the span as ended."""
        self.end_time = _utc_now_iso()

    @property
    def is_ended(self) -> bool:
        """Return whether the span has been ended."""
        return self.end_time is not None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation."""
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "attributes": dict(self.attributes),
            "status": self.status,
            "status_msg": self.status_msg,
        }

    def duration_ms(self) -> float | None:
        """Return the span duration in milliseconds, or ``None``."""
        if self.start_time and self.end_time:
            try:
                start = datetime.fromisoformat(self.start_time)
                end = datetime.fromisoformat(self.end_time)
                return (end - start).total_seconds() * 1000
            except (ValueError, TypeError):
                return None
        return None


# ---------------------------------------------------------------------------
# In-memory exporter (for testing)
# ---------------------------------------------------------------------------

class InMemorySpanExporter:
    """Collects exported spans in memory for inspection in tests.

    Usage::

        exporter = InMemorySpanExporter()
        emitter = TracingEmitter(exporter=exporter)
        # ... run task ...
        spans = exporter.get_finished_spans()
        assert any(s.name == "swarm.task.abc" for s in spans)
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._spans: list[Span] = []

    def export(self, span: Span) -> None:
        """Record a finished span."""
        with self._lock:
            self._spans.append(span)

    def get_finished_spans(self) -> list[Span]:
        """Return all exported spans."""
        with self._lock:
            return list(self._spans)

    def get_spans_by_trace(self, trace_id: str) -> list[Span]:
        """Return all spans belonging to a specific trace."""
        with self._lock:
            return [s for s in self._spans if s.trace_id == trace_id]

    def get_spans_by_name(self, name: str) -> list[Span]:
        """Return all spans with the given name."""
        with self._lock:
            return [s for s in self._spans if s.name == name]

    def clear(self) -> None:
        """Remove all collected spans."""
        with self._lock:
            self._spans.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._spans)


# ---------------------------------------------------------------------------
# TracingEmitter
# ---------------------------------------------------------------------------

class TracingEmitter:
    """Emits OpenTelemetry-compatible spans for swarm operations.

    Maintains the active span stack per trace so that child spans
    are automatically parented.  Spans are exported to the configured
    :class:`InMemorySpanExporter` (or any object with an ``export``
    method) when they end.

    Span naming convention (per architecture doc):

    * ``swarm.task.{task_id}``          — root span per task
    * ``swarm.dispatch.{worker}``       — child of root, per worker
    * ``llm.call.{model}``              — sub-child of dispatch
    * ``tool.execute.{tool}``           — sub-child of dispatch
    * ``swarm.aggregate.{strategy}``    — child of root for fan-out
    * ``swarm.synthesize``              — child of root for consult
    * ``swarm.handoff.{from}->{to}``    — child of dispatch

    Usage::

        emitter = TracingEmitter(exporter=InMemorySpanExporter())
        root = emitter.start_task_span("task-abc")
        child = emitter.start_dispatch_span("task-abc", "analyst")
        # ... worker dispatches ...
        emitter.end_span(child)
        emitter.end_span(root)
    """

    def __init__(self, exporter: InMemorySpanExporter | None = None) -> None:
        self._exporter = exporter if exporter is not None else InMemorySpanExporter()
        self._lock = threading.Lock()
        # Active spans keyed by trace_id, maintained as a stack.
        self._active: dict[str, list[Span]] = {}
        # All spans by span_id for quick lookup.
        self._span_index: dict[str, Span] = {}

    @property
    def exporter(self) -> InMemorySpanExporter:
        """Return the configured span exporter."""
        return self._exporter

    # ------------------------------------------------------------------
    # Span lifecycle
    # ------------------------------------------------------------------

    def start_span(
        self,
        name: str,
        trace_id: str | None = None,
        parent_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Span:
        """Create and activate a new span.

        If *trace_id* is ``None``, a new trace id is generated.  If
        *parent_id* is ``None`` and there are active spans for the
        trace, the most recent active span becomes the parent.
        """
        with self._lock:
            if trace_id is None:
                trace_id = _new_hex_id(32)

            # Auto-parent to the current active span for this trace.
            if parent_id is None:
                stack = self._active.get(trace_id, [])
                if stack:
                    parent_id = stack[-1].span_id

            span = Span(
                trace_id=trace_id,
                name=name,
                parent_id=parent_id,
                attributes=dict(attributes or {}),
            )

            self._active.setdefault(trace_id, []).append(span)
            self._span_index[span.span_id] = span
            return span

    def end_span(self, span: Span, *, status: str = "ok", status_msg: str = "") -> None:
        """End a span and export it.

        Removes the span from the active stack and exports it to the
        configured exporter.
        """
        if span.is_ended:
            return

        span.end()
        if status != "unset":
            span.set_status(status, status_msg)

        with self._lock:
            stack = self._active.get(span.trace_id, [])
            if stack and stack[-1].span_id == span.span_id:
                stack.pop()
            elif stack:
                # The span may not be at the top (e.g. out-of-order end).
                self._active[span.trace_id] = [
                    s for s in stack if s.span_id != span.span_id
                ]

        self._exporter.export(span)

    def record_exception(
        self, span: Span, exc: BaseException, *, status_msg: str = ""
    ) -> None:
        """Record an exception on a span and mark it as errored."""
        span.set_attribute("exception.type", type(exc).__name__)
        span.set_attribute("exception.message", str(exc)[:500])
        span.set_status("error", status_msg or str(exc)[:200])

    # ------------------------------------------------------------------
    # Convenience span factories
    # ------------------------------------------------------------------

    def start_task_span(
        self,
        task_id: str,
        task_type: str = "dispatch",
        workers: list[str] | None = None,
    ) -> Span:
        """Start the root span for a swarm task.

        Creates a new trace and returns the root span named
        ``swarm.task.{task_id}``.
        """
        trace_id = _new_hex_id(32)
        attrs: dict[str, Any] = {
            "swarm.task.id": task_id,
            "swarm.task.type": task_type,
        }
        if workers:
            attrs["swarm.task.workers"] = ",".join(workers)
        return self.start_span(
            name=f"swarm.task.{task_id}",
            trace_id=trace_id,
            attributes=attrs,
        )

    def start_dispatch_span(
        self,
        trace_id: str,
        worker_name: str,
        task_id: str = "",
    ) -> Span:
        """Start a child span for a worker dispatch."""
        return self.start_span(
            name=f"swarm.dispatch.{worker_name}",
            trace_id=trace_id,
            attributes={
                "swarm.worker.name": worker_name,
                "swarm.task.id": task_id,
            },
        )

    def start_llm_span(
        self,
        trace_id: str,
        model: str,
        parent_id: str | None = None,
    ) -> Span:
        """Start a sub-child span for an LLM call."""
        return self.start_span(
            name=f"llm.call.{model}",
            trace_id=trace_id,
            parent_id=parent_id,
            attributes={"llm.model": model},
        )

    def start_tool_span(
        self,
        trace_id: str,
        tool_name: str,
        parent_id: str | None = None,
    ) -> Span:
        """Start a sub-child span for a tool execution."""
        return self.start_span(
            name=f"tool.execute.{tool_name}",
            trace_id=trace_id,
            parent_id=parent_id,
            attributes={"tool.name": tool_name},
        )

    def start_aggregate_span(
        self,
        trace_id: str,
        strategy: str,
    ) -> Span:
        """Start a child span for result aggregation."""
        return self.start_span(
            name=f"swarm.aggregate.{strategy}",
            trace_id=trace_id,
            attributes={"swarm.aggregation.strategy": strategy},
        )

    def start_synthesize_span(
        self,
        trace_id: str,
    ) -> Span:
        """Start a child span for consult synthesis."""
        return self.start_span(
            name="swarm.synthesize",
            trace_id=trace_id,
        )

    def start_handoff_span(
        self,
        trace_id: str,
        from_worker: str,
        to_worker: str,
    ) -> Span:
        """Start a child span for a worker handoff."""
        return self.start_span(
            name=f"swarm.handoff.{from_worker}->{to_worker}",
            trace_id=trace_id,
            attributes={
                "swarm.handoff.from": from_worker,
                "swarm.handoff.to": to_worker,
            },
        )

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_trace_spans(self, trace_id: str) -> list[Span]:
        """Return all exported spans for a given trace id."""
        return self._exporter.get_spans_by_trace(trace_id)

    def get_active_span(self, trace_id: str) -> Span | None:
        """Return the current active span for a trace, or ``None``."""
        with self._lock:
            stack = self._active.get(trace_id, [])
            return stack[-1] if stack else None

    def build_span_tree(self, trace_id: str) -> list[dict[str, Any]]:
        """Build a nested tree representation of a trace's spans.

        Returns a list of root-level span dicts, each with a
        ``"children"`` key containing nested spans.
        """
        spans = self._exporter.get_spans_by_trace(trace_id)
        by_id: dict[str, dict[str, Any]] = {}
        roots: list[dict[str, Any]] = []

        for span in spans:
            node = {**span.to_dict(), "children": []}
            by_id[span.span_id] = node

        for span in spans:
            node = by_id[span.span_id]
            if span.parent_id and span.parent_id in by_id:
                by_id[span.parent_id]["children"].append(node)
            else:
                roots.append(node)

        return roots

    def reconstruct_handoff_chain(self, trace_id: str) -> list[dict[str, str]]:
        """Reconstruct the handoff chain from a trace's spans.

        Returns a list of dicts with ``from``, ``to``, and ``span_id``
        keys, ordered by span start time.
        """
        handoff_spans = [
            s
            for s in self._exporter.get_spans_by_trace(trace_id)
            if s.name.startswith("swarm.handoff.")
        ]
        return [
            {
                "from": s.attributes.get("swarm.handoff.from", ""),
                "to": s.attributes.get("swarm.handoff.to", ""),
                "span_id": s.span_id,
            }
            for s in sorted(handoff_spans, key=lambda s: s.start_time)
        ]

    def reset(self) -> None:
        """Clear all state (useful for tests)."""
        with self._lock:
            self._active.clear()
            self._span_index.clear()
        self._exporter.clear()
