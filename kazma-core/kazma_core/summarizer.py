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

__all__ = ["SUMMARIZATION_SYSTEM_PROMPT", "SUMMARY_TEMPLATE", "TOKEN_THRESHOLD", "clear_summary", "estimate_tokens", "format_summary", "get_summary", "store_summary", "summarize"]

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


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate token count from messages using a chars/4 heuristic.

    Args:
        messages: List of message dicts with 'content' fields.

    Returns:
        Estimated token count.
    """
    total_chars = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        # Account for tool calls
        tool_calls = msg.get("tool_calls", [])
        for tc in tool_calls:
            fn = tc.get("function", {})
            total_chars += len(str(fn.get("name", ""))) + len(str(fn.get("arguments", "")))
    return total_chars // 4


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


async def summarize(messages: list[dict[str, Any]], llm: Any, thread_id: str = "") -> str:
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
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if role == "system":
            continue  # skip system messages
        if content:
            conversation_text.append(f"{role}: {content}")
        # Include tool calls if present
        tool_calls = msg.get("tool_calls", [])
        for tc in tool_calls:
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


def _fallback_summary(messages: list[dict[str, Any]]) -> str:
    """Generate a simple extractive summary when LLM is unavailable."""
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user" and content:
            parts.append(f"- User asked: {content[:100]}")
        elif role == "assistant" and content:
            parts.append(f"- Agent responded: {content[:100]}")
    return "\n".join(parts[-10:]) if parts else "(no conversation to summarize)"
