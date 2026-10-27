"""A retry of a retry stays in its first task's lineage (audit 2026-10-26 AUD-016).

A runbook takes one automatic step per lineage a day (AGENTS.md §52), and a
swarm task's lineage is ``swarm_task:<retry_of or id>``. ``build_retry_task``
recorded the PARENT as ``retry_of``, so in A -> B -> C the third task's
lineage was B's id, not A's: every human retry of a retry reset the daily
limit, and during a provider outage a switched-on runbook retried without
bound. The chain's root is recorded now.

Negative control: the old rule (``retry_of = task_id``) run inline over the
same chain gives C a lineage of its own parent.
"""

from __future__ import annotations

from kazma_core.runs.adapters.swarm import ADAPTER
from kazma_core.swarm.task import SwarmTask, TaskStatus, TaskType
from kazma_core.swarm.task_control import build_retry_task


def _failed(task: SwarmTask) -> SwarmTask:
    task.status = TaskStatus.FAILED
    task.completed_at = "2026-10-26T10:00:00+00:00"
    return task


def _retry(original: SwarmTask) -> SwarmTask:
    retried = build_retry_task(task_id=original.id, history={original.id: original}, active_tasks={},
                               task_store=None)
    assert retried is not None
    return _failed(retried)


def _lineage(task: SwarmTask) -> str:
    return ADAPTER._run(task, owned=False, engine_up=True).lineage


def test_three_hops_share_the_first_tasks_lineage():
    a = _failed(SwarmTask(type=TaskType.DISPATCH, prompt="research the outage", workers=["w1"]))
    b = _retry(a)
    c = _retry(b)
    d = _retry(c)
    assert b.metadata["retry_of"] == a.id
    assert c.metadata["retry_of"] == a.id, "a retry of a retry names the root, not its parent"
    assert d.metadata["retry_of"] == a.id
    assert {_lineage(t) for t in (a, b, c, d)} == {f"swarm_task:{a.id}"}


def test_a_chain_stored_before_the_fix_still_finds_its_root():
    """B was retried under the old rule (``retry_of`` = A, its parent and
    root alike); retrying B now gives A, not B."""
    a = _failed(SwarmTask(type=TaskType.DISPATCH, prompt="x", workers=["w1"]))
    b = _failed(SwarmTask(type=TaskType.DISPATCH, prompt="x", workers=["w1"], metadata={"retry_of": a.id}))
    assert _retry(b).metadata["retry_of"] == a.id


def test_negative_control_the_old_rule_gives_each_hop_a_new_lineage():
    a = _failed(SwarmTask(type=TaskType.DISPATCH, prompt="x", workers=["w1"]))

    def old_retry(original: SwarmTask) -> SwarmTask:
        new_metadata = dict(original.metadata or {})
        new_metadata["retry_of"] = original.id  # the rule until 2026-10-26
        return _failed(SwarmTask(type=original.type, prompt=original.prompt, workers=list(original.workers),
                                 metadata=new_metadata))

    b = old_retry(a)
    c = old_retry(b)
    assert _lineage(c) == f"swarm_task:{b.id}" != _lineage(b), "the old rule: a new lineage per hop"
