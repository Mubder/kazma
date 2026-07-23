"""Graph Builder — Compiles the Supervisor LangGraph StateMachine.

Graph topology
══════════════

    ┌───────────────────┐    (over 80% token budget)
    │ CHECK_SATURATION  │ ──────────────────────► ┌───────────┐
    │  ← entry point    │                        │ SUMMARIZE │ ─┐
    └────────┬──────────┘                        └───────────┘ │
             │ (under budget)                                   │
             ▼                                                  │
    ┌──────────────┐     ┌────────────────┐                     │
    │  SUPERVISOR  │────►│  TOOL_WORKER   │                     │
    └──┬────────┬──┘     └───────┬────────┘                     │
       │        │                │ (loop back)                  │
       │        │          SUPERVISOR                            │
       │        │                                              │
       │        └────────────────────────┐                     │
       ▼                                 ▼                     │
    ┌──────────┐                 ┌──────────┐                   │
    │ RESPOND  │                 │ (re-enter│ ◄─────────────────┘
    └────┬─────┘                 │SUPERVISOR)│
         │                       └──────────┘
         ▼
        END

CHECK_SATURATION is the entry point. When token usage exceeds 80% it
routes to SUMMARIZE (compaction), then back to SUPERVISOR. Otherwise it
goes straight to SUPERVISOR. The Supervisor decides TOOL_WORKER (tool
calls) or RESPOND (final text). TOOL_WORKER always loops back to
SUPERVISOR. RESPOND is terminal.

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
import os
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

__all__ = ["TOOL_RESULT_MAX_CHARS", "build_supervisor_graph", "check_saturation_node", "respond_node", "sanitize_tool_chains", "summarize_node", "supervisor_node", "tool_worker_node", "truncate_tool_result"]

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# Personality injection helper
# ══════════════════════════════════════════════════════════════════════════

_PERSONALITY_MARKER = "[KAZMA_PERSONALITY]"

# Default cap for ordinary tools (env-overridable).
TOOL_RESULT_MAX_CHARS = int(
    os.environ.get("KAZMA_TOOL_RESULT_MAX_CHARS", "4000") or "4000"
)
# Higher cap for research / web-read tools so long pages can reach the model.
TOOL_RESULT_RESEARCH_MAX_CHARS = int(
    os.environ.get("KAZMA_TOOL_RESULT_RESEARCH_MAX_CHARS", "16000") or "16000"
)
_RESEARCH_TOOL_NAMES = frozenset(
    {
        "read_url",
        "crawl_page",
        "crawl_site",
        "read_url_to_file",
        "list_research_chunks",
        "read_research_chunk",
        "summarize_research_file",
        "digest_research_file",
        "web_search",
        "web_search_duckduckgo",
    }
)


def truncate_tool_result(
    content: str,
    max_chars: int | None = None,
    *,
    tool_name: str | None = None,
) -> str:
    """Truncate tool result content with a truncation marker.

    Research tools (``read_url``, chunk tools, …) use a higher default cap
    (``KAZMA_TOOL_RESULT_RESEARCH_MAX_CHARS``, default 16000) so paging and
    research workflows are not cut to 4k after a successful scrape.
    """
    if max_chars is None:
        if tool_name and tool_name in _RESEARCH_TOOL_NAMES:
            max_chars = max(4000, min(100_000, TOOL_RESULT_RESEARCH_MAX_CHARS))
        else:
            max_chars = max(500, min(100_000, TOOL_RESULT_MAX_CHARS))
    if len(content) > max_chars:
        original_len = len(content)
        return content[:max_chars] + f"\n[truncated {original_len - max_chars} chars]"
    return content


def sanitize_tool_chains(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Repair broken tool-call chains ANYWHERE in the message history.

    OpenAI-compatible providers reject a history in which an assistant
    message with ``tool_calls`` is not followed by a ``tool`` response for
    every ``tool_call_id`` (HTTP 400 "insufficient tool messages"). A chain
    can break mid-history when a HITL interrupt pauses a turn and the error
    turn is later committed on top of it, poisoning the thread permanently.

    Repairs applied:
      - assistant ``tool_calls`` entries with no later ``tool`` response are
        removed; if none remain, the message is kept as plain text (when it
        has content) or dropped entirely.
      - orphaned ``tool`` messages (no surviving matching assistant
        ``tool_calls``) are dropped.
    """
    msgs = list(messages)

    # tool_call_id → indices of every tool response (ids can repeat across turns)
    response_indices: dict[str, list[int]] = {}
    for i, m in enumerate(msgs):
        if isinstance(m, dict) and m.get("role") == "tool":
            tcid = m.get("tool_call_id") or ""
            if tcid:
                response_indices.setdefault(tcid, []).append(i)

    valid_ids: set[str] = set()
    out: list[dict[str, Any]] = []
    dropped = 0
    for i, m in enumerate(msgs):
        if not isinstance(m, dict):
            out.append(m)
            continue
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            kept = [
                tc for tc in m["tool_calls"]
                if any(j > i for j in response_indices.get(tc.get("id") or "", []))
            ]
            if kept:
                if len(kept) != len(m["tool_calls"]):
                    dropped += len(m["tool_calls"]) - len(kept)
                    m = {**m, "tool_calls": kept}
                valid_ids.update(tc.get("id") or "" for tc in kept)
                out.append(m)
            else:
                dropped += len(m["tool_calls"])
                content = m.get("content")
                if isinstance(content, str) and content.strip():
                    out.append({k: v for k, v in m.items() if k != "tool_calls"})
                # else: tool-calls-only message with no responses — drop
            continue
        if role == "tool":
            if (m.get("tool_call_id") or "") in valid_ids:
                out.append(m)
            else:
                dropped += 1
            continue
        out.append(m)

    if dropped:
        logger.warning(
            "[Sanitize] Repaired broken tool chains: removed %d dangling "
            "tool_calls/tool entries (%d → %d messages)",
            dropped, len(msgs), len(out),
        )
    return out


# ══════════════════════════════════════════════════════════════════════════
# Per-turn memory retrieval (RAG) helpers
# ══════════════════════════════════════════════════════════════════════════


def _rag_top_k() -> int:
    """Read the per-turn retrieval top-k from kazma.yaml (default 5)."""
    try:
        import yaml
        from pathlib import Path

        cfg_path = Path("kazma.yaml")
        if cfg_path.exists():
            with open(cfg_path) as f:
                cfg = yaml.safe_load(f) or {}
            return int(cfg.get("memory", {}).get("retrieval_top_k", 5))
    except Exception:
        pass
    return 5


def _format_retrieved_memories(memories: list[dict[str, Any]]) -> str:
    """Render retrieved memories as a compact system-message block.

    Mirrors the compaction format (compaction.py:_build_compacted_system)
    but for per-turn injection — a short "## Relevant context from memory"
    block. Each memory is capped to keep the prompt lean.
    """
    if not memories:
        return ""
    parts = ["## Relevant context from memory"]
    for i, mem in enumerate(memories, 1):
        content = mem.get("content", mem.get("text", ""))
        if not content:
            continue
        # Cap each memory at 300 chars so 5 memories ≤ ~1500 chars.
        text = str(content).strip()
        if len(text) > 300:
            text = text[:300] + "…"
        parts.append(f"{i}. {text}")
    if len(parts) == 1:
        return ""  # all memories were empty
    return "\n".join(parts)


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
    # On compaction, CONTINUE this supervisor call with the compacted
    # messages instead of returning early. The old early-return routed to
    # RESPOND (there is no supervisor self-edge), which ended the turn with
    # no answer and replaced the checkpoint with just the summary — the
    # "agent forgot everything and said nothing" bug.
    state_for_check = {**state, "messages": messages}
    compacted_state = await authority.check_and_enforce(state_for_check)
    if compacted_state is not state_for_check:
        logger.info("[Supervisor] Context compacted — continuing turn with compacted context")
        messages = list(compacted_state.get("messages", []))
        breaker_reset = {**breaker_reset, "needs_compaction": False}

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
    # Extract the latest user message (used by both the model router and
    # per-turn memory retrieval below).
    last_user_content = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user_content = str(m.get("content", ""))
            break

    # Classify and route to optimal model if router is available
    routed_model = None
    if model_router is not None:
        from kazma_core.models.router import ModelRouter

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

    # ── Per-turn memory retrieval (RAG) ──────────────────────────
    # Retrieve relevant memories for the current user message and inject
    # them as a system message before the LLM call. Gated on iteration==0
    # so it fires once per user turn (not per ReAct iteration). This is
    # the key difference from compaction-only retrieval — the agent now
    # has recall on EVERY turn, not just when the context window is full.
    # Honours memory.per_turn_retrieval in kazma.yaml (default true).
    _per_turn_on = True
    try:
        import yaml
        from pathlib import Path as _Path

        _cfg_path = _Path("kazma.yaml")
        if _cfg_path.exists():
            with open(_cfg_path) as _f:
                _mcfg = (yaml.safe_load(_f) or {}).get("memory", {}) or {}
            _per_turn_on = bool(_mcfg.get("per_turn_retrieval", True))
            if not bool(_mcfg.get("enabled", True)):
                _per_turn_on = False
    except Exception:
        pass

    if _per_turn_on and iteration == 0 and last_user_content:
        try:
            _top_k = _rag_top_k()
            memories = await authority.compactor.retrieve_memories(
                last_user_content, limit=_top_k,
            )
            if memories:
                mem_block = _format_retrieved_memories(memories)
                if mem_block:
                    # Insert after the base system prompt (position 0) so
                    # the memory block sits with the persona/env context,
                    # not in the user/assistant conversation thread.
                    messages.insert(1, {"role": "system", "content": mem_block})
                    logger.info(
                        "[Supervisor] Retrieved %d memories for turn", len(memories),
                    )
        except Exception:
            logger.warning("[Supervisor] per-turn memory retrieval failed — recall degraded", exc_info=True)

    # Per-turn language lock (again at graph level so Telegram/Discord paths
    # get it even when SSE already injected one — duplicate is harmless).
    if iteration == 0 and last_user_content:
        try:
            from kazma_core.language_lock import language_lock_message

            lock = language_lock_message(last_user_content)
            if lock and not any(
                m.get("role") == "system" and "LANGUAGE LOCK" in str(m.get("content", ""))
                for m in messages
            ):
                # Place just before the last user message so it is the nearest
                # instruction to the model.
                insert_at = len(messages)
                for i in range(len(messages) - 1, -1, -1):
                    if messages[i].get("role") == "user":
                        insert_at = i
                        break
                messages.insert(insert_at, {"role": "system", "content": lock})
        except Exception:
            logger.debug("[Supervisor] language lock skipped", exc_info=True)

    # ── Repair broken tool chains before the LLM call ──────────────
    # A checkpoint poisoned by a paused HITL turn (assistant tool_calls
    # with no tool responses, possibly mid-history) would 400 on every
    # provider call forever. Sanitizing here also heals the thread: the
    # repaired list is what gets persisted by the return paths below.
    messages = sanitize_tool_chains(messages)

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
        content = response.content.strip() if response.content else ""

        # ── Empty-response recovery ────────────────────────────────
        # Some providers (Groq compound-mini, certain Ollama models)
        # return content="" on the final turn after a tool call —
        # especially when the tool result (e.g. memory_search JSON)
        # was large. Without this guard the user sees
        # "(No response generated)". Retry once with an explicit nudge.
        if not content and iteration > 0:
            logger.warning(
                "[Supervisor] LLM returned empty content after tool calls "
                "(iteration=%d) — retrying with nudge", iteration,
            )
            messages_with_nudge = messages + [
                {"role": "system", "content": (
                    "Your previous response was empty. Please provide a "
                    "clear, helpful text answer to the user based on the "
                    "conversation and tool results above."
                )},
            ]
            try:
                nudge_response = await llm.chat(
                    messages=messages_with_nudge,
                    tools=[],
                    model=routed_model,
                )
                if nudge_response.content and nudge_response.content.strip():
                    content = nudge_response.content.strip()
                    response = nudge_response  # update for tracing/cost
                    logger.info("[Supervisor] Nudge retry succeeded — content recovered")
            except Exception as nudge_exc:
                logger.warning("[Supervisor] Nudge retry failed: %s", nudge_exc)

        # Pure text response → RESPOND
        assistant_msg = {"role": "assistant", "content": content or "I apologize, I couldn't generate a response. Please try rephrasing your question."}
        return {
            **breaker_reset,
            "messages": messages + [assistant_msg],
            "next_node": NodeName.RESPOND,
            "last_model": response.model,
            "last_tokens": response.usage.get("total_tokens", 0),
            "last_cost_usd": response.cost_usd,
        }

    # Tool calls → build pending list and route to TOOL_WORKER.
    # NOTE: Do NOT convert content to None when it's an empty string.
    # Some providers (Groq compound-mini, certain Ollama models) return
    # content="" alongside tool_calls. Converting to None breaks the
    # message history on the next LLM call (API rejects null content).
    # Keep the original value — empty string is valid per OpenAI spec.
    assistant_msg: dict[str, Any] = {
        "role": "assistant",
        "content": response.content if response.content is not None else "",
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

        # Signal the tool registry that the graph is the HITL authority for
        # this turn, so LocalToolRegistry.execute() skips the redundant
        # SwarmMessageBus safety.check() (mechanism B) — the graph's
        # interrupt() is the sole gate for single-agent chat. Restored in
        # the finally below.
        _graph_gate_token = None
        if hitl_config:
            from kazma_core.agent.tool_registry import _graph_hitl_gate_ctx

            _graph_gate_token = _graph_hitl_gate_ctx.set(True)
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
            content = truncate_tool_result(raw_content, tool_name=tc.get("name"))
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

        # ── HITL: one combined interrupt for the whole danger batch ──
        # (stops N-click floods when the model emits several danger tools
        # in one turn). Scope grants (tool/yolo) are applied by /api/approve
        # *before* resume so later turns skip the gate entirely.
        if danger_tools:
            tools_payload = [
                {
                    "id": tc.get("id"),
                    "name": tc["name"],
                    "args": tc.get("arguments") or {},
                }
                for tc in danger_tools
            ]
            if len(danger_tools) == 1:
                tc0 = danger_tools[0]
                message = f"Agent wants to run: {tc0['name']}({tc0.get('arguments') or {}})"
                primary_tool = tc0["name"]
                primary_args = tc0.get("arguments") or {}
            else:
                names = ", ".join(tc["name"] for tc in danger_tools)
                message = (
                    f"Agent wants to run {len(danger_tools)} danger tools: {names}"
                )
                primary_tool = f"{len(danger_tools)} tools"
                primary_args = {"tools": [t["name"] for t in tools_payload]}

            approval_input = {
                "type": "hitl_approval",
                "tool": primary_tool,
                "args": primary_args,
                "tools": tools_payload,
                "message": message,
            }

            # interrupt() pauses the graph — resumes when /api/approve calls
            # graph.ainvoke(Command(resume=...), config)
            approval = interrupt(approval_input)

            approved = isinstance(approval, dict) and approval.get("approved", False)
            # Optional selective ids; None/missing → all tools in the batch.
            approved_ids = None
            if isinstance(approval, dict):
                raw_ids = approval.get("approved_ids")
                if isinstance(raw_ids, list):
                    approved_ids = {str(x) for x in raw_ids}

            from kazma_core.agent.tool_registry import _hitl_approved_ctx

            for tc in danger_tools:
                tc_id = str(tc.get("id") or "")
                allow = approved and (approved_ids is None or tc_id in approved_ids)
                if allow:
                    logger.info("[ToolWorker] HITL approved: %s", tc["name"])
                    _token = _hitl_approved_ctx.set(True)
                    try:
                        results.append(await _exec_one(tc))
                    finally:
                        _hitl_approved_ctx.reset(_token)
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
        if _graph_gate_token is not None:
            from kazma_core.agent.tool_registry import _graph_hitl_gate_ctx

            _graph_hitl_gate_ctx.reset(_graph_gate_token)


async def respond_node(state: SupervisorState) -> dict[str, Any]:
    """Respond node — finalizes the turn.

    Extracts the last assistant message as the response and increments
    the iteration counter. Also schedules automatic long-term memory
    writes (durable facts / turn snapshots) so recall is not tool-only.
    """
    messages = state.get("messages", [])
    iteration = state.get("iteration", 0) + 1

    logger.info(
        "[Respond] Finalizing turn (iteration=%d, messages=%d)",
        iteration,
        len(messages),
    )

    # Auto-store durable user facts (and optional turn snapshots) so
    # per-turn RAG has something to retrieve without requiring memory_store.
    try:
        from kazma_core.memory.auto_store import schedule_auto_store

        schedule_auto_store(messages)
    except Exception:
        logger.debug("[Respond] auto_store schedule failed", exc_info=True)

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

