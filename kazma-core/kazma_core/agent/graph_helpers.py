"""Shared helpers for the supervisor graph (extracted from graph_builder)."""

from __future__ import annotations

import logging
import os
import re
from contextvars import ContextVar
from typing import Any

from kazma_core.summarizer import _normalize_msg

logger = logging.getLogger(__name__)

# Per-turn memory explain payload (chat UI panel). Set on iteration 0 inject;
# read by the thin ``_supervisor`` wrapper so every return path is covered.
_memory_explain_cv: ContextVar[dict[str, Any] | None] = ContextVar(
    "kazma_memory_explain", default=None
)

_PERSONALITY_MARKER = "[KAZMA_PERSONALITY]"

# Default cap for ordinary tools (env-overridable).
TOOL_RESULT_MAX_CHARS = int(
    os.environ.get("KAZMA_TOOL_RESULT_MAX_CHARS", "100000") or "100000"
)

# Higher cap for research / crawl tools so long pages reach the model.
TOOL_RESULT_RESEARCH_MAX_CHARS = int(
    os.environ.get("KAZMA_TOOL_RESULT_RESEARCH_MAX_CHARS", "200000") or "200000"
)

# Workspace file tools used to inherit the research 200k cap via a
# `"file" in name` substring test — file_search/file_read then re-inflated
# a just-trimmed prompt to 45–51k tokens mid-turn. Keep a tighter dedicated
# cap; operators can raise KAZMA_TOOL_RESULT_FILE_MAX_CHARS.
TOOL_RESULT_FILE_MAX_CHARS = int(
    os.environ.get("KAZMA_TOOL_RESULT_FILE_MAX_CHARS", "32000") or "32000"
)
_FILE_TOOL_NAMES = frozenset(
    {
        "file_read",
        "file_search",
        "file_list",
        "file_view",
        "read_file_part",
        "codebase_search",
        "mcp__filesystem__read_text_file",
        "mcp__filesystem__read_multiple_files",
        "mcp__filesystem__read_file",
        "mcp__filesystem__directory_tree",
        "mcp__filesystem__list_directory",
    }
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
        "shell_exec",
        "python_exec",
        "run",
        "run_file",
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

    Research/crawl tools use a higher default cap (200,000 chars) so long
    pages reach the model. Workspace file tools use a tighter cap (32,000)
    so a just-trimmed prompt cannot be re-inflated by file_read/file_search.
    Set env variable KAZMA_TOOL_RESULT_MAX_CHARS=0 or <= 0 for unlimited.
    """
    if TOOL_RESULT_MAX_CHARS <= 0 or os.environ.get("KAZMA_NO_TRUNCATE") == "1":
        return content

    if max_chars is None:
        name = (tool_name or "").strip()
        if name in _FILE_TOOL_NAMES:
            max_chars = max(1000, TOOL_RESULT_FILE_MAX_CHARS)
        elif name in _RESEARCH_TOOL_NAMES:
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

