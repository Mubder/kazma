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
                            data={"status": "routing_node", "active_node": ev_name},
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

        except Exception as exc:
            logger.exception("[EventBridge] Exception in event stream processing: %s", exc)
            yield TelemetryEvent(
                type="graph_error",
                data={
                    "error_type": type(exc).__name__,
                    "message": str(exc),
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
    ) -> TelemetryEvent:
        """Create a TelemetryEvent for HITL tool approval required."""
        return TelemetryEvent(
            type="status_update",
            data={
                "status": "paused_for_approval",
                "thread_id": thread_id,
                "tool": tool_name,
                "args": args or {},
                "tools": tools or [],
                "message": message or f"Approval required for danger tool '{tool_name}'",
            },
            thread_id=thread_id,
        )

    @staticmethod
    def create_idle_event(thread_id: Optional[str] = None) -> TelemetryEvent:
        """Create an idle status update event."""
        return TelemetryEvent(
            type="status_update",
            data={"status": "idle"},
            thread_id=thread_id,
        )
