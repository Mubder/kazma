"""Graph Builder — Compiles the Supervisor LangGraph StateMachine.

Graph topology
══════════════

    START → SUPERVISOR ⇄ TOOL_WORKER
                │
                ▼
             RESPOND → END

Node implementations live in focused modules (extracted 2026-08-25,
behavior-preserving split — industry stack part 2):

  * ``graph_helpers.py``     — truncate / sanitize / prune / HITL card / RAG
  * ``graph_supervisor.py``  — supervisor_node + failover
  * ``graph_tool_worker.py`` — tool_worker_node + commitment gate
  * ``graph_respond.py``     — respond_node

This module keeps the public import path and compiles the graph.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, StateGraph

from kazma_core.agent.graph_helpers import (
    TOOL_RESULT_MAX_CHARS,
    TOOL_RESULT_RESEARCH_MAX_CHARS,
    _ensure_personality,
    _format_retrieved_memories,
    _memory_explain_cv,
    _rag_top_k,
    _resolve_tool_timeout,
    is_unusable_assistant_content,
    prune_messages_if_exceeding_cap,
    sanitize_tool_chains,
    truncate_tool_result,
)
from kazma_core.agent.plan_fence import (
    is_plan_only,
    normalize_plan_fence,
    pick_user_facing_text,
)
from kazma_core.agent.graph_respond import respond_node
from kazma_core.agent.graph_supervisor import supervisor_node
from kazma_core.agent.graph_tool_worker import (
    _commitment_resolve_gate,
    tool_worker_node,
)
from kazma_core.agent.state import NodeName, SupervisorState
from kazma_core.llm_provider import LLMProvider
from kazma_core.time_travel import SnapshotRecorder

__all__ = [
    "TOOL_RESULT_MAX_CHARS",
    "build_supervisor_graph",
    "is_plan_only",
    "is_unusable_assistant_content",
    "normalize_plan_fence",
    "pick_user_facing_text",
    "respond_node",
    "sanitize_tool_chains",
    "supervisor_node",
    "tool_worker_node",
    "truncate_tool_result",
]

logger = logging.getLogger(__name__)


def build_supervisor_graph(
    *,
    llm: LLMProvider,
    system_prompt: str,
    tool_definitions: list[dict[str, Any]],
    tool_executor: Any,
    cost_breaker: Any,
    authority: Any,
    tracer: Any,
    checkpointer: AsyncSqliteSaver | None = None,
    hitl_config: dict[str, Any] | None = None,
    model_router: Any | None = None,
    personality_prompt: str | None = None,
    snapshot_recorder: SnapshotRecorder | None = None,
) -> Any:
    """Build and compile the Supervisor StateGraph.

    Args:
        llm: Configured LLMProvider for model calls.
        system_prompt: System prompt injected on first message.
        tool_definitions: OpenAI-format tool schemas.
        tool_executor: Object with async execute(name, args) -> dict.
        cost_breaker: CostCircuitBreaker instance.
        authority: ContextAuthority for 80% compaction.
        tracer: KazmaTracer for observability.
        checkpointer: Optional AsyncSqliteSaver for durable checkpointing.
        hitl_config: Optional HITL config from kazma.yaml safety.hitl.
            If provided, danger-tier tools trigger interrupt() before execution.
        model_router: Optional ModelRouter for multi-model routing.
            If provided, classifies messages and selects the optimal model.

    Returns:
        Compiled LangGraph app (invoke / ainvoke ready).
    """

    # ── Wrap node functions with their dependencies (closures) ──────

    def _resolve_personality_prompt() -> str | None:
        """Resolve the active personality prompt dynamically.

        Checks the runtime override first (set by /personality command),
        then falls back to the personality_prompt passed at build time.
        This is called on every supervisor iteration so runtime switches
        take effect immediately without rebuilding the graph.
        """
        from kazma_core.personalities import PERSONALITIES, get_runtime_personality

        runtime = get_runtime_personality()
        if runtime is not None:
            return PERSONALITIES[runtime]["system_prompt"]
        if personality_prompt is not None:
            return personality_prompt
        return None

    async def _supervisor(state: SupervisorState) -> dict[str, Any]:
        # Watchdog heartbeat — lets the supervised envelope distinguish
        # "working" from "wedged" (no-op cost, never raises).
        try:
            from kazma_core.agent.supervisor_watchdog import record_heartbeat

            record_heartbeat(str(state.get("thread_id", "")))
        except Exception:
            pass
        # Clear prior turn's explain so we don't leak across concurrent tasks
        try:
            _memory_explain_cv.set(None)
        except Exception:
            pass
        result = await supervisor_node(
            state,
            llm=llm,
            system_prompt=system_prompt,
            tool_definitions=tool_definitions,
            tool_executor=tool_executor,
            cost_breaker=cost_breaker,
            authority=authority,
            tracer=tracer,
            model_router=model_router,
            personality_prompt=_resolve_personality_prompt(),
        )
        # Attach memory explain once (iteration 0 inject) for SSE/WS clients
        try:
            explain_payload = _memory_explain_cv.get()
            if explain_payload:
                result["memory_explain"] = explain_payload
                _memory_explain_cv.set(None)
        except Exception:
            pass
        # ── Time Travel: capture snapshot after supervisor iteration ──
        if snapshot_recorder is not None and snapshot_recorder.enabled:
            # Merge current state with result to get the full picture
            merged = {**state, **result}
            record = snapshot_recorder.capture(merged)
            if record is not None:
                result["snapshot_id"] = record.id
                result["snapshot_iteration"] = merged.get("iteration", 0)
        return result

    async def _tool_worker(state: SupervisorState) -> dict[str, Any]:
        try:
            from kazma_core.agent.supervisor_watchdog import record_heartbeat

            record_heartbeat(str(state.get("thread_id", "")))
        except Exception:
            pass
        return await tool_worker_node(state, tool_executor=tool_executor, tracer=tracer, hitl_config=hitl_config)

    async def _respond(state: SupervisorState) -> dict[str, Any]:
        return await respond_node(state, llm=llm)

    # ── Routing function ────────────────────────────────────────────
    def _route(state: SupervisorState) -> str:
        """Route from Supervisor based on next_node field."""
        next_node = state.get("next_node", NodeName.RESPOND)
        iteration = state.get("iteration", 0)
        max_iter = state.get("max_iterations", 15)

        # Force respond on max iterations — unless mission mode still has
        # hard-wall budget left (wave extend / high mission ceiling).
        if iteration >= max_iter:
            _allow_mission_continue = False
            try:
                from kazma_core.agent.long_task import (
                    is_mission_mode,
                    mission_hard_rounds,
                )

                _tid = str(state.get("thread_id") or "") or None
                if _tid and is_mission_mode(_tid):
                    _used = int(state.get("mission_rounds_used") or 0) + int(iteration or 0)
                    _hard = int(
                        state.get("mission_hard_rounds") or mission_hard_rounds()
                    )
                    # Mission max_iterations is normally the hard wall; if a
                    # soft wave is smaller, still allow tool_worker under wall.
                    if _used < _hard and next_node == NodeName.TOOL_WORKER:
                        _allow_mission_continue = True
                    elif _used < _hard and max_iter < _hard:
                        # Soft wave hit but hard wall remains — let tools run;
                        # supervisor will wave-extend on the next entry.
                        if next_node == NodeName.TOOL_WORKER:
                            _allow_mission_continue = True
            except Exception:
                logger.debug("[Router] mission check failed", exc_info=True)

            if not _allow_mission_continue:
                logger.warning(
                    "[Router] Max iterations (%d) hit — forcing respond", max_iter
                )
                return NodeName.RESPOND
            logger.info(
                "[Router] Mission under hard wall — allowing %s past soft max %d",
                next_node,
                max_iter,
            )

        if next_node == NodeName.SUPERVISOR:
            # Auto-continue path: supervisor returned a continuation message
            # (next_node=SUPERVISOR) — loop straight back into the supervisor
            # instead of dead-ending into respond. Must be below the max-iter
            # gate so an exhausted budget still forces respond.
            return NodeName.SUPERVISOR
        if next_node == NodeName.TOOL_WORKER:
            return NodeName.TOOL_WORKER
        return NodeName.RESPOND

    def _route_from_worker(state: SupervisorState) -> str:
        """Route from Tool Worker — no mid-turn summarize; always back to supervisor.

        Oversized context is handled inside supervisor via deterministic trim.
        """
        next_n = state.get("next_node")
        if next_n == NodeName.RESPOND or state.get("circuit_breaker_tripped") or (state.get("consecutive_tool_failures", 0) >= 3):
            return NodeName.RESPOND
        return state.get("next_node", NodeName.SUPERVISOR)

    # ── Build the graph ─────────────────────────────────────────────
    graph = StateGraph(SupervisorState)

    graph.add_node(NodeName.SUPERVISOR, _supervisor)
    graph.add_node(NodeName.TOOL_WORKER, _tool_worker)
    graph.add_node(NodeName.RESPOND, _respond)

    # Entry: START → supervisor (no LLM summarize path)
    graph.set_entry_point(NodeName.SUPERVISOR)

    # Supervisor → {supervisor (auto-continue), tool_worker, respond}
    graph.add_conditional_edges(
        NodeName.SUPERVISOR,
        _route,
        {
            NodeName.SUPERVISOR: NodeName.SUPERVISOR,
            NodeName.TOOL_WORKER: NodeName.TOOL_WORKER,
            NodeName.RESPOND: NodeName.RESPOND,
        },
    )

    # Tool Worker → Supervisor / Respond
    graph.add_conditional_edges(
        NodeName.TOOL_WORKER,
        _route_from_worker,
        {
            NodeName.SUPERVISOR: NodeName.SUPERVISOR,
            NodeName.RESPOND: NodeName.RESPOND,
        },
    )

    # Respond → END
    graph.add_edge(NodeName.RESPOND, END)

    # ── Compile ─────────────────────────────────────────────────────
    if checkpointer is not None:
        return graph.compile(checkpointer=checkpointer)
    return graph.compile()

