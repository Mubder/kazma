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


# ── #4: delivery_target is captured, persisted, and used by _deliver ────


class _StubGraph2:
    """Compiled-graph stand-in whose reply echoes the prompt."""

    async def ainvoke(self, state, config=None):
        msgs = list(state.get("messages", []))
        user = ""
        for m in reversed(msgs):
            if isinstance(m, dict) and m.get("role") == "user":
                user = str(m.get("content", ""))
                break
        msgs.append({"role": "assistant", "content": f"done: {user}"})
        return {"messages": msgs}


class TestCronDeliveryTarget:
    """delivery_target routes results back to the originating chat."""

    async def test_delivery_target_persisted_and_used(self, tmp_path, monkeypatch) -> None:
        """A job scheduled with delivery_target delivers to that target."""
        store = SQLiteCronStore(db_path=str(tmp_path / "cron.db"))
        await store.init()

        delivered: list[tuple[str, str]] = []
        # NOTE: `kazma_core.tools.__init__` re-exports `send_message`, so the
        # name `kazma_core.tools.send_message` can resolve to the *function*
        # rather than the module. Reach the real module via sys.modules so the
        # patch is visible to _deliver's lazy `from ... import send_message`.
        import sys
        import kazma_core.tools.send_message  # noqa: F401 — ensure module loaded

        sm_mod = sys.modules["kazma_core.tools.send_message"]

        async def stub_send(target_id, text, *, backend="telegram"):
            delivered.append((target_id, text))
            return f"sent:{target_id}"

        monkeypatch.setattr(sm_mod, "send_message", stub_send, raising=False)

        scheduler = CronScheduler(
            store=store,
            graph_builder=lambda: _StubGraph2(),
            poll_interval=0.05,
        )

        info = await scheduler.schedule(
            timing="0m",
            prompt="ping",
            platform="telegram",
            delivery_target="telegram:12345",
        )
        job_id = info["job_id"]
        assert info["delivery_target"] == "telegram:12345"

        # delivery_target persisted on the job row.
        jobs = {j.job_id: j for j in await store.list_all()}
        assert jobs[job_id].delivery_target == "telegram:12345"

        await scheduler.start()
        try:
            # _deliver runs AFTER update_status(DONE), so poll for delivery
            # (not just DONE) to avoid a race that exits before _deliver fires.
            for _ in range(100):
                if delivered:
                    break
                await asyncio.sleep(0.05)
        finally:
            await scheduler.stop()

        assert delivered, "no delivery was made"
        assert delivered[0][0] == "telegram:12345", f"wrong target: {delivered[0][0]}"
        await store.close()

    async def test_empty_delivery_target_refuses_to_misdeliver(
        self, tmp_path, monkeypatch, caplog
    ) -> None:
        """An unrepairable target is reported, never sent to a guessed address.

        This used to assert that a job with no ``delivery_target`` fell back to
        its ``thread_id``. Commit 93803f59 removed that on purpose: a gateway
        thread id like ``gw-telegram-1`` is not an address, and
        ``send_message`` rejects anything that is not ``platform:id`` -- so the
        "fallback" could only ever fail downstream, and would misdeliver if it
        ever stopped failing.

        The repair chain (sibling target, then the session store) still runs
        first. What is asserted here is the end of it: when nothing valid can
        be recovered, the result is NOT sent, and the job is named at CRITICAL
        so the operator can fix or re-create it.
        """
        store = SQLiteCronStore(db_path=str(tmp_path / "cron.db"))
        await store.init()

        delivered: list[str] = []
        import sys
        import kazma_core.tools.send_message  # noqa: F401 — ensure module loaded

        sm_mod = sys.modules["kazma_core.tools.send_message"]

        async def stub_send(target_id, text, *, backend="telegram"):
            delivered.append(target_id)
            return f"sent:{target_id}"

        monkeypatch.setattr(sm_mod, "send_message", stub_send, raising=False)

        scheduler = CronScheduler(
            store=store,
            graph_builder=lambda: _StubGraph2(),
            poll_interval=0.05,
        )
        # No delivery_target → legacy behavior.
        info = await scheduler.schedule(
            timing="0m", prompt="ping", platform="telegram", thread_id="gw-telegram-1",
        )
        job_id = info["job_id"]

        await scheduler.start()
        try:
            # Nothing should ever be delivered here, so this waits for the job
            # to finish rather than for a delivery that must not arrive.
            for _ in range(60):
                jobs = [j for j in await store.list_all() if j.job_id == job_id]
                if jobs and str(getattr(jobs[0], "status", "")).upper().endswith("DONE"):
                    break
                if delivered:
                    break
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.2)  # let _deliver run past update_status
        finally:
            await scheduler.stop()

        assert not delivered, (
            f"delivered to a guessed address {delivered!r}; a thread id is not "
            "a delivery target and send_message would reject it anyway"
        )
        undeliverable = [
            r for r in caplog.records
            if r.levelname == "CRITICAL" and "UNDELIVERABLE" in r.getMessage()
        ]
        assert undeliverable, (
            "the result was dropped without telling anyone -- the whole point "
            "of 93803f59 was that these failures used to be silent"
        )
        assert job_id in undeliverable[0].getMessage(), (
            "the alert must name the job so it can actually be fixed"
        )
        await store.close()

    async def test_delivery_target_column_migration(self, tmp_path) -> None:
        """An existing cron.db without delivery_target gets the column added."""
        import aiosqlite

        db_path = str(tmp_path / "legacy.db")
        # Build a legacy schema WITHOUT the delivery_target column.
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute(
                "CREATE TABLE cron_jobs (job_id TEXT PRIMARY KEY, timing TEXT, "
                "prompt TEXT, platform TEXT, thread_id TEXT, status TEXT, "
                "created_at TEXT, next_run TEXT, last_result TEXT, tenant_id TEXT)"
            )
            await conn.execute(
                "INSERT INTO cron_jobs (job_id, timing, prompt, platform, thread_id, "
                "status) VALUES ('legacy-1', '5m', 'x', 'telegram', 't', 'pending')"
            )
            await conn.commit()

        # init() runs the idempotent ALTER — must not raise.
        store = SQLiteCronStore(db_path=db_path)
        await store.init()
        jobs = await store.list_all()
        assert len(jobs) == 1
        # Legacy row defaults to empty delivery_target, not an error.
        assert jobs[0].delivery_target == ""
        assert jobs[0].job_id == "legacy-1"
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
