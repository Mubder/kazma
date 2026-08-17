"""Composer: research_then_document — chains research handler into document handler.

§19 of KAZMA_INTENT_ENGINE.md. Only runs when the research handler produced
a real report path. If research fails → escalate (do NOT generate a PDF of
the error).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from kazma_core.agent.intent.types import (
    ActKind,
    EntitySet,
    HandlerResult,
    IntentAct,
    ResolvedFile,
    RouteKind,
    TurnDecision,
)

__all__ = ["run_research_then_document", "register_composer"]

logger = logging.getLogger(__name__)


async def run_research_then_document(
    decision: TurnDecision, state: dict[str, Any], **ctx: Any
) -> HandlerResult:
    """Chain research_deep → document_generate.

    Step 1: Run research handler; require a report path in artifacts.
    Step 2: Build a new TurnDecision with document_generate + that file.
    Step 3: Run document handler.
    """
    tool_executor = ctx.get("tool_executor")
    llm = ctx.get("llm")

    # ── Step 1: Research ────────────────────────────────────────────
    from kazma_core.agent.intent.handlers.research import run_research_deep

    research_decision = _extract_act_decision(decision, ActKind.RESEARCH_DEEP)
    if research_decision is None:
        return HandlerResult(
            ok=False, escalate=True, message="composer: no research_deep act"
        )

    research_result = await asyncio.wait_for(
        run_research_deep(research_decision, state, **ctx),
        timeout=60.0,
    )

    if not research_result.ok:
        return HandlerResult(
            ok=False,
            escalate=True,
            message=f"composer: research failed — {research_result.message}",
        )

    # Research session started in background — we need to wait for it
    # or escalate to the loop. Since start_deep_research is fire-and-forget,
    # we can't wait synchronously. The composer escalates with a plan note.
    session_id = research_result.artifacts.get("session_id", "")
    if not session_id:
        return HandlerResult(
            ok=False,
            escalate=True,
            message="composer: research started but no session ID returned",
        )

    # ── Step 2: Build document decision from research result ────────
    doc_act = _extract_act_decision(decision, ActKind.DOCUMENT_GENERATE)
    if doc_act is None:
        return HandlerResult(
            ok=False, escalate=True, message="composer: no document_generate act"
        )

    # The research session runs in background — the document handler needs
    # a file to work with. Since the report isn't ready yet, we escalate
    # with a composed plan note that tells the supervisor to:
    # 1) Wait for the research to complete (check session status)
    # 2) Then generate the document from the report
    fmt = doc_act.primary.slots.get("format", "pdf") if doc_act.primary else "pdf"
    topic = research_decision.primary.slots.get("topic", "") if research_decision.primary else ""

    plan_note = (
        f"INTENT ENGINE (composed research_then_document): "
        f"1) Research session `{session_id}` is running in background on topic "
        f'"{topic}". '
        f"2) Check its status via `GET /api/research/sessions/{session_id}` or wait "
        f"for the user to say 'continue'. "
        f"3) When the report is ready, read it via `file_read` and generate "
        f"a {fmt.upper()} via `generate_{fmt}(markdown_path=...)`."
    )

    # Return the research result + composed plan
    return HandlerResult(
        ok=True,
        message=research_result.message + "\n\n" + plan_note,
        artifacts={
            **research_result.artifacts,
            "composed": True,
            "next_action": "generate_document",
            "format": fmt,
        },
        # Don't escalate — the research started successfully and the
        # plan note tells the supervisor what to do next
    )


def _extract_act_decision(
    decision: TurnDecision, act_kind: str
) -> TurnDecision | None:
    """Return a new TurnDecision containing only the specified act."""
    matching = tuple(a for a in decision.acts if a.kind == act_kind)
    if not matching:
        return None
    return TurnDecision(
        focus=decision.focus,
        acts=matching,
        entities=decision.entities,
        route=RouteKind.EXECUTE,
        handler=act_kind,
        reason=f"composer_extract:{act_kind}",
        plan_note="",
        source=decision.source,
    )


def register_composer() -> None:
    """Register the research_then_document composer."""
    from kazma_core.agent.intent.registry import IntentHandler, get_registry

    get_registry().register_composer(
        frozenset({ActKind.RESEARCH_DEEP, ActKind.DOCUMENT_GENERATE}),
        IntentHandler(
            name="research_then_document",
            act="research_deep+document_generate",
            required_slots=("topic", "format"),
            uses_execute=False,
            mutating=False,
            timeout_seconds=120.0,
            run=run_research_then_document,
        ),
    )
