"""Spaces S2-05/S2-07, the second review: a turn's queued memory work runs
as the person the turn was for, never as the space's maker.

``post_turn_memory`` and ``micro_consolidation`` (the deep pass, a model
call) were bound to ``identity.acting_account(tenant)``: in a shared space,
Reem's deep pass ran as the owner who made the space, so the owner's models
and daily limit applied, and Reem's disabled account changed nothing. Now
the turn's person travels in the payload (``asker``; ``said_by``; the
episode row's ``metadata.said_by`` for a task queued without it) and the
task runs as them, through the worker's own dispatch. The deep pass waits
(put back, no attempt spent) while they are disabled or past their daily
limit, and is not run for a person who left the space. Today's binding is
the control; nothing changes while Spaces is not enforced.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest
from kazma_core.spaces import people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

from tests.test_memory_spaces import A, _bound_as, _turn, mem  # noqa: F401 -- mem is the shared fixture


@pytest.fixture
def reem(mem) -> str:
    person = people.create_person("reem-s207-memory", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
    people.add_member(A, person["id"], by=OWNER_ACCOUNT_ID)
    return person["id"]


def _maker_binding(task_type: str, payload: Any) -> Any:
    """Today's binding (the control): the space's maker."""
    from kazma_core.memory.task_queue import TaskBinding
    from kazma_core.spaces.identity import acting_account

    tenant = str((payload or {}).get("tenant_id") or "") or A
    return TaskBinding(space=tenant, asker=acting_account(tenant))


def test_a_turns_memory_write_runs_as_its_person(mem, reem) -> None:
    seen = _bound_as("post_turn_memory", {"tenant_id": A, "said_by": reem, "user_text": "hi"})
    assert seen["scope"] == scope.Scope(A, reem), seen


def test_a_deep_pass_runs_as_its_person(mem, reem) -> None:
    episode = _turn(A, "chat-r", 1, "Which crane did we rent for the Tagus site?")
    seen = _bound_as("micro_consolidation", {"episode_id": episode, "asker": reem})
    assert seen["scope"] == scope.Scope(A, reem), seen


def test_a_deep_pass_queued_without_its_person_reads_the_rows_speaker(mem, reem) -> None:
    episode = _turn(A, "chat-s", 1, "Which crane did we rent for the Tagus site?")
    mem.db.execute("UPDATE episodes SET metadata_json = ? WHERE id = ?", (json.dumps({"said_by": reem}), episode))
    mem.db.commit()
    seen = _bound_as("micro_consolidation", {"episode_id": episode})
    assert seen["scope"] == scope.Scope(A, reem), seen


def test_control_the_space_makers_binding_ran_it_as_the_owner(mem, reem, monkeypatch) -> None:
    from kazma_core.memory import task_queue

    monkeypatch.setattr(task_queue, "_task_binding", _maker_binding)
    seen = _bound_as("post_turn_memory", {"tenant_id": A, "said_by": reem, "user_text": "hi"})
    assert seen["scope"] == scope.Scope(A, OWNER_ACCOUNT_ID), seen


def _deep_pass_outcome(mem: Any, payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The deep pass through the worker's dispatch: what the handler saw, and
    how the task ended (``ack`` or ``put_back``)."""
    import asyncio

    from kazma_core.memory import task_queue

    seen: dict[str, Any] = {}
    ended: list[str] = []

    async def handler(_payload: dict[str, Any]) -> bool:
        seen["ran"] = scope.require_scope()
        return True

    async def run() -> None:
        worker = task_queue._MemoryWorker()
        worker._ack = lambda task, **k: ended.append("ack:" + str(k.get("success")))  # type: ignore[method-assign]
        worker._put_back = lambda task, why: ended.append("put_back")  # type: ignore[method-assign]
        await worker._process("memory", {"id": "t1", "task_type": "micro_consolidation",
                                         "payload_json": json.dumps(payload)})

    old = task_queue._HANDLERS.get("micro_consolidation")
    task_queue._HANDLERS["micro_consolidation"] = handler
    try:
        asyncio.run(run())
    finally:
        if old is None:
            task_queue._HANDLERS.pop("micro_consolidation", None)
        else:
            task_queue._HANDLERS["micro_consolidation"] = old
    return seen, ended


def test_a_disabled_persons_deep_pass_waits(mem, reem) -> None:
    episode = _turn(A, "chat-d", 1, "Which crane did we rent for the Tagus site?")
    people.set_enabled(reem, False, by=OWNER_ACCOUNT_ID)
    seen, ended = _deep_pass_outcome(mem, {"episode_id": episode, "asker": reem})
    assert seen == {} and ended == ["put_back"]
    people.set_enabled(reem, True, by=OWNER_ACCOUNT_ID)
    seen, ended = _deep_pass_outcome(mem, {"episode_id": episode, "asker": reem})
    assert seen["ran"] == scope.Scope(A, reem) and ended == ["ack:True"]


def test_a_deep_pass_waits_past_the_persons_daily_limit(mem, reem, monkeypatch) -> None:
    from kazma_core.spaces import daily_limits

    episode = _turn(A, "chat-l", 1, "Which crane did we rent for the Tagus site?")
    monkeypatch.setattr(daily_limits, "verdict", lambda asker=None: {"over": asker == reem})
    seen, ended = _deep_pass_outcome(mem, {"episode_id": episode, "asker": reem})
    assert seen == {} and ended == ["put_back"]


def test_a_departed_persons_deep_pass_is_not_run(mem, reem) -> None:
    episode = _turn(A, "chat-g", 1, "Which crane did we rent for the Tagus site?")
    people.leave_space(A, by=reem)
    seen, ended = _deep_pass_outcome(mem, {"episode_id": episode, "asker": reem})
    assert seen == {} and ended == ["ack:True"]
    # Their message is still remembered: the write is Kazma's own work.
    written = _bound_as("post_turn_memory", {"tenant_id": A, "said_by": reem, "user_text": "hi"})
    assert written["scope"] == scope.Scope(A, OWNER_ACCOUNT_ID)


def test_the_put_back_spends_no_attempt(mem) -> None:
    from kazma_core.memory import task_queue

    task_id = task_queue.enqueue_task("micro_consolidation", {"episode_id": "e_x"})
    ops = sqlite3.connect(str(mem.tmp / "memory_ops.db"))
    ops.execute("UPDATE memory_task_queue SET status='processing', lease_token='L' WHERE id=?", (task_id,))
    ops.commit()
    task_queue._MemoryWorker()._put_back({"id": task_id, "lease_token": "L"}, "a reason")
    row = ops.execute("SELECT status, attempts, next_attempt_at FROM memory_task_queue WHERE id=?",
                      (task_id,)).fetchone()
    ops.close()
    assert row[0] == "pending" and row[1] == 0 and row[2] > 0


def test_the_turn_queues_its_deep_pass_with_its_person(mem, reem, monkeypatch) -> None:
    from kazma_core.memory import consolidator, task_queue

    queued: list[dict[str, Any]] = []
    monkeypatch.setattr(task_queue, "enqueue_task",
                        lambda kind, payload, **_k: queued.append({"kind": kind, **payload}) or "t")
    turn = [{"role": "user", "content": "Which crane did we rent for the Tagus site?"},
            {"role": "assistant", "content": "The Liebherr LTM 1100, for three weeks."}]
    assert consolidator._run_turn_memory(turn, session_id="chat-q", turn=1, tenant_id=A, said_by=reem)
    deep = [q for q in queued if q["kind"] == "micro_consolidation"]
    assert deep and deep[0]["asker"] == reem, queued


def test_with_spaces_off_nothing_changes(mem, reem) -> None:
    from kazma_core.memory.task_queue import TaskBinding, _task_binding

    mem.mp.setattr(scope, "_enforced", False)
    assert _task_binding("post_turn_memory", {"tenant_id": A, "said_by": reem}) == TaskBinding(space=A)
