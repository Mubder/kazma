"""Which keys of a swarm task's metadata a request may set (Spaces S1-15 review).

``POST /api/swarm/dispatch`` takes a free-form ``metadata`` object (the Swarm
page's Play mode has a JSON field for it). Some keys the swarm reads are
Kazma's own record of a task: whose it is, where it came from and where its
result and its incidents are sent, what its worker may do, and how a run
ended. A request that named them would run a worker as another person or in
another space, send the result to a chat of its choosing, lift the worker's
limits, or forge an outcome. :data:`KAZMA_KEYS` names them, each with what it
is, and the route refuses a request that names one (:func:`kazma_keys_in`).
:data:`REQUEST_KEYS` names what a request may set.

``tests/test_swarm_task_metadata.py`` reads every key the swarm reads from a
metadata mapping and holds each to one of the two lists: a key read with
neither fails the build.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["KAZMA_KEYS", "REQUEST_KEYS", "kazma_keys_in"]

#: What a dispatch request may set, and why it may.
REQUEST_KEYS: dict[str, str] = {
    "routes": "a conditional task's routes (the page's Conditional form)",
    "default": "a conditional task's route when none of its routes matches",
    "max_concurrent": "a fan-out's concurrency (the request's own field too)",
    "max_retries": "a worker's retries (the request's own field too)",
    "hitl_checkpoints": "the pipeline steps that wait for the operator (the page's pipeline form)",
    "checkpoint_timeout": "how long a pipeline checkpoint waits for the operator",
    "kind": "a label the task list filters by",
}

#: What only Kazma writes on a task, never taken from a request.
KAZMA_KEYS: dict[str, str] = {
    # Whose task it is (stamped at admission, Spaces S1-15).
    "tenant_id": "the task's space",
    "asker": "the account the task acts for: its worker runs as them",
    # Where it came from: its result and its incidents are sent there.
    "source": "the surface that started the task",
    "source_platform": "the chat app a task from a chat came from",
    "source_delivery_target": "the chat address its result and incidents are sent to",
    "source_thread_id": "the conversation that started it",
    "source_chat_id": "the chat app's chat that started it",
    "source_user": "the chat app's sender that started it",
    # What its worker may do.
    "unrestricted": "the chat's /unrestricted: the worker's longer limits",
    "outside_text": "what the delegating turn had read: the fetch check after outside text",
    "commitment_scope": "the worker's privilege cap (the commitment layer's scope token)",
    "workspace_id": "the workspace the worker's file tools use (the IDE sets it)",
    # Its history and how a run ended.
    "retry_of": "the task a retry repeats",
    "retry_acknowledged_unknown": "a retry of a run whose effects were unknown, acknowledged",
    "execution_outcome": "whether a run that stopped had invoked tools",
    "effects_uncertain": "a result whose effects are unknown",
    "invoked_tools": "the tools an interrupted run had invoked",
    "interrupted_by": "what stopped a run",
    "result_status": "a stored result's status",
    "orphan_results": "an older run's result kept beside the current one",
    "step_journal": "a pipeline's journaled steps",
    "recovery_count": "how often recovery picked the task up",
    "recovery_error": "why recovery could not",
    "checkpoint": "a paused pipeline's checkpoint",
    "hitl_checkpoint": "the checkpoint a paused pipeline waits at",
    "paused_blackboard": "a paused pipeline's blackboard",
    "workflow_id": "the durable path's workflow",
    "durable": "the durable path that ran it",
    "routing_diagnostics": "the candidates semantic routing weighed",
    "admission_denied": "the engine refused the task at capacity",
    "max_concurrent_tasks": "the capacity it was refused at",
    "admission_unrecorded": "the admission write did not land",
    "code": "a refusal's code",
    "summary": "a worker registry entry's summary",
}


def kazma_keys_in(metadata: Mapping[str, Any] | None) -> list[str]:
    """The keys of *metadata* that only Kazma sets, sorted (``[]`` for none
    or for no mapping)."""
    if not isinstance(metadata, Mapping):
        return []
    return sorted(key for key in metadata if key in KAZMA_KEYS)
