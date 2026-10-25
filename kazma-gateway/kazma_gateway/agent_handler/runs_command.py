"""``/runs`` in the chat apps: running work, and acting on an incident.

Subcommands::

    /runs                          what needs attention, then what runs
    /runs all                      and what finished in the last day
    /runs show <run id|incident>   one run in detail, with its actions
    /runs act <incident> <action>  cancel, retry, retry_checked, close,
                                   dismiss or diagnose (admin)

An incident card's button arrives here as ``/runs act`` from the person who
pressed it (``kazma_gateway.run_cards``); ``graph.py`` has already refused
it for a non-admin (``slash_commands.changes_global_config``). The decision
is the incident's (``kazma_core.runs.decide``): the first one wins on every
surface, and a second press answers with what was decided. Replies are
English, as every chat-app reply.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from kazma_gateway.gateway import IncomingMessage

__all__ = ["try_runs_command"]

logger = logging.getLogger(__name__)

_REFUSAL = "Running work is shown on a single-operator install only."


async def _answer(text: str) -> str:
    """The reply to a ``/runs`` command (no I/O on the loop)."""
    from kazma_core.runs.incidents import get_incident_store
    from kazma_core.runs.registry import get_run, list_runs
    from kazma_core.runs.text import describe_run, format_listing

    parts = text.split()
    sub = parts[1].lower() if len(parts) > 1 else ""
    if sub in ("", "all"):
        listing = await list_runs()
        return format_listing(listing.runs, listing.errors, include_finished=sub == "all")
    if sub == "show" and len(parts) > 2:
        ref = parts[2]
        store = get_incident_store()
        if ref.startswith("inc_"):
            row = await asyncio.to_thread(store.get, ref)
            if row is None:
                return f"No incident {ref}."
            ref = row["run_id"]
        try:
            run = await get_run(ref)
        except ValueError:
            return f"'{ref}' is not a run id. /runs lists them."
        except RuntimeError as exc:
            return f"⚠️ {exc}"
        if run is None:
            return f"No run {ref} is listed now: it finished more than a day ago or was removed."
        incident = await asyncio.to_thread(store.open_for_run, run.run_id)
        detail = describe_run(run, incident=incident)
        if incident is not None:
            detail += f"\nIncident {incident['id']}: /runs act {incident['id']} <action>"
        return detail
    return "Usage: /runs · /runs all · /runs show <id> · /runs act <incident> <action>"


async def _act(msg: IncomingMessage, parts: list[str]) -> str:
    if len(parts) < 4:
        return "Usage: /runs act <incident> <action> (cancel, retry, retry_checked, close, dismiss, diagnose)"
    from kazma_core.runs.decide import decide

    outcome = await decide(parts[2], parts[3].lower(), actor=str(msg.sender_id or ""),
                           platform=str(msg.platform or ""))
    icon = "✅" if outcome.ok else "⚠️"
    return f"{icon} {outcome.message}"


async def try_runs_command(msg: IncomingMessage, send: Any) -> bool:
    """Handle ``/runs``; *send(text)* replies in the chat. True when handled."""
    text = (msg.text or "").strip()
    head = text.split(maxsplit=1)[0].lower().split("@", 1)[0] if text else ""
    if head != "/runs":
        return False
    from kazma_core.security.single_operator import single_operator_refusal

    if await asyncio.to_thread(single_operator_refusal, _REFUSAL):
        await send(f"⚠️ {_REFUSAL}")
        return True
    parts = text.split()
    try:
        if len(parts) > 1 and parts[1].lower() == "act":
            reply = await _act(msg, parts)
        else:
            reply = await _answer(text)
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning("[runs] /runs failed: %s", exc, exc_info=True)
        reply = f"⚠️ Running work could not be read: {type(exc).__name__}"
    await send(reply)
    return True
