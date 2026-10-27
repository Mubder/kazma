"""The owner's decision on an incident, from any surface.

A chat card's button, ``/runs act`` and the Dashboard's buttons all land here.
The incident row is claimed first (compare-and-set: the first decision wins,
a second answers with what was decided), then the action runs through the run
registry, which asks the run as it is NOW and refuses an action it no longer
offers, then the row records the outcome. Nothing is retried on its own: an
action that was refused leaves the incident open with the reason.

"Ask Kazma to diagnose" is not a decision: it starts a read-only look at the
run (``runs.diagnose``) and leaves the incident open.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from kazma_core.runs.claims import WORK_FAILURES, settle_on_error
from kazma_core.runs.incidents import DecisionConflictError, get_incident_store
from kazma_core.runs.text import action_word

__all__ = ["DecisionOutcome", "decide"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DecisionOutcome:
    """What happened. ``code`` is stable; ``message`` is for people (English)."""

    ok: bool
    code: str
    message: str
    incident: dict[str, Any] | None = None


def _already(row: dict[str, Any] | None) -> DecisionOutcome:
    if row is None:
        return DecisionOutcome(False, "unknown_incident", "No such incident.")
    if row["state"] == "resolved":
        return DecisionOutcome(False, "resolved", "Already settled: the run moved on by itself.", row)
    if row["state"] == "expired":
        return DecisionOutcome(False, "expired", "This incident expired unanswered.", row)
    who = row.get("decided_by") or "someone"
    where = row.get("decided_platform") or "another surface"
    what = action_word(row.get("decided_action") or "", "en") or "a decision"
    tail = f" Outcome: {row['outcome_message']}" if row.get("outcome_message") else ""
    return DecisionOutcome(False, "already_decided",
                           f"Already handled: {what}, by {who} on {where}.{tail}", row)


async def decide(incident_id: str, action: str, *, actor: str, platform: str) -> DecisionOutcome:
    """Carry out *action* on incident *incident_id* for *actor* on *platform*."""
    store = get_incident_store()
    if action == "diagnose":
        row = await asyncio.to_thread(store.get, incident_id)
        if row is None or row["state"] not in ("open", "acting"):
            return _already(row)
        from kazma_core.runs.diagnose import start_diagnosis

        started = await start_diagnosis(row, actor=actor, platform=platform)
        return DecisionOutcome(started.ok, started.code, started.message, row)
    try:
        row = await asyncio.to_thread(store.claim_decision, incident_id, action, actor=actor, platform=platform)
    except DecisionConflictError as exc:
        return _already(exc.row)
    except ValueError as exc:
        return DecisionOutcome(False, "not_offered", str(exc))
    logger.info("[runs] decision incident=%s action=%s actor=%s platform=%s",
                incident_id, action, actor or "-", platform)
    if action == "dismiss":
        return DecisionOutcome(True, "dismissed", "Dismissed. The run is left as it is.", row)
    from kazma_core.runs.registry import perform

    async def _record_failure(exc: BaseException) -> dict[str, Any] | None:
        logger.warning("[runs] action failed incident=%s action=%s", incident_id, action, exc_info=exc)
        return await asyncio.to_thread(store.finish_decision, incident_id, ok=False,
                                       code=type(exc).__name__, message=str(exc)[:300])

    # The claimed "acting" row always ends recorded (audit 2026-10-26
    # AUD-018): a "database is locked", an httpx error, a KeyError or a
    # TypeError escaped the three types caught here and left it "acting" for
    # good. A cancellation leaves it "acting": a restart closes it as
    # interrupted, never replays it.
    async with settle_on_error(_record_failure):
        try:
            result = await perform(row["run_id"], action, actor=actor)
        except WORK_FAILURES as exc:
            final = await _record_failure(exc)
            return DecisionOutcome(False, "action_failed", f"The action failed: {exc}", final)
    final = await asyncio.to_thread(store.finish_decision, incident_id, ok=result.ok,
                                    code=result.code, message=result.message)
    return DecisionOutcome(result.ok, result.code, result.message or result.code, final)
