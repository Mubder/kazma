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
    from kazma_core.summarizer import estimate_tokens

    total_tokens = estimate_tokens(messages)

    model = "unknown"
    provider = "unknown"
    try:
        from kazma_core.model_registry import get_model_registry

        prof = get_model_registry().get_active_profile() or {}
        model = str(prof.get("model") or "unknown")
        provider = str(prof.get("provider") or "unknown")
    except Exception:
        logger.debug("[context_cmd] active model unavailable", exc_info=True)

    # Same window and trim budget the supervisor uses. The old meter
    # reported summarizer.TOKEN_THRESHOLD (a hardcoded 4,000) against a
    # million-token window, so a short turn looked 123% over budget.
    from kazma_core.agent.turn_input import resolve_trim_token_budget
    from kazma_core.token_counter import resolve_context_window

    named_model = None if model == "unknown" else model
    context_window = resolve_context_window(None, named_model)
    trim_budget = resolve_trim_token_budget(last_model=named_model)

    pct = (total_tokens / context_window * 100) if context_window > 0 else 0
    threshold_utilization = (total_tokens / trim_budget * 100) if trim_budget > 0 else 0

    lines: list[str] = [
        "📊 Context Window",
        f"Tokens: {total_tokens:,} / {context_window:,} ({pct:.0f}%)",
    ]

    if detailed:
        role_counts = _count_by_role(messages)
        if role_counts:
            parts = [f"{role}={count:,}" for role, count in sorted(role_counts.items())]
            lines.append(f"Role breakdown: {', '.join(parts)}")

    lines.append(
        f"Summarization threshold: {trim_budget:,} tokens ({threshold_utilization:.0f}% utilized)"
    )

    # Identity block — always emit these lines (even as "(unavailable)").
    # context_info used to return only the token bar, so Part A could not
    # verify workspace / active model in-process (2026-09-17).
    workspace = "(unavailable)"
    try:
        from kazma_core.workspace.binding import resolve_active_root

        workspace = str(resolve_active_root())
    except Exception:
        logger.debug("[context_cmd] workspace root unavailable", exc_info=True)
    lines.append(f"Workspace: {workspace}")
    lines.append(f"Model: {model}  Provider: {provider}")

    return "\n".join(lines)
