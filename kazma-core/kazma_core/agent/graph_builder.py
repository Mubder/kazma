"""Graph Builder — Compiles the Supervisor LangGraph StateMachine.

Graph topology
══════════════

    ┌────────────┐
    │  SUPERVISOR │  ← entry point
    └──┬───────┬─┘
       │       │
       │       └──────────────────────────────┐
       │                                      │
       ▼                                      ▼
    ┌────────────┐                     ┌────────────┐
    │ TOOL_WORKER │                     │   RESPOND  │ → END
    └─────┬──────┘                     └────────────┘
          │
    SUPERVISOR

The Supervisor is the decision-maker.  On each iteration it:
  1. Calls the LLM with the current messages + tool schemas.
  2. If the LLM returns tool_calls → routes to TOOL_WORKER.
  3. If context is ≥ 80% full → compacts inline and re-enters SUPERVISOR.
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

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, StateGraph

from kazma_core.agent.state import (
    NodeName,
    PendingToolCall,
    SupervisorState,
    ToolResult,
)
from kazma_core.llm_provider import LLMConfig, LLMProvider
from kazma_core.time_travel import SnapshotRecorder

from kazma_core.tracing import KazmaTracer
from kazma_core.config_schema import TracingConfig

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# Personality injection helper
# ══════════════════════════════════════════════════════════════════════════

_PERSONALITY_MARKER = "[KAZMA_PERSONALITY]"

TOOL_RESULT_MAX_CHARS = 4000


def truncate_tool_result(content: str, max_chars: int = TOOL_RESULT_MAX_CHARS) -> str:
    """Truncate tool result content to *max_chars* with a truncation marker.

    If *content* is shorter than *max_chars*, it is returned unchanged.
    """
    if len(content) > max_chars:
        original_len = len(content)
        return content[:max_chars] + f"\n[truncated {original_len - max_chars} chars]"
    return content


def _ensure_personality(
    messages: list[dict[str, Any]],
    base_system_prompt: str,
    personality_prompt: str,
) -> list[dict[str, Any]]:
    """Inject personality system prompt, replacing any stale one.

    Layout after injection:
        [0] base system prompt  (Kazma identity)
        [1] personality system prompt  (tagged with _PERSONALITY_MARKER)
        [2+] conversation messages

    On subsequent calls (personality switch or re-entry), the old
    personality message is replaced in-place.
    """
    msgs = list(messages)

    # Remove any old personality-tagged system message
    msgs = [m for m in msgs if _PERSONALITY_MARKER not in m.get("content", "")]

    # Ensure base system prompt at position 0
    has_base = any(m.get("role") == "system" and _PERSONALITY_MARKER not in m.get("content", "") for m in msgs)
    if not has_base:
        msgs.insert(0, {"role": "system", "content": base_system_prompt})

    # Inject personality right after the base system prompt.
    # We tag it so we can find and replace it on the next switch.
    tagged = f"{_PERSONALITY_MARKER}\n{personality_prompt}"
    insert_at = 1 if msgs and msgs[0].get("role") == "system" else 0
    msgs.insert(insert_at, {"role": "system", "content": tagged})

    return msgs


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
    cost_breaker: Any,  # CostCircuitBreaker
    authority: Any,  # ContextAuthority
    tracer: Any,  # KazmaTracer
    model_router: Any | None = None,  # ModelRouter for multi-model routing
    personality_prompt: str | None = None,  # Active personality system prompt
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

    # ── Reset tool circuit breaker on new user turn ────────────────
    # The breaker trips after 2 consecutive empty/failed tool results.
    # Without this reset, the breaker stays tripped permanently across
    # all subsequent turns (state persists in the checkpointer).
    breaker_reset = {}
    if iteration == 0:
        if state.get("circuit_breaker_tripped", False) or state.get("consecutive_tool_failures", 0) > 0:
            logger.info("[Supervisor] Resetting tool circuit breaker for new turn")
        breaker_reset = {"circuit_breaker_tripped": False, "consecutive_tool_failures": 0}

    # ── Cost breaker gate ──────────────────────────────────────────
    if cost_breaker.should_halt():
        logger.warning("[Supervisor] Cost breaker tripped — forcing respond")
        return {
            **breaker_reset,
            "next_node": NodeName.RESPOND,
            "messages": messages
            + [
                {
                    "role": "assistant",
                    "content": "⚠️ ميزانية الجلسة انتهت. أعد التشغيل أو اتصل بالمسؤول.",
                }
            ],
        }

    # ── 80% context compaction check ───────────────────────────────
    state_for_check = {**state, "messages": messages}
    compacted_state = await authority.check_and_enforce(state_for_check)
    if compacted_state is not state_for_check:
        logger.info("[Supervisor] Context compacted — restarting with fresh context")
        return {
            **breaker_reset,
            "messages": compacted_state.get("messages", []),
            "needs_compaction": False,
            "next_node": NodeName.SUPERVISOR,  # re-enter supervisor
        }

    # ── Ensure system prompt and personality are present ───────────
    # The personality prompt is injected at position 0, replacing any
    # stale personality message from a previous personality setting.
    # The base system_prompt goes at position 0 if no system message
    # exists yet. Personality goes right after the base system prompt.
    if personality_prompt:
        messages = _ensure_personality(messages, system_prompt, personality_prompt)
    elif not any(m.get("role") == "system" for m in messages):
        messages.insert(0, {"role": "system", "content": system_prompt})

    # ── LLM call ──────────────────────────────────────────────────
    # Classify and route to optimal model if router is available
    routed_model = None
    if model_router is not None:
        from kazma_core.models.router import ModelRouter

        last_user_content = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user_content = str(m.get("content", ""))
                break
        if last_user_content:
            profile = ModelRouter.classify(last_user_content)
            model_spec = model_router.route(profile)
            routed_model = model_spec.model
            logger.info(
                "[Supervisor] Routed to %s (profile=%s, model=%s)",
                profile.value,
                model_spec.provider,
                model_spec.model,
            )

    start = time.monotonic()
    try:
        from kazma_core.retry import friendly_llm_error, load_retry_config

        cfg = load_retry_config()
        retryable_exc: tuple[type[Exception], ...] = (ConnectionError, TimeoutError)
        try:
            import httpx

            retryable_exc = retryable_exc + (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.RemoteProtocolError,
            )
        except ImportError:
            pass

        _llm_attempts = 0

        async def _call_llm_with_retry() -> Any:
            nonlocal _llm_attempts
            last_exc: Exception | None = None
            for attempt in range(1, cfg["max_attempts"] + 1):
                try:
                    return await llm.chat(
                        messages=messages,
                        tools=tool_definitions if tool_definitions else None,
                        model=routed_model,
                    )
                except retryable_exc as exc:
                    last_exc = exc
                    _llm_attempts = attempt
                    if attempt < cfg["max_attempts"]:
                        wait_time = min(cfg["min_wait"] * (2 ** (attempt - 1)), cfg["max_wait"])
                        logger.warning(
                            "[Supervisor] LLM call attempt %d/%d failed: %s (retrying in %ds)",
                            attempt,
                            cfg["max_attempts"],
                            exc,
                            wait_time,
                        )
                        import asyncio

                        await asyncio.sleep(wait_time)
                    else:
                        raise
            raise last_exc  # type: ignore[misc]

        response = await _call_llm_with_retry()
    except Exception as exc:
        logger.error("[Supervisor] LLM call failed after retries: %s", exc)
        from kazma_core.retry import friendly_llm_error

        error_content = friendly_llm_error(exc)
        return {
            **breaker_reset,
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
        "[Supervisor] LLM responded: model=%s tokens=%d cost=$%.4f duration=%.0fms tool_calls=%d",
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
            **breaker_reset,
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

    pending = [PendingToolCall(id=tc.id, name=tc.name, arguments=tc.arguments) for tc in response.tool_calls]

    return {
        **breaker_reset,
        "messages": messages + [assistant_msg],
        "tool_calls_pending": pending,
        "tool_calls_done": [],  # reset for this iteration
        "next_node": NodeName.TOOL_WORKER,
        "iteration": iteration + 1,
        "last_model": response.model,
        "last_tokens": response.usage.get("total_tokens", 0),
        "last_cost_usd": response.cost_usd,
    }


async def tool_worker_node(
    state: SupervisorState,
    *,
    tool_executor: Any,
    tracer: Any,
    hitl_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Tool Worker node — executes pending tool calls (parallel fan-out).

    All pending tool calls are dispatched concurrently via asyncio.gather.
    Results are collected and appended as tool-role messages.

    HITL: If hitl_config is provided, danger-tier tools trigger an
    interrupt() before execution, pausing the graph until the user
    approves or denies via the /api/approve endpoint.
    """
    import asyncio

    from langgraph.types import interrupt

    from kazma_core.safety.hitl import requires_approval

    pending = state.get("tool_calls_pending", [])
    if not pending:
        logger.warning("[ToolWorker] No pending tool calls — routing back")
        return {"next_node": NodeName.SUPERVISOR}

    # ── Check Circuit Breaker ──────────────────────────────────────
    breaker_tripped = state.get("circuit_breaker_tripped", False) or (state.get("consecutive_tool_failures", 0) >= 2)
    if breaker_tripped:
        logger.warning("[ToolWorker] Circuit breaker is active! Bypassing all execution.")
        results = [
            ToolResult(
                tool_call_id=tc["id"],
                name=tc["name"],
                content="SYSTEM OVERRIDE: Tool blocked due to consecutive failures. Synthesize final answer now.",
                is_error=True,
                duration_ms=0.0,
            )
            for tc in pending
        ]
        # Build tool-role messages for the conversation
        messages = list(state.get("messages", []))
        tool_messages = [
            {
                "role": "tool",
                "tool_call_id": tr["tool_call_id"],
                "content": tr["content"],
            }
            for tr in results
        ]
        cumulative = dict(state.get("tool_results", {}))
        for tr in results:
            cumulative[tr["tool_call_id"]] = tr

        return {
            "messages": messages + tool_messages,
            "tool_calls_pending": [],
            "tool_calls_done": list(results),
            "tool_results": cumulative,
            "consecutive_tool_failures": state.get("consecutive_tool_failures", 0),
            "circuit_breaker_tripped": True,
            "next_node": NodeName.SUPERVISOR,
        }

    logger.info("[ToolWorker] Executing %d tool calls", len(pending))

    # ── Bind session messages to the current async context ─────────
    # Tools such as export_session and context_info need access to the
    # current conversation messages, but the LLM does not pass them as
    # arguments.  We publish the state's messages into a ContextVar so
    # each concurrent graph invocation sees its own messages (no shared
    # module-global list).  The token restores the prior value on exit.
    from kazma_core.tools.export_session import (
        reset_current_session_messages,
        set_current_session_messages,
    )

    session_messages = list(state.get("messages", []))
    _messages_token = set_current_session_messages(session_messages)

    try:
        # ── HITL: separate safe and danger tools ──────────────────────
        safe_tools: list[PendingToolCall] = []
        danger_tools: list[PendingToolCall] = []

        if hitl_config:
            for tc in pending:
                if requires_approval(tc["name"], hitl_config):
                    danger_tools.append(tc)
                else:
                    safe_tools.append(tc)
        else:
            safe_tools = list(pending)

        async def _exec_one(tc: PendingToolCall) -> ToolResult:
            start = time.monotonic()
            result = await tool_executor.execute(tc["name"], tc.get("arguments") or {})
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
                tc["name"],
                duration_ms,
                result.get("is_error", False),
            )

            # ── Truncation middleware ──────────────────────────────────
            raw_content = result.get("content", "")
            content = truncate_tool_result(raw_content)
            if len(content) != len(raw_content):
                logger.info(
                    "[ToolWorker] Truncated result from %s (%d → %d chars)", tc["name"], len(raw_content), len(content)
                )

            return ToolResult(
                tool_call_id=tc["id"],
                name=tc["name"],
                content=content,
                is_error=result.get("is_error", False),
                duration_ms=duration_ms,
            )

        def _denied_result(tc: PendingToolCall) -> ToolResult:
            """Create a ToolResult for a denied tool call."""
            return ToolResult(
                tool_call_id=tc["id"],
                name=tc["name"],
                content=f"Tool '{tc['name']}' denied by user. Operation not executed.",
                is_error=True,
                duration_ms=0,
            )

        # ── Execute safe tools in parallel ────────────────────────────
        results: list[ToolResult] = []
        if safe_tools:
            results.extend(await asyncio.gather(*(_exec_one(tc) for tc in safe_tools)))

        # ── HITL: interrupt for each danger tool ──────────────────────
        for tc in danger_tools:
            approval_input = {
                "type": "hitl_approval",
                "tool": tc["name"],
                "args": tc["arguments"],
                "message": f"Agent wants to run: {tc['name']}({tc['arguments']})",
            }

            # interrupt() pauses the graph — resumes when /api/approve calls
            # graph.ainvoke(Command(resume=...), config)
            approval = interrupt(approval_input)

            if isinstance(approval, dict) and approval.get("approved", False):
                logger.info("[ToolWorker] HITL approved: %s", tc["name"])
                # Mark as already-approved so tool_registry.execute() skips
                # the redundant swarm-bus check (double-gating prevention).
                approved_tc = dict(tc)
                approved_tc_args = dict(tc.get("arguments") or {})
                approved_tc_args["_hitl_approved"] = True
                approved_tc["arguments"] = approved_tc_args
                results.append(await _exec_one(approved_tc))
            else:
                logger.info("[ToolWorker] HITL denied: %s", tc["name"])
                results.append(_denied_result(tc))

        # ── Empty-Result Circuit Breaker ──────────────────────────────
        consecutive_failures = state.get("consecutive_tool_failures", 0)
        breaker_tripped_now = False

        # We process results, but instead of appending a stray system message,
        # we format the circuit breaker warning directly into the tool response
        # so we don't violate the API schema (1 tool response per tool call).
        for tr in results:
            if breaker_tripped_now:
                tr["content"] = "SYSTEM OVERRIDE: Tool blocked due to consecutive failures. Synthesize final answer now."
                tr["is_error"] = True
                continue

            content_str = str(tr.get("content", "")).strip()
            is_empty_or_denied = (
                not content_str or 
                content_str == "[]" or 
                "no results" in content_str.lower() or 
                "denied by user" in content_str.lower() or
                tr.get("is_error", False)
            )
            
            if is_empty_or_denied:
                consecutive_failures += 1
            else:
                consecutive_failures = 0

            if consecutive_failures >= 2:
                logger.warning("[ToolWorker] Circuit breaker tripped! %d consecutive tool failures.", consecutive_failures)
                tr["content"] = "SYSTEM OVERRIDE: Tool blocked due to consecutive failures. Synthesize final answer now."
                tr["is_error"] = True
                breaker_tripped_now = True

        # Build tool-role messages for the conversation
        messages = list(state.get("messages", []))
        tool_messages: list[dict[str, Any]] = []
        for tr in results:
            tool_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tr["tool_call_id"],
                    "content": tr["content"],
                }
            )

        # Merge into cumulative tool_results
        cumulative = dict(state.get("tool_results", {}))
        for tr in results:
            cumulative[tr["tool_call_id"]] = tr

        return {
            "messages": messages + tool_messages,
            "tool_calls_pending": [],  # all consumed
            "tool_calls_done": list(results),
            "tool_results": cumulative,
            "consecutive_tool_failures": consecutive_failures,
            "circuit_breaker_tripped": breaker_tripped_now,
            "next_node": NodeName.SUPERVISOR,  # loop back
        }
    finally:
        # Always restore the prior ContextVar value, even if a tool
        # raised or the graph was interrupted by HITL.
        reset_current_session_messages(_messages_token)


async def respond_node(state: SupervisorState) -> dict[str, Any]:
    """Respond node — finalizes the turn.

    Extracts the last assistant message as the response and increments
    the iteration counter.
    """
    messages = state.get("messages", [])
    iteration = state.get("iteration", 0) + 1

    logger.info(
        "[Respond] Finalizing turn (iteration=%d, messages=%d)",
        iteration,
        len(messages),
    )

    return {
        "messages": messages,
        "iteration": iteration,
        "tool_calls_pending": [],
        "tool_calls_done": [],
        "next_node": "end",
    }


async def check_saturation_node(state: SupervisorState) -> dict[str, Any]:
    """Check if conversation has exceeded the summarization threshold.

    Routes to SUMMARIZE if over threshold, otherwise to SUPERVISOR.
    """
    from kazma_core.summarizer import TOKEN_THRESHOLD, estimate_tokens

    messages = state.get("messages", [])
    estimated = estimate_tokens(messages)

    if estimated > TOKEN_THRESHOLD:
        logger.info(
            "[CheckSaturation] Estimated %d tokens > threshold %d — routing to summarize",
            estimated,
            TOKEN_THRESHOLD,
        )
        return {"next_node": NodeName.SUMMARIZE}

    logger.debug("[CheckSaturation] Estimated %d tokens — under threshold, proceeding", estimated)
    return {"next_node": NodeName.SUPERVISOR}


async def summarize_node(
    state: SupervisorState,
    *,
    llm: Any,
) -> dict[str, Any]:
    """Summarize the conversation and inject as a SystemMessage at position 0."""
    from kazma_core.summarizer import format_summary, get_summary, summarize

    messages = list(state.get("messages", []))
    thread_id = state.get("thread_id", "")

    # Check if we already have a summary for this thread
    existing = get_summary(thread_id)
    if existing:
        # Use cached summary, but regenerate if conversation has grown significantly
        summary_text = format_summary(existing)
    else:
        summary_text = await summarize(messages, llm, thread_id=thread_id)

    # Inject summary as system message at position 0
    summary_msg = {"role": "system", "content": summary_text}

    # Remove any existing summary messages (to avoid duplicates)
    filtered = [
        m for m in messages if not (m.get("role") == "system" and "CONVERSATION SUMMARY" in str(m.get("content", "")))
    ]

    new_messages = [summary_msg] + filtered

    logger.info("[Summarize] Injected summary (%d chars) at position 0", len(summary_text))

    return {
        "messages": new_messages,
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
        return await tool_worker_node(state, tool_executor=tool_executor, tracer=tracer, hitl_config=hitl_config)

    async def _respond(state: SupervisorState) -> dict[str, Any]:
        return await respond_node(state)

    async def _check_saturation(state: SupervisorState) -> dict[str, Any]:
        return await check_saturation_node(state)

    async def _summarize(state: SupervisorState) -> dict[str, Any]:
        return await summarize_node(state, llm=llm)

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
        return NodeName.RESPOND

    def _route_from_worker(state: SupervisorState) -> str:
        """Route from Tool Worker — always back to Supervisor."""
        return NodeName.SUPERVISOR

    def _route_from_saturation(state: SupervisorState) -> str:
        """Route from Check Saturation — to summarize if over threshold, else supervisor."""
        next_node = state.get("next_node", NodeName.SUPERVISOR)
        if next_node == NodeName.SUMMARIZE:
            return NodeName.SUMMARIZE
        return NodeName.SUPERVISOR

    def _route_from_summarize(state: SupervisorState) -> str:
        """Route from Summarize — always to Supervisor."""
        return NodeName.SUPERVISOR

    # ── Build the graph ─────────────────────────────────────────────
    graph = StateGraph(SupervisorState)

    graph.add_node(NodeName.CHECK_SATURATION, _check_saturation)
    graph.add_node(NodeName.SUPERVISOR, _supervisor)
    graph.add_node(NodeName.TOOL_WORKER, _tool_worker)
    graph.add_node(NodeName.RESPOND, _respond)
    graph.add_node(NodeName.SUMMARIZE, _summarize)

    # Entry: START → check_saturation
    graph.set_entry_point(NodeName.CHECK_SATURATION)

    # check_saturation → {summarize, supervisor}
    graph.add_conditional_edges(
        NodeName.CHECK_SATURATION,
        _route_from_saturation,
        {
            NodeName.SUMMARIZE: NodeName.SUMMARIZE,
            NodeName.SUPERVISOR: NodeName.SUPERVISOR,
        },
    )

    # summarize → supervisor
    graph.add_conditional_edges(
        NodeName.SUMMARIZE,
        _route_from_summarize,
        {NodeName.SUPERVISOR: NodeName.SUPERVISOR},
    )

    # Supervisor → {tool_worker, respond}
    graph.add_conditional_edges(
        NodeName.SUPERVISOR,
        _route,
        {
            NodeName.TOOL_WORKER: NodeName.TOOL_WORKER,
            NodeName.RESPOND: NodeName.RESPOND,
        },
    )

    # Tool Worker → Supervisor (loop back)
    graph.add_conditional_edges(
        NodeName.TOOL_WORKER,
        _route_from_worker,
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
    from pathlib import Path

    import aiosqlite
    import yaml

    from kazma_core.authority import create_authority
    from kazma_core.cost_breaker import create_cost_breaker
    from kazma_core.time_travel import create_recorder
    from kazma_core.tracing import KazmaTracer
    from kazma_core.url_utils import normalize_model_name, normalize_provider_url

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

    # ── Load personality from config / env ─────────────────────────
    personality_prompt: str | None = None
    try:
        from kazma_core.personalities import load_personality

        profile = load_personality(config=config)
        personality_prompt = profile.system_prompt
        logger.info("Personality loaded: %s (%s)", profile.name, profile.emoji)
    except Exception as exc:
        logger.warning("Personality loading failed, continuing without: %s", exc)
        logger.debug("Personality loading failure details:", exc_info=True)

    # Normalize LLM config URLs
    if "base_url" in llm_cfg:
        llm_cfg["base_url"] = normalize_provider_url(llm_cfg["base_url"])
    if "model" in llm_cfg:
        llm_cfg["model"] = normalize_model_name(llm_cfg["model"], llm_cfg.get("base_url", ""))

    logger.info(
        "LLM config: base_url=%s model=%s router=%s",
        llm_cfg.get("base_url", "(default)"),
        llm_cfg.get("model", "(default)"),
        llm_cfg.get("router", "none"),
    )

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
                logger.info("MCP manager connected %d tools from %d servers", count, len(mcp_servers))
            except Exception as exc:
                logger.warning("MCP manager failed to connect: %s", exc)
                logger.debug("MCP manager connection failure details:", exc_info=True)

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
    langfuse_cfg = tracing_cfg.get("langfuse", {})
    
    tracing_config = TracingConfig(
        enabled=langfuse_cfg.get("enabled", False),
        backend="langfuse" if langfuse_cfg.get("enabled") else "console",
        langfuse_public_key=langfuse_cfg.get("public_key"),
        langfuse_secret_key=langfuse_cfg.get("secret_key"),
        langfuse_host=langfuse_cfg.get("host", "http://localhost:3000"),
    )
    tracer = KazmaTracer(config=tracing_config)

    # Checkpointer
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(db_path)
    await apply_sqlite_pragmas_async(conn)
    checkpointer = AsyncSqliteSaver(conn)
    await checkpointer.setup()

    # Time Travel recorder
    snapshot_recorder = create_recorder(config=config)

    # Universal language directive — ALWAYS enforced regardless of which
    # system prompt or personality is configured. This is injected here
    # (not in the config) so it can never be accidentally removed.
    _LANG_DIRECTIVE = (
        "\n\nCRITICAL LANGUAGE RULE: You MUST respond in the EXACT language "
        "the user writes in. Arabic input = Arabic output. English input = "
        "English output. If they mix, match their pattern. This overrides "
        "all other instructions. NEVER switch languages unless explicitly asked."
    )
    if _LANG_DIRECTIVE not in system_prompt:
        system_prompt = system_prompt.rstrip() + _LANG_DIRECTIVE

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
        personality_prompt=personality_prompt,
        snapshot_recorder=snapshot_recorder,
    )

    logger.info("Supervisor app created (model=%s, tools=%d)", model, len(tool_definitions))
    return graph, checkpointer
