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
from typing import Any

__all__ = ["SUMMARIZATION_SYSTEM_PROMPT", "SUMMARY_TEMPLATE", "TOKEN_THRESHOLD", "clear_summary", "estimate_tokens", "format_summary", "get_summary", "prune_tool_outputs", "store_summary", "summarize"]

logger = logging.getLogger(__name__)

TOKEN_THRESHOLD = 4000

# In-memory summary store keyed by thread_id
_summaries: dict[str, str] = {}

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
    """Estimate token count from messages using a chars/4 heuristic.

    Args:
        messages: List of message dicts, tuples, or objects.

    Returns:
        Estimated token count.
    """
    total_chars = 0
    for raw_msg in messages:
        msg = _normalize_msg(raw_msg)
        content = msg.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        # Account for tool calls
        tool_calls = msg.get("tool_calls", [])
        for tc in tool_calls:
            if isinstance(tc, dict):
                if "name" in tc and "function" not in tc:
                    total_chars += len(str(tc.get("name", ""))) + len(str(tc.get("args", "")))
                else:
                    fn = tc.get("function", {})
                    total_chars += len(str(fn.get("name", ""))) + len(str(fn.get("arguments", "")))
    return total_chars // 4


def prune_tool_outputs(
    messages: list[Any],
    max_tokens: int = 24000,
    keep_recent_tool_outputs: int = 3,
) -> list[dict[str, Any]]:
    """Prune older tool/function output messages when conversation exceeds max_tokens.

    Retains schema validity, system prompts, user request, and the most recent
    `keep_recent_tool_outputs` tool results, while compacting older tool output
    bodies to prevent context saturation.

    Args:
        messages: List of message dicts/objects.
        max_tokens: Estimated token budget threshold.
        keep_recent_tool_outputs: Number of recent tool outputs to keep intact.

    Returns:
        New message list with older tool output bodies truncated if budget exceeded.
    """
    normalized = [_normalize_msg(m) for m in messages]
    if estimate_tokens(normalized) <= max_tokens:
        return normalized

    # Find indices of tool/function output messages
    tool_indices = [
        i for i, m in enumerate(normalized)
        if isinstance(m, dict) and m.get("role") in ("tool", "function")
    ]

    # Older tool output indices to prune
    indices_to_prune = (
        set(tool_indices[:-keep_recent_tool_outputs])
        if len(tool_indices) > keep_recent_tool_outputs
        else set()
    )
    pruned_messages: list[dict[str, Any]] = []

    for i, msg in enumerate(normalized):
        if i in indices_to_prune:
            content = msg.get("content", "")
            if isinstance(content, str) and len(content) > 250:
                truncated_content = (
                    content[:250] + f"\n\n[Tool output truncated from {len(content)} to 250 chars to fit context budget]"
                )
                msg_copy = dict(msg)
                msg_copy["content"] = truncated_content
                pruned_messages.append(msg_copy)
            else:
                pruned_messages.append(msg)
        else:
            pruned_messages.append(msg)

    # CRITICAL: If estimate_tokens is STILL > max_tokens, then even recent tool outputs
    # that are massive (e.g. > 1500 chars) must be capped to protect the context budget.
    if estimate_tokens(pruned_messages) > max_tokens and tool_indices:
        further_pruned: list[dict[str, Any]] = []
        for msg in pruned_messages:
            if isinstance(msg, dict) and msg.get("role") in ("tool", "function"):
                content = msg.get("content", "")
                if isinstance(content, str) and len(content) > 1500:
                    truncated = (
                        content[:1500] + f"\n\n[Tool output truncated from {len(content)} to 1500 chars to fit context budget]"
                    )
                    msg_copy = dict(msg)
                    msg_copy["content"] = truncated
                    further_pruned.append(msg_copy)
                else:
                    further_pruned.append(msg)
            else:
                further_pruned.append(msg)
        pruned_messages = further_pruned

    before_tokens = estimate_tokens(normalized)
    after_tokens = estimate_tokens(pruned_messages)
    logger.info(
        "[Summarizer] Pruned tool outputs: tokens %d -> %d",
        before_tokens,
        after_tokens,
    )
    return pruned_messages


def get_summary(thread_id: str) -> str | None:
    """Retrieve a stored summary for a thread."""
    return _summaries.get(thread_id)


def store_summary(thread_id: str, summary: str) -> None:
    """Store a summary for a thread (persists in memory for the session)."""
    _summaries[thread_id] = summary
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
