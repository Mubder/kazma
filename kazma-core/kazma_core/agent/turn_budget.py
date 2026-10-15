"""A turn's wall-clock budget: one rule for every surface.

Until 2026-10-15 the chat apps, the CLI and reminders (``agent/turn.py``'s
``run_agent_turn``) honoured ``KAZMA_TURN_TIMEOUT_SECONDS`` while the web chat
(``kazma_ui.sse_chat``) had no wall-clock budget at all, the standalone agent
runner read the same variable with a default of its own (600 s), and the
resume after an approval was unbounded everywhere (full audit 2026-09-30).
Now every graph run goes through :func:`turn_segment_budget`, which asks
:func:`resolve_turn_budget`; nothing else reads the variable or the setting
(``tests/test_turn_budget.py``).

**The rule.** :func:`resolve_turn_budget` answers, for one thread:

* a ``/long`` task active on the chat (``long_task``, budget or mission
  mode, ``/unrestricted`` too): no time limit. The operator asked that chat
  to run long; its raised step budget and the mission's hard wall still
  bound it. ``long_task`` defines no wall-clock budget of its own.
* else ``KAZMA_TURN_TIMEOUT_SECONDS`` when it is set (whole seconds; ``0``
  is no limit; a value that is not a whole number from 0 to 86400 is ignored
  with one warning);
* else the setting ``agent.turn_timeout_seconds`` (Settings -> System; ``0``
  is no limit);
* else no limit. The step budget (``recursion_limit``) and each tool's own
  timeout bound a turn, as they always did.

The non-stop envelope (``agent.nonstop``) adds no budget of its own: its
stall watchdog and recovery attempts run inside the segment's budget, so a
timed-out run is never resumed past it.

**What counts.** The budget bounds one *running segment* of a turn: from the
moment the graph starts -- a new message, or the resume after an approval --
until it answers, pauses for a person, or is stopped. Waiting for an approval
is never counted: the pause ends the segment and the resume starts a fresh
one, so a human who takes ten minutes to decide never times a turn out. A
runaway tool loop never pauses, so it is one segment, which is what the
budget is for.

**What happens.** The run is cancelled -- the task running the graph is
cancelled, as the web's Stop and ``/abort`` cancel it, so no graph keeps
running behind the reply -- and the segment ends honestly: the checkpoint
gets the reply :func:`_timed_out_text` as the turn's last assistant message
(``turn_failed``, ``error_message``, no step pending), so every reader --
``close_turn``, the history, the gateway's reply, the next turn -- sees the
same answer; the owner is paged once (ops alert ``turn.timed_out``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "EXPIRED_KEY",
    "MAX_TURN_TIMEOUT_SECONDS",
    "TURN_TIMEOUT_ENV",
    "TURN_TIMEOUT_KEY",
    "TurnBudget",
    "TurnSegment",
    "parse_turn_timeout",
    "resolve_turn_budget",
    "timed_out_reply",
    "turn_segment_budget",
    "turn_timeout_setting",
]

TURN_TIMEOUT_ENV = "KAZMA_TURN_TIMEOUT_SECONDS"
TURN_TIMEOUT_KEY = "agent.turn_timeout_seconds"
#: The longest limit the setting takes: a day.
MAX_TURN_TIMEOUT_SECONDS = 86_400
#: Carried by the state a cut segment hands back (never written to the
#: checkpoint: the schema does not declare it), so a caller that reads the
#: run's result can tell a timeout from an answer.
EXPIRED_KEY = "_turn_budget_expired"

_warned: set[str] = set()


@dataclass(frozen=True)
class TurnBudget:
    """How long a running segment of this turn may take, and who said so."""

    seconds: int
    #: ``env`` (``KAZMA_TURN_TIMEOUT_SECONDS``) or ``setting``
    #: (``agent.turn_timeout_seconds``).
    source: str


@dataclass
class TurnSegment:
    """What :func:`turn_segment_budget` knows about the segment it bounds."""

    budget: TurnBudget | None = None
    #: True when the budget ran out and the segment was cut.
    expired: bool = False
    #: The honest reply, when it was cut.
    text: str = ""
    #: The state the cut segment ends on (the checkpoint's, with the reply).
    state: dict[str, Any] = field(default_factory=dict)


def parse_turn_timeout(value: Any) -> int | None:
    """*value* as whole seconds from 0 to a day, or ``None`` when it is not one."""
    if isinstance(value, bool):
        return None
    try:
        seconds = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return seconds if 0 <= seconds <= MAX_TURN_TIMEOUT_SECONDS else None


def _warn_once(key: str, message: str, *args: Any) -> None:
    if key not in _warned:
        _warned.add(key)
        logger.warning(message, *args)


def _env_limit() -> int | None:
    """``KAZMA_TURN_TIMEOUT_SECONDS`` as whole seconds, or ``None`` when it is
    unset or not one (that is said once)."""
    raw = (os.environ.get(TURN_TIMEOUT_ENV) or "").strip()
    if not raw:
        return None
    seconds = parse_turn_timeout(raw)
    if seconds is None:
        _warn_once(
            f"env:{raw}",
            "[turn-budget] %s=%r is not a whole number of seconds from 0 to %d; ignored",
            TURN_TIMEOUT_ENV, raw, MAX_TURN_TIMEOUT_SECONDS,
        )
    return seconds


def turn_timeout_setting(config_store: Any = None) -> dict[str, Any]:
    """The configured limit and where it comes from (no thread: no ``/long``).

    ``seconds`` is 0 for no limit; ``source`` is ``env``, ``setting`` or
    ``default``. Settings -> System shows this; :func:`resolve_turn_budget`
    applies it. Reads the settings store (unless the environment decides):
    never on the event loop.
    """
    env = _env_limit()
    if env is not None:
        return {"seconds": env, "source": "env"}
    if config_store is None:
        from kazma_core.config_store import get_config_store

        config_store = get_config_store()
    raw = config_store.get(TURN_TIMEOUT_KEY)
    if raw is None or str(raw).strip() == "":
        return {"seconds": 0, "source": "default"}
    seconds = parse_turn_timeout(raw)
    if seconds is None:
        _warn_once(
            f"setting:{raw}",
            "[turn-budget] %s=%r is not a whole number of seconds from 0 to %d; ignored",
            TURN_TIMEOUT_KEY, raw, MAX_TURN_TIMEOUT_SECONDS,
        )
        return {"seconds": 0, "source": "default"}
    return {"seconds": seconds, "source": "setting"}


def resolve_turn_budget(thread_id: str | None = None) -> TurnBudget | None:
    """This turn's wall-clock budget, or ``None`` for no limit (module doc).

    The one answer for every surface. Never raises: a settings store it
    cannot read leaves the environment's limit alone in force. Reads the
    store, so async code calls it through ``asyncio.to_thread``.
    """
    try:
        if thread_id:
            from kazma_core.agent.long_task import long_task_status

            if long_task_status(str(thread_id)).get("active"):
                return None
        current = turn_timeout_setting()
    except Exception:  # noqa: BLE001 -- an unreadable store must not stop a turn
        logger.warning(
            "[turn-budget] the settings store could not be read; only %s applies "
            "to this turn", TURN_TIMEOUT_ENV, exc_info=True,
        )
        current = {"seconds": _env_limit() or 0, "source": "env"}
    seconds = int(current.get("seconds") or 0)
    if seconds <= 0:
        return None
    return TurnBudget(seconds=seconds, source=str(current.get("source") or "setting"))


def _timed_out_text(budget: TurnBudget) -> str:
    """The reply a cut turn gives, on every surface."""
    if budget.source == "env":
        where = f"raise {TURN_TIMEOUT_ENV} in the server's environment"
    else:
        where = "raise the turn time limit in Settings > System"
    return (
        f"⚠️ Turn timed out after {budget.seconds}s and was stopped. "
        "Try a shorter request, send /long first for a task that needs longer "
        f"(it lifts the limit for that chat), or {where}."
    )


def timed_out_reply(state: Any) -> str:
    """The honest reply when *state* is what a cut segment handed back, else ``""``."""
    if not isinstance(state, dict) or not state.get(EXPIRED_KEY):
        return ""
    return str(state.get("error_message") or "")


def _report_turn_timeout(seconds: float, thread_id: str) -> None:
    """Page the owner: a turn hit its wall-clock budget (usually a runaway
    tool loop). The alert lived in the WebSocket graph client, which ran no
    turns, until 2026-09-30 (AUD-026); it sits beside the budget. ``alert``
    never raises."""
    try:
        from kazma_core.observability.ops_alerts import alert
    except ImportError:
        logger.warning("[turn-budget] ops alerts unavailable; turn.timed_out not sent")
        return
    alert(
        "turn.timed_out",
        f"A turn hit the {int(seconds)}s wall-clock limit and was cut off.",
        f"thread={str(thread_id or '')[:12]}. Usually a runaway tool loop.",
        severity="warn",
    )


def _thread_of(config: dict[str, Any] | None) -> str:
    try:
        return str(((config or {}).get("configurable") or {}).get("thread_id") or "")
    except (AttributeError, TypeError):
        return ""


def _user_turns(messages: list[Any]) -> int:
    return sum(1 for m in messages if isinstance(m, dict) and m.get("role") == "user")


async def _stored_values(graph: Any, config: dict[str, Any]) -> dict[str, Any]:
    snapshot = await graph.aget_state(config)
    values = getattr(snapshot, "values", None)
    return dict(values) if isinstance(values, dict) else {}


def _reply_messages(values: dict[str, Any], input_state: Any, text: str) -> list[Any]:
    """The turn's history, quiescent, with the honest reply at the end.

    The checkpoint is the base. A run cancelled before its first step was
    saved left the checkpoint without the question; the input then is (it
    holds the history the transport read plus the question). A tool call the
    cancelled run never answered is dropped (strict providers reject it).
    """
    from kazma_core.agent.graph_helpers import sanitize_tool_chains

    stored = sanitize_tool_chains(list(values.get("messages") or []))
    asked: list[Any] = []
    if isinstance(input_state, dict):
        asked = sanitize_tool_chains(list(input_state.get("messages") or []))
    base = asked if _user_turns(asked) > _user_turns(stored) else stored
    return [*base, {"role": "assistant", "content": text}]


def _reply_update(values: dict[str, Any], input_state: Any, text: str) -> dict[str, Any]:
    return {
        "messages": _reply_messages(values, input_state, text),
        "turn_failed": True,
        "error_message": text,
        "tool_calls_pending": [],
        "next_node": "end",
    }


async def _write_reply(graph: Any, config: dict[str, Any], thread_id: str,
                       update: dict[str, Any]) -> None:
    """Write the cut turn's end into the checkpoint, as its last step.

    Under the thread's checkpoint-mutation lock (TT-04), written as the
    ``respond`` node so the checkpoint has no next step.
    """
    try:
        from kazma_ui.thread_locks import thread_mutation

        lock = thread_mutation(thread_id)
    except ImportError:  # kazma_core alone: no other checkpoint rewriter runs
        lock = contextlib.nullcontext()
    async with lock:
        nodes = getattr(graph, "nodes", None) or {}
        if "respond" in nodes:
            await graph.aupdate_state(config, update, as_node="respond")
        else:
            await graph.aupdate_state(config, update)


async def _end_timed_out_segment(
    graph: Any,
    config: dict[str, Any],
    *,
    thread_id: str,
    budget: TurnBudget,
    input_state: Any,
) -> tuple[str, dict[str, Any]]:
    """Log, page once, and write the honest end; returns ``(text, state)``.

    A graph without a checkpointer (a scheduled task's or a sub-agent's
    one-shot graph) keeps nothing to write. A checkpoint that cannot be read
    is not written over: the caller still shows the reply.
    """
    text = _timed_out_text(budget)
    logger.error(
        "[turn] timed out after %ds (limit from %s, thread=%s); the run was cancelled",
        budget.seconds, budget.source, thread_id[:12],
    )
    _report_turn_timeout(budget.seconds, thread_id)
    values: dict[str, Any] = {}
    update: dict[str, Any] = {}
    if getattr(graph, "checkpointer", None) is not None:
        try:
            values = await _stored_values(graph, config)
            update = _reply_update(values, input_state, text)
            await _write_reply(graph, config, thread_id, update)
        except Exception:  # noqa: BLE001 -- the reply still reaches the caller
            logger.warning(
                "[turn-budget] the timed-out reply was not written to the checkpoint "
                "thread=%s; the caller still shows it", thread_id[:12], exc_info=True,
            )
    if not update:
        update = _reply_update(values, input_state, text)
    return text, {**values, **update, EXPIRED_KEY: budget.seconds}


@contextlib.asynccontextmanager
async def turn_segment_budget(
    graph: Any,
    config: dict[str, Any] | None,
    *,
    thread_id: str = "",
    input_state: Any = None,
    enforce: bool = True,
) -> AsyncIterator[TurnSegment]:
    """Bound one running segment of a turn by its wall-clock budget.

    Wrap the code that runs the graph -- ``graph.ainvoke``, or the loop that
    reads ``graph.astream_events`` -- in the task that runs it. When the
    budget runs out, that task is cancelled where it waits (the graph run
    unwinds), the honest end is written (module doc), the timeout is
    swallowed and ``segment.expired`` is True: the caller reads
    ``segment.text`` / ``segment.state``. A ``TimeoutError`` raised inside the
    turn itself (a tool's, a provider's) is not this budget's and propagates
    as before. A cancellation from outside (Stop, ``/abort``, shutdown) wins
    and propagates.

    ``enforce=False`` is for ``/compact`` only (maintenance the operator ran,
    not a turn; declared in ``tests/test_turn_budget.py``).
    """
    cfg = dict(config or {})
    tid = str(thread_id or _thread_of(cfg))
    budget = await asyncio.to_thread(resolve_turn_budget, tid) if enforce else None
    segment = TurnSegment(budget=budget)
    if budget is None:
        yield segment
        return
    window = asyncio.timeout(budget.seconds)
    try:
        async with window:
            yield segment
    except TimeoutError:
        if not window.expired():
            raise
        segment.expired = True
        segment.text, segment.state = await _end_timed_out_segment(
            graph, cfg, thread_id=tid, budget=budget, input_state=input_state,
        )
