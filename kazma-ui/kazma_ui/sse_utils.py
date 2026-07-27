"""Shared SSE (Server-Sent Events) utility functions.

Used by both ``sse_chat.py`` and ``swarm_sse.py`` to avoid duplication.
"""

from __future__ import annotations

import json
import traceback
from datetime import UTC, datetime
from typing import Any

__all__ = ["sse_frame", "ApprovalEventBridge"]


def sse_frame(event: str, data: str | dict[str, Any] | list[Any]) -> str:
    """Format a single SSE frame.

    Args:
        event: The event type name.
        data: Payload -- dict/list is JSON-serialized, str is used as-is.

    Returns:
        Formatted SSE string: ``event: <type>\ndata: <json>\n\n``
    """
    if isinstance(data, (dict, list)):
        data = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {data}\n\n"


class ApprovalEventBridge:
    """Stream approval progress events to client with full transparency.
    
    Provides structured events for every stage of the approval process,
    ensuring users always know what's happening and can debug issues.
    """

    @staticmethod
    def create_approval_started_event(
        thread_id: str,
        tool: str,
        scope: str,
        request_id: str = "",
    ) -> dict[str, Any]:
        """Approval process has started."""
        return {
            "type": "approval_started",
            "data": {
                "tool": tool,
                "scope": scope,
                "status": "starting",
                "thread_id": thread_id,
                "request_id": request_id or thread_id[:12],
                "timestamp": datetime.now(UTC).isoformat(),
            },
        }

    @staticmethod
    def create_approval_progress_event(
        thread_id: str,
        message: str,
        step: str,
        details: dict[str, Any] | None = None,
        progress_percent: int | None = None,
    ) -> dict[str, Any]:
        """Approval is in progress with a specific step."""
        return {
            "type": "approval_progress",
            "data": {
                "step": step,
                "message": message,
                "details": details or {},
                "progress_percent": progress_percent,
                "thread_id": thread_id,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        }

    @staticmethod
    def create_approval_resuming_event(
        thread_id: str,
        tool: str,
        scope: str,
    ) -> dict[str, Any]:
        """Graph is being resumed after approval."""
        return {
            "type": "approval_resuming",
            "data": {
                "tool": tool,
                "scope": scope,
                # English fallback for non-UI clients; chat UI localizes via step.
                "message": "Resuming graph execution...",
                "step": "resuming",
                "thread_id": thread_id,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        }

    @staticmethod
    def create_approval_supervisor_event(
        thread_id: str,
        iteration: int,
        node: str,
        thought: str = "",
    ) -> dict[str, Any]:
        """Supervisor is processing an iteration."""
        return {
            "type": "approval_supervisor_iteration",
            "data": {
                "iteration": iteration,
                "node": node,
                "thought": thought[:500] if thought else "",
                "thread_id": thread_id,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        }

    @staticmethod
    def create_approval_tool_event(
        thread_id: str,
        tool: str,
        status: str,  # "starting", "running", "completed", "failed"
        result: str = "",
    ) -> dict[str, Any]:
        """Tool execution status during approval."""
        return {
            "type": "approval_tool_executing",
            "data": {
                "tool": tool,
                "status": status,
                "result": result[:1000] if result else "",
                "thread_id": thread_id,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        }

    @staticmethod
    def create_approval_complete_event(
        thread_id: str,
        tool: str,
        scope: str,
        duration_ms: float,
    ) -> dict[str, Any]:
        """Approval completed successfully."""
        return {
            "type": "approval_complete",
            "data": {
                "tool": tool,
                "scope": scope,
                "message": "Approval completed successfully!",
                "step": "complete",
                "thread_id": thread_id,
                "duration_ms": round(duration_ms, 0),
                "timestamp": datetime.now(UTC).isoformat(),
            },
        }

    @staticmethod
    def create_approval_error_event(
        thread_id: str,
        error: str,
        code: str,
        traceback_str: str | None = None,
        tool: str = "",
        scope: str = "",
    ) -> dict[str, Any]:
        """Approval failed with detailed error information."""
        return {
            "type": "approval_error",
            "data": {
                "error": error,
                "code": code,
                "tool": tool,
                "scope": scope,
                "traceback": traceback_str[:2000] if traceback_str else "",
                "thread_id": thread_id,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        }

    @staticmethod
    def create_approval_timeout_event(
        thread_id: str,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        """Approval timed out."""
        return {
            "type": "approval_timeout",
            "data": {
                "error": f"Approval timed out after {timeout_seconds} seconds",
                "code": "APPROVAL_TIMEOUT",
                "timeout_seconds": timeout_seconds,
                "thread_id": thread_id,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        }

    @staticmethod
    def to_sse_frame(event_dict: dict[str, Any]) -> str:
        """Convert an approval event dict to SSE frame format."""
        return sse_frame(event_dict["type"], event_dict["data"])
