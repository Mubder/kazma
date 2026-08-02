"""Context Window Indicator — /context slash command.

Reports token usage, role breakdown, and summarization threshold.

Usage:
    from kazma_core.tools.context_cmd import context_cmd
    result = await context_cmd(messages)
"""

from __future__ import annotations

import logging
from typing import Any

__all__ = ["context_cmd"]

logger = logging.getLogger(__name__)


def _count_by_role(messages: list[dict[str, Any]]) -> dict[str, int]:
    """Estimate tokens per role."""

    role_counts: dict[str, int] = {}
    for msg in messages:
        role = msg.get("role", "unknown")
        # Estimate this single message's tokens
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls", [])
        chars = len(str(content))
        for tc in tool_calls:
            fn = tc.get("function", {})
            chars += len(str(fn.get("name", ""))) + len(str(fn.get("arguments", "")))
        tokens = chars // 4
        role_counts[role] = role_counts.get(role, 0) + tokens
    return role_counts


async def context_cmd(messages: list[dict[str, Any]], detailed: bool = False) -> str:
    """Report context window usage.

    Args:
        messages: Current session messages.
        detailed: If True, include per-role breakdown.

    Returns:
        Formatted context report.
    """
    from kazma_core.summarizer import TOKEN_THRESHOLD, estimate_tokens

    total_tokens = estimate_tokens(messages)

    # Context window: Settings UI (ConfigStore context.*) → yaml
    # (memory.max_context_tokens) → 128k default. Mirrors
    # KazmaAgent._resolve_context_window() — previously this fell back to
    # 16k, understating utilization for every 128k-context model.
    context_window = 128_000
    try:
        from kazma_core.config_store import get_config_store

        store = get_config_store()
        cs_val = store.get("context.max_context_tokens")
        if cs_val is not None:
            try:
                context_window = max(1024, int(cs_val))
            except (TypeError, ValueError):
                pass
    except Exception as exc:
        logger.debug("Failed to read context_window config: %s", exc)
    if context_window == 128_000:
        try:
            from kazma_core.config_store import get_config_store

            yaml_val = get_config_store().get("memory.max_context_tokens")
            if yaml_val is not None:
                try:
                    context_window = max(1024, int(yaml_val))
                except (TypeError, ValueError):
                    pass
        except Exception as exc:
            logger.debug("Failed to read yaml context_window config: %s", exc)

    pct = (total_tokens / context_window * 100) if context_window > 0 else 0
    threshold_pct = (TOKEN_THRESHOLD / context_window * 100) if context_window > 0 else 0
    threshold_utilization = (total_tokens / TOKEN_THRESHOLD * 100) if TOKEN_THRESHOLD > 0 else 0

    lines: list[str] = [
        "📊 Context Window",
        f"Tokens: {total_tokens:,} / {context_window:,} ({pct:.0f}%)",
    ]

    if detailed:
        role_counts = _count_by_role(messages)
        if role_counts:
            parts = [f"{role}={count:,}" for role, count in sorted(role_counts.items())]
            lines.append(f"Role breakdown: {', '.join(parts)}")

    lines.append(f"Summarization threshold: {TOKEN_THRESHOLD:,} tokens ({threshold_utilization:.0f}% utilized)")

    return "\n".join(lines)
