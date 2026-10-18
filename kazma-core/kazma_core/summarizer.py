"""Conversation Summarization Middleware.

Auto-summarizes session history when the context window fills up,
injecting a compressed preamble so the agent never loses thread.

Usage:
    from kazma_core.summarizer import estimate_tokens, summarize, TOKEN_THRESHOLD

    tokens = estimate_tokens(messages)
    if tokens > TOKEN_THRESHOLD:
        summary = await summarize(messages, llm)
"""

from __future__ import annotations

import logging
import os
from typing import Any

__all__ = ["SUMMARIZATION_SYSTEM_PROMPT", "SUMMARY_TEMPLATE", "TOKEN_THRESHOLD", "clear_summary", "estimate_tokens", "format_summary", "get_summary", "prune_tool_outputs", "store_summary", "summarize"]

logger = logging.getLogger(__name__)

TOKEN_THRESHOLD = 4000

# In-memory summary store keyed by thread_id.
# Bounded LRU (audit: unbounded module-global dict grew forever across
# long-horizon sessions). OrderedDict + move_to_end on access; oldest
# entries evicted past _SUMMARIES_MAX_ENTRIES.
from collections import OrderedDict

_SUMMARIES_MAX_ENTRIES = int(os.environ.get("KAZMA_SUMMARIES_MAX_ENTRIES", "500") or "500")
_summaries: OrderedDict[str, str] = OrderedDict()

SUMMARIZATION_SYSTEM_PROMPT = """\
You are a conversation summarizer. Below is a conversation between a user and an AI agent.
Summarize it compactly. Include:
- What the user asked for
- Decisions made
- Tools invoked and their results
- Files created or modified
- Open questions or pending tasks
- User preferences expressed

Keep it under 500 words. Write in past tense. This summary will become the agent's memory \
for future turns."""

SUMMARY_TEMPLATE = """\
[CONVERSATION SUMMARY — generated automatically to keep context manageable]

Summary of prior conversation:
{summary}

[End summary. The conversation continues below.]"""


def _normalize_msg(msg: Any) -> dict[str, Any]:
    """Normalize a message (dict, tuple, or BaseMessage) to a dict."""
    if isinstance(msg, dict):
        return msg
    if isinstance(msg, tuple) and len(msg) == 2:
        return {"role": str(msg[0]), "content": msg[1], "tool_calls": []}
    if hasattr(msg, "content"):
        return {
            "role": getattr(msg, "type", getattr(msg, "role", "unknown")),
            "content": msg.content,
            "tool_calls": getattr(msg, "tool_calls", []) or [],
        }
    return {"role": "unknown", "content": str(msg), "tool_calls": []}


def estimate_tokens(messages: list[Any]) -> int:
    """Estimate token count from messages using a script-aware heuristic.

    Latin text runs ~4 chars/token, but Arabic (this product's core
    audience) runs ~2.5 chars/token — a flat chars/4 undercounted
    Arabic-heavy conversations, letting them exceed the trim budget until
    the provider 400'd (caught downstream by compaction, but degraded).

    Args:
        messages: List of message dicts, tuples, or objects.

    Returns:
        Estimated token count.
    """
    total = 0
    for raw_msg in messages:
        msg = _normalize_msg(raw_msg)
        content = msg.get("content", "")
        if isinstance(content, str):
            total += _estimate_text_tokens(content)
        # Account for tool calls
        tool_calls = msg.get("tool_calls", [])
        for tc in tool_calls:
            if isinstance(tc, dict):
                if "name" in tc and "function" not in tc:
                    total += _estimate_text_tokens(str(tc.get("name", "")) + str(tc.get("args", "")))
                else:
                    fn = tc.get("function", {})
                    total += _estimate_text_tokens(
                        str(fn.get("name", "")) + str(fn.get("arguments", ""))
                    )
    return total


_ARABIC_RANGES = (
    (0x0600, 0x06FF),   # Arabic
    (0x0750, 0x077F),   # Arabic Supplement
    (0x08A0, 0x08FF),   # Arabic Extended-A
    (0xFB50, 0xFDFF),   # Arabic Presentation Forms-A
    (0xFE70, 0xFEFF),   # Arabic Presentation Forms-B
)


def _estimate_text_tokens(text: str) -> int:
    """Estimate tokens for one string: chars/4 for Latin, ~2.5 for Arabic."""
    if not text:
        return 0
    arabic = 0
    for ch in text:
        cp = ord(ch)
        for lo, hi in _ARABIC_RANGES:
            if lo <= cp <= hi:
                arabic += 1
                break
    other = len(text) - arabic
    return (other // 4) + (arabic * 2) // 5


#: Older turns' tool outputs are cut to this many characters first.
_OLDER_TURN_KEEP_CHARS = 250
#: Then, still over budget, any older turn's output past this length.
_OLDER_TURN_CAP_CHARS = 1500
#: The running turn's outputs are cut only past this length, newest last,
#: and only while the context is still over its budget.
_CURRENT_TURN_FLOOR_CHARS = 8000


def _current_turn_start(messages: list[dict[str, Any]]) -> int:
    """Index of the running turn's question: the last user message."""
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if isinstance(m, dict) and m.get("role") == "user":
            return i
    return 0


def _cut(msg: dict[str, Any], keep: int, why: str) -> dict[str, Any]:
    content = msg.get("content", "")
    if not isinstance(content, str) or len(content) <= keep:
        return msg
    out = dict(msg)
    out["content"] = (
        content[:keep]
        + f"\n\n[Tool output truncated from {len(content)} to {keep} chars to fit "
        f"context budget{why}]"
    )
    return out


def prune_tool_outputs(
    messages: list[Any],
    max_tokens: int = 24000,
    keep_recent_tool_outputs: int = 3,
) -> list[dict[str, Any]]:
    """Cut tool outputs, older turns first, when the conversation is over budget.

    The running turn's outputs (after the last user message) are what the
    model is working from, and the supervisor saves the pruned list back to
    the checkpoint, so a cut is permanent. Live 2026-10-18 (Telegram, an
    X archive): the newest result of the turn, ``x_status`` at 1,571
    characters, was cut to 1,500 as it arrived -- the fourth post's id lost
    -- and two tool calls later to 250, "the most recent 3" being counted
    over the whole chat. The model built the archive from what was left and
    then told the owner the tool could not return more.

    Order: older turns' outputs beyond the newest ``keep_recent_tool_outputs``
    to 250 characters; still over, any older turn's output past 1,500; still
    over, the running turn's outputs past 8,000 characters, oldest first,
    stopping as soon as the context fits (each cut says what to do).
    """
    normalized = [_normalize_msg(m) for m in messages]
    if estimate_tokens(normalized) <= max_tokens:
        return normalized

    turn_start = _current_turn_start(normalized)
    tool_indices = [
        i for i, m in enumerate(normalized)
        if isinstance(m, dict) and m.get("role") in ("tool", "function")
    ]
    older = [i for i in tool_indices if i < turn_start]
    current = [i for i in tool_indices if i > turn_start]
    protected_recent = set(tool_indices[-keep_recent_tool_outputs:]) if keep_recent_tool_outputs > 0 else set()

    pruned = list(normalized)
    for i in older:
        if i not in protected_recent:
            pruned[i] = _cut(pruned[i], _OLDER_TURN_KEEP_CHARS, "")

    if estimate_tokens(pruned) > max_tokens:
        for i in older:
            pruned[i] = _cut(pruned[i], _OLDER_TURN_CAP_CHARS, "")

    if estimate_tokens(pruned) > max_tokens:
        for i in current:
            if estimate_tokens(pruned) <= max_tokens:
                break
            pruned[i] = _cut(
                pruned[i], _CURRENT_TURN_FLOOR_CHARS,
                "; call the tool again with a narrower request for the rest",
            )

    before_tokens = estimate_tokens(normalized)
    after_tokens = estimate_tokens(pruned)
    logger.info(
        "[Summarizer] Pruned tool outputs: tokens %d -> %d",
        before_tokens,
        after_tokens,
    )
    return pruned


def get_summary(thread_id: str) -> str | None:
    """Retrieve a stored summary for a thread (marks it most-recently-used)."""
    summary = _summaries.get(thread_id)
    if summary is not None:
        _summaries.move_to_end(thread_id)
    return summary


def store_summary(thread_id: str, summary: str) -> None:
    """Store a summary for a thread (bounded LRU — oldest evicted past cap)."""
    _summaries[thread_id] = summary
    _summaries.move_to_end(thread_id)
    while len(_summaries) > _SUMMARIES_MAX_ENTRIES:
        evicted_tid, _ = _summaries.popitem(last=False)
        logger.debug("[Summarizer] Evicted summary for thread %s (LRU cap)", evicted_tid)
    logger.info("[Summarizer] Stored summary for thread %s (%d chars)", thread_id, len(summary))


def clear_summary(thread_id: str) -> None:
    """Clear a stored summary."""
    _summaries.pop(thread_id, None)


def format_summary(summary_text: str) -> str:
    """Format a summary into the injection template."""
    return SUMMARY_TEMPLATE.format(summary=summary_text)


async def summarize(messages: list[Any], llm: Any, thread_id: str = "") -> str:
    """Generate a conversation summary using the LLM.

    Args:
        messages:   Full conversation messages.
        llm:        LLMProvider instance with async chat() method.
        thread_id:  Optional thread ID for persistence.

    Returns:
        Formatted summary string ready for injection as a SystemMessage.
    """
    # Build the summarization prompt
    conversation_text: list[str] = []
    for raw_msg in messages:
        msg = _normalize_msg(raw_msg)
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if role == "system":
            continue  # skip system messages
        if content:
            conversation_text.append(f"{role}: {content}")
        # Include tool calls if present
        tool_calls = msg.get("tool_calls", [])
        for tc in tool_calls:
            if isinstance(tc, dict):
                if "name" in tc and "function" not in tc:
                    conversation_text.append(f"tool_call: {tc.get('name', '?')}({tc.get('args', '')})")
                else:
                    fn = tc.get("function", {})
                    conversation_text.append(f"tool_call: {fn.get('name', '?')}({fn.get('arguments', '')})")

    conversation_block = "\n".join(conversation_text)

    # Call the LLM
    try:
        response = await llm.chat(
            messages=[
                {"role": "system", "content": SUMMARIZATION_SYSTEM_PROMPT},
                {"role": "user", "content": f"Summarize this conversation:\n\n{conversation_block}"},
            ],
        )
        summary_text = response.content
    except Exception as exc:
        logger.error("[Summarizer] LLM call failed: %s", exc)
        # Fallback: simple extractive summary
        summary_text = _fallback_summary(messages)

    # Format and store
    formatted = format_summary(summary_text)
    if thread_id:
        store_summary(thread_id, summary_text)

    logger.info("[Summarizer] Generated summary (%d chars)", len(formatted))
    return formatted


def _fallback_summary(messages: list[Any]) -> str:
    """Generate a simple extractive summary when LLM is unavailable."""
    parts: list[str] = []
    for raw_msg in messages:
        msg = _normalize_msg(raw_msg)
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user" and content and isinstance(content, str):
            parts.append(f"- User asked: {content[:100]}")
        elif role == "assistant" and content and isinstance(content, str):
            parts.append(f"- Agent responded: {content[:100]}")
    return "\n".join(parts[-10:]) if parts else "(no conversation to summarize)"
