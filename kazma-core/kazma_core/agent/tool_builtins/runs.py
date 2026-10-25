"""The agent's view of running work: ``list_runs`` and ``run_status``.

Read tier. They answer "what is running?", "why is the crawl stuck?" from the
run registry (``kazma_core.runs``), the same read model the Dashboard and
``/runs`` show. A run's title and failure reason are its own data (a prompt,
a file name, an error a remote service wrote): they reach the model fenced.
Acting on a run is the owner's, through the incident card or the page, never
these tools.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["register_run_tools"]


def register_run_tools(registry: Any) -> None:
    """Register the run tools onto *registry*."""

    @registry.register(
        description=(
            "List the work Kazma is running or ran in the last day: chat turns, "
            "swarm tasks, scheduled jobs, document jobs, research sessions, crawls, "
            "X posts, memory tasks and maintenance sweeps, each with its state "
            "(running, stalled, failed, waiting for the owner...). Stalled and failed "
            "runs come first. include_finished=true adds what finished in the last day."
        ),
        category="system",
    )
    async def list_runs(include_finished: bool = False) -> str:
        from kazma_core.runs.registry import list_runs as _list
        from kazma_core.runs.text import format_listing
        from kazma_core.safety.prompt_fence import fence_untrusted
        from kazma_core.security.single_operator import single_operator_refusal

        refusal = await asyncio.to_thread(
            single_operator_refusal,
            "Running work is shown on a single-operator install only.")
        if refusal:
            return f"Error: {refusal}"
        listing = await _list()
        body = format_listing(listing.runs, listing.errors, include_finished=include_finished)
        return fence_untrusted(body, source="runs")

    @registry.register(
        description=(
            "Show one run in detail by its id (from list_runs, e.g. 'swarm_task:abc'): "
            "its state, when it last made progress, why it failed, whether its effects "
            "are known, and the actions the owner can take on it."
        ),
        category="system",
    )
    async def run_status(run_id: str) -> str:
        from kazma_core.runs.incidents import get_incident_store
        from kazma_core.runs.registry import get_run
        from kazma_core.runs.text import describe_run
        from kazma_core.safety.prompt_fence import fence_untrusted
        from kazma_core.security.single_operator import single_operator_refusal

        refusal = await asyncio.to_thread(
            single_operator_refusal,
            "Running work is shown on a single-operator install only.")
        if refusal:
            return f"Error: {refusal}"
        try:
            run = await get_run(run_id)
        except ValueError:
            return f"Error: '{run_id}' is not a run id (use list_runs to find one)."
        except RuntimeError as exc:
            return f"Error: {exc}"
        if run is None:
            return f"No run {run_id} is listed now: it finished more than a day ago or was removed."
        incident = await asyncio.to_thread(get_incident_store().open_for_run, run.run_id)
        return fence_untrusted(describe_run(run, incident=incident), source="runs")
