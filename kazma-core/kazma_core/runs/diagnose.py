"""Ask Kazma to diagnose: a read-only look at a stuck or failed run.

The diagnosis runs on a one-shot child graph offered READ-tier tools only --
the graph a scheduled task with ``read`` capabilities fires on
(``cron.capabilities.cron_fire_graph``): no write, danger or MCP tool, no
checkpoint, refused tools refused by the executor. It reads the run
(``run_status``, ``list_runs``) and anything else read-only, and answers in a
few sentences: what happened, the likely cause, the safest next step among
the actions the incident offers. It proposes; acting stays the owner's, on
the card.

The incident's own text (the run's title and failure reason) reaches the
model fenced: it is the run's data, not instructions. The answer is stored on
the incident (the Dashboard shows it) and sent to the incident's chat.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

__all__ = ["DiagnosisStart", "set_graph_builder", "start_diagnosis"]

logger = logging.getLogger(__name__)

#: A diagnosis that has not answered in this long is given up.
DIAGNOSIS_BUDGET_S = 240.0

_graph_builder: Callable[[], Any] | None = None

_NOTE = (
    "You are diagnosing a stuck or failed piece of work Kazma ran. Nobody is "
    "chatting with you; your answer is sent to the owner as it is. Use only the "
    "read-only tools you are offered; start with run_status('{run_id}'). Then "
    "answer in at most five short sentences: what happened, the likely cause, "
    "and the safest next step among these actions: {actions}. Do not claim you "
    "did anything. Do not emit a ```plan fence."
)


@dataclass(frozen=True)
class DiagnosisStart:
    ok: bool
    code: str
    message: str


def set_graph_builder(builder: Callable[[], Any]) -> None:
    """How to build the read-only diagnosis graph (app startup)."""
    global _graph_builder
    _graph_builder = builder


async def start_diagnosis(incident: dict[str, Any], *, actor: str, platform: str) -> DiagnosisStart:
    """Start a diagnosis of *incident* in the background; at most one at a time."""
    from kazma_core.runs.incidents import get_incident_store

    if _graph_builder is None:
        return DiagnosisStart(False, "diagnosis_unavailable", "The agent is not ready to diagnose.")
    store = get_incident_store()
    if not await asyncio.to_thread(store.claim_diagnosis, incident["id"]):
        return DiagnosisStart(False, "diagnosis_running", "A diagnosis of this incident is already running.")
    from kazma_core.background import spawn_background

    spawn_background(_diagnose(incident, actor=actor, platform=platform), name=f"run-diagnosis:{incident['id']}")
    return DiagnosisStart(True, "diagnosing", "Diagnosing; the answer comes here in a minute or two.")


def _messages(incident: dict[str, Any]) -> list[dict[str, str]]:
    from kazma_core.runs.text import action_word
    from kazma_core.safety.prompt_fence import fence_untrusted

    offered = [a for a in incident.get("actions", []) if a != "diagnose"]
    facts = "\n".join([
        f"Run: {incident['run_id']} ({incident['kind']})",
        f"Condition: {incident['condition']}",
        f"Title: {incident.get('title', '')}",
        f"Failure reason: {incident.get('detail', '') or 'none recorded'}",
        f"Effects outside Kazma: {incident.get('effects', 'none')}",
    ])
    return [
        {"role": "system", "content": _NOTE.format(
            run_id=incident["run_id"], actions=", ".join(action_word(a) for a in offered) or "none")},
        {"role": "system", "content": fence_untrusted(facts, source="run_incident")},
        {"role": "user", "content": f"Diagnose run {incident['run_id']}."},
    ]


async def _diagnose(incident: dict[str, Any], *, actor: str, platform: str) -> None:
    from kazma_core.agent.turn import run_agent_turn
    from kazma_core.runs.claims import WORK_FAILURES, settle_on_error
    from kazma_core.runs.incidents import get_incident_store

    store = get_incident_store()
    text, ok = "", False

    async def _could_not_run(exc: BaseException) -> None:
        logger.warning("[runs] diagnosis failed incident=%s", incident["id"], exc_info=exc)
        await asyncio.to_thread(store.finish_diagnosis, incident["id"],
                                text=f"The diagnosis could not run: {type(exc).__name__}", ok=False)

    # The claimed "running" diagnosis always ends recorded (audit 2026-10-26
    # AUD-018): an error outside the three types caught here left it
    # "running", and every later "diagnose" answered "already running".
    async with settle_on_error(_could_not_run):
        try:
            graph = await asyncio.to_thread(_graph_builder)
            thread_id = f"diagnose-{incident['id']}"
            turn = await asyncio.wait_for(
                run_agent_turn(graph=graph, thread_id=thread_id, state={"messages": _messages(incident)},
                               config={"configurable": {"thread_id": thread_id}}, persist=False),
                timeout=DIAGNOSIS_BUDGET_S)
            failed = bool(getattr(turn, "turn_failed", False) or getattr(turn, "error", None))
            text = (getattr(turn, "text", "") or "").strip()
            ok = bool(text) and not failed
            if not ok:
                text = text or f"The diagnosis could not finish: {getattr(turn, 'error', '') or 'no answer'}"
        except TimeoutError:
            text = f"The diagnosis took longer than {int(DIAGNOSIS_BUDGET_S)} s and was stopped."
        except WORK_FAILURES as exc:
            logger.warning("[runs] diagnosis failed incident=%s", incident["id"], exc_info=True)
            text = f"The diagnosis could not run: {type(exc).__name__}"
    await asyncio.to_thread(store.finish_diagnosis, incident["id"], text=text, ok=ok)
    logger.info("[runs] diagnosis incident=%s ok=%s by=%s on=%s", incident["id"], ok, actor or "-", platform)
    from kazma_core.runs.notify import send_diagnosis

    await send_diagnosis(incident, text)
