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
from kazma_core.summarizer import _normalize_msg

__all__ = ["TOOL_RESULT_MAX_CHARS", "build_supervisor_graph", "check_saturation_node", "respond_node", "sanitize_tool_chains", "summarize_node", "supervisor_node", "tool_worker_node", "truncate_tool_result"]

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# Personality injection helper
# ══════════════════════════════════════════════════════════════════════════

_PERSONALITY_MARKER = "[KAZMA_PERSONALITY]"

# Default cap for ordinary tools (env-overridable).
TOOL_RESULT_MAX_CHARS = int(
    os.environ.get("KAZMA_TOOL_RESULT_MAX_CHARS", "100000") or "100000"
)
# Higher cap for research, file-read, and MCP tools so long files reach the model.
TOOL_RESULT_RESEARCH_MAX_CHARS = int(
    os.environ.get("KAZMA_TOOL_RESULT_RESEARCH_MAX_CHARS", "200000") or "200000"
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
        "synthesize_from_digests",
        "run_research_pipeline",
        "web_search",
        "web_search_duckduckgo",
        # File & code tools
        "file_read",
        "file_view",
        "read_file_part",
        "shell_exec",
        "python_exec",
        "run",
        "run_file",
        # MCP filesystem tools
        "mcp__filesystem__read_text_file",
        "mcp__filesystem__read_multiple_files",
        "mcp__filesystem__read_file",
        "mcp__filesystem__directory_tree",
        "mcp__filesystem__list_directory",
    }
)


def truncate_tool_result(
    content: str,
    max_chars: int | None = None,
    *,
    tool_name: str | None = None,
) -> str:
    """Truncate tool result content with a truncation marker.

    File and research tools use a higher default cap (200,000 chars)
    so full files and long pages reach the model. Set env variable
    KAZMA_TOOL_RESULT_MAX_CHARS=0 or <= 0 for unlimited output.
    """
    if TOOL_RESULT_MAX_CHARS <= 0 or os.environ.get("KAZMA_NO_TRUNCATE") == "1":
        return content

    if max_chars is None:
        if tool_name and (
            tool_name in _RESEARCH_TOOL_NAMES
            or "file" in tool_name.lower()
            or "read" in tool_name.lower()
        ):
            max_chars = max(1000, TOOL_RESULT_RESEARCH_MAX_CHARS)
        else:
            max_chars = max(500, TOOL_RESULT_MAX_CHARS)
    if max_chars > 0 and len(content) > max_chars:
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
    msgs = [_normalize_msg(m) for m in messages]

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
                has_text = bool(content.strip()) if isinstance(content, str) else bool(content)
                if has_text:
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
# HITL approval summary (compact, never dumps large blob args verbatim)
# ══════════════════════════════════════════════════════════════════════════

# Keys that typically carry large file/document BODIES. Showing these in the
# approval card is what produced the giant "file_write({...whole README...})"
# prompts. We summarise them as a length hint instead. Note: command/query/sql
# are intentionally NOT here — they are the exact content the user must see to
# approve a shell/db tool, so they're shown (truncated) via the hint path.
_HITL_BLOB_KEYS = frozenset(
    {
        "content", "code", "text", "source", "body", "data",
        "patch", "diff", "payload", "image", "images", "base64",
    }
)

# High-signal keys shown up-front (path/command/repo/branch/branch_name/etc.).
_HITL_HINT_KEYS = (
    "path", "file_path", "filename", "file", "directory", "dir",
    "repo", "repository", "owner",
    "branch", "branch_name", "head", "base",
    "message", "commit_message",
    "action", "subaction", "mode",
    "url", "endpoint",
    "name", "key",
)


def _summarize_args_for_hitl(args: Any, *, max_len: int = 240) -> str:
    """Render tool arguments as a short, approval-card-friendly summary.

    Large blob keys (``content``/``code``/``text``/…) are replaced by a
    ``<{len} chars>`` placeholder so a ``file_write`` of an entire README
    doesn't blow up the HITL card. High-signal keys (path/command/repo/…)
    are shown first. Anything else is shown as ``key=value`` truncated to
    ``max_len`` total.
    """
    if not args:
        return ""
    if not isinstance(args, dict):
        # Non-dict args (e.g. a bare string): truncate in place.
        s = str(args)
        return s if len(s) <= max_len else s[: max_len - 1] + "…"

    hints: list[str] = []
    rest: list[str] = []
    for k, v in args.items():
        # Normalise values to a compact string form.
        if isinstance(v, str):
            val = v
        else:
            try:
                val = json.dumps(v, default=str, ensure_ascii=False)
            except Exception:
                val = str(v)
        # Mask blob fields entirely — only their length is useful in a prompt.
        if k in _HITL_BLOB_KEYS:
            val = f"<{len(val)} chars>" if val else "<empty>"
        rest.append(f"{k}={val}")
    # Promote high-signal keys to the front for at-a-glance readability.
    promoted = [k for k in _HITL_HINT_KEYS if k in args]
    promoted_set = set(promoted)
    ordered = [f"{k}={_compact_value(k, args[k])}" for k in promoted]
    ordered += [item for item in rest if not item.split("=", 1)[0] in promoted_set]
    summary = ", ".join(ordered)
    return summary if len(summary) <= max_len else summary[: max_len - 1] + "…"


def _compact_value(key: str, value: Any) -> str:
    """Compact a single hint value for the HITL summary (masks blob keys)."""
    if key in _HITL_BLOB_KEYS:
        if isinstance(value, str):
            return f"<{len(value)} chars>" if value else "<empty>"
        return "<blob>"
    if isinstance(value, str):
        return value if len(value) <= 80 else value[:79] + "…"
    try:
        s = json.dumps(value, default=str, ensure_ascii=False)
    except Exception:
        s = str(value)
    return s if len(s) <= 80 else s[:79] + "…"


def _format_hitl_message(tool: str, args: Any) -> str:
    """Build the single-line ``Agent wants to run: <tool>(...)`` summary.

    Kept compact (≈240 chars of args) so the approval card never shows the
    full body of e.g. a ``file_write`` ``content`` blob.
    """
    summary = _summarize_args_for_hitl(args)
    if summary:
        return f"Agent wants to run: {tool}({summary})"
    return f"Agent wants to run: {tool}()"


# ══════════════════════════════════════════════════════════════════════════
# Per-turn memory retrieval (RAG) helpers
# ══════════════════════════════════════════════════════════════════════════


def _rag_top_k() -> int:
    """Read per-turn retrieval top-k (ConfigStore ← yaml, default 5)."""
    try:
        from kazma_core.memory.config import memory_retrieval_top_k

        return memory_retrieval_top_k()
    except Exception:
        return 5


def _format_retrieved_memories(memories: list[dict[str, Any]]) -> str:
    """Render retrieved memories as a fenced untrusted system-message block.

    Per-turn RAG hits come from conversation-derived stores (auto_store /
    consolidator). Defense-in-depth (P4):
    * drop rows that look like prompt-injection overrides
    * wrap the rest in :func:`format_untrusted_block` so the model treats
      them as observation data, not instructions
    """
    if not memories:
        return ""
    try:
        from kazma_core.safety.prompt_fence import (
            format_untrusted_block,
            is_override_delta,
        )
    except Exception:  # pragma: no cover — fence always ship with core
        format_untrusted_block = None  # type: ignore[assignment]
        is_override_delta = None  # type: ignore[assignment]

    lines: list[str] = []
    for mem in memories:
        content = mem.get("content", mem.get("text", ""))
        if not content:
            continue
        text = str(content).strip()
        if not text:
            continue
        if is_override_delta is not None and is_override_delta(text):
            logger.warning(
                "[graph_builder] dropped injection-like memory hit: %.80s", text
            )
            continue
        # Cap each memory at 300 chars so 5 memories ≤ ~1500 chars.
        if len(text) > 300:
            text = text[:300] + "…"
        lines.append(f"- {text}")
    if not lines:
        return ""
    body = "## Relevant context from memory\n" + "\n".join(lines)
    if format_untrusted_block is not None:
        return format_untrusted_block(body, source="memory_rag")
    return body


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
    msgs = [_normalize_msg(m) for m in messages]

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
    messages = [_normalize_msg(m) for m in state.get("messages", [])]

    logger.info("[Supervisor] iteration=%d messages=%d", iteration, len(messages))

    # ── Reset tool circuit breaker and cost breaker timer on new user turn ──
    # The breaker trips after 2 consecutive empty/failed tool results.
    # Without this reset, the breaker stays tripped permanently across
    # all subsequent turns (state persists in the checkpointer).
    breaker_reset = {}
    if iteration == 0:
        if state.get("circuit_breaker_tripped", False) or state.get("consecutive_tool_failures", 0) > 0:
            logger.info("[Supervisor] Resetting tool circuit breaker for new turn")
        breaker_reset = {"circuit_breaker_tripped": False, "consecutive_tool_failures": 0}
        if cost_breaker and hasattr(cost_breaker, "record_user_interaction"):
            cost_breaker.record_user_interaction()

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
    # Honours memory.per_turn_retrieval via ConfigStore ← yaml (default true).
    _per_turn_on = True
    try:
        from kazma_core.memory.config import memory_per_turn_enabled

        _per_turn_on = memory_per_turn_enabled()
    except Exception:
        pass

    if _per_turn_on and iteration == 0 and last_user_content:
        # ── V2 cognitive recall path (bi-temporal beliefs + PPR) ──────
        # Active when memory.v2.use_new_stack is True. Falls back to the
        # legacy 4-layer RRF adapter when False (the dual-write transition
        # default). The V2 path never blocks: any failure degrades to the
        # legacy path or to no injection.
        _use_v2 = False
        try:
            from kazma_core.memory.config import memory_v2_enabled

            _use_v2 = memory_v2_enabled()
        except Exception:
            pass
        # Remember whether V2 was the intended path. If V2 recall RAISES,
        # we degrade to "no memory injection" (V2 is authoritative, an
        # empty/silent turn is safer than consulting a stale V1 store) —
        # NOT to the legacy retrieve_memories path.
        _v2_was_active = _use_v2

        if _use_v2:
            try:
                _top_k = _rag_top_k()
                from kazma_core.memory.recall import format_recall_block, recall

                result = recall(
                    last_user_content, limit=_top_k,
                    session_id=state.get("thread_id"),
                )
                if not result.empty:
                    mem_block = format_recall_block(result)
                    if mem_block:
                        messages.insert(1, {"role": "system", "content": mem_block})
                        logger.info(
                            "[Supervisor] V2 recall: %d beliefs, %d episodes for turn",
                            len(result.beliefs), len(result.episodes),
                        )
            except Exception:
                logger.warning(
                    "[Supervisor] V2 recall failed — skipping memory injection",
                    exc_info=True,
                )
                # V2 is authoritative: a failure degrades to no injection,
                # NOT to the legacy retrieve_memories path. Keep _use_v2=True
                # so the `if not _use_v2:` block below is skipped.
                _use_v2 = True

        if not _use_v2 and not _v2_was_active:
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

    # Soft force-plan: on the first supervisor hop of a tool-capable turn,
    # remind the model to open with a ```plan fence so the UI workbench
    # can pin a checklist (providers rarely expose true chain-of-thought).
    if iteration == 0 and tool_definitions:
        _plan_nudge = (
            "UI WORKBENCH: If you will call any tools this turn, put a short "
            "```plan fence (3–7 bullets) in your content field before or "
            "alongside tool_calls so the user sees your plan. Then use tools."
        )
        if not any(
            m.get("role") == "system" and "UI WORKBENCH" in str(m.get("content", ""))
            for m in messages
        ):
            messages.append({"role": "system", "content": _plan_nudge})

    start = time.monotonic()
    try:
        from kazma_core.retry import friendly_llm_error, load_retry_config
        from kazma_core.llm_provider import LLMError

        cfg = load_retry_config()
        retryable_exc: tuple[type[Exception], ...] = (ConnectionError, TimeoutError)
        try:
            import httpx

            retryable_exc = retryable_exc + (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.RemoteProtocolError,
                httpx.ReadError,
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
                except LLMError as exc:
                    # ``llm.chat`` wraps every failure in LLMError. Only
                    # transient errors (network blips, 429) are worth
                    # retrying; permanent 4xx content/schema errors fail
                    # fast so we don't waste attempts that cannot succeed.
                    is_transient = bool(getattr(exc, "transient", False))
                    last_exc = exc
                    _llm_attempts = attempt
                    if is_transient and attempt < cfg["max_attempts"]:
                        wait_time = min(cfg["min_wait"] * (2 ** (attempt - 1)), cfg["max_wait"])
                        logger.warning(
                            "[Supervisor] LLM call attempt %d/%d failed (transient): %s "
                            "(retrying in %ds)",
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
        # Surface an HONEST failure rather than disguising it as a normal
        # assistant reply. ``turn_failed`` tells respond_node to skip
        # synthesis (no fabricated final answer over the broken turn) — the
        # user gets a clear error they can act on (the "model stopped
        # thinking" symptom's real cause).
        return {
            **breaker_reset,
            "next_node": NodeName.RESPOND,
            "turn_failed": True,
            "error_message": error_content,
            "messages": messages
            + [
                {
                    "role": "assistant",
                    "content": error_content,
                }
            ],
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
        # Some providers (Groq compound-mini, certain Ollama models, and
        # deepseek-v4-flash on long/summarised contexts) return content=""
        # — either on the final turn after a tool call (large tool result
        # like memory_search JSON) OR on a first-pass reply with no tool
        # calls at all. Without this guard the user sees "Done" with no
        # bubble text. Retry once with an explicit nudge on ANY iteration.
        # (Previously gated on `iteration > 0`, which let iteration=0 empty
        # replies reach respond_node and stream as empty — 2026-07-31.)
        if not content:
            logger.warning(
                "[Supervisor] LLM returned empty content "
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

        # Auto-continuation guard for multi-step goals/tasks
        is_auto = state.get("auto_continue", False)
        if not is_auto and content:
            _content_lower = content.lower()
            if any(marker in _content_lower for marker in ["now section", "proceeding to section", "next section", "proceeding with section"]):
                is_auto = True

        if is_auto and iteration + 1 < max_iter:
            logger.info("[Supervisor] Auto-continue active (iteration=%d/%d) — looping back to supervisor", iteration + 1, max_iter)
            assistant_msg = {"role": "assistant", "content": content}
            continuation_msg = {"role": "user", "content": "Please proceed automatically with the remaining steps and complete the task."}
            return {
                **breaker_reset,
                "messages": messages + [assistant_msg, continuation_msg],
                "next_node": NodeName.SUPERVISOR,
                "iteration": iteration + 1,
                "last_model": response.model,
                "last_tokens": response.usage.get("total_tokens", 0),
                "last_cost_usd": response.cost_usd,
            }

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
    breaker_tripped = state.get("circuit_breaker_tripped", False) or (state.get("consecutive_tool_failures", 0) >= 3)
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
        messages = [_normalize_msg(m) for m in state.get("messages", [])]
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
            "consecutive_tool_failures": state.get("consecutive_tool_failures", 3),
            "circuit_breaker_tripped": True,
            "next_node": NodeName.RESPOND,
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

    session_messages = [_normalize_msg(m) for m in state.get("messages", [])]
    _messages_token = set_current_session_messages(session_messages)

    # ── Bind YOLO/HITL thread id from checkpointed state ────────────
    # Transports (SSE/WS) set the ContextVar, but LangGraph node execution
    # (or a missing transport bind) can leave it empty. state.thread_id is
    # the durable identity used when enabling YOLO/tool grants — without
    # this fallback, YOLO Session behaves like Approve once forever.
    from kazma_core.safety.hitl import (
        get_current_thread_id,
        reset_current_thread_id,
        set_current_thread_id,
    )

    _state_tid_token = None
    if not get_current_thread_id():
        _state_tid = state.get("thread_id")
        if _state_tid:
            _state_tid_token = set_current_thread_id(str(_state_tid))

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

        # ── HITL: one combined interrupt for the whole danger batch ──
        # (stops N-click floods when the model emits several danger tools
        # in one turn). Scope grants (tool/yolo) are applied by /api/approve
        # *before* resume so later turns skip the gate entirely.
        approved = False
        approved_ids = None
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
                message = _format_hitl_message(tc0["name"], tc0.get("arguments") or {})
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

            # Defense-in-depth: requires_approval() already filtered YOLO/grants
            # when splitting safe/danger above. Re-check here in case grants
            # were applied mid-turn (e.g. YOLO enabled just before resume).
            from kazma_core.safety.yolo import is_yolo_active

            current_thread = get_current_thread_id() or state.get("thread_id") or ""
            if current_thread and is_yolo_active(str(current_thread)):
                logger.warning(
                    "[ToolWorker] YOLO active for thread=%s — auto-approving %d danger tool(s)",
                    current_thread,
                    len(danger_tools),
                )
                approved = True
                approval = {"approved": True, "yolo": True}
            else:
                # interrupt() pauses the graph — resumes when /api/approve
                # calls graph.ainvoke(Command(resume=...), config)
                approval = interrupt(approval_input)
                approved = isinstance(approval, dict) and approval.get("approved", False)
            # Optional selective ids; None/missing → all tools in the batch.
            if isinstance(approval, dict):
                raw_ids = approval.get("approved_ids")
                if isinstance(raw_ids, list):
                    approved_ids = {str(x) for x in raw_ids}

        # ── Execute safe tools in parallel ────────────────────────────
        results: list[ToolResult] = []
        if safe_tools:
            results.extend(await asyncio.gather(*(_exec_one(tc) for tc in safe_tools)))

        # ── Execute/deny danger tools ─────────────────────────────────
        if danger_tools:
            from kazma_core.agent.tool_registry import _hitl_approved_ctx

            for tc in danger_tools:
                tc_id = str(tc.get("id") or "")
                allow = approved and (approved_ids is None or tc_id in approved_ids)
                if allow:
                    logger.info("[ToolWorker] HITL approved: %s", tc["name"])
                    consecutive_failures = 0
                    _token = _hitl_approved_ctx.set(True)
                    try:
                        results.append(await _exec_one(tc))
                    finally:
                        _hitl_approved_ctx.reset(_token)
                else:
                    logger.info("[ToolWorker] HITL denied: %s", tc["name"])
                    results.append(_denied_result(tc))

        # ── Tool-loop breaker (typed outcomes, per-round credit) ─────
        # Policy / HITL deny / empty results do not trip. Parallel hard
        # errors in one batch credit +1 only (not +N). See tool_loop_breaker.
        from kazma_core.agent.tool_loop_breaker import update_breaker

        prev_failures = int(state.get("consecutive_tool_failures", 0) or 0)
        breaker_state, results = update_breaker(prev_failures, list(results))
        consecutive_failures = breaker_state.consecutive_hard_rounds
        breaker_tripped_now = breaker_state.tripped
        if breaker_tripped_now:
            logger.warning(
                "[ToolWorker] Circuit breaker tripped! %d consecutive hard tool rounds.",
                consecutive_failures,
            )

        # Build tool-role messages for the conversation
        messages = [_normalize_msg(m) for m in state.get("messages", [])]
        tool_messages: list[dict[str, Any]] = []
        for tr in results:
            tool_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tr["tool_call_id"],
                    "content": tr["content"],
                }
            )

        # Soft research-depth gate: deep intent + search-only → nudge once
        try:
            from kazma_core.agent.research_policy import should_nudge_more_sources

            already = bool(state.get("_research_depth_nudged"))
            turn_tools = [str(tc.get("name") or "") for tc in (safe_tools + danger_tools)]
            # Include prior tool names this iteration chain from cumulative? use turn only
            nudge = should_nudge_more_sources(
                messages, turn_tools, already_nudged=already
            )
            if nudge:
                tool_messages.append(
                    {
                        "role": "system",
                        "content": nudge,
                    }
                )
                # mark via state field below
                state = {**state, "_research_depth_nudged": True}
        except Exception:
            pass

        # Merge into cumulative tool_results
        cumulative = dict(state.get("tool_results", {}))
        for tr in results:
            cumulative[tr["tool_call_id"]] = tr

        out: dict[str, Any] = {
            "messages": messages + tool_messages,
            "tool_calls_pending": [],  # all consumed
            "tool_calls_done": list(results),
            "tool_results": cumulative,
            "consecutive_tool_failures": consecutive_failures,
            "circuit_breaker_tripped": breaker_tripped_now,
            # If the breaker just tripped or max consecutive failures hit, force RESPOND
            "next_node": NodeName.RESPOND if (breaker_tripped_now or consecutive_failures >= 3) else NodeName.SUPERVISOR,
        }
        if state.get("_research_depth_nudged"):
            out["_research_depth_nudged"] = True
        return out
    finally:
        # Always restore prior ContextVar values, even if a tool raised or
        # the graph was interrupted by HITL.
        reset_current_session_messages(_messages_token)
        try:
            if _graph_gate_token is not None:
                from kazma_core.agent.tool_registry import _graph_hitl_gate_ctx

                _graph_hitl_gate_ctx.reset(_graph_gate_token)
        except NameError:
            pass
        if _state_tid_token is not None:
            reset_current_thread_id(_state_tid_token)


async def respond_node(state: SupervisorState, llm: Any = None) -> dict[str, Any]:
    """Respond node — finalizes the turn.

    Extracts the last assistant message as the response and increments
    the iteration counter. Also schedules automatic long-term memory
    writes (durable facts / turn snapshots) so recall is not tool-only.

    If the last message is a tool result (max-iterations forced respond
    mid-tool-loop), makes a final LLM call to synthesize a text answer
    from the collected tool results so the user gets a response.

    Args:
        state: The current supervisor state.
        llm:   The LLMProvider for synthesizing a final answer when
               max-iterations forces a respond mid-tool-loop. Optional
               for backward compat (the synthesis step is skipped if None).
    """
    messages = [_normalize_msg(m) for m in state.get("messages", [])]
    iteration = state.get("iteration", 0) + 1

    # Sanitize tool chains to remove any unhandled/dangling tool_calls
    # (e.g. when max_iterations forced routing to respond before ToolWorker ran)
    messages = sanitize_tool_chains(messages)

    logger.info(
        "[Respond] Finalizing turn (iteration=%d, messages=%d)",
        iteration,
        len(messages),
    )

    # If max iterations forced us here mid-tool-loop, there is often no
    # *final* user-visible answer after the last tool results.
    #
    # Bugs this guards against:
    # 1. Early assistant chatter (pre-tool "ممتاز…") counted as final.
    # 2. Sanitize strips dangling tool_calls from the last supervisor
    #    turn, leaving a short preamble (~100 chars) that looked like a
    #    final answer — smoke test "done" with no real reply (2026-07-27).
    # Only treat **substantial** plain assistant text AFTER the last tool
    # as a real final answer when max-iter forces stop.
    _MIN_FINAL_CHARS_ON_MAX = 250

    def _final_assistant_text_after_tools(msgs: list[dict[str, Any]]) -> str:
        last_tool_idx = -1
        for i, m in enumerate(msgs):
            if isinstance(m, dict) and m.get("role") in ("tool", "function"):
                last_tool_idx = i
        candidates: list[str] = []
        scan = msgs if last_tool_idx < 0 else msgs[last_tool_idx + 1 :]
        for m in scan:
            if not isinstance(m, dict):
                continue
            if m.get("role") not in ("assistant", "ai"):
                continue
            if m.get("tool_calls"):
                continue
            content = m.get("content") or ""
            if isinstance(content, str) and content.strip():
                candidates.append(content.strip())
        return candidates[-1] if candidates else ""

    def _has_final_assistant_after_tools(
        msgs: list[dict[str, Any]], *, min_chars: int = 1
    ) -> bool:
        text = _final_assistant_text_after_tools(msgs)
        return len(text) >= min_chars

    _last = messages[-1] if messages else {}
    _last_role = _last.get("role") if isinstance(_last, dict) else None
    _max_hit = iteration >= state.get("max_iterations", 15)
    # Always synthesize when we stop on a tool result, or when there is no
    # *substantial* plain assistant text after the last tool.
    _final_text = _final_assistant_text_after_tools(messages)
    _needs_synthesis = _max_hit and (
        _last_role in ("tool", "function")
        or len(_final_text) < _MIN_FINAL_CHARS_ON_MAX
        or bool(isinstance(_last, dict) and _last.get("tool_calls"))
    )
    # If the supervisor's LLM call failed (after retries), the assistant
    # message above is an honest error notice, NOT a real answer. Never
    # synthesize a plausible-looking final answer over a broken turn — that
    # was the root cause of the "model stopped thinking" symptom. Surface
    # the error and end the turn.
    if state.get("turn_failed"):
        logger.info(
            "[Respond] Turn failed (turn_failed=True) — skipping synthesis, "
            "surfacing honest error (iteration=%d messages=%d)",
            iteration,
            len(messages),
        )
        _needs_synthesis = False
    if _needs_synthesis:
        _llm = llm or state.get("_llm")
        if _llm is not None:
            try:
                _wrap_msg = {
                    "role": "user",
                    "content": (
                        "You hit the tool-round limit. Based on ALL tool results "
                        "above (including errors like unauthorized SQL functions), "
                        "write the final answer to the user NOW. "
                        "Do not call any more tools. Do not dig into source code. "
                        "If checking memory: report which of memory_search / "
                        "memory_store / layers worked or failed, and what needs "
                        "attention. Match the user's language (Arabic if they wrote Arabic)."
                    ),
                }
                _resp = await _llm.chat(messages + [_wrap_msg], tools=None)
                _content = getattr(_resp, "content", "") or ""
                if _content.strip():
                    messages.append({"role": "assistant", "content": _content})
                    logger.info(
                        "[Respond] Synthesized final answer (%d chars) after max iterations",
                        len(_content),
                    )
                else:
                    logger.warning(
                        "[Respond] Synthesis returned empty content "
                        "(last_role=%s messages=%d) — injecting recovery notice",
                        _last_role,
                        len(messages),
                    )
                    messages.append(
                        {
                            "role": "assistant",
                            "content": (
                                "⚠️ I hit the tool-round limit while investigating and "
                                "could not produce a summary. Please ask a narrower "
                                "follow-up, or say “summarize what you found.”"
                            ),
                        }
                    )
            except Exception as exc:
                logger.warning("[Respond] Could not synthesize final answer: %s", exc)
                messages.append(
                    {
                        "role": "assistant",
                        "content": (
                            "⚠️ I hit the tool-round limit and failed to summarize "
                            f"({type(exc).__name__}). Please try again with a smaller ask."
                        ),
                    }
                )
        else:
            logger.warning(
                "[Respond] Max iterations with no assistant text and no LLM bound "
                "(last_role=%s) — injecting recovery notice",
                _last_role,
            )
            messages.append(
                {
                    "role": "assistant",
                    "content": (
                        "⚠️ Turn stopped at the tool-round limit without a written "
                        "answer. Please send another message to continue."
                    ),
                }
            )
    elif _max_hit:
        logger.info(
            "[Respond] Max iterations but substantial final text already present "
            "(last_role=%s chars=%d) — skipping synthesis",
            _last_role,
            len(_final_text),
        )

    # ── Final empty-answer safety net ───────────────────────────────
    # The supervisor's nudge recovery (supervisor_node) handles empty LLM
    # content, but defence-in-depth: if the last assistant message that we
    # are about to stream to the user is empty/whitespace, inject an honest
    # fallback so the UI never shows "Done" with no bubble text. This guards
    # non-max-iteration turns (the max-iter path is handled above).
    if not _max_hit:
        _final_for_user = _final_assistant_text_after_tools(messages)
        if not _final_for_user:
            logger.warning(
                "[Respond] Final assistant text is empty on a normal turn "
                "(iteration=%d messages=%d last_role=%s) — injecting fallback "
                "so the turn is not silently empty",
                iteration, len(messages), _last_role,
            )
            messages.append(
                {
                    "role": "assistant",
                    "content": (
                        "⚠️ I completed my thinking but produced no written "
                        "answer this turn. Please send the message again, or "
                        "rephrase it — some models occasionally return an "
                        "empty reply."
                    ),
                }
            )

    # Post-turn memory: auto_store (vacuum) then consolidator (librarian)
    # in one task so consolidator can dedup against auto_store texts.
    # Provenance (session_id/turn) flows through to the V2 dual-write mirror
    # so beliefs/episodes carry source traces (resolution #3).
    try:
        from kazma_core.memory.consolidator import schedule_post_turn_memory

        schedule_post_turn_memory(
            messages,
            session_id=state.get("thread_id"),
            turn=iteration,
        )
    except Exception:
        logger.debug("[Respond] post_turn memory schedule failed", exc_info=True)

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

    messages = [_normalize_msg(m) for m in state.get("messages", [])]
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

    messages = [_normalize_msg(m) for m in state.get("messages", [])]
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
        return await respond_node(state, llm=llm)

    async def _check_saturation(state: SupervisorState) -> dict[str, Any]:
        return await check_saturation_node(state)

    async def _summarize(state: SupervisorState) -> dict[str, Any]:
        return await summarize_node(state, llm=llm)

    # ── Routing function ────────────────────────────────────────────
    def _route(state: SupervisorState) -> str:
        """Route from Supervisor based on next_node field."""
        next_node = state.get("next_node", NodeName.RESPOND)
        iteration = state.get("iteration", 0)
        max_iter = state.get("max_iterations", 15)

        # Force respond on max iterations
        if iteration >= max_iter:
            logger.warning("[Router] Max iterations (%d) hit — forcing respond", max_iter)
            # Inject a final instruction so the LLM synthesizes what it has
            # instead of producing an empty response.
            _msgs = state.get("messages", [])
            _has_final = any(
                isinstance(m, dict) and m.get("role") == "assistant" and (m.get("content") or "").strip()
                and not m.get("tool_calls")
                for m in _msgs[-3:]
            )
            if not _has_final:
                # No recent text answer — the model was stuck in tool loops.
                # We can't mutate state here (routing function), but the
                # respond_node will handle synthesizing from tool results.
                pass
            return NodeName.RESPOND

        if next_node == NodeName.TOOL_WORKER:
            return NodeName.TOOL_WORKER
        return NodeName.RESPOND

    def _route_from_worker(state: SupervisorState) -> str:
        """Route from Tool Worker — respects next_node (e.g. RESPOND when circuit breaker trips)."""
        next_n = state.get("next_node")
        if next_n == NodeName.RESPOND or state.get("circuit_breaker_tripped") or (state.get("consecutive_tool_failures", 0) >= 3):
            return NodeName.RESPOND
        return state.get("next_node", NodeName.SUPERVISOR)

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

    # Tool Worker → Supervisor / Respond (circuit breaker route)
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

