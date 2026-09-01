"""Standardized Telemetry Event Protocol for Kazma Real-time Stream & Event Bus.

Translates LangGraph execution streams (astream_events v2) and state interrupts
into unified JSON telemetry frames for WebSocket/SSE consumers.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, Optional

logger = logging.getLogger(__name__)


def _budget_fields(data: Any) -> dict[str, Any]:
    """Pull iteration / max_iterations off a LangGraph event payload if present."""
    if not isinstance(data, dict):
        return {}
    inp = data.get("input")
    if not isinstance(inp, dict):
        inp = data
    out: dict[str, Any] = {}
    try:
        if inp.get("iteration") is not None:
            out["iteration"] = int(inp["iteration"])
        if inp.get("max_iterations") is not None:
            out["max_iterations"] = int(inp["max_iterations"])
    except (TypeError, ValueError):
        return {}
    return out


@dataclass
class TelemetryEvent:
    """Standardized event frame for agent state, tool, LLM, and error telemetry."""

    type: str  # 'status_update', 'tool_lifecycle', 'llm_delta', 'graph_error'
    data: Dict[str, Any] = field(default_factory=dict)
    thread_id: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {
            "type": self.type,
            "data": self.data,
            "timestamp": self.timestamp,
        }
        if self.thread_id:
            res["thread_id"] = self.thread_id
        return res

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


class EventBridge:
    """Translates LangGraph `astream_events(version='v2')` into TelemetryEvents."""

    @staticmethod
    async def process_stream(
        event_stream: AsyncGenerator[Dict[str, Any], None],
        thread_id: Optional[str] = None,
    ) -> AsyncGenerator[TelemetryEvent, None]:
        """Yields standardized TelemetryEvents from raw LangGraph event stream."""
        content_accum: str = ""

        # Initial thinking event
        yield TelemetryEvent(
            type="status_update",
            data={"status": "thinking", "active_node": "Supervisor"},
            thread_id=thread_id,
        )

        try:
            async for ev in event_stream:
                ev_type = ev.get("event")
                ev_name = ev.get("name", "")
                data = ev.get("data", {})

                # 1. Routing / Node start
                if ev_type == "on_chain_start":
                    if ev_name in ("Supervisor", "ToolWorker", "tool_worker_node", "agent"):
                        yield TelemetryEvent(
                            type="status_update",
                            data={
                                "status": "routing_node",
                                "active_node": ev_name,
                                **_budget_fields(data),
                            },
                            thread_id=thread_id,
                        )

                # 2. LLM token delta streaming
                elif ev_type == "on_chat_model_stream":
                    chunk = data.get("chunk")
                    if chunk:
                        text = getattr(chunk, "content", None)
                        if not text and isinstance(chunk, dict):
                            text = chunk.get("text") or chunk.get("content")
                        if not text and hasattr(chunk, "text"):
                            text = getattr(chunk, "text", None)
                        if text:
                            content_accum += str(text)
                            yield TelemetryEvent(
                                type="llm_delta",
                                data={"content": str(text)},
                                thread_id=thread_id,
                            )

                # 3. Tool lifecycle events
                elif ev_type == "on_tool_start":
                    inputs = data.get("input", {})
                    yield TelemetryEvent(
                        type="tool_lifecycle",
                        data={
                            "status": "tool_running",
                            "tool_name": ev_name,
                            "inputs": inputs if isinstance(inputs, dict) else {"args": str(inputs)},
                        },
                        thread_id=thread_id,
                    )

                elif ev_type == "on_tool_end":
                    output = data.get("output", "")
                    yield TelemetryEvent(
                        type="tool_lifecycle",
                        data={
                            "status": "tool_completed",
                            "tool_name": ev_name,
                            "result": str(output),
                        },
                        thread_id=thread_id,
                    )

                elif ev_type in ("on_tool_error", "on_chain_error"):
                    err = data.get("error", "Unknown tool error")
                    yield TelemetryEvent(
                        type="tool_lifecycle",
                        data={
                            "status": "tool_failed",
                            "tool_name": ev_name,
                            "error": str(err),
                        },
                        thread_id=thread_id,
                    )

                # 3b. Supervisor finished inject — memory explain panel payload
                elif ev_type == "on_chain_end" and (
                    ev_name in ("supervisor", "Supervisor", "_supervisor")
                    or "supervisor" in str(ev_name).lower()
                ):
                    output = data.get("output", {})
                    if isinstance(output, dict) and output.get("memory_explain"):
                        yield TelemetryEvent(
                            type="memory_explain",
                            data=output["memory_explain"],
                            thread_id=thread_id,
                        )

                # 4. Terminal chain end — the graph is done, the final answer
                # is about to be backfilled. Emit a "synthesizing" status so
                # the client keeps the thinking indicator alive until the
                # actual text arrives. Without this, the spinner dies the
                # instant the graph loop ends, creating the "Done 0s" silence
                # gap before the backfilled answer appears.
                elif ev_type == "on_chain_end" and ev_name in ("__end__", "LangGraph"):
                    output = data.get("output", {})
                    if isinstance(output, dict) and output.get("memory_explain"):
                        yield TelemetryEvent(
                            type="memory_explain",
                            data=output["memory_explain"],
                            thread_id=thread_id,
                        )
                    yield TelemetryEvent(
                        type="status_update",
                        data={"status": "synthesizing", "active_node": "Respond"},
                        thread_id=thread_id,
                    )

        except Exception as exc:
            logger.exception("[EventBridge] Exception in event stream processing: %s", exc)
            raw = str(exc) or type(exc).__name__
            # LangGraph default recursion_limit=25 is a common false "agent died" UX
            if "Recursion limit" in raw or type(exc).__name__ == "GraphRecursionError":
                friendly = (
                    "This turn used too many graph steps (tool/supervisor hops) and hit "
                    "LangGraph's recursion ceiling. Try a smaller ask, or continue in a "
                    "follow-up message — split large smoke tests into sections."
                )
            else:
                friendly = raw if len(raw) <= 500 else raw[:500] + "…"
            yield TelemetryEvent(
                type="graph_error",
                data={
                    "error_type": type(exc).__name__,
                    "message": friendly,
                },
                thread_id=thread_id,
            )

    @staticmethod
    def create_approval_event(
        thread_id: str,
        tool_name: str,
        args: Dict[str, Any],
        message: str = "",
        tools: Optional[list] = None,
        kind: Optional[str] = None,
        items: Optional[list] = None,
        interrupt_id: Optional[str] = None,
        yolo_allowed: Optional[bool] = None,
    ) -> TelemetryEvent:
        """Create a TelemetryEvent for HITL tool approval required.

        For semantic clarify/confirm interrupts, pass ``kind`` (e.g.
        ``"semantic_clarify"``) and ``items`` (the clarify payload's ``items``
        with their ``options``) so the UI can render discrete option buttons
        instead of a generic Approve/YOLO card (chat.js renderHitlCard gates
        on ``data.kind`` starting with ``semantic_``). Security/danger tools
        leave these unset → existing generic card behavior.
        """
        data: Dict[str, Any] = {
            "status": "paused_for_approval",
            "thread_id": thread_id,
            "tool": tool_name,
            "args": args or {},
            "tools": tools or [],
            "message": message or f"Approval required for danger tool '{tool_name}'",
        }
        if kind:
            data["kind"] = kind
        if items:
            data["items"] = items
        if interrupt_id:
            data["interrupt_id"] = interrupt_id
        if yolo_allowed is not None:
            data["yolo_allowed"] = bool(yolo_allowed)
        return TelemetryEvent(type="status_update", data=data, thread_id=thread_id)

    @staticmethod
    def create_idle_event(thread_id: Optional[str] = None) -> TelemetryEvent:
        """Create an idle status update event."""
        return TelemetryEvent(
            type="status_update",
            data={"status": "idle"},
            thread_id=thread_id,
        )
