"""Graph Builder — Compiles the Supervisor LangGraph StateMachine.

Graph topology
══════════════

    ┌────────────┐
    │  SUPERVISOR │  ← entry point
    └──┬───┬───┬─┘
       │   │   │
       │   │   └──────────────────────────────┐
       │   │                                  │
       ▼   ▼                                  ▼
    ┌────────────┐                     ┌────────────┐
    │    COMPACT  │                     │   RESPOND  │ → END
    └─────┬──────┘                     └────────────┘
          │                                   ▲
          └────────────► SUPERVISOR ──────────┘
                              │
                      ┌───────▼───────┐
                      │  TOOL_WORKER  │
                      └───────┬───────┘
                              │
                        SUPERVISOR

The Supervisor is the decision-maker.  On each iteration it:
  1. Calls the LLM with the current messages + tool schemas.
  2. If the LLM returns tool_calls → routes to TOOL_WORKER.
  3. If context is ≥ 80% full → routes to COMPACT.
  4. If the LLM returns a final text response → routes to RESPOND.
  5. If max_iterations is hit → forced RESPOND.

Every node is fully async.  The graph compiles with an
AsyncSqliteSaver checkpointer for SIGKILL-safe durability.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from kazma_core.agent.state import (
    NodeName,
    PendingToolCall,
    SupervisorState,
    ToolResult,
)
from kazma_core.llm_provider import LLMProvider, LLMConfig

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# Node functions
# ══════════════════════════════════════════════════════════════════════════


async def supervisor_node(
    state: SupervisorState,
    *,
    llm: LLMProvider,
    system_prompt: str,
    tool_definitions: list[dict[str, Any]],
    tool_executor: Any,  # LocalToolRegistry or ToolRegistry
    cost_breaker: Any,   # CostCircuitBreaker
    authority: Any,       # ContextAuthority
    tracer: Any,          # KazmaTracer
) -> dict[str, Any]:
    """Supervisor node — the brain of the ReAct loop.

    Responsibilities:
      1. Enforce cost circuit breaker.
      2. Check & trigger 80% context compaction.
      3. Call the LLM with conversation + tool schemas.
      4. Route: tool_calls → TOOL_WORKER, text → RESPOND.
    """
    iteration = state.get("iteration", 0)
    messages = list(state.get("messages", []))

    logger.info("[Supervisor] iteration=%d messages=%d", iteration, len(messages))

    # ── Cost breaker gate ──────────────────────────────────────────
    if cost_breaker.should_halt():
        logger.warning("[Supervisor] Cost breaker tripped — forcing respond")
        return {
            "next_node": NodeName.RESPOND,
            "messages": messages + [{
                "role": "assistant",
                "content": "⚠️ ميزانية الجلسة انتهت. أعد التشغيل أو اتصل بالمسؤول.",
            }],
        }

    # ── 80% context compaction check ───────────────────────────────
    state_for_check = {**state, "messages": messages}
    compacted_state = await authority.check_and_enforce(state_for_check)
    if compacted_state is not state_for_check:
        logger.info("[Supervisor] Context compacted — restarting with fresh context")
        return {
            "messages": compacted_state.get("messages", []),
            "needs_compaction": False,
            "next_node": NodeName.SUPERVISOR,  # re-enter supervisor
        }

    # ── Ensure system prompt is present ────────────────────────────
    if not any(m.get("role") == "system" for m in messages):
        messages.insert(0, {"role": "system", "content": system_prompt})

    # ── LLM call ──────────────────────────────────────────────────
    start = time.monotonic()
    try:
        response = await llm.chat(
            messages=messages,
            tools=tool_definitions if tool_definitions else None,
        )
    except Exception as exc:
        logger.error("[Supervisor] LLM call failed: %s", exc)
        error_content = f"عذراً، حدث خطأ في الاتصال: {exc}"
        return {
            "next_node": NodeName.RESPOND,
            "messages": messages + [{"role": "assistant", "content": error_content}],
        }

    duration_ms = (time.monotonic() - start) * 1000
    cost_breaker.record_cost(response.cost_usd)

    # Trace
    tracer.trace_llm_call(
        model=response.model,
        prompt=str(messages[-1].get("content", ""))[:500],
        response=response.content[:500],
        tokens=response.usage.get("total_tokens", 0),
        cost=response.cost_usd,
        duration_ms=duration_ms,
    )

    logger.info(
        "[Supervisor] LLM responded: model=%s tokens=%d cost=$%.4f "
        "duration=%.0fms tool_calls=%d",
        response.model,
        response.usage.get("total_tokens", 0),
        response.cost_usd,
        duration_ms,
        len(response.tool_calls),
    )

    # ── Route decision ─────────────────────────────────────────────
    if not response.tool_calls:
        # Pure text response → RESPOND
        assistant_msg = {"role": "assistant", "content": response.content}
        return {
            "messages": messages + [assistant_msg],
            "next_node": NodeName.RESPOND,
            "last_model": response.model,
            "last_tokens": response.usage.get("total_tokens", 0),
            "last_cost_usd": response.cost_usd,
        }

    # Tool calls → build pending list and route to TOOL_WORKER
    assistant_msg: dict[str, Any] = {
        "role": "assistant",
        "content": response.content or None,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments),
                },
            }
            for tc in response.tool_calls
        ],
    }

    pending = [
        PendingToolCall(id=tc.id, name=tc.name, arguments=tc.arguments)
        for tc in response.tool_calls
    ]

    return {
        "messages": messages + [assistant_msg],
        "tool_calls_pending": pending,
        "tool_calls_done": [],  # reset for this iteration
        "next_node": NodeName.TOOL_WORKER,
        "iteration": iteration,
        "last_model": response.model,
        "last_tokens": response.usage.get("total_tokens", 0),
        "last_cost_usd": response.cost_usd,
    }


async def tool_worker_node(
    state: SupervisorState,
    *,
    tool_executor: Any,
    tracer: Any,
) -> dict[str, Any]:
    """Tool Worker node — executes pending tool calls (parallel fan-out).

    All pending tool calls are dispatched concurrently via asyncio.gather.
    Results are collected and appended as tool-role messages.
    """
    import asyncio

    pending = state.get("tool_calls_pending", [])
    if not pending:
        logger.warning("[ToolWorker] No pending tool calls — routing back")
        return {"next_node": NodeName.SUPERVISOR}

    logger.info("[ToolWorker] Executing %d tool calls", len(pending))

    async def _exec_one(tc: PendingToolCall) -> ToolResult:
        start = time.monotonic()
        result = await tool_executor.execute(tc["name"], tc["arguments"])
        duration_ms = (time.monotonic() - start) * 1000

        tracer.trace_tool_execution(
            tool_name=tc["name"],
            input_data=tc["arguments"],
            output_data=result,
            duration_ms=duration_ms,
            success=not result.get("is_error", False),
        )

        logger.info(
            "[ToolWorker] %s → %.0fms (error=%s)",
            tc["name"], duration_ms, result.get("is_error", False),
        )

        return ToolResult(
            tool_call_id=tc["id"],
            name=tc["name"],
            content=result.get("content", ""),
            is_error=result.get("is_error", False),
            duration_ms=duration_ms,
        )

    results = await asyncio.gather(*(_exec_one(tc) for tc in pending))

    # Build tool-role messages for the conversation
    messages = list(state.get("messages", []))
    tool_messages: list[dict[str, Any]] = []
    for tr in results:
        tool_messages.append({
            "role": "tool",
            "tool_call_id": tr["tool_call_id"],
            "content": tr["content"],
        })

    # Merge into cumulative tool_results
    cumulative = dict(state.get("tool_results", {}))
    for tr in results:
        cumulative[tr["tool_call_id"]] = tr

    return {
        "messages": messages + tool_messages,
        "tool_calls_pending": [],  # all consumed
        "tool_calls_done": list(results),
        "tool_results": cumulative,
        "next_node": NodeName.SUPERVISOR,  # loop back
    }


async def respond_node(state: SupervisorState) -> dict[str, Any]:
    """Respond node — finalizes the turn.

    Extracts the last assistant message as the response and increments
    the iteration counter.
    """
    messages = state.get("messages", [])
    iteration = state.get("iteration", 0) + 1

    logger.info(
        "[Respond] Finalizing turn (iteration=%d, messages=%d)",
        iteration, len(messages),
    )

    return {
        "messages": messages,
        "iteration": iteration,
        "tool_calls_pending": [],
        "tool_calls_done": [],
        "next_node": "end",
    }


async def compact_node(
    state: SupervisorState,
    *,
    authority: Any,
) -> dict[str, Any]:
    """Compact node — triggers context compaction and loops back.

    This is a dedicated node (rather than inline in supervisor) so the
    compaction event gets its own checkpoint and trace.
    """
    logger.info("[Compact] Triggering context compaction")

    compacted = await authority.check_and_enforce({**state, "messages": state.get("messages", [])})

    return {
        "messages": compacted.get("messages", []),
        "needs_compaction": False,
        "next_node": NodeName.SUPERVISOR,
    }


# ══════════════════════════════════════════════════════════════════════════
# Graph builder
# ══════════════════════════════════════════════════════════════════════════


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

    Returns:
        Compiled LangGraph app (invoke / ainvoke ready).
    """

    # ── Wrap node functions with their dependencies (closures) ──────
    async def _supervisor(state: SupervisorState) -> dict[str, Any]:
        return await supervisor_node(
            state,
            llm=llm,
            system_prompt=system_prompt,
            tool_definitions=tool_definitions,
            tool_executor=tool_executor,
            cost_breaker=cost_breaker,
            authority=authority,
            tracer=tracer,
        )

    async def _tool_worker(state: SupervisorState) -> dict[str, Any]:
        return await tool_worker_node(state, tool_executor=tool_executor, tracer=tracer)

    async def _respond(state: SupervisorState) -> dict[str, Any]:
        return await respond_node(state)

    async def _compact(state: SupervisorState) -> dict[str, Any]:
        return await compact_node(state, authority=authority)

    # ── Routing function ────────────────────────────────────────────
    def _route(state: SupervisorState) -> str:
        """Route from Supervisor based on next_node field."""
        next_node = state.get("next_node", NodeName.RESPOND)
        iteration = state.get("iteration", 0)
        max_iter = state.get("max_iterations", 10)

        # Force respond on max iterations
        if iteration >= max_iter:
            logger.warning("[Router] Max iterations (%d) hit — forcing respond", max_iter)
            return NodeName.RESPOND

        if next_node == NodeName.TOOL_WORKER:
            return NodeName.TOOL_WORKER
        if next_node == NodeName.COMPACT:
            return NodeName.COMPACT
        return NodeName.RESPOND

    def _route_from_worker(state: SupervisorState) -> str:
        """Route from Tool Worker — always back to Supervisor."""
        return NodeName.SUPERVISOR

    def _route_from_compact(state: SupervisorState) -> str:
        """Route from Compact — always back to Supervisor."""
        return NodeName.SUPERVISOR

    # ── Build the graph ─────────────────────────────────────────────
    graph = StateGraph(SupervisorState)

    graph.add_node(NodeName.SUPERVISOR, _supervisor)
    graph.add_node(NodeName.TOOL_WORKER, _tool_worker)
    graph.add_node(NodeName.RESPOND, _respond)
    graph.add_node(NodeName.COMPACT, _compact)

    graph.set_entry_point(NodeName.SUPERVISOR)

    # Supervisor → {tool_worker, compact, respond}
    graph.add_conditional_edges(
        NodeName.SUPERVISOR,
        _route,
        {
            NodeName.TOOL_WORKER: NodeName.TOOL_WORKER,
            NodeName.COMPACT: NodeName.COMPACT,
            NodeName.RESPOND: NodeName.RESPOND,
        },
    )

    # Tool Worker → Supervisor (loop back)
    graph.add_conditional_edges(
        NodeName.TOOL_WORKER,
        _route_from_worker,
        {NodeName.SUPERVISOR: NodeName.SUPERVISOR},
    )

    # Compact → Supervisor (loop back)
    graph.add_conditional_edges(
        NodeName.COMPACT,
        _route_from_compact,
        {NodeName.SUPERVISOR: NodeName.SUPERVISOR},
    )

    # Respond → END
    graph.add_edge(NodeName.RESPOND, END)

    # ── Compile ─────────────────────────────────────────────────────
    if checkpointer is not None:
        return graph.compile(checkpointer=checkpointer)
    return graph.compile()


# ══════════════════════════════════════════════════════════════════════════
# Factory — fully wired, ready-to-invoke
# ══════════════════════════════════════════════════════════════════════════


async def create_supervisor_app(
    *,
    config: dict[str, Any] | None = None,
    llm: LLMProvider | None = None,
    tool_executor: Any = None,
    mcp_manager: Any = None,
    db_path: str = "kazma-data/checkpoints.db",
) -> tuple[Any, AsyncSqliteSaver]:
    """Create a fully-wired Supervisor graph with SQLite checkpointer.

    This is the high-level entry point.  Returns (compiled_graph, checkpointer)
    so the caller can invoke the graph and close the checkpointer on shutdown.

    Args:
        config: Raw kazma.yaml dict (loads from disk if None).
        llm: Pre-built LLMProvider (created from config if None).
        tool_executor: Tool registry (created with builtins if None).
        mcp_manager: Optional AsyncMCPManager for MCP tools.
            If provided and config has ``mcp.servers``, the manager is
            connected and a UnifiedToolExecutor wraps both backends.
        db_path: Path to the SQLite checkpoint database.

    Returns:
        (compiled_graph, AsyncSqliteSaver)
    """
    import aiosqlite
    import yaml
    from pathlib import Path

    from kazma_core.authority import create_authority
    from kazma_core.cost_breaker import create_cost_breaker
    from kazma_core.tracing import KazmaTracer, get_trace_store

    # Load config
    if config is None:
        config_path = Path("kazma.yaml")
        if config_path.exists():
            with open(config_path) as f:
                config = yaml.safe_load(f) or {}
        else:
            config = {}

    llm_cfg = config.get("llm", {})
    system_prompt = config.get("system_prompt", "You are Kazma (كاظمه).")

    # LLM
    if llm is None:
        llm = LLMProvider(LLMConfig.from_dict(llm_cfg))

    # Local tool executor
    if tool_executor is None:
        from kazma_core.agent.tool_registry import LocalToolRegistry
        tool_executor = LocalToolRegistry(include_builtins=True)

    # MCP manager — connect to configured servers if provided
    if mcp_manager is not None:
        mcp_servers = config.get("mcp", {}).get("servers", [])
        if mcp_servers:
            try:
                count = await mcp_manager.connect_from_config(mcp_servers)
                logger.info("MCP manager connected %d tools from %d servers",
                            count, len(mcp_servers))
            except Exception as exc:
                logger.warning("MCP manager failed to connect: %s", exc)

    # Wrap local + MCP into a unified executor
    from kazma_core.mcp.manager import UnifiedToolExecutor
    unified = UnifiedToolExecutor(local=tool_executor, mcp=mcp_manager)

    tool_definitions = unified.get_tool_definitions()

    # Cost breaker
    cost_breaker = create_cost_breaker()

    # Context authority
    model = llm_cfg.get("model", "gpt-4o-mini")
    window = config.get("memory", {}).get("max_context_tokens", 128_000)
    authority = create_authority(model=model, window=window)

    # Tracer
    tracing_cfg = config.get("logging", {})
    tracer = KazmaTracer(
        backend="langfuse" if tracing_cfg.get("langfuse", {}).get("enabled") else "console",
        config=tracing_cfg.get("langfuse", {}),
    )

    # Checkpointer
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(db_path)
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA synchronous=NORMAL")
    checkpointer = AsyncSqliteSaver(conn)
    await checkpointer.setup()

    # Build graph
    graph = build_supervisor_graph(
        llm=llm,
        system_prompt=system_prompt,
        tool_definitions=tool_definitions,
        tool_executor=unified,
        cost_breaker=cost_breaker,
        authority=authority,
        tracer=tracer,
        checkpointer=checkpointer,
    )

    logger.info("Supervisor app created (model=%s, tools=%d)", model, len(tool_definitions))
    return graph, checkpointer
