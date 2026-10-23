"""A model call the swarm makes for a task outside a worker: one way in.

The pipeline Refiner and the fan-out / consult synthesis ask the model on a
task's behalf after its workers ran. Swarm audit SW-E15: the Refiner's call
ran for every pipeline, its output never reached the stored result, and its
tokens and cost reached neither the task's totals nor the per-call ledger;
the synthesis's tokens and cost were missing from the totals and the ledger
too. Every such call goes through :func:`call_task_model` (a gate holds it:
``tests/test_swarm_refiner.py``), which

* asks through ``agent.resilient_chat`` -- the path every swarm worker's call
  takes: transient retries, the failover chain, and one row per call in the
  per-call LLM ledger (``llm_calls.db``, ``thread_id`` = the task's id, as a
  worker's rows), ok or error;
* bounds what the model may write (``max_tokens``) and how long the caller
  waits (``deadline``);
* returns the answer with its usage, which the caller adds to the task's
  ``total_tokens`` / ``total_cost``; raises :class:`TaskModelUnavailableError`
  when there is no model or it answered nothing, and lets any other failure
  through. The caller keeps the unrefined result, logs a WARNING and counts
  it (``kazma_swarm_failures_total``).

What a worker wrote reaches this model only fenced (AGENTS §9, SW-S10): the
callers build the messages with ``fence_untrusted``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["TaskModelAnswer", "TaskModelUnavailableError", "call_task_model"]


class TaskModelUnavailableError(RuntimeError):
    """No model to ask, or it answered nothing."""


@dataclass(frozen=True)
class TaskModelAnswer:
    """The model's text and what the call cost."""

    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def record(self) -> dict[str, Any]:
        """What the task's metadata keeps of the call."""
        return {"model": self.model, "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens, "tokens": self.tokens,
                "cost_usd": round(self.cost_usd, 6)}


async def call_task_model(
    client: Any,
    messages: list[dict[str, Any]],
    *,
    task_id: str,
    label: str,
    max_tokens: int,
    deadline: float | None,
) -> TaskModelAnswer:
    """Ask *client* once for *task_id*, within *deadline* seconds (None: no
    deadline of ours). See the module docstring."""
    if client is None:
        raise TaskModelUnavailableError("no model is configured")
    from kazma_core.agent.resilient_chat import resilient_chat

    call = resilient_chat(client, messages=messages, thread_id=task_id, label=label,
                          max_tokens=max_tokens)
    if deadline:
        from kazma_core.bounded_cancel import wait_for_bounded

        response = await wait_for_bounded(call, deadline, what=f"task model call for {task_id}")
    else:
        response = await call
    text = str(getattr(response, "content", "") or "").strip()
    if not text:
        raise TaskModelUnavailableError("the model answered nothing")
    usage = getattr(response, "usage", None) or {}
    try:
        prompt = int(usage.get("prompt_tokens", 0) or 0)
        completion = int(usage.get("completion_tokens", 0) or 0)
    except (AttributeError, TypeError, ValueError):
        prompt = completion = 0
    try:
        cost = float(getattr(response, "cost_usd", 0.0) or 0.0)
    except (TypeError, ValueError):
        cost = 0.0
    return TaskModelAnswer(text=text, prompt_tokens=prompt, completion_tokens=completion,
                           cost_usd=cost, model=str(getattr(response, "model", "") or ""))
