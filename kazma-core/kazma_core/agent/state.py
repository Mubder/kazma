"""Supervisor State — TypedDict definitions for the LangGraph orchestration layer.

This is the canonical state schema that flows through every node in the
Supervisor graph.  It extends the base AgentState with orchestration-specific
fields (iteration count, current node routing, parallel tool tracking,
and structured error state).

Design principles:
  - Every field has a sensible default so partial updates are safe.
  - Messages stay in OpenAI format for direct pass-through to LiteLLM.
  - tool_calls_pending / tool_calls_done track the parallel fan-out/fan-in
    pattern used by the Tool Worker node.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, TypedDict

# ── Node names (used in conditional routing) ────────────────────────────


class NodeName(StrEnum):
    """Canonical names for every node in the Supervisor graph."""

    SUPERVISOR = "supervisor"
    TOOL_WORKER = "tool_worker"
    RESPOND = "respond"
    COMPACT = "compact"


# ── Pending tool call (fan-out item) ───────────────────────────────────


class PendingToolCall(TypedDict):
    """A single tool call queued for execution by the Tool Worker."""

    id: str
    name: str
    arguments: dict[str, Any]


# ── Completed tool result (fan-in item) ────────────────────────────────


class ToolResult(TypedDict, total=False):
    """Result of a single tool execution."""

    tool_call_id: str
    name: str
    content: str
    is_error: bool
    duration_ms: float


# ── Supervisor State ────────────────────────────────────────────────────


class SupervisorState(TypedDict, total=False):
    """Core state that flows through the Supervisor graph.

    Fields marked ``total=False`` are optional — partial dicts are legal
    return values from node functions (LangGraph merges them).
    """

    # ── Conversation ────────────────────────────────────────────────
    messages: list[dict[str, Any]]
    """Full conversation in OpenAI message format."""

    # ── Orchestration routing ───────────────────────────────────────
    next_node: str
    """Supervisor's routing decision.  One of NodeName values or 'end'."""

    iteration: int
    """Current ReAct iteration count (0-indexed)."""

    max_iterations: int
    """Hard ceiling on iterations before forced respond."""

    # ── Tool fan-out / fan-in ───────────────────────────────────────
    tool_calls_pending: list[PendingToolCall]
    """Tool calls the Supervisor decided to execute this iteration."""

    tool_calls_done: list[ToolResult]
    """Completed tool results from the current (or last) iteration."""

    tool_results: dict[str, Any]
    """Historical tool results keyed by tool_call_id (cumulative)."""

    # ── Compaction ──────────────────────────────────────────────────
    needs_compaction: bool
    """Set by the Supervisor when the ContextAuthority signals 80% usage."""

    # ── Observability ───────────────────────────────────────────────
    last_model: str
    """Model used in the most recent LLM call."""

    last_tokens: int
    """Total tokens from the most recent LLM call."""

    last_cost_usd: float
    """Dollar cost of the most recent LLM call."""

    # ── Identity / persistence ──────────────────────────────────────
    thread_id: str
    """Stable conversation thread ID (persists across checkpoints)."""

    last_checkpoint_id: str
    """Most recent checkpoint UUID (avoids LangGraph reserved name)."""

    created_at: str
    """ISO-8601 UTC timestamp of state creation."""


# ── Factory ─────────────────────────────────────────────────────────────


def initial_supervisor_state(
    *,
    thread_id: str | None = None,
    max_iterations: int = 10,
) -> SupervisorState:
    """Create a fresh SupervisorState with sensible defaults.

    Args:
        thread_id: Stable conversation thread ID.  Auto-generated if omitted.
        max_iterations: ReAct loop ceiling (default 10).
    """
    now = datetime.now(UTC).isoformat()
    return SupervisorState(
        messages=[],
        next_node=NodeName.SUPERVISOR,
        iteration=0,
        max_iterations=max_iterations,
        tool_calls_pending=[],
        tool_calls_done=[],
        tool_results={},
        needs_compaction=False,
        last_model="",
        last_tokens=0,
        last_cost_usd=0.0,
        thread_id=thread_id or str(uuid.uuid4()),
        last_checkpoint_id=str(uuid.uuid4()),
        created_at=now,
    )
