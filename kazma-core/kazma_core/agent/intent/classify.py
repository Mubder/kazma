"""classify_turn — the single entry point for intent classification."""
from __future__ import annotations

import logging
from typing import Any

from kazma_core.agent.intent.entities import resolve_entities
from kazma_core.agent.intent.heuristics import detect_acts
from kazma_core.agent.intent.policy import decide
from kazma_core.agent.intent.registry import get_registry
from kazma_core.agent.intent.types import (
    TIER2_HIGH,
    ActKind,
    EntitySet,
    IntentAct,
    RouteKind,
    TurnDecision,
)

__all__ = ["classify_turn", "classify_turn_sync"]

logger = logging.getLogger(__name__)


def _merge_command_acts(
    acts: tuple[IntentAct, ...],
    command: str | None,
) -> tuple[IntentAct, ...]:
    """Seed acts from an explicit command, merge unique kinds."""
    if not command:
        return acts
    cmd_map = {
        "research": ActKind.RESEARCH,
        "research_deep": ActKind.RESEARCH_DEEP,
        "swarm": ActKind.SWARM,
        "documents": ActKind.DOCUMENT_INTEL,
    }
    cmd_act = cmd_map.get(command)
    if cmd_act is None:
        return acts
    existing_kinds = {a.kind for a in acts}
    if cmd_act in existing_kinds:
        return acts
    return acts + (IntentAct(kind=cmd_act, confidence=0.95, source="command"),)


def classify_turn_sync(
    text: str,
    *,
    messages: list[dict] | None = None,
    attachments: list[dict] | None = None,
    task_status: str = "",
    task_goal_summary: str = "",
    command: str | None = None,
    use_embedding_drift: bool = True,
) -> TurnDecision:
    """Synchronous classification — heuristics only, no LLM."""
    from kazma_core.agent.turn_input import classify_turn_intent

    focus = classify_turn_intent(
        text,
        messages=messages,
        task_status=task_status,
        task_goal_summary=task_goal_summary,
        use_embedding_drift=use_embedding_drift,
    )

    acts = detect_acts(text, attachments)
    acts = _merge_command_acts(acts, command)

    entities = resolve_entities(text=text, attachments=attachments, acts=acts)

    route, handler, reason, plan_note = decide(
        focus=focus,
        acts=acts,
        entities=entities,
        registry=get_registry(),
    )

    # Record metrics
    try:
        from kazma_core.agent.intent.metrics import record_decision

        primary_kind = primary.kind if (primary := max(acts, key=lambda a: a.confidence, default=None)) else "general"
        record_decision(str(route), str(primary_kind))
    except Exception:
        pass

    return TurnDecision(
        focus=focus,
        acts=acts,
        entities=entities,
        route=route,
        handler=handler,
        reason=reason,
        plan_note=plan_note,
        source="heuristic",
    )


async def classify_turn(
    text: str,
    *,
    messages: list[dict] | None = None,
    attachments: list[dict] | None = None,
    task_status: str = "",
    task_goal_summary: str = "",
    llm: Any = None,
    command: str | None = None,
    use_embedding_drift: bool = True,
) -> TurnDecision:
    """Full classification with optional Tier 2 LLM refinement."""
    from kazma_core.agent.intent.config import intent_tier2_enabled
    from kazma_core.agent.turn_input import classify_turn_intent

    focus = classify_turn_intent(
        text,
        messages=messages,
        task_status=task_status,
        task_goal_summary=task_goal_summary,
        use_embedding_drift=use_embedding_drift,
    )

    acts = detect_acts(text, attachments)
    acts = _merge_command_acts(acts, command)

    entities = resolve_entities(text=text, attachments=attachments, acts=acts)

    # Tier 2: LLM refinement for gray zone only
    if (
        intent_tier2_enabled()
        and llm is not None
        and len((text or "").strip()) > 20
    ):
        max_conf = max((a.confidence for a in acts), default=0)
        non_general_count = sum(1 for a in acts if a.kind != ActKind.GENERAL)
        gray = (
            max_conf < TIER2_HIGH
            or non_general_count != 1
            or bool(entities.unresolved)
            or bool(entities.ambiguous)
        )
        if gray:
            acts = await _refine_acts_llm(text, llm, acts)

    route, handler, reason, plan_note = decide(
        focus=focus,
        acts=acts,
        entities=entities,
        registry=get_registry(),
    )

    source = "heuristic"
    if any(a.source == "llm" for a in acts):
        source = "mixed"

    return TurnDecision(
        focus=focus,
        acts=acts,
        entities=entities,
        route=route,
        handler=handler,
        reason=reason,
        plan_note=plan_note,
        source=source,
    )


async def _refine_acts_llm(
    text: str,
    llm: Any,
    current_acts: tuple[IntentAct, ...],
) -> tuple[IntentAct, ...]:
    """Tier 2: one structured LLM call for gray-zone refinement."""
    import asyncio
    import json

    valid_kinds = {k.value for k in ActKind}
    prompt = (
        "Classify this user request. Reply with JSON only:\n"
        '{"acts": [{"kind": "document_generate|research|research_deep|swarm|'
        'code_exec|file_mgmt|analysis|remind|general", "confidence": 0.0-1.0, '
        '"slots": {}}]}\n\n'
        f"Request: {text[:500]}\n\nJSON:"
    )
    try:
        resp = await asyncio.wait_for(
            llm.chat([{"role": "user", "content": prompt}], tools=None),
            timeout=4.0,
        )
        content = (getattr(resp, "content", "") or "").strip()
        # Extract JSON
        start = content.find("{")
        end = content.rfind("}") + 1
        if start < 0 or end <= start:
            return current_acts
        data = json.loads(content[start:end])
        llm_acts: list[IntentAct] = []
        for a in data.get("acts", []):
            kind = str(a.get("kind", "")).strip()
            if kind not in valid_kinds:
                continue
            conf = min(1.0, max(0.0, float(a.get("confidence", 0.5))))
            slots = a.get("slots") or {}
            if not isinstance(slots, dict):
                slots = {}
            llm_acts.append(IntentAct(kind=kind, confidence=conf, slots=slots, source="llm"))
        if llm_acts:
            return tuple(llm_acts)
    except Exception as exc:
        logger.debug("[intent_tier2] LLM refinement failed: %s", exc)
    return current_acts
