"""Runbooks: at most one automatic first step on a stuck or failed run.

Each entry names a kind and condition, and the one action the supervisor may
take before asking the owner. Every entry is OFF until the owner switches it
on (``runs.runbooks``, on the Dashboard's Runs card): an automatic action is
the owner's decision, made in advance.

Never, whatever the switch says:

* on a run whose effects are unknown, or through ``retry_checked`` (only a
  person checks effects);
* an action the run does not offer now;
* more than :data:`MAX_STEPS_PER_DAY` per lineage in a day (``Run.lineage``):
  a retried task that fails again is not retried again by itself.

The step goes through :func:`kazma_core.runs.decide.decide`, as a person's
button would (actor ``kazma``, platform ``runbook``); the incident's message
then says what Kazma already did, with no buttons left to press.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from kazma_core.runs.contract import Run

__all__ = ["MAX_STEPS_PER_DAY", "RUNBOOK", "RUNBOOKS_KEY", "apply", "enabled", "refusal",
           "validate_runbooks"]

logger = logging.getLogger(__name__)

RUNBOOKS_KEY = "runs.runbooks"
MAX_STEPS_PER_DAY = 1

#: ``"<kind>.<condition>" -> action``: the steps that exist (each off by default).
RUNBOOK: dict[str, str] = {
    # A failed swarm task that touched nothing outside Kazma: run it again once.
    "swarm_task.failed": "retry",
    # A dead-lettered document job (outages are retried before that): once.
    "document_job.failed": "retry",
    # A memory dead letter (six attempts over ~100 min first): once.
    "memory_task.failed": "retry",
    # A chat turn left open with nothing running it: close it (the liveness heal).
    "chat_turn.dead": "close",
}


def validate_runbooks(value: Any) -> dict[str, bool]:
    """The setting: ``{"<kind>.<condition>": bool}`` for known entries only."""
    if not isinstance(value, dict):
        raise ValueError("runs.runbooks must be a mapping of runbook names to true/false.")
    unknown = sorted(set(value) - set(RUNBOOK))
    if unknown:
        raise ValueError(f"Unknown runbook(s): {', '.join(unknown)}. Known: {', '.join(sorted(RUNBOOK))}.")
    if any(type(v) is not bool for v in value.values()):
        raise ValueError("Each runbook is switched on or off with true or false.")
    return dict(value)


def enabled() -> dict[str, bool]:
    """Every runbook and whether it is on. Blocking (a settings read)."""
    from kazma_core.config_store import get_config_store

    stored = get_config_store().get(RUNBOOKS_KEY, {}) or {}
    flags = {name: False for name in RUNBOOK}
    if isinstance(stored, dict):
        flags.update({k: v is True for k, v in stored.items() if k in RUNBOOK})
    return flags


def refusal(run: Run, action: str) -> str:
    """Why *action* may not run automatically on *run*, or ""."""
    if run.effects == "unknown":
        return "its effects are unknown"
    if action == "retry_checked":
        return "only a person checks a run's effects"
    if action not in run.actions:
        return f"the run does not offer {action} now"
    return ""


async def apply(incident: dict[str, Any], run: Run) -> bool:
    """Take the incident's automatic first step if one is on and allowed."""
    name = f"{run.kind}.{incident['condition']}"
    action = RUNBOOK.get(name)
    if action is None:
        return False
    flags = await asyncio.to_thread(enabled)
    if not flags.get(name):
        return False
    why = refusal(run, action)
    if why:
        logger.info("[runs] runbook %s not applied to %s: %s", name, run.run_id, why)
        return False
    from kazma_core.runs.incidents import get_incident_store

    claimed = await asyncio.to_thread(get_incident_store().claim_auto_step, incident["id"],
                                      run.lineage or run.run_id, limit=MAX_STEPS_PER_DAY,
                                      since=time.time() - 86400.0)
    if not claimed:
        logger.info("[runs] runbook %s not applied to %s: already tried today", name, run.run_id)
        return False
    from kazma_core.runs.decide import decide

    outcome = await decide(incident["id"], action, actor="kazma", platform="runbook")
    logger.warning("[runs] runbook %s on %s: %s (%s)", name, run.run_id, outcome.code, outcome.message)
    return outcome.ok
