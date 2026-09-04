"""Research deep handler — executes via start_deep_research (§18).

Delegates to the existing research session machinery (not a new crawler,
not Swarm). Keeps suppress_chat_recording for sub-queries. The handler
is non-mutating (it orchestrates existing infrastructure).
"""
from __future__ import annotations

import logging
from typing import Any

from kazma_core.agent.intent.types import ActKind, HandlerResult, TurnDecision

__all__ = ["run_research_deep", "register"]

logger = logging.getLogger(__name__)


async def run_research_deep(decision: TurnDecision, state: dict[str, Any], **ctx: Any) -> HandlerResult:
    """Execute a deep research session via start_deep_research."""
    primary = decision.primary
    if primary is None or primary.kind != ActKind.RESEARCH_DEEP:
        return HandlerResult(ok=False, escalate=True, message="not_research_deep_act")

    topic = primary.slots.get("topic", "")
    if not topic:
        # Subject-extractable fallback ONLY (2026-09-04): a user message is
        # a usable topic when a research prefix strips off it ("research
        # cloud security" → "cloud security"). A message with nothing to
        # strip is an instruction, not a subject — the old verbatim
        # fallback sent the web pipeline off to research the command string
        # itself ("reproduce a full report" incident).
        try:
            from kazma_core.agent.research_policy import (
                extract_topic_hint as _hint,
                has_extractable_topic as _has_topic,
            )
        except ImportError:
            _hint = None  # type: ignore[assignment]
            _has_topic = None  # type: ignore[assignment]
        for m in reversed(state.get("messages", [])):
            if m.get("role") != "user" or not isinstance(m.get("content"), str):
                continue
            text = m["content"].strip()
            if len(text) > 10 and _has_topic and _has_topic(text):
                topic = _hint(text)
                break

    if not topic:
        return HandlerResult(
            ok=False,
            escalate=True,
            message="no_research_topic",
        )

    try:
        from kazma_core.tools.research_session import start_deep_research

        sess = await start_deep_research(topic, depth="deep", max_sources=8)
    except Exception as exc:
        logger.warning("[intent_research] start_deep_research failed: %s", exc)
        return HandlerResult(
            ok=False,
            escalate=True,
            message=f"Research pipeline failed: {exc}",
        )

    if sess is None:
        return HandlerResult(ok=False, escalate=True, message="Research session creation failed")

    if sess.status == "error":
        return HandlerResult(
            ok=False,
            escalate=True,
            message=f"Research failed: {sess.error or 'unknown error'}",
        )

    # Session created and running in background — report to user
    steps = [
        f"Research session started: {sess.id}",
        f"Topic: {topic}",
        f"Status: {sess.status} (stage: {sess.stage})",
        f"The pipeline is running in background. Track progress via the Research panel",
        f"or GET /api/research/sessions/{sess.id}",
    ]

    return HandlerResult(
        ok=True,
        message="🔍 Deep research started\n" + "\n".join(f"  {s}" for s in steps),
        artifacts={
            "session_id": sess.id,
            "topic": topic,
            "status": sess.status,
        },
    )


def register() -> None:
    """Register the research handler with the intent registry."""
    from kazma_core.agent.intent.registry import IntentHandler, get_registry

    get_registry().register(IntentHandler(
        name="research_deep",
        act="research_deep",
        required_slots=("topic",),
        uses_execute=False,
        mutating=False,
        timeout_seconds=60.0,
        run=run_research_deep,
    ))
