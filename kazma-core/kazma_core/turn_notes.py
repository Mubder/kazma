"""Per-turn system notes: named once, replaced every turn, never stacked.

A turn's messages carry system notes that describe THAT turn: the
environment block, the language lock, what the question recalled from
memory, the task ledger, the "latest message" pin, Knowledge Library and IDE
context, the working-memory anchor. The checkpoint keeps them with the turn
(``SupervisorState.messages`` has no reducer: every turn saves the whole
list), so the next turn's history holds them again.

Kept, they stack and go stale. Measured on 2026-09-29 through the real web
chat route: on a chat's fourth turn the model received sixteen system notes
-- four "LATEST USER MESSAGE PRIORITY", four task ledgers (three of them
earlier turns' tasks), three memory-recall blocks -- and the environment
block of the workspace the user had switched AWAY from: the transport's fresh
block was dropped by a dedupe on the first 80 characters, which every
environment block shares. Stale notes also cost tokens on every call and
moved the "stable" prompt-cache prefix every turn.

So a new turn starts from history without the earlier turns' notes
(:func:`strip_turn_notes`, called by ``turn_input.build_turn_messages``,
which every transport uses), and each producer adds this turn's copy. A note
is recognised by how its producer begins it -- the only mark a note already
saved in a checkpoint carries -- and ``tests/test_turn_notes.py`` builds
every producer's note and checks it is recognised as its kind.

Not turn notes, never stripped: the base system prompt and personality, the
"About me" block (replaced by ``graph_helpers``), ``[CONTEXT SUMMARY]`` (it
stands for dropped turns), the abort marker, and anything unrecognised.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "IDENTITY_OPENINGS",
    "TURN_NOTE_KINDS",
    "note_kind",
    "opens_with",
    "strip_turn_notes",
]

#: How Kazma's identity notes open: the base prompt (``graph_helpers._BASE_MARKER``),
#: the personality (``graph_helpers._PERSONALITY_MARKER``) and "About me"
#: (``memory.profile.ABOUT_MARKER``). Not turn notes; they name the other
#: notes freely, so nothing that judges a note by a phrase may take them for
#: one (``tests/test_messages_judged_by_how_they_open.py`` holds the three equal).
IDENTITY_OPENINGS: tuple[str, ...] = ("[KAZMA_BASE_PROMPT]", "[KAZMA_PERSONALITY]", "[kazma:about-user]")

#: Kind -> the text its producer's note begins with. Order matters only for
#: readability; no prefix is a prefix of another kind's.
TURN_NOTE_KINDS: dict[str, tuple[str, ...]] = {
    # graph_supervisor: re-pinned every iteration.
    "working_memory": ("[KAZMA_WORKING_MEMORY]",),
    # turn_input.latest_turn_priority_note, iteration 0.
    "latest_message_priority": ("LATEST USER MESSAGE PRIORITY",),
    # task_ledger.format_ledger_block, and graph_supervisor's two ledger
    # notes: a continuation's directive, and a clarify-only turn -- which,
    # kept, would lock every later turn into asking again.
    "task_ledger": (
        "[KAZMA:TASK-LEDGER",
        "KAZMA TASK CONTINUATION:",
        "KAZMA CLARIFY-ONLY TURN:",
    ),
    # memory.recall.format_recall_block.
    "memory_recall": ("## Memory recall rules",),
    # memory.recall.recall_incomplete_note: this turn's memory search could
    # not search by meaning, or at all (part F).
    "memory_incomplete": ("## Memory search was incomplete",),
    # memory.transcript_recall.format_transcript_block.
    "past_chats": ('<kazma:data source="chat_history"',),
    # memory.procedural.format_procedural_hints.
    "procedural_hints": ('<kazma:data source="memory_v2_procedural"',),
    # memory.changes.format_changes_note: what changed in the user's facts
    # since the last reply, said once (the audit's F1).
    "memory_changes": ("MEMORY CHANGES (",),
    # Knowledge Library auto-inject (transports and supervisor).
    "knowledge": ('<kazma:data source="knowledge"',),
    # Self-improvement Soul, re-read every turn (§11).
    "self_improvement": ('<kazma:data source="self_improvement"',),
    # The file the IDE chat has open (sse_chat).
    "ide_context": ('<kazma:data source="ide_context"',),
    # ide.env_context.build_env_context (§10C).
    "environment": ("## You are Kazma (this process)",),
    # language_lock.language_lock_message.
    "language_lock": ("LANGUAGE LOCK",),
    # graph_supervisor plan nudge, iteration 0.
    "ui_workbench": ("UI WORKBENCH:",),
    # gateway: an armed /skill for this turn.
    "active_skill": ("[ACTIVE AGENT SKILL:",),
    # long_task.consume_continue_context (§25): one turn only.
    "continue_context": ("[LONG-TASK CONTINUE CONTEXT",),
    # long_task.consume_long_task_turn: said once, never again.
    "unrestricted_notice": ("⚠️ **Unrestricted expired**",),
    # graph_supervisor iteration budget nudge.
    "budget_check": ("SYSTEM BUDGET CHECK",),
    # intent engine plan notes.
    "intent_plan": ("INTENT ENGINE",),
    # graph_supervisor: a short follow-up continues the open task.
    "continuity": ("CONTINUITY:",),
    # turn_input.proposal_nudge.
    "outbound_drafts": ("OUTBOUND DRAFTS:",),
    # research_policy.deep_research_route_hint.
    "deep_research_route": ("DEEP RESEARCH ROUTE",),
    # graph_tool_worker.approval_scope_note: current batch's decision only.
    # Retain on approval resume; remove at the next fresh user turn.
    "approval_scope": ("APPROVAL SCOPE (runtime decision facts):",),
    # agent.supervisor_watchdog._reflection_text: a recovery attempt's note
    # describes THAT attempt. Unregistered, the watchdog wrote it into the
    # checkpoint and every later turn sent it again (audit 2026-10-26
    # AUD-122); within a turn the watchdog keeps only the newest one.
    "recovery": ("[KAZMA RECOVERY]",),
}


def _text(message: Any) -> str:
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text") or "") for part in content if isinstance(part, dict)
        )
    return ""


def note_kind(message: Any) -> str | None:
    """The turn-note kind of a system message, or None for anything else."""
    if not isinstance(message, dict) or message.get("role") not in ("system", "developer"):
        return None
    head = _text(message).lstrip()
    for kind, prefixes in TURN_NOTE_KINDS.items():
        if head.startswith(prefixes):
            return kind
    return None


def opens_with(message: Any, *openings: str) -> bool:
    """True when *message* is a system message that BEGINS with one of *openings*.

    The one way code finds a note it replaces or removes: by how the note
    opens, never by a phrase anywhere in its text. Kazma's base prompt names
    the other notes ("A LANGUAGE LOCK system message for this turn is
    absolute"), and the language lock's replace step, which searched the
    whole text, removed the base prompt from the first model call of every
    turn -- identity, product knowledge, the date -- until 2026-11-15.
    ``tests/test_messages_judged_by_how_they_open.py`` holds every filter to
    it.
    """
    if not isinstance(message, dict) or message.get("role") not in ("system", "developer"):
        return False
    return bool(openings) and _text(message).lstrip().startswith(openings)


def strip_turn_notes(messages: list[Any]) -> list[Any]:
    """*messages* without any turn note: what a new turn starts from."""
    kept = [m for m in messages if note_kind(m) is None]
    dropped = len(messages) - len(kept)
    if dropped:
        logger.debug("[turn_notes] dropped %d earlier turn note(s)", dropped)
    return kept

