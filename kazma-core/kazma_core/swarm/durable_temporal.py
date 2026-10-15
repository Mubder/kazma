"""Temporal workflow + activity defs.

Kept thin so the Temporal workflow sandbox does not import SwarmEngine.
Import-safe when ``temporalio`` is not installed (CI default). The rules
they keep are in ``kazma_core.swarm.durable``'s docstring.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

try:
    from temporalio import activity, workflow
    from temporalio.common import RetryPolicy
except ImportError:  # pragma: no cover
    activity = None  # type: ignore[assignment]
    workflow = None  # type: ignore[assignment]


if activity is not None and workflow is not None:

    @activity.defn(name="kazma_swarm_run")
    async def kazma_swarm_run(payload: dict[str, Any]) -> dict[str, Any]:
        from kazma_core.swarm.durable import run_activity_payload

        return await run_activity_payload(payload)

    @workflow.defn(name="KazmaSwarmRun")
    class KazmaSwarmRun:
        """One run of a swarm task: its one activity, on the queue of the
        process that admitted it, with the limits that process computed from
        the engine's own budget (``durable.activity_timeouts``)."""

        @workflow.run
        async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
            limits = payload["timeouts"]
            return await workflow.execute_activity(
                kazma_swarm_run,
                payload,
                task_queue=payload["activity_queue"],
                start_to_close_timeout=timedelta(seconds=limits["start_to_close"]),
                schedule_to_start_timeout=timedelta(seconds=limits["schedule_to_start"]),
                schedule_to_close_timeout=timedelta(seconds=limits["schedule_to_close"]),
                heartbeat_timeout=timedelta(seconds=limits["heartbeat"]),
                # The activity is an entire agent task, whose external effects
                # are not universally idempotent: Temporal never runs it again.
                # A worker's own retries run inside it, under the engine's
                # rules (only while nothing that changes things has run).
                retry_policy=RetryPolicy(maximum_attempts=1),
                # A cancel made through Temporal reaches the work at its next
                # heartbeat; the run's ending is settled in Kazma, which waits
                # for the work to stop (``durable._settle``). Waiting for the
                # cancel here too cannot work without a workflow cache: the
                # SDK cannot evict a workflow waiting on a cancel (measured).
                cancellation_type=workflow.ActivityCancellationType.TRY_CANCEL,
            )

    @activity.defn(name="kazma_swarm_dispatch")
    async def kazma_swarm_dispatch(payload: dict[str, Any]) -> dict[str, Any]:
        from kazma_core.swarm.durable import run_legacy_activity

        return await run_legacy_activity(payload)

    @workflow.defn(name="KazmaSwarmTask")
    class KazmaSwarmTask:
        """An older build's workflow, kept so a run it started replays to
        its end; its activity is refused (``run_legacy_activity``)."""

        @workflow.run
        async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
            timeout = float((payload or {}).get("task", {}).get("timeout") or 300)
            return await workflow.execute_activity(
                kazma_swarm_dispatch,
                payload,
                start_to_close_timeout=timedelta(seconds=max(timeout, 60.0) + 120.0),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )

else:  # pragma: no cover

    async def kazma_swarm_run(payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("temporalio is not installed")

    async def kazma_swarm_dispatch(payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("temporalio is not installed")

    class KazmaSwarmRun:
        async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("temporalio is not installed")

    class KazmaSwarmTask:
        async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("temporalio is not installed")
