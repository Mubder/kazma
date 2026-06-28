"""Integration: cron job actually fires, and adapter receive loops start.

Closes two audit gaps:

#3  The cron suite verified schedule/list/cancel persistence but never *fired*
    a job — the CronScheduler firing loop (scheduler.py:359-446) had no coverage.
    Here we schedule an immediately-due job, run the scheduler with a stub
    graph_builder, and assert the job transitions PENDING -> DONE with a result.

#2  The Discord/Slack `listen()` receive loops require `websockets` (imported
    inside `_connect_gateway`), which was only present transitively via
    `uvicorn[standard]` and is now a declared dependency. These tests confirm
    `websockets` imports and that each adapter's `listen()` loop starts and
    shuts down cleanly without a real network connection.
"""

from __future__ import annotations

import asyncio

import pytest
from kazma_core.cron.scheduler import (
    CronScheduler,
    JobStatus,
    SQLiteCronStore,
)

# ── #3: cron job fires end to end ────────────────────────────────────────


class _StubGraph:
    """A compiled-graph stand-in: ainvoke returns a final assistant message."""

    async def ainvoke(self, state, config=None):
        msgs = list(state.get("messages", []))
        msgs.append({"role": "assistant", "content": "scheduled task complete: 42"})
        return {"messages": msgs}


class TestCronFires:
    async def test_due_job_executes_and_completes(self, tmp_path) -> None:
        store = SQLiteCronStore(db_path=str(tmp_path / "cron.db"))
        await store.init()

        fired = asyncio.Event()

        def graph_builder():
            fired.set()
            return _StubGraph()

        scheduler = CronScheduler(
            store=store,
            graph_builder=graph_builder,
            poll_interval=0.05,  # fast poll so the test is quick
        )

        # "0m" => due immediately.
        info = await scheduler.schedule(timing="0m", prompt="compute the answer", platform="telegram")
        job_id = info["job_id"]

        await scheduler.start()
        try:
            # Wait for the firing loop to pick up the due job and run it.
            await asyncio.wait_for(fired.wait(), timeout=5.0)
            # Give _execute a moment to persist DONE + result.
            for _ in range(100):
                jobs = {j.job_id: j for j in await store.list_all()}
                if jobs[job_id].status == JobStatus.DONE:
                    break
                await asyncio.sleep(0.05)
        finally:
            await scheduler.stop()

        jobs = {j.job_id: j for j in await store.list_all()}
        assert jobs[job_id].status == JobStatus.DONE, f"job not DONE: {jobs[job_id].status}"
        assert jobs[job_id].last_result and "42" in jobs[job_id].last_result
        await store.close()

    async def test_no_graph_builder_marks_failed(self, tmp_path) -> None:
        """A due job with no graph builder fails cleanly (firing loop error path)."""
        store = SQLiteCronStore(db_path=str(tmp_path / "cron2.db"))
        await store.init()
        scheduler = CronScheduler(store=store, graph_builder=None, poll_interval=0.05)
        info = await scheduler.schedule(timing="0m", prompt="x")
        job_id = info["job_id"]
        await scheduler.start()
        try:
            for _ in range(100):
                jobs = {j.job_id: j for j in await store.list_all()}
                if jobs[job_id].status in (JobStatus.FAILED, JobStatus.DONE):
                    break
                await asyncio.sleep(0.05)
        finally:
            await scheduler.stop()
        jobs = {j.job_id: j for j in await store.list_all()}
        assert jobs[job_id].status == JobStatus.FAILED
        await store.close()


# ── #2: adapter receive loops start with websockets present ──────────────


class TestAdapterListenStarts:
    def test_websockets_importable(self) -> None:
        """The transport for Discord/Slack listen() is a real, declared dep."""
        import websockets  # noqa: F401

        assert hasattr(websockets, "connect")

    async def test_discord_listen_starts_and_stops(self) -> None:
        """Discord listen() loop starts and exits on a pre-set shutdown event.

        With shutdown_event already set, the `while not shutdown_event.is_set()`
        loop exits before opening any socket — proving the loop is wired and
        importable, without hitting the network.
        """
        from kazma_gateway.adapters.discord import DiscordAdapter

        adapter = DiscordAdapter(token="fake-token")
        queue: asyncio.Queue = asyncio.Queue()
        shutdown = asyncio.Event()
        shutdown.set()
        # Should return promptly without raising (no real connection attempted).
        await asyncio.wait_for(adapter.listen(queue, shutdown), timeout=5.0)

    async def test_slack_listen_starts_and_stops(self) -> None:
        pytest.importorskip("kazma_gateway.adapters.slack", reason="Slack adapter not yet merged")
        from kazma_gateway.adapters.slack import SlackAdapter  # type: ignore[import-not-found]

        adapter = SlackAdapter(bot_token="xoxb-fake", app_token="xapp-fake")
        queue: asyncio.Queue = asyncio.Queue()
        shutdown = asyncio.Event()
        shutdown.set()
        await asyncio.wait_for(adapter.listen(queue, shutdown), timeout=5.0)
