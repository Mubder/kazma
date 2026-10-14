"""Memory writes and deep passes that fail are retried, never counted done;
the queue backs off, keeps its dead letters, runs each task once and keeps
memory writes clear of maintenance (Memory audits M12 and M13: MEM-R09,
MEM-L09, MEM-L04).

Found 2026-10-13:

* a turn whose episode was never written counted ``ok``, and its queue task
  ended "done" (``mirror_episode``'s None was ignored);
* an LLM outage turned every deep pass into a heuristic-only "done", never
  redone;
* a failed task went back to ``pending`` at once: three attempts in three
  polls, then a dead letter -- deleted a week later, and the health check
  that counted them cleared itself;
* the worker claimed eight rows and ran two; a row that waited past the stuck
  threshold was claimed again and ran twice; a pg_dump held up a user's turn.

Each test inverts one witness; each has its negative control.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from types import SimpleNamespace

import pytest

from kazma_core.memory import task_queue as tq

TURN = [{"role": "user", "content": "What did we decide about the Tagus bridge budget?"},
        {"role": "assistant", "content": "We capped it at 4M euros."}]


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory import consolidator, dual_write
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    monkeypatch.setattr("kazma_core.memory.forget.chat_keys", lambda key: [key] if key else [])
    monkeypatch.setattr(tq, "_worker", None)
    saved = dict(consolidator._post_turn_metrics)
    db = sqlite3.connect(str(tmp_path / "memory_state.db"), check_same_thread=False)
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"), check_same_thread=False)
    ops.row_factory = sqlite3.Row
    ensure_ops_schema(ops)
    dual_write._reset_mirror()
    yield SimpleNamespace(db=db, ops=ops, mp=monkeypatch)
    dual_write._reset_mirror()
    consolidator._post_turn_metrics.clear()
    consolidator._post_turn_metrics.update(saved)
    db.close()
    ops.close()


def _rows(mem) -> list[dict]:
    return [dict(r) for r in mem.ops.execute("SELECT * FROM memory_task_queue ORDER BY created_at")]


# ── M12: a failed episode write is not "ok" ───────────────────────────────


def test_a_turn_whose_episode_was_not_written_is_not_remembered(mem):
    from kazma_core.memory import consolidator, dual_write

    mem.mp.setattr(dual_write.DualWriteMirror, "_ensure", lambda self: False)
    before = consolidator.get_post_turn_metrics()
    assert consolidator._run_turn_memory(TURN, session_id="s-fail", turn=1, tenant_id="t") is False
    after = consolidator.get_post_turn_metrics()
    assert after["ok"] == before["ok"] and after["mirror_fail"] == before["mirror_fail"] + 1
    # The queue task is not done: the queue retries it.
    assert consolidator.run_deferred_turn_memory({
        "user_text": TURN[0]["content"], "assistant_text": TURN[1]["content"],
        "session_id": "s-fail-2", "turn": 1, "tenant_id": "t"}) is False


def test_the_old_mirror_call_counted_it_ok(mem):
    """Negative control: the call that ignored mirror_episode's None."""
    from kazma_core.memory import consolidator, dual_write

    def old(messages, *, session_id, turn, tenant_id="default", created_at=None):
        user, answer = consolidator.extract_turn_texts(messages)
        dual_write.mirror_episode(session_id=session_id or "unknown", turn_number=int(turn or 0),
                                  user_text=user, assistant_text=answer, tenant_id=tenant_id,
                                  created_at=created_at)

    mem.mp.setattr(dual_write.DualWriteMirror, "_ensure", lambda self: False)
    mem.mp.setattr(consolidator, "_mirror_turn_to_v2", old)
    assert consolidator._run_turn_memory(TURN, session_id="s-old", turn=1, tenant_id="t") is True


def test_a_refused_turn_is_not_a_failure(mem):
    """A chat the user keeps out of memory writes nothing and fails nothing."""
    from kazma_core.memory import consolidator, dual_write, forget

    assert forget.set_chat_remembered("s-off", False, tenant_id="t")["ok"]
    assert dual_write.write_episode(session_id="s-off", turn_number=1, user_text="hi there friend",
                                    tenant_id="t")[0] == "refused"
    before = consolidator.get_post_turn_metrics()["mirror_fail"]
    consolidator._run_turn_memory(TURN, session_id="s-off", turn=1, tenant_id="t")
    assert consolidator.get_post_turn_metrics()["mirror_fail"] == before


def test_the_live_thread_hands_a_failed_turn_to_the_queue(mem):
    from kazma_core.memory import consolidator, dual_write

    mem.mp.setattr(dual_write.DualWriteMirror, "_ensure", lambda self: False)

    async def turn():
        consolidator._schedule_post_turn_memory(TURN, session_id="s-live", turn=3, tenant_id="t")

    asyncio.run(turn())
    deadline = time.time() + 10
    while time.time() < deadline and not any(r["task_type"] == "post_turn_memory" for r in _rows(mem)):
        time.sleep(0.05)
    task = next(r for r in _rows(mem) if r["task_type"] == "post_turn_memory")
    assert json.loads(task["payload_json"])["session_id"] == "s-live"
    assert task["max_attempts"] == 6  # a memory write outlasts an hour's outage


# ── M12: an LLM outage is retried, an empty answer is done ─────────────────


class _Registry:
    def __init__(self, answer=None, fail: bool = False):
        self.answer, self.fail, self.calls = answer, fail, 0

    def get_client(self):
        registry = self

        class _Client:
            async def chat(self, messages, **kw):
                registry.calls += 1
                if registry.fail:
                    raise ConnectionError("provider unreachable")
                return registry.answer

        return _Client()


def _episode(mem, eid="e_noor"):
    mem.db.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, tier, "
        "created_at, metadata_json) VALUES (?, 't', 's1', 1, ?, 'Congratulations to her!', 'episodic', ?, '{}')",
        (eid, "My sister Noor started at the Gulbenkian museum as a curator.", time.time() - 60),
    )
    mem.db.commit()


NOOR = json.dumps({"beliefs": [{"subject": "noor", "predicate": "works_at", "object": "Gulbenkian museum",
                                "predicate_type": "functional", "confidence": 0.9, "importance": 2}]})


def test_a_deep_pass_during_an_outage_is_retried_and_completes_after(mem):
    from kazma_core.memory import consolidator
    from kazma_core.memory.worker_bootstrap import _handle_micro_consolidation

    _episode(mem)
    down = _Registry(fail=True)
    mem.mp.setattr("kazma_core.model_registry.get_model_registry", lambda: down)
    before = consolidator.get_post_turn_metrics()["deep_pass_failed"]
    assert asyncio.run(_handle_micro_consolidation({"episode_id": "e_noor"})) is False
    assert down.calls == 1 and consolidator.get_post_turn_metrics()["deep_pass_failed"] == before + 1
    assert mem.db.execute("SELECT COUNT(*) FROM beliefs").fetchone()[0] == 0
    # The provider is back: the retry learns the fact.
    mem.mp.setattr("kazma_core.model_registry.get_model_registry", lambda: _Registry(answer=NOOR))
    assert asyncio.run(_handle_micro_consolidation({"episode_id": "e_noor"})) is True
    assert mem.db.execute("SELECT object FROM beliefs WHERE predicate = 'works_at'").fetchone()[0] \
        == "Gulbenkian museum"


def test_an_empty_answer_is_done(mem):
    """The model answered and found nothing: no retry (not an outage)."""
    from kazma_core.memory.worker_bootstrap import _handle_micro_consolidation

    _episode(mem)
    mem.mp.setattr("kazma_core.model_registry.get_model_registry",
                   lambda: _Registry(answer=json.dumps({"beliefs": []})))
    assert asyncio.run(_handle_micro_consolidation({"episode_id": "e_noor"})) is True


def test_the_old_extractor_consumed_the_deep_pass(mem):
    """Negative control: one None for both -- the outage was "done"."""
    from kazma_core.memory import belief_extractor
    from kazma_core.memory.worker_bootstrap import _handle_micro_consolidation

    _episode(mem)

    async def old(*a, **k):
        return None, False

    mem.mp.setattr(belief_extractor, "_llm_extract", old)
    mem.mp.setattr("kazma_core.model_registry.get_model_registry", lambda: _Registry(fail=True))
    assert asyncio.run(_handle_micro_consolidation({"episode_id": "e_noor"})) is True


# ── M12: backoff, kept dead letters ───────────────────────────────────────


def _run_worker(seconds: float, **kw) -> None:
    async def main():
        worker = tq._MemoryWorker(poll_interval=0.05, **kw)
        worker.start()
        await asyncio.sleep(seconds)
        await worker.stop()

    asyncio.run(main())


def test_a_failed_task_waits_its_backoff(mem):
    tries: list[float] = []

    async def down(payload):
        tries.append(time.time())
        return False

    mem.mp.setitem(tq._HANDLERS, "probe_down", down)
    assert tq.enqueue_task("probe_down", {})
    _run_worker(1.0, max_concurrency=1)
    assert len(tries) == 1
    row = _rows(mem)[0]
    assert row["status"] == "pending" and row["attempts"] == 1
    assert row["next_attempt_at"] == pytest.approx(tries[0] + tq._retry_delay(1), abs=2)
    assert [tq._retry_delay(n) for n in (1, 2, 3, 4, 5, 6)] == [30, 120, 480, 1920, 3600, 3600]


def test_without_backoff_three_attempts_dead_letter_in_a_second(mem):
    """Negative control: the old immediate retry."""
    tries = []

    async def down(payload):
        tries.append(1)
        return False

    mem.mp.setattr(tq, "_retry_delay", lambda attempts: 0.0)
    mem.mp.setitem(tq._HANDLERS, "probe_down", down)
    assert tq.enqueue_task("probe_down", {})
    _run_worker(1.0, max_concurrency=1)
    assert len(tries) == 3 and _rows(mem)[0]["status"] == "failed"


def test_a_dead_letter_is_alerted_kept_and_counted_until_cleared(mem):
    alerts = []
    mem.mp.setattr("kazma_core.observability.ops_alerts.alert",
                   lambda key, title, detail="", **kw: alerts.append(key) or True)
    mem.mp.setattr(tq, "_retry_delay", lambda attempts: 0.0)

    async def down(payload):
        return False

    mem.mp.setitem(tq._HANDLERS, "probe_down", down)
    assert tq.enqueue_task("probe_down", {})
    _run_worker(1.0, max_concurrency=1)
    assert alerts == ["memory.dead_letter"]
    mem.ops.execute("UPDATE memory_task_queue SET updated_at = ?", (time.time() - 30 * 86400,))
    mem.ops.commit()
    tq.purge_completed_tasks(retention_days=7)
    assert [r["status"] for r in _rows(mem)] == ["failed"]  # kept past the retention
    from kazma_core.memory.v2_health import build_v2_health

    health = build_v2_health()
    assert health["queue"]["failed"] == 1 and health["status"] == "DEGRADED"
    # Negative control: the old purge took it.
    mem.ops.execute("DELETE FROM memory_task_queue WHERE status IN ('completed', 'failed') AND updated_at < ?",
                    (time.time() - 7 * 86400,))
    mem.ops.commit()
    assert _rows(mem) == []


# ── M13: each task runs once; memory writes have their own lane ───────────


async def _old_run(self) -> None:
    """The poll loop before M13: claim a batch of 8, run them on a semaphore."""
    sem = asyncio.Semaphore(self.max_concurrency)

    async def process(task):
        await self._process("maintenance", task)
        sem.release()

    while self._running:
        for task in await asyncio.to_thread(self._claim_batch):
            await sem.acquire()
            self._busy["maintenance"] += 1
            t = asyncio.create_task(process(task))
            self._inflight.add(t)
            t.add_done_callback(self._inflight.discard)
        try:
            self._wake.clear()
            await asyncio.wait_for(self._wake.wait(), timeout=self.poll_interval)
        except asyncio.TimeoutError:
            pass


def _first(mem, task_type: str) -> None:
    """Queue *task_type* ahead of the rest (the claim takes the oldest first)."""
    mem.ops.execute("UPDATE memory_task_queue SET created_at = created_at - 10 WHERE task_type = ?", (task_type,))
    mem.ops.commit()


def _slow_and_quick(mem):
    runs = {"slow": 0, "quick": 0}

    async def slow(payload):
        runs["slow"] += 1
        await asyncio.sleep(1.6)
        return True

    async def quick(payload):
        runs["quick"] += 1
        await asyncio.sleep(0.3)
        return True

    mem.mp.setattr(tq, "_STUCK_THRESHOLD_SEC", 1.0)
    mem.mp.setitem(tq._HANDLERS, "probe_slow", slow)
    mem.mp.setitem(tq._HANDLERS, "probe_quick", quick)
    assert tq.enqueue_task("probe_slow", {})
    assert tq.enqueue_task("probe_quick", {})
    _first(mem, "probe_slow")
    return runs


def test_a_task_runs_exactly_once(mem):
    runs = _slow_and_quick(mem)
    _run_worker(3.0, max_concurrency=1)
    assert runs == {"slow": 1, "quick": 1}
    assert {r["status"] for r in _rows(mem)} == {"completed"}


def test_the_batch_claim_ran_a_task_twice(mem):
    """Negative control: the old loop's batch claim."""
    mem.mp.setattr(tq._MemoryWorker, "_run", _old_run)
    runs = _slow_and_quick(mem)
    _run_worker(3.0, max_concurrency=1)
    assert runs["quick"] == 2


def _maintenance_then_turn(mem) -> dict:
    done: dict[str, float] = {}

    async def backup(payload):
        await asyncio.sleep(2.0)
        done.setdefault("backup", time.monotonic())
        return True

    async def turn(payload):
        done["turn"] = time.monotonic()
        return True

    mem.mp.setitem(tq._HANDLERS, "probe_backup", backup)
    mem.mp.setitem(tq._HANDLERS, "post_turn_memory", turn)
    assert tq.enqueue_task("probe_backup", {})
    assert tq.enqueue_task("probe_backup", {})
    assert tq.enqueue_task("post_turn_memory", {"user_text": "x"})
    _first(mem, "probe_backup")
    started = time.monotonic()
    _run_worker(2.5, max_concurrency=2)
    return {k: v - started for k, v in done.items()}


def test_a_memory_write_never_waits_behind_maintenance(mem):
    """Two long maintenance tasks hold both maintenance slots; a user's turn
    is written within half a second."""
    took = _maintenance_then_turn(mem)
    assert took["turn"] < 0.5, took


def test_without_lanes_the_turn_waited_for_the_backups(mem):
    """Negative control: one lane for everything."""
    mem.mp.setattr(tq, "MEMORY_LANE", frozenset())
    took = _maintenance_then_turn(mem)
    assert took.get("turn", 99) > 1.5, took
