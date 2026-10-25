"""``/runs`` typed in the web chat, answered without the model.

The chat apps handle ``/runs`` in the gateway (``agent_handler.runs_command``);
the web chat answers here, in the page's language: what needs attention,
then what runs (``/runs all`` adds the last day), or one run
(``/runs show <id>``). Acting is on the Dashboard's Runs card, whose buttons
go through the admin-checked API -- the reply links it.
"""

from __future__ import annotations

import re

__all__ = ["is_runs_command", "runs_reply"]

_COMMAND = re.compile(r"^/runs(?:\s|$)", re.IGNORECASE)


def is_runs_command(text: str) -> bool:
    return bool(_COMMAND.match((text or "").strip()))


async def runs_reply(text: str, lang: str = "en") -> str:
    """The web chat's answer to ``/runs``. Store reads run in threads."""
    import asyncio

    from kazma_core.runs.incidents import get_incident_store
    from kazma_core.runs.registry import get_run, list_runs
    from kazma_core.runs.text import describe_run, format_listing
    from kazma_core.security.single_operator import single_operator_refusal

    from kazma_ui.i18n import t

    if await asyncio.to_thread(single_operator_refusal, "refuse"):
        return t("runs.chat_single_operator", lang)
    parts = text.strip().split()
    sub = parts[1].lower() if len(parts) > 1 else ""
    link = t("runs.chat_dashboard_link", lang)
    if sub == "show" and len(parts) > 2:
        try:
            run = await get_run(parts[2])
        except ValueError:
            return t("runs.chat_not_a_run", lang, id=parts[2])
        if run is None:
            return t("runs.chat_not_listed", lang, id=parts[2])
        incident = await asyncio.to_thread(get_incident_store().open_for_run, run.run_id)
        return describe_run(run, lang=lang, incident=incident) + "\n\n" + link
    listing = await list_runs()
    return format_listing(listing.runs, listing.errors, lang=lang, include_finished=sub == "all") + "\n\n" + link
