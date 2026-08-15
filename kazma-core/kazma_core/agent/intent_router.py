"""Compat façade — delegates to the intent engine (kazma_core.agent.intent).

Keeps `classify_task`, `TaskIntent`, `IntentCategory`, `CONFIDENCE_THRESHOLD`
so old tests compile during migration. `should_route` follows the engine's
route decision, not a confidence threshold.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "TaskIntent",
    "IntentCategory",
    "classify_task",
    "classify_task_async",
    "CONFIDENCE_THRESHOLD",
]

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.86  # informational only; should_route follows engine


class IntentCategory:
    DOCUMENT = "document"
    RESEARCH = "research"
    CODE = "code"
    FILE_MGMT = "file_mgmt"
    SWARM = "swarm"
    ANALYSIS = "analysis"
    GENERAL = "general"
    CONTINUE = "continue"


_ACT_TO_CATEGORY = {
    "document_generate": IntentCategory.DOCUMENT,
    "document_intel": IntentCategory.DOCUMENT,
    "research": IntentCategory.RESEARCH,
    "research_deep": IntentCategory.RESEARCH,
    "code_exec": IntentCategory.CODE,
    "file_mgmt": IntentCategory.FILE_MGMT,
    "swarm": IntentCategory.SWARM,
    "analysis": IntentCategory.ANALYSIS,
    "general": IntentCategory.GENERAL,
}


@dataclass(frozen=True)
class TaskIntent:
    category: str
    confidence: float
    pipeline: str | None
    parameters: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    source: str = "heuristic"

    @property
    def should_route(self) -> bool:
        """True ONLY when the engine decided EXECUTE — not a threshold."""
        return self._route_execute

    _route_execute: bool = False


def classify_task(
    text: str,
    *,
    messages: list[dict] | None = None,
    attachments: list[dict] | None = None,
    llm: Any = None,
) -> TaskIntent:
    """Compat: delegates to classify_turn_sync."""
    from kazma_core.agent.intent.classify import classify_turn_sync

    d = classify_turn_sync(text, messages=messages, attachments=attachments)
    primary = d.primary

    if primary is None:
        category = IntentCategory.GENERAL
        confidence = 0.35
        pipeline = None
        params = {}
    else:
        category = _ACT_TO_CATEGORY.get(primary.kind, IntentCategory.GENERAL)
        confidence = primary.confidence
        pipeline = primary.kind if primary.kind != "general" else None
        params = dict(primary.slots)

    return TaskIntent(
        category=category,
        confidence=confidence,
        pipeline=pipeline,
        parameters=params,
        reason=d.reason,
        source=d.source,
        _route_execute=(d.route.value == "execute"),
    )


async def classify_task_async(
    text: str,
    *,
    messages: list[dict] | None = None,
    attachments: list[dict] | None = None,
    llm: Any = None,
) -> TaskIntent:
    """Compat: delegates to classify_turn (async, includes Tier 2)."""
    from kazma_core.agent.intent.classify import classify_turn

    d = await classify_turn(text, messages=messages, attachments=attachments, llm=llm)
    primary = d.primary

    if primary is None:
        category = IntentCategory.GENERAL
        confidence = 0.35
        pipeline = None
        params = {}
    else:
        category = _ACT_TO_CATEGORY.get(primary.kind, IntentCategory.GENERAL)
        confidence = primary.confidence
        pipeline = primary.kind if primary.kind != "general" else None
        params = dict(primary.slots)

    return TaskIntent(
        category=category,
        confidence=confidence,
        pipeline=pipeline,
        parameters=params,
        reason=d.reason,
        source=d.source,
        _route_execute=(d.route.value == "execute"),
    )
