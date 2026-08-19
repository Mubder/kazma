"""Graph Builder — Compiles the Supervisor LangGraph StateMachine.

Graph topology
══════════════

    START → SUPERVISOR ⇄ TOOL_WORKER
                │
                ▼
             RESPOND → END

Explicit State & Deterministic Pruning (no mid-turn LLM summarization):
  * ``active_goal`` / ``active_attachments`` / ``hard_constraints`` are
    parsed at iteration 0 and re-injected as a system Working Memory
    anchor on **every** supervisor iteration.
  * Oversized contexts are trimmed deterministically
    (System Anchor + Turn-0 user + last N tool rounds) — never via an
    LLM ``summarize_node``.
  * ``hard_constraints`` (e.g. audit_only) structurally filter
    write/execute tools from the schema for the whole turn.

The Supervisor is the decision-maker.  On each iteration it:
  1. Injects Working Memory + applies deterministic trim if over budget.
  2. Calls the LLM with messages + (possibly filtered) tool schemas.
  3. If the LLM returns tool_calls → routes to TOOL_WORKER.
  4. If the LLM returns a final text response → routes to RESPOND.
  5. If max_iterations is hit → forced RESPOND.

Every node is fully async.  The graph compiles with an
AsyncSqliteSaver checkpointer for SIGKILL-safe durability.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from contextvars import ContextVar
from typing import Any

# Per-turn memory explain payload (chat UI panel). Set on iteration 0 inject;
# read by the thin ``_supervisor`` wrapper so every return path is covered.
_memory_explain_cv: ContextVar[dict[str, Any] | None] = ContextVar(
    "kazma_memory_explain", default=None
)

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

__all__ = [
    "TOOL_RESULT_MAX_CHARS",
    "build_supervisor_graph",
    "is_unusable_assistant_content",
    "respond_node",
    "sanitize_tool_chains",
    "supervisor_node",
    "tool_worker_node",
    "truncate_tool_result",
]

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# Personality injection helper
# ══════════════════════════════════════════════════════════════════════════

_PERSONALITY_MARKER = "[KAZMA_PERSONALITY]"

# Default cap for ordinary tools (env-overridable).
TOOL_RESULT_MAX_CHARS = int(
    os.environ.get("KAZMA_TOOL_RESULT_MAX_CHARS", "100000") or "100000"
)

# ── Failover state (module-level, process-wide) ──────────────────────
# One-off failover clients cached by model id (created only on primary
# failure; never mutate the active profile). Cooldowns give a failing
# provider time to recover before it is tried again.
_failover_clients: dict[str, Any] = {}
_failover_cooldowns: dict[str, float] = {}
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


def _resolve_tool_timeout() -> float:
    """Per-tool wall-clock timeout in seconds (default 120, <=0 disables).

    Resolution order: ConfigStore ``agent.tool_timeout_seconds`` →
    ``KAZMA_TOOL_TIMEOUT_SECONDS`` env → 120.0. Never raises.
    """
    try:
        from kazma_core.config_store import get_config_store

        val = get_config_store().get("agent.tool_timeout_seconds")
        if val is not None:
            return float(val)
    except Exception:
        pass
    raw = (os.environ.get("KAZMA_TOOL_TIMEOUT_SECONDS") or "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return 120.0


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


def is_unusable_assistant_content(text: str | None) -> bool:
    """True when *text* must not be shown as a final user-facing answer.

    DeepSeek and other models sometimes:
      - return empty content (handled separately), or
      - "recover" from empty via nudge with leaked tool-call markup
        (DSML / invoke XML / raw ``tool_calls``) instead of a real answer
        (2026-08-03: memory cleanup ended with 315 chars of DSML junk).
    """
    if text is None:
        return True
    s = str(text).strip()
    if not s:
        return True
    low = s.lower()
    # Leaked tool-call / protocol markup (structured, not prose "tool calls")
    leak_markers = (
        "dsml",
        "<|",
        "|>",
        "invoke name=",
        "</invoke",
        "function_call",
        "```tool",
        "<tool_call",
        "</tool_call",
        "arguments>{}",
        "tool_calls>",
        '"tool_calls"',
    )
    if any(m in low for m in leak_markers):
        return True
    if re.search(r"<\s*\|?\s*dsml", low) or re.search(r"invoke\s+name\s*=", low):
        return True
    # Mid-task planning with no substance (ends with open colon / "let me")
    if len(s) < 80 and any(
        s.rstrip().endswith(p)
        for p in (":", "…", "...", "—", "-")
    ):
        return True
    if re.search(
        r"\b(let me (probe|check|verify|look)|i(?:'ll| will) (?:now )?(?:call|use|run))\b"
        r".{0,80}$",
        low,
    ) and len(s) < 400:
        # Short "Let me probe whether tools are exposed:" style stubs
        if not re.search(r"\b(status:|summary:|result:|done|completed|found:)\b", low):
            return True
    return False


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


def prune_messages_if_exceeding_cap(messages: list[dict[str, Any]], max_tokens: int = 30000) -> list[dict[str, Any]]:
    """Prune message history if estimated tokens exceeds max_tokens.

    Preserves system messages and the most recent turn history while truncating
    large intermediate tool outputs and dropping older turns to keep the LLM
    from exceeding vendor context limits and returning empty completions.
    """
    from kazma_core.summarizer import estimate_tokens

    if estimate_tokens(messages) <= max_tokens:
        return messages

    system_msgs = [m for m in messages if isinstance(m, dict) and m.get("role") == "system"]
    non_system = [m for m in messages if isinstance(m, dict) and m.get("role") != "system"]

    truncated_non_system = []
    for m in non_system:
        m_copy = dict(m)
        if m_copy.get("role") == "tool" and isinstance(m_copy.get("content"), str):
            if len(m_copy["content"]) > 2000:
                m_copy["content"] = m_copy["content"][:2000] + "\n[Tool output truncated for context limits]"
        truncated_non_system.append(m_copy)

    while len(truncated_non_system) > 6 and estimate_tokens(system_msgs + truncated_non_system) > max_tokens:
        truncated_non_system.pop(0)

    repaired = sanitize_tool_chains(system_msgs + truncated_non_system)
    logger.info(
        "[ContextPruner] Pruned message history from %d tokens to %d tokens",
        estimate_tokens(messages), estimate_tokens(repaired),
    )
    return repaired


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
    except Exception:  # pragma: no cover — fence always ships with core
        # Fence unavailable → fail CLOSED (deep-audit 2026-08-19, finding
        # #8): untrusted memory hits must not enter the system prompt raw,
        # with the injection filter also disabled.
        logger.warning(
            "[graph_builder] prompt_fence unavailable — dropping %d memory "
            "hits (fail-closed)",
            len(memories),
        )
        return ""

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

    # ── Iteration-efficiency nudges (audit 2026-08-15) ──────────────
    # Long tasks burn iterations on one-tool-per-turn patterns (71 of 117
    # calls were python_exec on a calendar reproduction task). Inject a
    # strategy-change hint at milestones so the model course-corrects
    # BEFORE hitting the hard iteration wall.
    _ITER_NUDGE_MARKERS = (20, 40, 60, 80)
    _budget_nudge: dict[str, Any] | None = None
    if iteration in _ITER_NUDGE_MARKERS:
        _max_iter_now = int(state.get("max_iterations") or 15)
        _remaining = max(0, _max_iter_now - iteration)
        _budget_nudge = {
            "role": "system",
            "content": (
                f"SYSTEM BUDGET CHECK: You have used {iteration} iterations "
                f"({_remaining} remaining of {_max_iter_now}). If you are making "
                "steady progress, continue. If you are LOOPING (re-reading files "
                "you already read, writing debug scripts, retrying similar code):\n"
                "- BATCH tool calls: issue MULTIPLE reads/writes in ONE response\n"
                "- Use structured tools (generate_pdf, file_write) not python_exec\n"
                "- Summarize what you have so far and produce the final output NOW"
            ),
        }
        logger.info(
            "[Supervisor] iteration-efficiency nudge injected at iteration=%d "
            "(ephemeral — not checkpointed)",
            iteration,
        )

    # ── Mission mode: auto-extend past soft max_iterations ─────────
    # Budget /long still force-stops. Mission mode resets the wave counter
    # and continues until mission_hard_rounds (safety wall), without asking
    # the user to "Proceed" after every 40 rounds.
    _mission_patch: dict[str, Any] = {}
    try:
        from kazma_core.agent.long_task import (
            is_mission_mode,
            mission_hard_rounds,
            record_long_task_event,
        )

        _tid = str(state.get("thread_id") or "") or None
        _max_iter = int(state.get("max_iterations") or 15)
        _rounds_used = int(state.get("mission_rounds_used") or 0)
        if (
            _tid
            and is_mission_mode(_tid)
            and iteration >= _max_iter
        ):
            _hard = int(state.get("mission_hard_rounds") or mission_hard_rounds())
            _next_used = _rounds_used + max(iteration, _max_iter)
            if _next_used < _hard:
                logger.warning(
                    "[Supervisor] MISSION wave extend: used≈%d hard=%d "
                    "(reset iteration %d→0)",
                    _next_used,
                    _hard,
                    iteration,
                )
                record_long_task_event("mission_wave")
                messages = list(messages) + [
                    {
                        "role": "system",
                        "content": (
                            f"[MISSION AUTO-CONTINUE — wave complete, "
                            f"~{_next_used}/{_hard} rounds used]\n"
                            "Do **not** stop for user confirmation. "
                            "Continue remaining work only; avoid re-doing "
                            "completed steps. When the full user goal is "
                            "satisfied, write the final report with "
                            "no further tool calls."
                        ),
                    }
                ]
                iteration = 0
                _mission_patch = {
                    "mission_rounds_used": _next_used,
                    "mission_hard_rounds": _hard,
                    "messages": messages,
                    "auto_continue": True,
                }
            else:
                logger.warning(
                    "[Supervisor] MISSION hard wall reached used≈%d hard=%d — "
                    "forcing final synthesis",
                    _next_used,
                    _hard,
                )
                record_long_task_event("mission_hard_wall")
                _mission_patch = {
                    "mission_rounds_used": _next_used,
                    "force_synthesis": True,
                    "next_node": NodeName.RESPOND,
                }
    except Exception:
        logger.debug("[Supervisor] mission extend skipped", exc_info=True)

    if _mission_patch.get("next_node") == NodeName.RESPOND:
        return {
            **_mission_patch,
            "iteration": iteration,
            "messages": messages,
        }
    if _mission_patch.get("messages") is not None:
        messages = _mission_patch["messages"]

    # Carry mission counters on every later return from this supervisor visit
    _mission_carry: dict[str, Any] = {}
    for _mk in ("mission_rounds_used", "mission_hard_rounds", "auto_continue"):
        if _mk in _mission_patch:
            _mission_carry[_mk] = _mission_patch[_mk]
    if not _mission_carry and int(state.get("mission_hard_rounds") or 0) > 0:
        _mission_carry = {
            "mission_rounds_used": int(state.get("mission_rounds_used") or 0),
            "mission_hard_rounds": int(state.get("mission_hard_rounds") or 0),
        }

    # Long-task progress heartbeat (Telegram/gateway when progress sender set)
    try:
        from kazma_core.agent.long_task import maybe_heartbeat

        _recent_tools = [
            str(t.get("name") or "")
            for t in (state.get("tool_calls_done") or [])[:6]
            if isinstance(t, dict)
        ]
        _wave = int(state.get("mission_rounds_used") or 0) + int(iteration or 0)
        await maybe_heartbeat(
            thread_id=str(state.get("thread_id") or "") or None,
            iteration=int(iteration or 0) if not _mission_patch else max(1, int(iteration or 0) or 5),
            max_iterations=int(state.get("max_iterations") or 15),
            last_tools=_recent_tools,
        )
        # Extra heartbeat on mission wave boundaries
        if _mission_patch.get("mission_rounds_used"):
            await maybe_heartbeat(
                thread_id=str(state.get("thread_id") or "") or None,
                iteration=5,  # force send
                max_iterations=int(state.get("max_iterations") or 15),
                last_tools=[f"mission_wave≈{_mission_patch['mission_rounds_used']}"],
            )
    except Exception:
        logger.debug("[Supervisor] long-task heartbeat skipped", exc_info=True)

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
            **_mission_carry,
            "next_node": NodeName.RESPOND,
            "messages": messages
            + [
                {
                    "role": "assistant",
                    "content": "⚠️ ميزانية الجلسة انتهت. أعد التشغيل أو اتصل بالمسؤول.",
                }
            ],
        }

    # Mid-turn LLM summarization removed. Oversized contexts are handled
    # later via deterministic trim_messages (after Working Memory is set).
    # ContextAuthority may still run for observability / needs_compaction
    # flag, but we never replace messages with an LLM summary mid-loop.
    try:
        state_for_check = {**state, "messages": messages}
        if authority is not None and hasattr(authority, "counter"):
            if authority.counter.should_compact(messages) or state.get("needs_compaction"):
                breaker_reset = {
                    **breaker_reset,
                    "needs_compaction": True,
                }
                logger.info(
                    "[Supervisor] Context over budget — will apply deterministic trim "
                    "(no LLM summarize)"
                )
    except Exception:
        logger.debug("[Supervisor] budget probe skipped", exc_info=True)

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

    # Same-session short continuations ("Proceed", "try now") must inherit
    # the prior task — expand recall query + pin a continuity system note.
    # Opposite case: bulk "add this to ShipX memory" after a reminder thread
    # must NOT inherit ZCode/session topics via recall session_boost.
    # Topic shifts drop session boost and supersede the open task focus.
    _recall_query = last_user_content
    _store_intent = False
    _graph_cleanup = False
    _multi_part = False
    _store_focus = ""
    _recall_session_id = state.get("thread_id")
    _intent_mode = "normal"
    # Merged into every supervisor return after classification (focus lifecycle).
    intent_patch: dict[str, Any] = {}
    try:
        from kazma_core.agent.state import TaskStatus
        from kazma_core.agent.turn_input import (
            classify_turn_intent,
            extract_store_focus_query,
            latest_turn_priority_note,
            prior_substantive_user_texts,
        )

        _prev_status = str(state.get("task_status") or TaskStatus.IDLE)
        _prev_goal = str(state.get("task_goal_summary") or "")
        _intent_mode = classify_turn_intent(
            last_user_content,
            messages=messages,
            task_status=_prev_status,
            task_goal_summary=_prev_goal,
            use_embedding_drift=(iteration == 0),
        )
        _graph_cleanup = _intent_mode == "cleanup"
        _multi_part = _intent_mode == "multi_part"
        _store_intent = _intent_mode in ("store", "multi_part")
        _is_continue = _intent_mode == "continue"
        _is_shift = _intent_mode == "shift"

        intent_patch["intent_mode"] = _intent_mode

        # ── Intent Engine (§14 of KAZMA_INTENT_ENGINE.md) ─────────────
        # Classify every turn (focus + acts + entities) and write the
        # decision onto SupervisorState. Route is execute/constrain/loop.
        # Phase 2+: execute allowlist = {document_generate, research_deep};
        # multi-act research+document dispatches the composer.
        _decision = None
        if iteration == 0:
            try:
                from kazma_core.agent.intent.classify import classify_turn
                from kazma_core.agent.intent.config import intent_engine_enabled

                if intent_engine_enabled():
                    _atts = list(state.get("active_attachments") or [])
                    if not _atts:
                        try:
                            from kazma_core.agent.turn_input import extract_active_attachments

                            _atts = extract_active_attachments(
                                messages, user_text=last_user_content
                            )
                        except Exception:
                            _atts = []
                    _decision = await classify_turn(
                        last_user_content,
                        messages=messages,
                        attachments=_atts,
                        task_status=_prev_status,
                        task_goal_summary=_prev_goal,
                        llm=llm,
                        use_embedding_drift=(iteration == 0),
                        focus=_intent_mode,
                    )
                    _intent_mode = _decision.focus
                    intent_patch["intent_mode"] = _decision.focus
                    intent_patch["intent_route"] = str(_decision.route)
                    intent_patch["intent_acts"] = [
                        {"kind": a.kind, "confidence": a.confidence, "slots": a.slots, "source": a.source}
                        for a in _decision.acts
                    ]
                    intent_patch["intent_reason"] = _decision.reason
                    logger.info(
                        "[Supervisor] Intent Engine: focus=%s route=%s acts=%s reason=%s source=%s",
                        _decision.focus,
                        _decision.route,
                        [(a.kind, round(a.confidence, 2)) for a in _decision.acts],
                        _decision.reason,
                        _decision.source,
                    )
            except Exception:
                logger.debug("[Supervisor] Intent engine failed (non-fatal)", exc_info=True)
                _decision = None

        # Execute (Phase 2+: document_generate / research_deep / composer)
        if _decision is not None and _decision.route.value == "execute" and _decision.handler:
            try:
                from kazma_core.agent.intent.registry import get_registry as _get_intent_registry

                _h = _get_intent_registry().resolve(_decision.handler)
                if _h is not None:
                    _res = await asyncio.wait_for(
                        _h.run(_decision, {**state, "messages": messages}, llm=llm, tool_executor=tool_executor),
                        timeout=_h.timeout_seconds,
                    )
                else:
                    _res = None
            except Exception as exc:
                logger.warning("[Supervisor] handler %s failed: %s — loop", _decision.handler, exc)
                _res = None
            if _res is not None and _res.ok and not _res.escalate:
                return {
                    **intent_patch,
                    "messages": messages + [{"role": "assistant", "content": _res.message}],
                    "next_node": NodeName.RESPOND,
                    "iteration": iteration + 1,
                }
            # else fall through to loop

        # Constrain: inject plan_note once
        if (
            _decision is not None
            and _decision.route.value == "constrain"
            and _decision.plan_note
            and not any(
                m.get("role") == "system" and "INTENT ENGINE" in str(m.get("content") or "")
                for m in messages
            )
        ):
            messages = list(messages) + [{"role": "system", "content": _decision.plan_note}]

        # Collapse prior multi-step tool payloads when focus is done/shifted
        # so attention is not dominated by stale tool chains (PR5).
        if iteration == 0:
            try:
                from kazma_core.agent.topic_drift import (
                    should_stub_prior_tools,
                    stub_prior_tool_chains,
                )

                if should_stub_prior_tools(
                    intent_mode=_intent_mode,
                    prev_task_status=_prev_status,
                ):
                    messages = stub_prior_tool_chains(
                        messages, keep_last_n_user_turns=1
                    )
            except Exception:
                logger.debug("[Supervisor] tool stub skipped", exc_info=True)

        if _graph_cleanup:
            # Focus list/merge tools on named projects in the message
            _store_focus = extract_store_focus_query(last_user_content) or "kazma entities graph"
            _recall_query = _store_focus
            _recall_session_id = None
            intent_patch["task_status"] = TaskStatus.IN_PROGRESS
            intent_patch["task_goal_summary"] = (_store_focus or last_user_content)[:240]
            logger.info(
                "[Supervisor] intent_mode=cleanup recall=%r (no session_boost)",
                (_recall_query or "")[:80],
            )
        elif _store_intent or _multi_part:
            _store_focus = extract_store_focus_query(last_user_content)
            if _multi_part and not _store_focus:
                # Prefer project names from the user line for multi-part PAT/read work
                _store_focus = (last_user_content or "")[:200]
            if _store_focus:
                _recall_query = _store_focus
            # Drop same-thread session boost so prior reminder turns do not
            # drown a document-store request in ZCode/Grok quota facts.
            _recall_session_id = None
            intent_patch["task_status"] = TaskStatus.IN_PROGRESS
            intent_patch["task_goal_summary"] = (
                _store_focus or last_user_content
            )[:240]
            logger.info(
                "[Supervisor] intent_mode=%s recall=%r (no session_boost)",
                "multi_part" if _multi_part else "store",
                (_recall_query or "")[:80],
            )
        elif _is_continue:
            prev_users = prior_substantive_user_texts(
                messages, exclude=last_user_content, min_chars=8, limit=3
            )
            if prev_users:
                # Prefer last 3 substantive user turns as the real goal.
                _recall_query = " | ".join(prev_users[-3:])
                intent_patch["task_goal_summary"] = _recall_query[:240]
                logger.info(
                    "[Supervisor] intent_mode=continue phrase=%r expanded_recall from %d prior user turns",
                    last_user_content[:40],
                    len(prev_users[-3:]),
                )
            # Keep open focus; only re-open if we were idle/completed.
            if _prev_status in ("", TaskStatus.IDLE, TaskStatus.COMPLETED, TaskStatus.SUPERSEDED):
                intent_patch["task_status"] = TaskStatus.IN_PROGRESS
            else:
                intent_patch["task_status"] = _prev_status or TaskStatus.IN_PROGRESS
            # Always inject continuity instruction when history has more than
            # this one short line (industry: do not re-ask "what should I do?").
            _user_turns = sum(
                1
                for m in messages
                if isinstance(m, dict)
                and m.get("role") == "user"
                and str(m.get("content") or "").strip()
            )
            if iteration == 0 and _user_turns >= 2:
                _cont_note = (
                    "CONTINUITY: The user sent a short follow-up "
                    f"({last_user_content!r}). The open task is in the conversation "
                    "history above (and prior user messages). Continue that work "
                    "— all unfinished steps (GitHub read, memory store, analysis), "
                    "not only graph cleanup if that was only part of the goal. "
                    "Do NOT claim you forgot the task or ask what to do unless the "
                    "history truly has no prior goal."
                )
                messages.insert(
                    1 if messages and messages[0].get("role") == "system" else 0,
                    {"role": "system", "content": _cont_note},
                )
        elif _is_shift:
            # Soft-reset focus: do not session-boost old-thread episodes and
            # never expand recall to prior goals on a pivot.
            _recall_session_id = None
            _recall_query = last_user_content
            intent_patch["task_status"] = TaskStatus.SUPERSEDED
            intent_patch["auto_continue"] = False
            intent_patch["task_goal_summary"] = (last_user_content or "")[:240]
            logger.info(
                "[Supervisor] intent_mode=shift — session_boost off, auto_continue cleared, "
                "prior task superseded",
            )
        else:
            # normal chat — keep session boost; do not expand query
            if last_user_content.strip() and len(last_user_content.strip()) >= 40:
                intent_patch["task_status"] = TaskStatus.IN_PROGRESS
                intent_patch["task_goal_summary"] = last_user_content.strip()[:240]
            logger.info(
                "[Supervisor] intent_mode=normal session_boost=%s",
                bool(_recall_session_id),
            )

        # Priority pin: every non-continuation turn (drop the old len>=80 gate).
        # Continuations get CONTINUITY above instead of fighting priority text.
        # Only on iteration 0 so ReAct tool rounds do not re-stack system notes.
        if iteration == 0 and last_user_content.strip() and not _is_continue:
            _prio = latest_turn_priority_note(
                store_intent=_store_intent,
                graph_cleanup=_graph_cleanup,
                multi_part=_multi_part,
                topic_shift=_is_shift,
                focus=_store_focus,
            )
            _ins = 1 if messages and messages[0].get("role") == "system" else 0
            messages.insert(_ins, {"role": "system", "content": _prio})

        if iteration == 0:
            logger.info(
                "[Supervisor] intent_mode=%s task_status=%s session_boost=%s recall_q=%r",
                _intent_mode,
                intent_patch.get("task_status", _prev_status),
                _recall_session_id is not None,
                (_recall_query or "")[:80],
            )
        # Mid-turn ReAct: keep prior focus fields; do not re-supersede from the
        # same user message re-read as "latest" after tool messages.
        if iteration > 0:
            intent_patch = {
                "intent_mode": state.get("intent_mode") or _intent_mode,
                "task_status": state.get("task_status")
                or intent_patch.get("task_status")
                or _prev_status,
                "task_goal_summary": state.get("task_goal_summary")
                or intent_patch.get("task_goal_summary")
                or "",
            }
            if state.get("auto_continue") is False or _is_shift:
                # Preserve soft-reset once shift cleared auto_continue
                if state.get("task_status") == "superseded" or _is_shift:
                    intent_patch["auto_continue"] = False
                    intent_patch["task_status"] = "superseded"
    except Exception:
        logger.debug("[Supervisor] continuation/store intent expand skipped", exc_info=True)
        intent_patch = {}

    # ── Explicit Working Memory (immutable turn anchors) ───────────
    # Parse once at iteration 0; re-inject the system anchor every iteration.
    from kazma_core.agent.turn_input import (
        extract_active_attachments,
        extract_latest_user_text,
        filter_tools_for_constraints,
        format_working_memory_anchor,
        parse_hard_constraints,
        resolve_trim_token_budget,
        trim_messages_deterministic,
        WORKING_MEMORY_MARKER,
    )

    from kazma_core.agent.turn_input import (
        should_suppress_memory_recall,
        should_quarantine_documents_search,
        set_active_turn_context,
        reset_active_turn_context,
        bind_scratchpad_thread,
        reset_scratchpad_thread,
        drain_scratchpad_writes,
    )

    # Merge any tool-side scratchpad writes from the previous tool_worker hop.
    _scratch = dict(state.get("scratchpad") or {})
    try:
        _delta = drain_scratchpad_writes(str(state.get("thread_id") or ""))
        if _delta:
            _scratch.update(_delta)
    except Exception:
        pass

    working_memory_patch: dict[str, Any] = {}
    if iteration == 0:
        # Prefer transport-pinned fields when already set on input_state.
        _goal = str(state.get("active_goal") or "").strip()
        if not _goal:
            _goal = (last_user_content or extract_latest_user_text(messages) or "").strip()
        _atts = list(state.get("active_attachments") or [])
        if not _atts:
            _atts = extract_active_attachments(messages, user_text=_goal)
        _constraints = list(state.get("hard_constraints") or [])
        if not _constraints:
            _constraints = parse_hard_constraints(_goal)
        if state.get("scratchpad"):
            _scratch = dict(state.get("scratchpad") or {})
        working_memory_patch = {
            "active_goal": _goal[:4000],
            "active_attachments": _atts,
            "hard_constraints": _constraints,
            "scratchpad": _scratch,
        }
        if _constraints:
            logger.info(
                "[Supervisor] hard_constraints=%s attachments=%d goal_chars=%d",
                _constraints,
                len(_atts),
                len(_goal),
            )
    else:
        working_memory_patch = {
            "active_goal": str(state.get("active_goal") or ""),
            "active_attachments": list(state.get("active_attachments") or []),
            "hard_constraints": list(state.get("hard_constraints") or []),
            "scratchpad": _scratch,
        }
        # If iter>0 but goal empty (old checkpoint), backfill from messages once.
        if not working_memory_patch["active_goal"] and last_user_content:
            working_memory_patch["active_goal"] = last_user_content.strip()[:4000]
            if not working_memory_patch["hard_constraints"]:
                working_memory_patch["hard_constraints"] = parse_hard_constraints(
                    last_user_content
                )
            if not working_memory_patch["active_attachments"]:
                working_memory_patch["active_attachments"] = extract_active_attachments(
                    messages, user_text=last_user_content
                )

    # Merge working memory into intent_patch so every return path persists it.
    intent_patch = {**working_memory_patch, **intent_patch}

    _intent_for_anchor = str(
        intent_patch.get("intent_mode") or state.get("intent_mode") or ""
    )
    _suppress_recall = should_suppress_memory_recall(
        intent_mode=_intent_for_anchor,
        hard_constraints=list(intent_patch.get("hard_constraints") or []),
    )
    _quarantine_docs = should_quarantine_documents_search(
        intent_mode=_intent_for_anchor,
        hard_constraints=list(intent_patch.get("hard_constraints") or []),
        active_attachments=list(intent_patch.get("active_attachments") or []),
    )

    # Bind tool-side ContextVars for this supervisor hop (file_search quarantine, etc.)
    _turn_tok = set_active_turn_context(
        active_goal=str(intent_patch.get("active_goal") or ""),
        active_attachments=list(intent_patch.get("active_attachments") or []),
        hard_constraints=list(intent_patch.get("hard_constraints") or []),
        intent_mode=_intent_for_anchor,
        suppress_memory_recall=_suppress_recall,
        quarantine_documents_search=_quarantine_docs,
    )
    _sp_tok = bind_scratchpad_thread(str(state.get("thread_id") or ""))

    _wm_block = format_working_memory_anchor(
        active_goal=str(intent_patch.get("active_goal") or ""),
        active_attachments=list(intent_patch.get("active_attachments") or []),
        hard_constraints=list(intent_patch.get("hard_constraints") or []),
        intent_mode=_intent_for_anchor,
        scratchpad=dict(intent_patch.get("scratchpad") or {}),
    )
    # Replace any prior working-memory system message, then pin at index 0/1.
    messages = [
        m
        for m in messages
        if not (
            m.get("role") == "system"
            and WORKING_MEMORY_MARKER in str(m.get("content") or "")
        )
    ]
    _wm_ins = 1 if messages and messages[0].get("role") == "system" else 0
    messages.insert(_wm_ins, {"role": "system", "content": _wm_block})

    # Structural tool filter for the whole turn (audit_only / read_only / …).
    effective_tool_definitions = filter_tools_for_constraints(
        tool_definitions,
        list(intent_patch.get("hard_constraints") or []),
    )

    # Classify and route to optimal model if router is available
    routed_model = None
    if model_router is not None:
        from kazma_core.models.router import ModelRouter

        if last_user_content:
            # Route on expanded query so "proceed" does not pick a trivial profile
            profile = ModelRouter.classify(_recall_query or last_user_content)
            model_spec = model_router.route(profile)
            routed_model = model_spec.model
            logger.info(
                "[Supervisor] Routed to %s (profile=%s, model=%s)",
                profile.value,
                model_spec.provider,
                model_spec.model,
            )

    # Per-turn pin from the mouth (SSE/WS body.model) wins over the router
    # and does NOT mutate the process-wide active profile.
    turn_llm = llm
    try:
        from kazma_core.runtime.turn_model import resolve_turn_client

        turn_llm, _pinned = resolve_turn_client(llm)
        if _pinned:
            routed_model = _pinned
            logger.info("[Supervisor] turn-model pin=%s", _pinned)
    except Exception:
        turn_llm = llm

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

    if _suppress_recall and iteration == 0:
        logger.info(
            "[Supervisor] V2 recall suppressed (intent=%s constraints=%s)",
            _intent_for_anchor,
            list(intent_patch.get("hard_constraints") or []),
        )

    if (
        _per_turn_on
        and iteration == 0
        and last_user_content
        and not _suppress_recall
    ):
        # ── V2 cognitive recall (single memory stack) ─────────────────
        # Suppressed on topic-shift / audit_only so prior radiology/ZCode
        # beliefs cannot hijack the active attachment turn.
        # When memory.v2.use_new_stack is False, skip injection entirely
        # (V1 RRF was removed — there is no legacy rollback path).
        _use_v2 = False
        try:
            from kazma_core.memory.config import memory_v2_enabled

            _use_v2 = memory_v2_enabled()
        except Exception:
            pass

        if _use_v2:
            try:
                _top_k = _rag_top_k()
                from kazma_core.memory.recall import format_recall_block, recall

                _explain = None
                try:
                    from kazma_core.memory.config import read_memory_cfg

                    _explain = bool(
                        ((read_memory_cfg() or {}).get("v2") or {}).get(
                            "explain_recall", False
                        )
                    )
                except Exception:
                    _explain = False
                # to_thread: recall() is synchronous SQLite + embedding work
                # — running it on the loop stalled concurrent SSE/WS turns.
                result = await asyncio.to_thread(
                    recall,
                    _recall_query or last_user_content,
                    limit=_top_k,
                    session_id=_recall_session_id,
                    tenant_id=state.get("tenant_id", "default"),
                    explain=_explain,
                )
                if not result.empty:
                    mem_block = format_recall_block(result, explain=_explain)
                    if mem_block:
                        messages.insert(1, {"role": "system", "content": mem_block})
                        logger.info(
                            "[Supervisor] V2 recall: %d beliefs, %d episodes for turn",
                            len(result.beliefs), len(result.episodes),
                        )
                # Merge Knowledge Library into chat path (labeled, fenced).
                # Product merge: inject KB next to memory; optional promote to episodes.
                _kb_hits_for_explain: list[dict[str, Any]] = []
                try:
                    from kazma_core.memory.config import read_memory_cfg
                    from kazma_core.safety.prompt_fence import format_untrusted_block

                    _v2m = (read_memory_cfg() or {}).get("v2") or {}
                    if _v2m.get("merge_knowledge_into_chat", True):
                        from kazma_core.memory.federated_search import (
                            federated_search,
                            format_kb_hits_for_prompt,
                            promote_kb_hits_to_episodes,
                        )

                        # Industry path: hybrid RRF over inject-scoped libs first
                        # (auto_inject + optional smart search), same stack as
                        # get_knowledge_auto_inject_block — not a parallel FTS path.
                        fed = federated_search(
                            last_user_content,
                            tenant_id=state.get("tenant_id", "default"),
                            session_id=state.get("thread_id"),
                            limit_memory=0,
                            limit_kb=3,
                            include_memory=False,
                            include_knowledge=True,
                            kb_mode="inject",
                        )
                        _kb_hits_for_explain = list(fed.get("hits") or [])
                        kb_md = format_kb_hits_for_prompt(
                            _kb_hits_for_explain, max_hits=3
                        )
                        if not kb_md:
                            # Fallback: explicit inject helper (same RRF; handles
                            # footer wording if federated returned empty edge case)
                            try:
                                from kazma_core.stores.knowledge_index import (
                                    get_knowledge_auto_inject_block,
                                )

                                kb_md = await get_knowledge_auto_inject_block(
                                    last_user_content
                                )
                            except Exception:
                                kb_md = ""
                        if kb_md:
                            messages.insert(
                                1,
                                {
                                    "role": "system",
                                    "content": format_untrusted_block(
                                        kb_md, source="knowledge"
                                    ),
                                },
                            )
                            logger.info("[Supervisor] Knowledge Library merged into chat inject")
                            if _v2m.get("promote_kb_to_episodes", True):
                                try:
                                    promote_kb_hits_to_episodes(
                                        _kb_hits_for_explain,
                                        session_id=str(state.get("thread_id") or "kb"),
                                        tenant_id=state.get("tenant_id", "default"),
                                        max_promote=2,
                                    )
                                except Exception:
                                    logger.debug(
                                        "[Supervisor] kb promote skipped",
                                        exc_info=True,
                                    )
                except Exception:
                    logger.debug(
                        "[Supervisor] knowledge merge inject skipped", exc_info=True
                    )
                # Chat-turn Memory context (always when inject ran; full chips
                # when explain_recall is on — industry observability default).
                try:
                    from kazma_core.memory.recall import build_memory_explain_payload

                    _had_inject = (not result.empty) or bool(_kb_hits_for_explain)
                    if _explain or _had_inject:
                        _payload = build_memory_explain_payload(
                            query=_recall_query or last_user_content,
                            result=result if not result.empty else None,
                            kb_hits=_kb_hits_for_explain,
                            explain=True if _explain else "summary",
                        )
                        if _payload is not None:
                            if not _explain:
                                _payload["detail"] = "summary"
                                _payload["hint"] = (
                                    "Enable Settings → Memory → Explain recall "
                                    "for full channel chips on every hit."
                                )
                            else:
                                _payload["detail"] = "full"
                            _memory_explain_cv.set(_payload)
                except Exception:
                    logger.debug(
                        "[Supervisor] memory explain payload skipped",
                        exc_info=True,
                    )
                # Phase C: procedural skill hints (fenced, untrusted)
                try:
                    import sqlite3

                    from kazma_core.memory.procedural import (
                        format_procedural_hints,
                        match_procedural_dags,
                    )
                    from kazma_core.memory.schema_v2 import ensure_primary_schema
                    from kazma_core.paths import primary_memory_db

                    def _fetch_procedural_dags() -> list[Any]:
                        # to_thread: sync SQLite + DAG matching must stay off
                        # the event loop (same rule as recall() above).
                        pconn = sqlite3.connect(
                            primary_memory_db(), check_same_thread=False
                        )
                        try:
                            ensure_primary_schema(pconn)
                            return list(match_procedural_dags(
                                pconn,
                                last_user_content,
                                tenant_id=state.get("tenant_id", "default"),
                                limit=3,
                            ))
                        finally:
                            pconn.close()

                    dags = await asyncio.to_thread(_fetch_procedural_dags)
                    if dags:
                        hint = format_procedural_hints(dags)
                        if hint:
                            messages.insert(1, {"role": "system", "content": hint})
                except Exception:
                    logger.debug(
                        "[Supervisor] procedural inject skipped", exc_info=True
                    )
            except Exception:
                logger.warning(
                    "[Supervisor] V2 recall failed — skipping memory injection",
                    exc_info=True,
                )

    # Per-turn language lock (graph level so Telegram/Discord/Web share one path).
    # Always replace any prior LANGUAGE LOCK messages so a session that started
    # in Arabic does not keep an old lock (or skip injecting English) after the
    # user flips language mid-thread.
    if iteration == 0 and last_user_content:
        try:
            from kazma_core.language_lock import language_lock_message

            lock = language_lock_message(last_user_content)
            if lock:
                messages = [
                    m
                    for m in messages
                    if not (
                        m.get("role") == "system"
                        and "LANGUAGE LOCK" in str(m.get("content", ""))
                    )
                ]
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
    messages = prune_messages_if_exceeding_cap(messages)
    from kazma_core.summarizer import prune_tool_outputs
    messages = prune_tool_outputs(messages, max_tokens=24000)

    # Deterministic trim (PATH B replacement): never LLM-summarize mid-loop.
    _lm_for_trim = state.get("last_model") or routed_model
    _trim_budget = resolve_trim_token_budget(
        last_model=_lm_for_trim if isinstance(_lm_for_trim, str) else None
    )
    _before_trim = len(messages)
    messages = trim_messages_deterministic(
        messages,
        max_tokens=_trim_budget,
        keep_last_tool_rounds=8,
        active_goal=str(intent_patch.get("active_goal") or ""),
        working_memory_block=_wm_block,
    )
    messages = sanitize_tool_chains(messages)
    if len(messages) < _before_trim:
        breaker_reset = {
            **breaker_reset,
            "needs_compaction": False,
            "circuit_breaker_tripped": False,
            "consecutive_tool_failures": 0,
        }
        logger.info(
            "[Supervisor] Deterministic trim applied: %d → %d messages (budget=%d)",
            _before_trim,
            len(messages),
            _trim_budget,
        )

    # Soft force-plan: on the first supervisor hop of a tool-capable turn,
    # remind the model to open with a ```plan fence so the UI workbench
    # can pin a checklist (providers rarely expose true chain-of-thought).
    if iteration == 0 and effective_tool_definitions:
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

    # R4: soft-route deep research intent toward run_research_pipeline
    # §18 Phase 2: skip when the intent engine's constrain note already
    # covers research_deep (avoid double-nudging)
    if iteration == 0 and effective_tool_definitions and last_user_content:
        _intent_covers_research = (
            _decision is not None
            and any(a.kind == "research_deep" for a in _decision.acts)
            and any(
                m.get("role") == "system" and "INTENT ENGINE" in str(m.get("content", ""))
                for m in messages
            )
        )
        if not _intent_covers_research:
            try:
                from kazma_core.agent.research_policy import deep_research_route_hint

                route = deep_research_route_hint(last_user_content)
                if route and not any(
                    m.get("role") == "system"
                    and "DEEP RESEARCH ROUTE" in str(m.get("content", ""))
                    for m in messages
                ):
                    messages.append({"role": "system", "content": route})
            except Exception:
                logger.debug("[Supervisor] deep research route hint skipped", exc_info=True)

    # ── Steer: hard pause + soft drain, right before the LLM call ────
    # HARD steer: if one is pending, fire a LangGraph interrupt so the
    # running turn pauses cleanly. The /api/chat/steer (mode=hard) / gateway
    # /steer! poller detects the pause and resumes via
    # ainvoke(Command(resume=...)). On resume LangGraph re-runs this node;
    # peek-then-pop keeps the text present across that re-run (interrupt()
    # returns the resume value instead of pausing the second time), so we
    # pop+apply exactly once. Mirrors the commitment-gate conditional
    # interrupt (~line 1909).
    #
    # SOFT steer drain runs AFTER the hard gate (unconditionally). This
    # placement matters: LangGraph re-runs the node on resume, so draining
    # before the interrupt would pop soft steers on the pre-pause pass and
    # then discard them. Draining after the gate guarantees a single drain
    # on the path that actually reaches the LLM.
    try:
        from kazma_core.agent.steer import (
            drain_soft_steers,
            hard_steer_note,
            hard_steer_payload,
            peek_hard_steer,
            pop_hard_steer,
            soft_steer_note,
        )

        _steer_tid = str(state.get("thread_id") or "")
        if not _steer_tid:
            try:
                from kazma_core.safety.hitl import get_current_thread_id

                _steer_tid = str(get_current_thread_id() or "")
            except Exception:
                _steer_tid = ""
        _hard_text = peek_hard_steer(_steer_tid)
        if _hard_text:
            from langgraph.types import interrupt  # local import (cf. commitment gate)

            logger.info("[Supervisor] hard steer interrupt thread=%s", _steer_tid[:12])
            interrupt(hard_steer_payload(_hard_text))  # pauses; returns on resume
            _applied = pop_hard_steer(_steer_tid) or _hard_text
            messages.append({"role": "user", "content": hard_steer_note(_applied)})

        # Soft steer: append queued user notes for this LLM call. Persisted
        # into the checkpoint (messages), so they apply for the rest of the
        # turn + future turns. Zero disruption — no cancel, no pause.
        for _s in drain_soft_steers(_steer_tid):
            messages.append({"role": "user", "content": soft_steer_note(_s["text"])})
    except Exception:
        logger.exception("[Supervisor] steer gate/drain failed")

    start = time.monotonic()
    try:
        from kazma_core.retry import friendly_llm_error, load_retry_config
        from kazma_core.llm_provider import LLMError

        cfg = load_retry_config()
        # Guard: max_attempts <= 0 would make range(1, 1) empty and raise
        # last_exc=None — the LLM would never be called. Clamp to >= 1.
        cfg["max_attempts"] = max(1, int(cfg.get("max_attempts", 1) or 1))
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
        _served_by: list[str] = []  # failover bookkeeping: [model] when a chain model answered
        _llm_messages = list(messages) + ([_budget_nudge] if _budget_nudge else [])

        async def _call_llm_with_retry() -> Any:
            nonlocal _llm_attempts
            last_exc: Exception | None = None
            # Per-call max_tokens: tool-call iterations need less output
            # (tool JSON is bounded) — use 8192 to save cost. Content-only
            # turns (no tools) may produce long answers — use the full
            # configured limit (16384 default). This prevents the wasteful
            # truncation-retry loop on content-generation tasks.
            _call_max_tokens = 8192 if effective_tool_definitions else None
            for attempt in range(1, cfg["max_attempts"] + 1):
                try:
                    return await turn_llm.chat(
                        messages=_llm_messages,
                        tools=effective_tool_definitions if effective_tool_definitions else None,
                        model=routed_model,
                        max_tokens=_call_max_tokens,
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
                    # A rate-limit-exhausted 429 is transient (so failover
                    # fires) but the provider already did bounded backoff, so
                    # don't re-retry the SAME provider here.
                    skip_retry = getattr(exc, "kind", "") == "rate_limit_exhausted"
                    if is_transient and not skip_retry and attempt < cfg["max_attempts"]:
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

        # ── Model failover chain (agent.nonstop.failover) ───────────
        # After the primary model exhausts its retries on a TRANSIENT
        # failure (network/429/outage), try each model in the configured
        # chain via a one-off registry client — the active profile is NOT
        # mutated. Permanent 4xx errors never trigger failover (they would
        # fail identically on every model). Per-model cooldown prevents
        # hammering a provider that just failed.
        async def _try_failover_models(last_exc: Exception) -> Any:
            is_transient = isinstance(last_exc, retryable_exc) or bool(
                getattr(last_exc, "transient", False)
            )
            if not is_transient:
                return None
            try:
                from kazma_core.agent.nonstop import get_nonstop_config

                ns = get_nonstop_config()
            except Exception:
                return None
            if not ns.failover.enabled or not ns.failover.chain:
                return None
            try:
                from kazma_core.model_registry import get_model_registry

                registry = get_model_registry()
            except Exception:
                return None
            now = time.monotonic()
            for fb_model in ns.failover.chain:
                if not fb_model or fb_model == routed_model:
                    continue
                cooled_until = _failover_cooldowns.get(fb_model, 0.0)
                if now < cooled_until:
                    logger.info(
                        "[Failover] %s in cooldown (%.0fs left) — skipping",
                        fb_model,
                        cooled_until - now,
                    )
                    continue
                try:
                    client = _failover_clients.get(fb_model)
                    if client is None:
                        client = registry.get_client(fb_model)
                        _failover_clients[fb_model] = client
                    logger.warning(
                        "[Failover] Primary model '%s' failed transiently — "
                        "trying failover model '%s'",
                        routed_model,
                        fb_model,
                    )
                    response = await client.chat(
                        messages=_llm_messages,
                        tools=effective_tool_definitions if effective_tool_definitions else None,
                        model=fb_model,
                    )
                    logger.warning(
                        "[Failover] model '%s' answered after primary failure",
                        fb_model,
                    )
                    _served_by.append(fb_model)
                    return response
                except Exception as fb_exc:
                    _failover_cooldowns[fb_model] = now + ns.failover.cooldown_seconds
                    logger.warning(
                        "[Failover] model '%s' also failed: %s (cooldown %.0fs)",
                        fb_model,
                        fb_exc,
                        ns.failover.cooldown_seconds,
                    )
            return None

        async def _call_llm_resilient() -> Any:
            try:
                return await _call_llm_with_retry()
            except Exception as primary_exc:
                fb = await _try_failover_models(primary_exc)
                if fb is not None:
                    return fb
                raise

        response = await _call_llm_resilient()
    except Exception as exc:
        logger.error("[Supervisor] LLM call failed after retries: %s", exc)
        from kazma_core.retry import friendly_llm_error

        # Per-call ledger (observability §A): durable record of the failure.
        try:
            from kazma_core.agent.nonstop import get_nonstop_config

            if get_nonstop_config().ledger_enabled:
                from kazma_core.observability.llm_ledger import record_llm_call

                record_llm_call(
                    thread_id=str(state.get("thread_id", "")),
                    iteration=int(state.get("iteration", 0) or 0),
                    provider=type(llm).__name__,
                    model=str(routed_model or ""),
                    duration_ms=(time.monotonic() - start) * 1000,
                    status="error",
                    error_kind=str(getattr(exc, "kind", "") or type(exc).__name__),
                )
        except Exception:
            pass

        error_content = friendly_llm_error(exc)
        # Surface an HONEST failure rather than disguising it as a normal
        # assistant reply. ``turn_failed`` tells respond_node to skip
        # synthesis (no fabricated final answer over the broken turn) — the
        # user gets a clear error they can act on (the "model stopped
        # thinking" symptom's real cause).
        return {
            **breaker_reset,
            **intent_patch,
            **_mission_carry,
            "next_node": NodeName.RESPOND,
            "turn_failed": True,
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

    # Per-call ledger (observability §A): durable record of the success,
    # including failover attribution (failover_from = primary model).
    try:
        from kazma_core.agent.nonstop import get_nonstop_config

        if get_nonstop_config().ledger_enabled:
            from kazma_core.observability.llm_ledger import record_llm_call

            _served_model = _served_by[-1] if _served_by else ""
            record_llm_call(
                thread_id=str(state.get("thread_id", "")),
                iteration=int(state.get("iteration", 0) or 0),
                provider=type(llm).__name__,
                model=str(response.model or _served_model or routed_model or ""),
                prompt_tokens=int(response.usage.get("prompt_tokens", 0) or 0),
                completion_tokens=int(response.usage.get("completion_tokens", 0) or 0),
                cost_usd=float(response.cost_usd or 0.0),
                duration_ms=duration_ms,
                status="ok",
                failover_from=str(routed_model or "") if _served_model else "",
            )
    except Exception:
        pass

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
                "(iteration=%d) — retrying with pruned context nudge", iteration,
            )
            # Prune context specifically for nudge call to prevent sending bloated prompt
            _nudge_tail = (
                "Answer only the latest user request; do not resume a superseded or abandoned prior task."
                if intent_patch.get("intent_mode") == "shift"
                or intent_patch.get("task_status") in ("superseded", "abandoned")
                else (
                    "Based on the conversation and tool results above, tell the "
                    "user what you found and what remains unfinished."
                )
            )
            pruned_nudge_msgs = prune_tool_outputs(messages, max_tokens=14000, keep_recent_tool_outputs=2) + [
                {"role": "system", "content": (
                    "Your previous response was empty. Provide a clear, helpful "
                    "TEXT answer only (no tools, no XML, no DSML, no tool_calls). "
                    + _nudge_tail
                )},
            ]
            try:
                nudge_response = await turn_llm.chat(
                    messages=pruned_nudge_msgs,
                    tools=[],
                    model=routed_model,
                )
                if nudge_response.content and nudge_response.content.strip():
                    content = nudge_response.content.strip()
                    response = nudge_response  # update for tracing/cost
                    logger.info("[Supervisor] Nudge retry succeeded — content recovered (%d chars)", len(content))
                else:
                    logger.warning("[Supervisor] Nudge retry returned empty content — routing to synthesis fallback")
            except Exception as nudge_exc:
                logger.warning("[Supervisor] Nudge retry failed: %s — routing to synthesis fallback", nudge_exc)

        # Reject leaked tool-call markup / mid-thought stubs (not a real answer)
        if content and is_unusable_assistant_content(content):
            logger.warning(
                "[Supervisor] Unusable assistant content (%d chars) — "
                "forcing synthesis (leak/stub), iteration=%d",
                len(content),
                iteration,
            )
            content = ""

        # Auto-continuation guard for multi-step goals/tasks.
        # Topic shifts / superseded focus never auto-continue the old goal.
        is_auto = bool(state.get("auto_continue", False))
        if "auto_continue" in intent_patch:
            is_auto = bool(intent_patch["auto_continue"])
        if intent_patch.get("task_status") in ("superseded", "abandoned") or intent_patch.get("intent_mode") == "shift":
            is_auto = False
        if not is_auto and content:
            _content_lower = content.lower()
            if any(marker in _content_lower for marker in ["now section", "proceeding to section", "next section", "proceeding with section"]):
                # Only section-auto when not on a soft-reset pivot
                if intent_patch.get("intent_mode") != "shift":
                    is_auto = True

        # ``max_iter`` was referenced here without ever being bound in
        # ``supervisor_node`` (only the mission-mode local ``_max_iter`` and the
        # router's own ``max_iter`` exist), so the auto-continue path raised
        # NameError whenever ``is_auto`` was truthy. Resolve it from the same
        # source the router uses (state.max_iterations, default 15).
        max_iter = int(state.get("max_iterations") or 15)
        if is_auto and iteration + 1 < max_iter and content:
            logger.info("[Supervisor] Auto-continue active (iteration=%d/%d) — looping back to supervisor", iteration + 1, max_iter)
            assistant_msg = {"role": "assistant", "content": content}
            continuation_msg = {"role": "user", "content": "Please proceed automatically with the remaining steps and complete the task."}
            return {
                **breaker_reset,
                **intent_patch,
                **_mission_carry,
                "messages": messages + [assistant_msg, continuation_msg],
                "next_node": NodeName.SUPERVISOR,
                "iteration": iteration + 1,
                "last_model": response.model,
                "last_tokens": response.usage.get("total_tokens", 0),
                "last_cost_usd": response.cost_usd,
            }

        # If content is still empty or unusable after nudge, force synthesis
        if not content:
            logger.warning(
                "[Supervisor] Turn finished with no usable text (iteration=%d) — "
                "forcing respond_node synthesis",
                iteration,
            )
            return {
                **breaker_reset,
                **intent_patch,
                **_mission_carry,
                "messages": messages,
                "next_node": NodeName.RESPOND,
                "force_synthesis": True,
                "last_model": response.model,
                "last_tokens": response.usage.get("total_tokens", 0),
                "last_cost_usd": response.cost_usd,
            }

        # Pure text response → RESPOND. Mark completed when focus was open
        # and this turn is not mid multi-step tool work.
        _done_patch = dict(intent_patch)
        if _done_patch.get("intent_mode") in ("normal", "shift") and _done_patch.get(
            "task_status"
        ) not in ("superseded", "abandoned"):
            # Final answer with no tools — focus can rest as completed for
            # short Q&A; multi-part/store stay in_progress until tools finish.
            if _done_patch.get("intent_mode") == "normal" and len(
                (last_user_content or "").strip()
            ) < 120:
                _done_patch.setdefault("task_status", "completed")
        assistant_msg = {"role": "assistant", "content": content}
        return {
            **breaker_reset,
            **_done_patch,
            **_mission_carry,
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

    # Truncation detection: finish_reason="length" means the provider cut the
    # response at max_tokens — tool-call JSON is likely severed mid-string.
    # The tool worker reads this to give a truncation-specific corrective
    # error ("write in smaller chunks") instead of a generic schema complaint.
    _finish_reason = getattr(response, "finish_reason", "") or ""
    if _finish_reason == "length":
        logger.warning(
            "[Supervisor] Response TRUNCATED at max_tokens (finish_reason=length); "
            "tool-call arguments may be incomplete"
        )

    # Tools imply an open multi-step focus (unless user already superseded).
    _tool_patch = dict(intent_patch)
    if _tool_patch.get("task_status") != "superseded":
        _tool_patch["task_status"] = "in_progress"

    return {
        **breaker_reset,
        **_tool_patch,
        **_mission_carry,
        "messages": messages + [assistant_msg],
        "tool_calls_pending": pending,
        "tool_calls_done": [],  # reset for this iteration
        "next_node": NodeName.TOOL_WORKER,
        "iteration": iteration + 1,
        "last_model": response.model,
        "last_tokens": response.usage.get("total_tokens", 0),
        "last_cost_usd": response.cost_usd,
        "_last_finish_reason": _finish_reason,
    }


def _last_user_text(state: SupervisorState) -> str:
    """Most recent user message text (the commitment gate anchors relative
    phrases to it). Returns '' if none — the gate then degrades to audit-only."""
    msgs = state.get("messages") or []
    for m in reversed(msgs):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str):
            return c
        if isinstance(c, list):  # multimodal parts
            return " ".join(
                str(p.get("text", "")) for p in c
                if isinstance(p, dict) and p.get("type") == "text"
            )
    return ""


def _commitment_resolve_gate(
    state: SupervisorState,
    pending: list[PendingToolCall],
) -> tuple[list[PendingToolCall], list[ToolResult]]:
    """Phase 2.5 (SRP): the semantic commitment gate, extracted from
    tool_worker_node for independent testing + cleaner separation of policy
    (resolve) from execution. Runs authorize_effect per semantic tool, rewrites
    args, holds clarify/confirm (interrupt()), blocks deny.
    """
    from langgraph.types import interrupt  # not imported at module level
    semantic_blocked: list[ToolResult] = []
    semantic_hold: list[tuple[PendingToolCall, Any]] = []
    try:
        from datetime import datetime as _dt, timezone as _tz
        from kazma_core.safety.commitment import authorize_effect as _authz
        from kazma_core.safety.commitment.constraints import (
            is_commitment_enabled as _cmt_on,
            load_constraint_beliefs as _load_beliefs,
        )
        from kazma_core.safety.side_effects import requires_semantic_check as _needs_sem
        from kazma_core.metrics import record_commitment_terminal
    except Exception:
        _cmt_on = None  # type: ignore[assignment]
        _authz = None; _load_beliefs = None; _needs_sem = None  # type: ignore[assignment]
        record_commitment_terminal = None  # type: ignore[assignment]
    if _cmt_on and _cmt_on() and _needs_sem and _authz:
        try:
            _sem = [_tc for _tc in pending if _needs_sem(_tc["name"])]
            if _sem:
                _tenant = state.get("tenant_id") or "default"
                _req_at = _dt.now(_tz.utc)
                try:
                    _beliefs = _load_beliefs(_tenant) if _load_beliefs else []
                except Exception:
                    # Transient belief-store failure must not skip the whole
                    # gate (that would free-fire every semantic tool) —
                    # authorize without memory anchors instead
                    # (deep-audit 2026-08-19, finding #9).
                    logger.warning(
                        "[ToolWorker] constraint beliefs unavailable — gating "
                        "without memory anchors",
                        exc_info=True,
                    )
                    _beliefs = []
                _user_text = _last_user_text(state)
                _kept: list[PendingToolCall] = [_tc for _tc in pending if not _needs_sem(_tc["name"])]
                for _tc in _sem:
                    try:
                        _dec = _authz(
                            _tc["name"], _tc.get("arguments") or {},
                            user_text=_user_text, request_at=_req_at, memory_beliefs=_beliefs,
                            thread_id=state.get("thread_id"), tenant_id=_tenant,
                            context={"source": "graph"},
                        )
                    except Exception:
                        # Fail CLOSED (deep-audit 2026-08-19, finding #9):
                        # mirror the registry choke's posture so a broken
                        # policy engine cannot free-fire semantic acts (the
                        # remind/CoPilot class). The error tells the model to
                        # surface the failure instead of retrying blind.
                        logger.warning(
                            "[ToolWorker] commitment gate errored for %s — fail-closed",
                            _tc["name"],
                            exc_info=True,
                        )
                        semantic_blocked.append(ToolResult(
                            tool_call_id=str(_tc.get("id") or ""),
                            name=_tc["name"],
                            content=(
                                f"Commitment gate unavailable for {_tc['name']} — "
                                "blocked while the policy engine is unhealthy. "
                                "Do not retry; tell the user what failed."
                            ),
                            is_error=True, duration_ms=0, outcome="terminal",
                        ))
                        continue
                    if _dec.decision == "allow":
                        if _dec.rewritten_args:
                            _tc["arguments"] = _dec.rewritten_args
                        _kept.append(_tc)
                    elif _dec.decision in ("clarify", "confirm"):
                        semantic_hold.append((_tc, _dec))
                    else:
                        _q = _dec.reason or "denied by commitment gate"
                        semantic_blocked.append(ToolResult(
                            tool_call_id=str(_tc.get("id") or ""),
                            name=_tc["name"],
                        content=(f"Commitment gate denied {_tc['name']}: {_q}. "
                                 "Do not retry; tell the user why and what they can do."),
                        is_error=True, duration_ms=0, outcome="terminal",
                    ))
                    if record_commitment_terminal:
                        try: record_commitment_terminal("denied")
                        except Exception: pass
                pending = _kept
        except Exception:
            # Structural breakage inside the gate (not per-tool). Treated
            # like the layer being disabled — the kill-switch posture is
            # fail-open — but logged loudly so it is investigated
            # (deep-audit 2026-08-19, finding #9).
            logger.warning("[ToolWorker] commitment gate skipped (structural failure)", exc_info=True)

    if semantic_hold:
        _items = [{
            "tool_call_id": str(_tc.get("id") or ""),
            "tool": _tc["name"],
            "commitment_id": _dec.commitment_id,
            "question": _dec.clarify_question or _dec.reason or "needs clarification",
            "options": list(_dec.options) if _dec.options else [],
        } for _tc, _dec in semantic_hold]
        _sem_kind = ("semantic_confirm" if all(d.decision == "confirm" for _, d in semantic_hold)
                     else "semantic_clarify")
        _sem_payload = {
            "type": "hitl_approval", "kind": _sem_kind, "items": _items,
            "message": (_items[0]["question"] if len(_items) == 1
                        else f"{len(_items)} actions need clarification"),
        }
        _sem_choice = interrupt(_sem_payload)
        _choice_map = (_sem_choice if isinstance(_sem_choice, dict)
                       else {str(semantic_hold[0][0].get("id") or ""): _sem_choice})
        for _tc, _dec in semantic_hold:
            _tcid = str(_tc.get("id") or "")
            _opt_id = _choice_map.get(_tcid) if isinstance(_choice_map, dict) else _sem_choice
            _opt = next((o for o in (_dec.options or []) if o.get("id") == _opt_id), None)
            _patch = (_opt or {}).get("slots_patch")
            if _opt_id == "cancel":
                semantic_blocked.append(ToolResult(
                    tool_call_id=_tcid, name=_tc["name"],
                    content=("Commitment clarify cancelled by the user. Stop this "
                             "scheduling attempt; confirm what the user wants instead."),
                    is_error=True, duration_ms=0, outcome="terminal",
                ))
                if record_commitment_terminal:
                    try: record_commitment_terminal("cancelled")
                    except Exception: pass
            elif _patch is not None:
                # No-late-approve (AGENTS.md §20C): the LangGraph interrupt stays
                # resumable in the checkpointer long after the commitment's TTL
                # has passed, and sweep_expired may have already moved it to
                # 'expired'. The store-level "no late approve" guard only covers
                # resumes that go through update_status — NOT this clarify path,
                # which applies slots_patch directly. Re-check liveness here so a
                # stale fire_at cannot slip through.
                _alive = True
                if _dec.commitment_id:
                    try:
                        from kazma_core.safety.commitment.store import get_commitment
                        _c = get_commitment(_dec.commitment_id)
                        if _c is None or _c.status not in ("needs_clarify", "needs_confirm") or (
                            _c.expires_at is not None and time.time() > _c.expires_at
                        ):
                            _alive = False
                    except Exception:  # noqa: BLE001
                        logger.debug("[ToolWorker] commitment liveness check failed; assuming live")
                if not _alive:
                    semantic_blocked.append(ToolResult(
                        tool_call_id=_tcid, name=_tc["name"],
                        content=("This scheduling clarification has expired — the original "
                                 "timing is no longer valid. Ask the user to confirm the "
                                 "exact date/time again before re-scheduling."),
                        is_error=True, duration_ms=0, outcome="terminal",
                    ))
                    if record_commitment_terminal:
                        try:
                            record_commitment_terminal("clarify_expired")
                        except Exception:
                            pass
                else:
                    _tc["arguments"] = {**(_tc.get("arguments") or {}), **_patch}
                    pending.append(_tc)
            else:
                semantic_blocked.append(ToolResult(
                    tool_call_id=_tcid, name=_tc["name"],
                    content=(f"Commitment clarify unresolved for {_tc['name']}: "
                             f"{_dec.clarify_question or 'a specific time is required'}. "
                             "Ask the user for the exact date/time; do not re-call "
                             "this tool until they answer."),
                    is_error=True, duration_ms=0, outcome="terminal",
                ))
                if record_commitment_terminal:
                    try: record_commitment_terminal("clarify_unresolved")
                    except Exception: pass
    return pending, semantic_blocked


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

    # ── Structural hard_constraints gate (defense in depth) ────────
    # Schema allowlist at supervisor is primary; YOLO cannot expand it —
    # any non-allowlisted tool is blocked here too.
    constraint_blocked_results: list[ToolResult] = []
    _hc_list = list(state.get("hard_constraints") or [])
    try:
        from kazma_core.agent.turn_input import (
            is_tool_allowed_under_constraints,
            set_active_turn_context,
            reset_active_turn_context,
            bind_scratchpad_thread,
            reset_scratchpad_thread,
            drain_scratchpad_writes,
            should_quarantine_documents_search,
        )

        _turn_tok_tw = set_active_turn_context(
            active_goal=str(state.get("active_goal") or ""),
            active_attachments=list(state.get("active_attachments") or []),
            hard_constraints=_hc_list,
            intent_mode=str(state.get("intent_mode") or ""),
            quarantine_documents_search=should_quarantine_documents_search(
                intent_mode=str(state.get("intent_mode") or ""),
                hard_constraints=_hc_list,
                active_attachments=list(state.get("active_attachments") or []),
            ),
        )
        _sp_tok_tw = bind_scratchpad_thread(str(state.get("thread_id") or ""))
    except Exception:
        _turn_tok_tw = None
        _sp_tok_tw = None
        logger.debug("[ToolWorker] turn context bind skipped", exc_info=True)

    if _hc_list:
        try:
            from kazma_core.agent.turn_input import is_tool_allowed_under_constraints

            allowed: list[PendingToolCall] = []
            for tc in pending:
                name = str(tc.get("name") or "")
                if not is_tool_allowed_under_constraints(name, _hc_list):
                    constraint_blocked_results.append(
                        ToolResult(
                            tool_call_id=tc["id"],
                            name=name,
                            content=(
                                f"BLOCKED by hard_constraints {_hc_list}: "
                                f"tool '{name}' is not on the audit_only allowlist. "
                                "YOLO cannot override this. Use read/inspect tools only."
                            ),
                            is_error=True,
                            duration_ms=0.0,
                        )
                    )
                else:
                    allowed.append(tc)
            if constraint_blocked_results:
                logger.warning(
                    "[ToolWorker] hard_constraints=%s blocked tools: %s",
                    _hc_list,
                    [r.get("name") for r in constraint_blocked_results],
                )
            pending = allowed
        except Exception:
            logger.debug("[ToolWorker] constraint filter skipped", exc_info=True)

    if not pending and constraint_blocked_results:
        messages = [_normalize_msg(m) for m in state.get("messages", [])]
        tool_messages = [
            {
                "role": "tool",
                "tool_call_id": tr["tool_call_id"],
                "content": tr["content"],
            }
            for tr in constraint_blocked_results
        ]
        cumulative = dict(state.get("tool_results", {}))
        for tr in constraint_blocked_results:
            cumulative[tr["tool_call_id"]] = tr
        try:
            if _turn_tok_tw is not None:
                from kazma_core.agent.turn_input import reset_active_turn_context

                reset_active_turn_context(_turn_tok_tw)
            if _sp_tok_tw is not None:
                from kazma_core.agent.turn_input import reset_scratchpad_thread

                reset_scratchpad_thread(_sp_tok_tw)
        except Exception:
            pass
        return {
            "messages": messages + tool_messages,
            "tool_calls_pending": [],
            "tool_calls_done": list(constraint_blocked_results),
            "tool_results": cumulative,
            "next_node": NodeName.SUPERVISOR,
            "scratchpad": dict(state.get("scratchpad") or {}),
        }

    if not pending:
        logger.warning("[ToolWorker] No pending tool calls after filters — routing back")
        return {"next_node": NodeName.SUPERVISOR}

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
        reset_current_tenant_id,
        set_current_thread_id,
        set_current_tenant_id,
    )

    _state_tid_token = None
    if not get_current_thread_id():
        _state_tid = state.get("thread_id")
        if _state_tid:
            _state_tid_token = set_current_thread_id(str(_state_tid))

    # Bind tenant_id into the ContextVar so stateless memory tools
    # (memory_search / memory_store) can read it without a state handle.
    _state_tenant_token = set_current_tenant_id(str(state.get("tenant_id", "default")))

    # ── Bind delivery target from the _gateway routing block ───────
    # Authoritative node-level bind (the reliable layer — see the note above
    # about transport-layer ContextVars not crossing into node execution).
    # `schedule_task` reads this so a reminder can route back to the chat it
    # was booked from, even after the SessionStore row is TTL-evicted.
    from kazma_core.tools.send_message import (
        get_current_delivery_target,
        reset_current_delivery_target,
        set_current_delivery_target,
    )

    _delivery_token = None
    if not get_current_delivery_target():
        _gw = state.get("_gateway") or {}
        _delivery = _gw.get("delivery_target") if isinstance(_gw, dict) else None
        if _delivery:
            _delivery_token = set_current_delivery_target(str(_delivery))

    # Phase 2.5: the semantic commitment gate is extracted to
    # _commitment_resolve_gate for SRP (policy != execution). Runs INSIDE
    # the try below: the gate can raise GraphInterrupt (clarify/confirm
    # card), and the finally must restore the session/thread/tenant/
    # delivery ContextVar binds on that path too — with the call outside
    # the try, every clarify/confirm interrupt leaked the binds.

    try:
        pending, semantic_blocked = _commitment_resolve_gate(state, pending)

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
            _args = tc.get("arguments") or {}
            # Truncated-response guard: the provider cut the completion at
            # max_tokens (finish_reason="length"), severing the tool-call
            # JSON mid-string. The args arrive as {"raw": "<partial…"} or
            # empty. Don't execute — tell the model exactly what happened and
            # how to recover (smaller chunks), or it will retry the identical
            # oversized call until the circuit breaker trips.
            _malformed = not _args or set(_args.keys()) <= {"raw", "_malformed"}
            if _malformed and state.get("_last_finish_reason") == "length":
                result = {
                    "content": (
                        f"Error: Your previous response was TRUNCATED by the output token "
                        f"limit (max_tokens), which severed the '{tc['name']}' arguments "
                        f"mid-JSON — the tool was NOT executed. Do NOT retry the same "
                        f"large call. Instead, write the file in SMALLER pieces: call "
                        f"file_write once with the first section, then call file_append "
                        f"for each following section (keep each chunk under ~1500 "
                        f"characters). The provider already auto-retried with a doubled "
                        f"output limit; if you still see this, the content is simply too "
                        f"large for one response."
                    ),
                    "is_error": True,
                }
                duration_ms = (time.monotonic() - start) * 1000
                tracer.trace_tool_execution(
                    tool_name=tc["name"],
                    input_data=tc["arguments"],
                    output_data=result,
                    duration_ms=duration_ms,
                    success=False,
                )
                logger.warning(
                    "[ToolWorker] %s skipped — arguments truncated by max_tokens", tc["name"]
                )
                return ToolResult(
                    tool_call_id=tc["id"],
                    name=tc["name"],
                    content=result["content"],
                    is_error=True,
                    duration_ms=duration_ms,
                )

            # Document-generation guard: the model is trying to build a
            # PDF/DOCX/XLSX via raw Python (reportlab/fpdf/openpyxl) instead
            # of the purpose-built generate_* tools. This burns dozens of
            # iterations on code that produces inferior output — intercept
            # and redirect BEFORE execution (audit 2026-08-14: 100-iteration
            # rabbit hole on a 24-post Arabic calendar).
            _tool_name_low = str(tc["name"]).lower()
            if _tool_name_low in ("python_exec", "code_exec", "shell_exec"):
                _code = str(_args.get("code") or _args.get("command") or "")
                _code_low = _code.lower()
                if any(
                    marker in _code_low
                    for marker in (
                        "reportlab", "fpdf", "from fpdf", "simpledoctemplate",
                        "canvas(", "platypus", "openpyxl.workbook",
                        "docx.document", "from docx",
                    )
                ) and "generate_pdf" not in _code_low and "generate_docx" not in _code_low:
                    _doc_hint = (
                        "SYSTEM OVERRIDE: You are trying to build a document via raw "
                        "Python. STOP — use the dedicated document generator tools instead:\n"
                        "1. Write content to a .md file: file_write(path='output.md', content=...)\n"
                        "2. Generate: generate_pdf(title='...', markdown_path='output.md')\n"
                        "The generate_pdf tool handles Arabic shaping, RTL layout, styling, "
                        "and page numbering automatically — your manual code cannot. "
                        "Do NOT retry with Python."
                    )
                    _dur = (time.monotonic() - start) * 1000
                    return ToolResult(
                        tool_call_id=tc["id"],
                        name=tc["name"],
                        content=_doc_hint,
                        is_error=True,
                        duration_ms=_dur,
                    )
            # Per-tool wall-clock timeout (audit B: fault isolation). A hung
            # tool (deadlocked MCP server, wedged subprocess) previously
            # blocked the whole turn until the outer KAZMA_TURN_TIMEOUT
            # fired. Bound each call individually; a timeout is returned as
            # a HARD tool error so the loop breaker can trip after repeated
            # hangs. Config: ConfigStore agent.tool_timeout_seconds or
            # KAZMA_TOOL_TIMEOUT_SECONDS (default 120s; <=0 disables).
            _tool_timeout = _resolve_tool_timeout()
            # Phase 0 instrumentation (Commitment Layer): log every tool
            # execution with its side-effect tier + an args digest so mutator
            # traffic is observable before authorize_effect lands (Phase 1/2).
            # Interim tier detection reuses the existing TOOL_TIERS taxonomy
            # (safety.hitl.get_tool_tier); the unified side_effects.py registry
            # (plan §5) replaces this lookup in Phase 1.
            try:
                from kazma_core.safety.hitl import get_tool_tier

                _tier = get_tool_tier(tc["name"])
            except Exception:
                _tier = "unknown"
            logger.info(
                "[ToolWorker] exec name=%s tier=%s args=%s",
                tc["name"], _tier, _summarize_args_for_hitl(_args),
            )
            try:
                if _tool_timeout and _tool_timeout > 0:
                    result = await asyncio.wait_for(
                        tool_executor.execute(tc["name"], _args),
                        timeout=_tool_timeout,
                    )
                else:
                    result = await tool_executor.execute(tc["name"], _args)
            except asyncio.TimeoutError:
                duration_ms = (time.monotonic() - start) * 1000
                logger.error(
                    "[ToolWorker] %s timed out after %.0fs — returning tool error",
                    tc["name"],
                    _tool_timeout,
                )
                tracer.trace_tool_execution(
                    tool_name=tc["name"],
                    input_data=tc["arguments"],
                    output_data={"error": "timeout"},
                    duration_ms=duration_ms,
                    success=False,
                )
                return ToolResult(
                    tool_call_id=tc["id"],
                    name=tc["name"],
                    content=(
                        f"Error: Tool '{tc['name']}' timed out after "
                        f"{_tool_timeout:.0f}s and was aborted. Do NOT retry the "
                        "same call unchanged — narrow the request (smaller scope, "
                        "fewer results) or pick a different tool."
                    ),
                    is_error=True,
                    duration_ms=duration_ms,
                )
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
        # Bound here (not at the safe-tool section below) because the
        # auto_deny block appends deny ToolResults before that section runs.
        results: list[ToolResult] = []
        # Sub-agent auto_deny policy (AGENTS.md §7A): spawned child graphs are
        # built with checkpointer=None, so LangGraph interrupt() cannot persist
        # a pause and the external approval-timeout watcher doesn't cover the
        # ephemeral child thread_ids — the documented "1s auto-deny" never
        # fired, and _graph_hitl_gate_ctx skipped the SwarmMessageBus gate,
        # leaving child danger tools ungated/broken (audit finding). Deny them
        # directly here instead of routing through the non-functional
        # interrupt() path.
        if danger_tools and hitl_config and hitl_config.get("auto_deny"):
            for tc in danger_tools:
                results.append(ToolResult(
                    tool_call_id=str(tc.get("id") or ""),
                    name=tc["name"],
                    content=(
                        f"Tool '{tc['name']}' is a danger tool and was auto-denied "
                        "(sub-agent safety mode is 'auto_deny')."
                    ),
                    is_error=True,
                    duration_ms=0.0,
                    outcome="hard",
                ))
            try:
                from kazma_core.metrics import record_commitment_terminal
            except Exception:
                record_commitment_terminal = None  # type: ignore[assignment]
            if record_commitment_terminal:
                try:
                    record_commitment_terminal("auto_denied")
                except Exception:
                    pass
            danger_tools = []

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

            from kazma_core.safety.yolo import yolo_allowed as _yolo_allowed

            approval_input = {
                "type": "hitl_approval",
                "kind": "security",  # self-describing (§4.3): every payload carries kind
                "tool": primary_tool,
                "args": primary_args,
                "tools": tools_payload,
                "message": message,
                "yolo_allowed": _yolo_allowed(),
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
        results.extend(list(constraint_blocked_results) + list(semantic_blocked))
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

        # PR3 (loop kill): a TERMINAL outcome (unresolved / cancelled / denied
        # commitment clarify) ends the turn immediately. It is classified
        # TERMINAL (not HARD) so it doesn't credit the failure counter; here we
        # just force RESPOND so the model is never handed a retryable tool error
        # for a gate outcome. This is the invariant that makes the permission-
        # card loop class unkillable (incident 2026-08-12).
        _terminal_now = any(str(r.get("outcome", "")) == "terminal" for r in results)

        # ── Semantic stagnation detection ───────────────────────────
        # The hard-failure breaker misses loops of *successful-but-useless*
        # calls (same no-op edit, same denied path, same empty search). Track
        # (tool, canonical-args) signatures over a sliding window; a signature
        # repeated >= threshold times trips the same "stop and synthesize"
        # path with a strategy-change hint for the model.
        from kazma_core.agent.tool_loop_breaker import detect_stagnation, tool_signature

        # Only outcomes that represent real model behaviour feed the window:
        # policy denials / user denies / empties are control-plane signals and
        # must NOT count as stagnation (mirrors the hard-breaker credit rules —
        # repeating a denied call is the model being correctly blocked).
        _id_to_outcome = {str(tr.get("tool_call_id")): str(tr.get("outcome", "")) for tr in results}
        sigs = list(state.get("tool_signatures") or [])
        for tc in safe_tools + danger_tools:
            outcome = _id_to_outcome.get(str(tc.get("id")), "")
            if outcome in ("policy", "user_deny", "empty"):
                continue
            sigs.append(tool_signature(tc["name"], tc.get("arguments") or {}))
        sigs = sigs[-24:]  # bounded window
        stagnant_sig = detect_stagnation(sigs)
        if stagnant_sig and not breaker_tripped_now:
            stagnant_names = [
                tc["name"]
                for tc in (safe_tools + danger_tools)
                if tool_signature(tc["name"], tc.get("arguments") or {}) == stagnant_sig
            ]
            logger.warning(
                "[ToolWorker] Semantic stagnation detected (repeated identical "
                "calls: %s) — forcing synthesis with strategy-change hint",
                sorted(set(stagnant_names)),
            )
            try:
                from kazma_core.agent.long_task import record_long_task_event

                record_long_task_event("tool_loop_break")
            except Exception:
                pass
            breaker_tripped_now = True
            # Stamp this round's results with the strategy-change message.
            stamped_results: list[ToolResult] = []
            for tr in results:
                tr2 = dict(tr)
                tr2["content"] = (
                    "SYSTEM OVERRIDE: You have repeated the same tool call(s) "
                    f"({', '.join(sorted(set(stagnant_names))) or 'identical calls'}) "
                    "multiple times without progress. STOP retrying the identical "
                    "call — change strategy (different tool, different arguments, "
                    "or narrower scope) or synthesize your final answer now."
                )
                tr2["is_error"] = True
                tr2["outcome"] = "hard"
                stamped_results.append(tr2)
            results = stamped_results

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

        # Soft research-depth gate + R4 pipeline prefer (nudge once each)
        try:
            from kazma_core.agent.research_policy import (
                should_nudge_more_sources,
                should_prefer_pipeline,
            )

            already = bool(state.get("_research_depth_nudged"))
            already_pipe = bool(state.get("_research_pipeline_nudged"))
            turn_tools = [str(tc.get("name") or "") for tc in (safe_tools + danger_tools)]
            # Prefer pipeline first when deep intent + manual tools
            pipe_nudge = should_prefer_pipeline(
                messages, turn_tools, already_nudged=already_pipe
            )
            if pipe_nudge:
                tool_messages.append({"role": "system", "content": pipe_nudge})
                state = {**state, "_research_pipeline_nudged": True}
            else:
                nudge = should_nudge_more_sources(
                    messages, turn_tools, already_nudged=already
                )
                if nudge:
                    tool_messages.append({"role": "system", "content": nudge})
                    state = {**state, "_research_depth_nudged": True}
        except Exception:
            pass

        # Merge into cumulative tool_results, bounded to a recent window —
        # the dict is checkpointed every superstep and previously grew for
        # the thread's whole life (memory + checkpoint bloat on long
        # mission threads). dict order ≈ insertion order, so the tail is
        # the newest entries.
        cumulative = dict(state.get("tool_results", {}))
        for tr in results:
            cumulative[tr["tool_call_id"]] = tr
        if len(cumulative) > 200:
            cumulative = dict(list(cumulative.items())[-200:])

        out: dict[str, Any] = {
            "messages": messages + tool_messages,
            "tool_calls_pending": [],  # all consumed
            "tool_calls_done": list(results),
            "tool_results": cumulative,
            "consecutive_tool_failures": consecutive_failures,
            "circuit_breaker_tripped": breaker_tripped_now,
            "tool_signatures": sigs,
            # If the breaker just tripped or max consecutive failures hit, force RESPOND
            "next_node": NodeName.RESPOND if (breaker_tripped_now or consecutive_failures >= 3 or _terminal_now) else NodeName.SUPERVISOR,
        }
        if state.get("_research_depth_nudged"):
            out["_research_depth_nudged"] = True
        if state.get("_research_pipeline_nudged"):
            out["_research_pipeline_nudged"] = True
        # Merge typed scratchpad writes from update_scratchpad this hop
        try:
            from kazma_core.agent.turn_input import drain_scratchpad_writes

            _sp = dict(state.get("scratchpad") or {})
            _sp.update(drain_scratchpad_writes(str(state.get("thread_id") or "")))
            out["scratchpad"] = _sp
        except Exception:
            pass
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
        reset_current_tenant_id(_state_tenant_token)
        if _delivery_token is not None:
            reset_current_delivery_target(_delivery_token)
        try:
            if _turn_tok_tw is not None:
                from kazma_core.agent.turn_input import reset_active_turn_context

                reset_active_turn_context(_turn_tok_tw)
            if _sp_tok_tw is not None:
                from kazma_core.agent.turn_input import reset_scratchpad_thread

                reset_scratchpad_thread(_sp_tok_tw)
        except Exception:
            pass


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

    # Clear the per-turn file-read dedup cache (turn boundary — audit 2026-08-15)
    try:
        from kazma_core.tools.file_read import clear_turn_read_cache

        clear_turn_read_cache()
    except Exception:
        pass

    # Sanitize tool chains to remove any unhandled/dangling tool_calls
    # (e.g. when max_iterations forced routing to respond before ToolWorker ran)
    messages = sanitize_tool_chains(messages)

    logger.info(
        "[Respond] Finalizing turn (iteration=%d, messages=%d)",
        iteration,
        len(messages),
    )

    # If max iterations forced us here mid-tool-loop, there is often no
    # *complete* user-visible answer. Industry rule: ALWAYS run a final
    # synthesis LLM call on max-iter (unless turn_failed). Char-count
    # heuristics failed in production — a 382-char mid-diagnosis ("Let me
    # verify…") looked "substantial" and the UI showed Done with no
    # finished report (2026-08-03 long-horizon cleanup).
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

    _last = messages[-1] if messages else {}
    _last_role = _last.get("role") if isinstance(_last, dict) else None
    _max_hit = iteration >= state.get("max_iterations", 15)
    _final_text = _final_assistant_text_after_tools(messages)
    _force_synth = bool(state.get("force_synthesis"))
    _junk_final = bool(_final_text and is_unusable_assistant_content(_final_text))
    _usable_final = bool(_final_text) and not _junk_final
    # Synthesize when: max-iter, supervisor forced it, final is junk/leak,
    # OR there is simply no usable final text (e.g. last msg is tool result
    # after empty/leak was stripped). Never ship "no written answer" without
    # attempting synthesis first (2026-08-03 force_synthesis drop regression).
    _needs_synthesis = bool(
        _max_hit or _force_synth or _junk_final or not _usable_final
    )
    if _junk_final:
        logger.warning(
            "[Respond] Final draft unusable (leak/stub, %d chars) — forcing synthesis",
            len(_final_text or ""),
        )
    elif _force_synth:
        logger.info("[Respond] force_synthesis=True — running final synthesis")
    elif not _usable_final:
        logger.info(
            "[Respond] No usable final text (last_role=%s) — running final synthesis",
            _last_role,
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
                from kazma_core.runtime.turn_model import resolve_turn_client

                _llm, _ = resolve_turn_client(_llm)
            except Exception:
                pass
            try:
                from kazma_core.summarizer import prune_tool_outputs
                pruned_for_synth = prune_tool_outputs(messages, max_tokens=18000)
                _reason = (
                    "tool-round / long-horizon limit"
                    if _max_hit
                    else "unusable draft (leaked tool markup or incomplete stub)"
                    if _junk_final
                    else "forced finalization"
                )
                _wrap_msg = {
                    "role": "user",
                    "content": (
                        f"SYSTEM: Finalization required ({_reason}). "
                        "Write the COMPLETE final answer for the user NOW.\n"
                        "Rules:\n"
                        "- Do not call any more tools.\n"
                        "- Do not emit tool XML/DSML/markup.\n"
                        "- Do not continue mid-thought ('let me check…', 'next I will…').\n"
                        "- Summarize what you DID find/complete from tool results.\n"
                        "- Explicitly list what you did NOT finish and the next step "
                        "the user can ask for.\n"
                        "- Start with a one-line status (done / partial / blocked).\n"
                        "- Match the user's language (Arabic if they wrote Arabic)."
                    ),
                }
                _resp = await _llm.chat(pruned_for_synth + [_wrap_msg], tools=None)
                _content = getattr(_resp, "content", "") or ""
                if _content.strip() and not is_unusable_assistant_content(_content):
                    # Prefer synthesis as the terminal message; keep prior
                    # drafts in history but surface the complete answer last.
                    messages.append({"role": "assistant", "content": _content})
                    logger.info(
                        "[Respond] Synthesized final answer (%d chars, prior_draft=%d)",
                        len(_content),
                        len(_final_text or ""),
                    )
                elif _content.strip() and is_unusable_assistant_content(_content):
                    logger.warning(
                        "[Respond] Synthesis still unusable (%d chars) — fallback notice",
                        len(_content),
                    )
                    messages.append(
                        {
                            "role": "assistant",
                            "content": (
                                "⚠️ Partial result: I finished tools but could not "
                                "produce a clean final report (model returned tool "
                                "markup instead of text). Send **continue** or a "
                                "narrower request (e.g. list entities / invalidate X)."
                            ),
                        }
                    )
                else:
                    logger.warning(
                        "[Respond] Synthesis returned empty content "
                        "(last_role=%s messages=%d) — generating action summary fallback",
                        _last_role,
                        len(messages),
                    )
                    tools_used = [
                        tc.get("function", {}).get("name") or tc.get("name", "tool")
                        for m in messages if isinstance(m, dict) and m.get("tool_calls")
                        for tc in (m.get("tool_calls") or []) if isinstance(tc, dict)
                    ]
                    tool_summary_str = (
                        f" used tools ({', '.join(sorted(set(tools_used)))})"
                        if tools_used
                        else " hit the tool-round limit"
                    )
                    messages.append(
                        {
                            "role": "assistant",
                            "content": (
                                f"⚠️ Partial result:{tool_summary_str}. "
                                "I could not finish a full report before the step limit. "
                                "Send **continue** or a narrower request to finish."
                            ),
                        }
                    )
            except Exception as exc:
                logger.warning("[Respond] Could not synthesize final answer: %s", exc)
                messages.append(
                    {
                        "role": "assistant",
                        "content": (
                            "⚠️ Turn stopped at the tool-round limit. "
                            "Send **continue** with a shorter goal so I can finish."
                        ),
                    }
                )
        else:
            logger.warning(
                "[Respond] Max iterations with no LLM bound "
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

    # ── Final empty-answer safety net ───────────────────────────────
    # After synthesis (or if synthesis was skipped), ensure the user never
    # sees a blank turn. Prefer tool-aware partial notice over a vague
    # "no written answer" line.
    _final_for_user = _final_assistant_text_after_tools(messages)
    if not _final_for_user or is_unusable_assistant_content(_final_for_user):
        tools_used = [
            tc.get("function", {}).get("name") or tc.get("name", "tool")
            for m in messages
            if isinstance(m, dict) and m.get("tool_calls")
            for tc in (m.get("tool_calls") or [])
            if isinstance(tc, dict)
        ]
        tool_note = (
            f" Tools used: {', '.join(sorted(set(tools_used))[:12])}."
            if tools_used
            else ""
        )
        logger.warning(
            "[Respond] Still no usable final text after synthesis path "
            "(iteration=%d messages=%d last_role=%s force=%s) — terminal fallback",
            iteration,
            len(messages),
            _last_role,
            _force_synth,
        )
        messages.append(
            {
                "role": "assistant",
                "content": (
                    "⚠️ Partial result: I could not produce a clean final answer "
                    f"this turn.{tool_note} "
                    "Send **continue** or a narrower request "
                    "(e.g. `list memory entities` / `invalidate belief …`)."
                ),
            }
        )

    # Post-turn memory: signal that memory work is pending so the gateway
    # handler can fire it AFTER the graph reaches terminal state (preventing
    # the CoT "active again" flicker — the memory thread's SQLite writes
    # would otherwise re-trigger the CoT panel while it's showing "Done").
    return {
        "messages": messages,
        "iteration": iteration,
        "tool_calls_pending": [],
        "tool_calls_done": [],
        "next_node": "end",
        "_post_turn_memory": {
            "session_id": state.get("thread_id"),
            "turn": iteration,
            "tenant_id": state.get("tenant_id", "default"),
        },
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

