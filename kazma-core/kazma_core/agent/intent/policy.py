"""Policy — the ONLY place that sets route=execute. Phase 0: allowlist empty."""
from __future__ import annotations

import logging

from kazma_core.agent.intent.types import (
    EXECUTE_MIN,
    ActKind,
    EntitySet,
    IntentAct,
    RouteKind,
)

__all__ = ["decide"]

logger = logging.getLogger(__name__)

# Phase 1: document_generate added to the execute allowlist.
# Phase 2: add "research_deep"
# Phase 3: add composer "research_then_document"
_PHASE_EXECUTE_ALLOWLIST: frozenset[str] = frozenset({
    ActKind.DOCUMENT_GENERATE,
})

_SOFT_KINDS = frozenset({
    ActKind.RESEARCH,
    ActKind.RESEARCH_DEEP,
    ActKind.SWARM,
    ActKind.DOCUMENT_GENERATE,
    ActKind.DOCUMENT_INTEL,
    ActKind.FILE_MGMT,
    ActKind.CODE_EXEC,
    ActKind.REMIND,
    ActKind.ANALYSIS,
})


def _plan_note_for(kind: str, slots: dict, entities: EntitySet) -> str:
    """Generate an INTENT ENGINE constrain plan note."""
    if kind == ActKind.RESEARCH_DEEP:
        return (
            "INTENT ENGINE: Deep research detected. Use `run_research_pipeline` "
            "once for a thorough multi-source report. Do not claim thorough "
            "research from snippets alone."
        )
    if kind == ActKind.RESEARCH:
        return (
            "INTENT ENGINE: Use at least 2 `web_search` calls plus `read_url_to_file` "
            "for depth. Do not claim thorough research from snippets."
        )
    if kind == ActKind.DOCUMENT_GENERATE:
        fmt = slots.get("format", "pdf")
        if entities.unresolved:
            return (
                f"INTENT ENGINE: Document generation ({fmt}) needs a source file. "
                "Ask the user which file to reproduce, or use the content they "
                "provided in their message."
            )
        if entities.files:
            fn = entities.files[0].filename
            return (
                f"INTENT ENGINE: Call `file_read` on {fn}, write structured markdown "
                f"via `file_write`, then `generate_{fmt}` with `markdown_path`. "
                "Do NOT write a Python PDF script."
            )
        return (
            f"INTENT ENGINE: Write the content to a markdown file via `file_write`, "
            f"then `generate_{fmt}` with `markdown_path`. Do NOT write Python code "
            "to build the document manually."
        )
    if kind == ActKind.SWARM:
        return (
            "INTENT ENGINE: If parallel workers are needed, tell the user to use "
            "`/swarm` or the swarm panel. Do not invent a dispatch."
        )
    return f"INTENT ENGINE: {kind} act detected."


def _multi_act_plan_note(acts: tuple[IntentAct, ...]) -> str:
    """Plan note for multi-act turns."""
    parts = []
    for i, a in enumerate(sorted(acts, key=lambda x: -x.confidence), 1):
        if a.kind == ActKind.GENERAL:
            continue
        if a.kind == ActKind.RESEARCH_DEEP:
            parts.append(f"{i}) Run research (`run_research_pipeline` if deep)")
        elif a.kind == ActKind.RESEARCH:
            parts.append(f"{i}) Use `web_search` + `read_url_to_file` for research")
        elif a.kind == ActKind.DOCUMENT_GENERATE:
            fmt = a.slots.get("format", "pdf")
            parts.append(f"{i}) Generate a {fmt.upper()} from the report via `generate_{fmt}(markdown_path=...)`")
        elif a.kind == ActKind.SWARM:
            parts.append(f"{i}) Use `/swarm` for parallel workers")
        else:
            parts.append(f"{i}) {a.kind}")
    return "INTENT ENGINE: " + " ".join(parts)


def decide(
    *,
    focus: str,
    acts: tuple[IntentAct, ...],
    entities: EntitySet,
    registry: Any,
) -> tuple[RouteKind, str | None, str, str]:
    """Returns (route, handler_name, reason, plan_note)."""
    from kazma_core.agent.intent.config import intent_engine_enabled, intent_execute_enabled

    # 1. Engine disabled
    if not intent_engine_enabled():
        return RouteKind.LOOP, None, "engine_disabled", ""

    # 2. Focus gates — never execute on continue/cleanup/shift
    if focus in ("continue", "cleanup", "shift"):
        return RouteKind.LOOP, None, f"focus_{focus}", ""

    # 3. Drop GENERAL acts; if none left → loop
    non_general = tuple(a for a in acts if a.kind != ActKind.GENERAL)
    if not non_general:
        return RouteKind.LOOP, None, "no_act", ""

    # 4. Multi-act without composer → constrain
    if len(non_general) > 1:
        kinds = frozenset(a.kind for a in non_general)
        composer = registry.get_composer(kinds)
        if composer is None:
            return RouteKind.CONSTRAIN, None, "multi_act", _multi_act_plan_note(non_general)

    # 5. Unresolved/ambiguous entities → constrain
    if entities.unresolved or entities.ambiguous:
        primary = max(non_general, key=lambda a: a.confidence)
        note = _plan_note_for(primary.kind, primary.slots, entities)
        reason = "unresolved" if entities.unresolved else "ambiguous"
        return RouteKind.CONSTRAIN, None, reason, note

    # 6. Primary act
    primary = max(non_general, key=lambda a: a.confidence)

    # 7. Execute disabled → constrain
    if not intent_execute_enabled():
        if primary.kind in _SOFT_KINDS:
            return RouteKind.CONSTRAIN, None, "execute_disabled", _plan_note_for(primary.kind, primary.slots, entities)
        return RouteKind.LOOP, None, "execute_disabled", ""

    # 8. Confidence below threshold
    if primary.confidence < EXECUTE_MIN:
        if primary.kind in _SOFT_KINDS:
            return RouteKind.CONSTRAIN, None, "low_confidence", _plan_note_for(primary.kind, primary.slots, entities)
        return RouteKind.LOOP, None, "low_confidence", ""

    # 9. No handler
    handler = registry.get_for_act(primary.kind)
    if handler is None:
        if primary.kind in _SOFT_KINDS:
            return RouteKind.CONSTRAIN, None, "no_handler", _plan_note_for(primary.kind, primary.slots, entities)
        return RouteKind.LOOP, None, "no_handler", ""

    # 10. Required slots
    slot_sources = {**primary.slots}
    if entities.files:
        slot_sources["source_file"] = entities.files[0].path
    for req in handler.required_slots:
        if req not in slot_sources:
            return RouteKind.CONSTRAIN, None, "missing_slot", _plan_note_for(primary.kind, primary.slots, entities)

    # 11. Unsafe handler
    if handler.mutating and not handler.uses_execute:
        return RouteKind.LOOP, None, "handler_unsafe", ""

    # 12. Phase execute allowlist (Phase 0: EMPTY — always constrains)
    if primary.kind not in _PHASE_EXECUTE_ALLOWLIST:
        if primary.kind in _SOFT_KINDS:
            return RouteKind.CONSTRAIN, None, "phase_allowlist", _plan_note_for(primary.kind, primary.slots, entities)
        return RouteKind.LOOP, None, "phase_allowlist", ""

    return RouteKind.EXECUTE, handler.name, "checklist_passed", ""
