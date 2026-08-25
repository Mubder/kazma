"""Semantic compact — summarize dropped middle history instead of deleting it.

Deterministic trim (``trim_messages_deterministic``) is the cheap first cut.
When the user asks ``/compact``, the 80% budget still fires, or the provider
returns ``context_overflow``, this module summarizes the *dropped* slice with
:class:`CompactionEngine` (LLM, heuristic fallback) and re-injects it as one
untrusted system note so the model keeps task facts.

Kill-switch: ``KAZMA_SEMANTIC_COMPACT=0``.
"""

from __future__ import annotations

import logging
import os
from typing import Any

__all__ = ["inject_summary_of_dropped", "semantic_compact_enabled", "semantic_compact_messages"]

logger = logging.getLogger(__name__)


def semantic_compact_enabled() -> bool:
    raw = (os.environ.get("KAZMA_SEMANTIC_COMPACT") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _fp(msg: dict[str, Any]) -> tuple[Any, ...]:
    return (
        msg.get("role"),
        str(msg.get("content") or "")[:400],
        str(msg.get("tool_call_id") or ""),
        str((msg.get("tool_calls") or ""))[:120],
    )


def _dropped_conversation(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    kept = {_fp(m) for m in after if isinstance(m, dict)}
    out: list[dict[str, Any]] = []
    for m in before:
        if not isinstance(m, dict):
            continue
        if m.get("role") not in ("user", "assistant", "tool"):
            continue
        if _fp(m) not in kept:
            out.append(m)
    return out


async def inject_summary_of_dropped(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    *,
    llm: Any | None = None,
) -> list[dict[str, Any]]:
    """If trim dropped conversation turns, insert an LLM/heuristic summary."""
    if not semantic_compact_enabled():
        return after
    dropped = _dropped_conversation(before, after)
    if not dropped:
        return after
    try:
        from kazma_core.compaction import CompactionEngine

        engine = CompactionEngine(llm_client=llm)
        summary = await engine.summarize(dropped)
    except Exception:
        logger.warning("[semantic_compact] summarize failed", exc_info=True)
        return after
    if not (summary or "").strip():
        return after
    try:
        from kazma_core.safety.prompt_fence import format_untrusted_block

        body = format_untrusted_block(summary, source="compaction")
    except Exception:
        body = summary
    out = list(after)
    insert_at = 1 if out and isinstance(out[0], dict) and out[0].get("role") == "system" else 0
    out.insert(insert_at, {"role": "system", "content": body})
    logger.info(
        "[semantic_compact] summarized %d dropped turns (%d chars)",
        len(dropped),
        len(body),
    )
    return out


async def semantic_compact_messages(
    messages: list[dict[str, Any]],
    *,
    llm: Any | None = None,
    max_tokens: int = 18000,
) -> list[dict[str, Any]]:
    """Trim then summarize whatever the trim dropped (overflow recovery)."""
    if not semantic_compact_enabled() or not messages:
        return messages
    from kazma_core.agent.turn_input import (
        format_working_memory_anchor,
        trim_messages_deterministic,
        WORKING_MEMORY_MARKER,
    )

    wm = ""
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "system":
            text = str(m.get("content") or "")
            if WORKING_MEMORY_MARKER in text:
                wm = text
                break
    if not wm:
        try:
            wm = format_working_memory_anchor(active_goal="", active_attachments=[], hard_constraints=[])
        except Exception:
            wm = ""
    trimmed = trim_messages_deterministic(
        messages,
        max_tokens=max_tokens,
        keep_last_tool_rounds=6,
        working_memory_block=wm,
    )
    return await inject_summary_of_dropped(messages, trimmed, llm=llm)
