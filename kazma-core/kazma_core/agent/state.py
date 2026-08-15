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

__all__ = [
    "NodeName",
    "PendingToolCall",
    "SupervisorState",
    "TaskStatus",
    "ToolResult",
    "initial_supervisor_state",
]

# ── Node names (used in conditional routing) ────────────────────────────


class NodeName(StrEnum):
    """Canonical names for every node in the Supervisor graph.

    Mid-turn LLM summarization (CHECK_SATURATION / SUMMARIZE) was removed —
    the graph uses Explicit State + Deterministic Trimming instead.
    """

    SUPERVISOR = "supervisor"
    TOOL_WORKER = "tool_worker"
    RESPOND = "respond"


class TaskStatus(StrEnum):
    """Lifecycle of the open dialog focus / multi-step goal.

    Used to soft-reset continuity (auto_continue, session_boost RAG) when the
    user pivots subjects without requiring a full ``/reset``.
    """

    IDLE = "idle"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SUPERSEDED = "superseded"
    # /abort: the user explicitly cancelled and abandoned the running task.
    # Treated like SUPERSEDED for continuity suppression, but the abort
    # marker message tells the model NOT to resume unless re-asked.
    ABANDONED = "abandoned"


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
    # Optional structured outcome honored by tool_loop_breaker.classify_tool_result
    # (ok | empty | policy | user_deny | transient | hard | terminal). "terminal"
    # marks a control-plane turn-ender (e.g. unresolved commitment clarify) —
    # not tool death; forces RESPOND and does not credit the hard-failure breaker.
    outcome: str


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

    _last_finish_reason: str
    """finish_reason of the most recent LLM call. ``"length"`` means the
    provider truncated the completion at max_tokens — tool-call JSON may be
    severed mid-string; the tool worker uses this to emit a truncation-aware
    corrective error instead of executing garbage arguments."""

    # ── Identity / persistence ──────────────────────────────────────
    thread_id: str
    """Stable conversation thread ID (persists across checkpoints)."""

    tenant_id: str
    """Tenant/sender identity for multi-tenant memory isolation.

    Defaults to ``"default"``. Set to the platform sender_id (e.g.
    ``"telegram:12345"``, ``"web:<session>"``) so each user's beliefs,
    episodes, and entities are isolated in the V2 cognitive engine.
    """

    # ── Time Travel ─────────────────────────────────────────────────
    snapshot_id: str
    """UUID of the most recent Time Travel snapshot captured for this state."""

    snapshot_iteration: int
    """Iteration number at which the last Time Travel snapshot was captured."""

    created_at: str
    """ISO-8601 UTC timestamp of state creation."""

    # ── Circuit Breaker ─────────────────────────────────────────────
    consecutive_tool_failures: int
    """Counter for consecutive empty/failed results from tools."""

    circuit_breaker_tripped: bool
    """Flag indicating whether the tool execution circuit breaker has tripped."""

    tool_signatures: list[str]
    """Recent (tool_name, canonical-args) signatures for semantic-loop
    detection (see ``tool_loop_breaker.detect_stagnation``). Capped window;
    used to catch repeated identical calls that never produce hard errors
    (no-op edits, policy-denied retries)."""

    auto_continue: bool
    """Flag indicating whether the supervisor should auto-continue turns for multi-step goals."""

    task_status: str
    """Dialog focus lifecycle — see :class:`TaskStatus` (idle/in_progress/completed/superseded)."""

    task_goal_summary: str
    """Short summary of the open multi-step goal (for drift checks / logging)."""

    intent_mode: str
    """Last classified turn intent: continue|store|cleanup|multi_part|shift|normal."""

    # ── Explicit Working Memory (immutable for the active turn) ─────
    active_goal: str
    """User goal for *this* turn — set at iteration 0, never overwritten by tools/trim."""

    active_attachments: list[dict[str, Any]]
    """Attachment descriptors (id/filename/path/kind) bound at iteration 0."""

    hard_constraints: list[str]
    """Structural turn constraints (e.g. audit_only, no_code_change, read_only)."""

    scratchpad: dict[str, str]
    """Typed findings scratchpad — survives deterministic trim; updated via update_scratchpad."""

    # ── Turn failure ────────────────────────────────────────────────
    turn_failed: bool
    """Set when the supervisor's LLM call failed (after retries) and could not produce a real answer.

    When True, ``respond_node`` MUST NOT synthesize a plausible-looking
    final answer over the failure — it must surface the honest error so the
    user knows the turn broke rather than mistaking a fabricated answer for
    a real result (the "model stopped thinking" symptom).
    """

    force_synthesis: bool
    """When True, ``respond_node`` MUST run a final synthesis LLM call.

    Set by the supervisor when the model returned empty content or unusable
    leaked tool-markup (DSML). Must be a declared state key — undeclared
    fields are dropped by LangGraph and never reach respond_node
    (2026-08-03 empty-reply regression).
    """

    _research_depth_nudged: bool
    """One-shot guard so the research-depth "more sources" nudge fires once per
    turn, not every tool-worker iteration. Must be declared (undeclared keys
    are dropped by LangGraph) — previously this was always read as False, so
    the nudge spammed the model every iteration (audit finding)."""

    _research_pipeline_nudged: bool
    """One-shot guard for the R4 deep-research pipeline nudge (see above)."""

    _post_turn_memory: dict[str, Any]
    """Signal that post-turn memory work is pending for this thread.

    Written by ``respond_node`` (session_id/turn/tenant_id) and consumed by
    the gateway handler AFTER the graph reaches terminal state. Must be a
    declared state key — undeclared fields are dropped by LangGraph and the
    gateway's ``result_state.get("_post_turn_memory")`` would always see
    None, silently disabling post-turn memory (episode mirror + belief
    extraction + micro-consolidation).
    """

    intent_route: str
    """Intent engine route for this turn: execute | constrain | loop | ""."""

    intent_acts: list[dict]
    """Serialized IntentAct dicts from the intent engine."""

    intent_reason: str
    """Human-readable reason for the intent engine's route decision."""

    mission_rounds_used: int
    """Cumulative tool rounds already consumed in mission mode (wave tracking)."""

    mission_hard_rounds: int
    """Mission safety wall (from long_task / env). 0 = not in mission."""


# ── Factory ─────────────────────────────────────────────────────────────


def initial_supervisor_state(
    *,
    thread_id: str | None = None,
    max_iterations: int | None = None,
    tenant_id: str = "default",
) -> SupervisorState:
    """Create a fresh SupervisorState with sensible defaults.

    Args:
        thread_id: Stable conversation thread ID.  Auto-generated if omitted.
        max_iterations: ReAct loop ceiling. If None, reads from ConfigStore
            key ``agent.max_iterations`` (default 15). Settable via the
            Web UI Settings page. Mission mode may go well above 100.
    """
    # Budgets: long-task mode (thread) or Settings max_iterations, with
    # recursion aligned via long_task.derive_recursion_limit (callers also
    # use resolve_turn_budgets for LangGraph config).
    _mission_hard = 0
    _mode = "budget"
    if max_iterations is None:
        try:
            from kazma_core.agent.long_task import resolve_turn_budgets

            _budgets = resolve_turn_budgets(thread_id)
            max_iterations = _budgets["max_iterations"]
            _mode = str(_budgets.get("mode") or "budget")
            _mission_hard = int(_budgets.get("mission_hard_rounds") or 0)
        except Exception:
            try:
                from kazma_core.config_store import get_config_store

                max_iterations = int(get_config_store().get("agent.max_iterations", 15))
            except Exception:
                max_iterations = 15
    try:
        _cap = 2000 if _mode == "mission" else 100
        max_iterations = max(5, min(_cap, int(max_iterations)))
    except (TypeError, ValueError):
        max_iterations = 15
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
        tenant_id=tenant_id,
        snapshot_id="",
        snapshot_iteration=-1,
        created_at=now,
        consecutive_tool_failures=0,
        circuit_breaker_tripped=False,
        tool_signatures=[],
        auto_continue=False,
        task_status=TaskStatus.IDLE,
        task_goal_summary="",
        intent_mode="normal",
        active_goal="",
        active_attachments=[],
        hard_constraints=[],
        scratchpad={},
        force_synthesis=False,
        _research_depth_nudged=False,
        _research_pipeline_nudged=False,
        turn_failed=False,
        mission_rounds_used=0,
        mission_hard_rounds=_mission_hard if _mode == "mission" else 0,
    )
