"""Policy — the ONLY place that sets route=execute.

Execute allowlist is job-starters only ({research_deep}). Document
generation is constrain so writes go through tool_worker + HITL.
"""
from __future__ import annotations

import logging
from typing import Any

from kazma_core.agent.intent.types import (
    EXECUTE_MIN,
    ActKind,
    EntitySet,
    IntentAct,
    RouteKind,
)

__all__ = ["decide"]

logger = logging.getLogger(__name__)

# Execute only starts background jobs that already go through tools.
# document_generate is CONSTRAIN — writes must run in tool_worker so HITL
# interrupt() can fire (supervisor_node cannot pause for approval).
_PHASE_EXECUTE_ALLOWLIST: frozenset[str] = frozenset({
    ActKind.RESEARCH_DEEP,
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
            "INTENT ENGINE: If the user asked for parallel workers, dispatch via "
            "the swarm tools. Do not invent workers. Do not skip HITL."
        )
    if kind == ActKind.CODE_EXEC:
        return (
            "INTENT ENGINE: Use `python_exec` for short scripts only. For document "
            "generation use generate_* tools. For file operations use file_* tools."
        )
    if kind == ActKind.FILE_MGMT:
        return (
            "INTENT ENGINE: Use `file_list` to see what exists, then `file_*` tools "
            "for the requested operation. Confirm before bulk deletions."
        )
    if kind == ActKind.ANALYSIS:
        return (
            "INTENT ENGINE: Use `python_exec` with pandas/matplotlib for data "
            "analysis and charting. Save outputs via `file_write`."
        )
    if kind == ActKind.REMIND:
        return (
            "INTENT ENGINE: Use the `schedule_task` tool to set a reminder. "
            "Confirm the date/time with the user before scheduling."
        )
    if kind == ActKind.DOCUMENT_INTEL:
        return (
            "INTENT ENGINE: Use document_* tools (import, read, index, search) "
            "for ingestion and redaction. Do not send the user to a page."
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
            parts.append(f"{i}) Dispatch swarm workers for the parallel part")
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

    # 2. Focus gates — never execute on continue/cleanup/shift (S2-1 split:
    #    explicit and inferred pivots both loop — neither executes handlers)
    if focus in ("continue", "cleanup", "shift", "shift_explicit", "shift_inferred"):
        return RouteKind.LOOP, None, f"focus_{focus}", ""

    # 3. Drop GENERAL acts; if none left → loop
    non_general = tuple(a for a in acts if a.kind != ActKind.GENERAL)
    if not non_general:
        return RouteKind.LOOP, None, "no_act", ""

    # 4. Multi-act: dispatch a registered composer, else constrain
    if len(non_general) > 1:
        kinds = frozenset(a.kind for a in non_general)
        composer = registry.get_composer(kinds)
        if composer is None:
            return RouteKind.CONSTRAIN, None, "multi_act", _multi_act_plan_note(non_general)

        # Composer found — run it through the same safety gates as a
        # single-act handler before dispatching.
        if not intent_execute_enabled():
            return RouteKind.CONSTRAIN, None, "execute_disabled", _multi_act_plan_note(non_general)

        # Every participating act must be allowlisted and confident enough.
        for a in non_general:
            if a.kind not in _PHASE_EXECUTE_ALLOWLIST:
                return RouteKind.CONSTRAIN, None, "phase_allowlist", _multi_act_plan_note(non_general)
            if a.confidence < EXECUTE_MIN:
                return RouteKind.CONSTRAIN, None, "low_confidence", _multi_act_plan_note(non_general)

        # Merge slots across all non-general acts for the composer's
        # required_slots (topic comes from research, format from document).
        merged_slots: dict[str, Any] = {}
        for a in non_general:
            merged_slots.update(a.slots)
        slot_sources = {**merged_slots}
        if entities.files:
            slot_sources["source_file"] = entities.files[0].path
        for req in composer.required_slots:
            if req not in slot_sources:
                return RouteKind.CONSTRAIN, None, "missing_slot", _multi_act_plan_note(non_general)

        if composer.mutating and not composer.uses_execute:
            return RouteKind.LOOP, None, "handler_unsafe", ""

        return RouteKind.EXECUTE, composer.name, "checklist_passed", ""

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

    # 12. Phase execute allowlist ({document_generate, research_deep})
    if primary.kind not in _PHASE_EXECUTE_ALLOWLIST:
        if primary.kind in _SOFT_KINDS:
            return RouteKind.CONSTRAIN, None, "phase_allowlist", _plan_note_for(primary.kind, primary.slots, entities)
        return RouteKind.LOOP, None, "phase_allowlist", ""

    return RouteKind.EXECUTE, handler.name, "checklist_passed", ""
