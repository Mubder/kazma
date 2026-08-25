"""Temporal workflow + activity defs.

Kept thin so the Temporal workflow sandbox does not import SwarmEngine.
Import-safe when ``temporalio`` is not installed (CI default).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

try:
    from temporalio import activity, workflow
except ImportError:  # pragma: no cover
    activity = None  # type: ignore[assignment]
    workflow = None  # type: ignore[assignment]


if activity is not None and workflow is not None:

    @activity.defn(name="kazma_swarm_dispatch")
    async def kazma_swarm_dispatch(payload: dict[str, Any]) -> dict[str, Any]:
        from kazma_core.swarm.durable import run_activity_payload

        return await run_activity_payload(payload)

    @workflow.defn(name="KazmaSwarmTask")
    class KazmaSwarmTask:
        @workflow.run
        async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
            timeout = float((payload or {}).get("task", {}).get("timeout") or 300)
            return await workflow.execute_activity(
                kazma_swarm_dispatch,
                payload,
                start_to_close_timeout=timedelta(seconds=max(timeout, 60.0) + 120.0),
            )

else:  # pragma: no cover

    async def kazma_swarm_dispatch(payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("temporalio is not installed")

    class KazmaSwarmTask:
        async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("temporalio is not installed")
