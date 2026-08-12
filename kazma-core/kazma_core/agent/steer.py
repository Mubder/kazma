"""Steering & abort buffers for in-progress supervisor turns.

Three out-of-band signals a user can send to a RUNNING turn, without
cancelling-and-restarting:

* **Soft steer** (``/steer <text>``) — buffered; the supervisor drains it at
  the top of the next iteration and folds it into the LLM call. Zero
  disruption, ~one tool-round-trip latency.
* **Hard steer** (``/steer! <text>``) — pauses the running turn at the next
  supervisor entry via a LangGraph ``interrupt()``; the note is injected as a
  first-class message and the turn resumes. Forceful: the model must see it
  before proceeding.
* **Abort** (``/abort``) — handled in the UI/gateway layers; this module
  provides the marker text + ``clear_all_steers`` so an abort also drops any
  pending steers.

This mirrors the scratchpad buffer pattern (``turn_input.py``): a
process-wide dict keyed by ``thread_id``, drained by the supervisor node.
Producers are external HTTP/gateway requests with no live node
``ContextVar``, so — like the scratchpad *drain* site — everything is keyed
by an explicit ``thread_id`` argument.

Injected steer text is **trusted user direction** (the user typed it), so it
is NOT wrapped in ``format_untrusted_block``. That fence is reserved for
untrusted LLM-generated deltas (the self-improvement Soul contract,
AGENTS.md §11); fencing a steer would tell the model to treat the user's own
guidance as low-trust observation data, defeating the purpose. A
``[KAZMA …]`` prefix is used purely for traceability — the same convention
as the watchdog's ``[KAZMA RECOVERY]`` notes and auto-continue's injected
user message.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


# ── Buffers (process-wide, keyed by thread_id) ─────────────────────────
# All access is within the single asyncio event loop (HTTP/gateway handlers
# and supervisor nodes), so plain dicts suffice — same model as the
# scratchpad buffer in turn_input.py.

# Soft steer: FIFO list of {"text": str, "ts": str}. Drained (popped) each
# supervisor iteration; the note is appended to messages for that LLM call.
_soft_steer_buffers: dict[str, list[dict[str, str]]] = {}

# Hard steer: FIFO deque of steer texts. The supervisor peeks the head before
# calling interrupt(); on resume it pops the same item. One interrupt cycle
# per item, so stacked hard steers each get their own pause/resume.
_hard_steer_pending: dict[str, deque[str]] = {}


# ── Soft steer ─────────────────────────────────────────────────────────


def push_soft_steer(thread_id: str, text: str) -> None:
    """Buffer a soft-steer note for *thread_id* (drained next iteration)."""
    tid = thread_id or "_default"
    val = str(text or "").strip()
    if not val:
        return
    buf = _soft_steer_buffers.setdefault(tid, [])
    buf.append({"text": val[:8000], "ts": _now_iso()})
    logger.info("[steer] soft queued thread=%s queue_len=%d", tid[:12], len(buf))


def drain_soft_steers(thread_id: str) -> list[dict[str, str]]:
    """Pop all queued soft steers for *thread_id* (FIFO)."""
    tid = thread_id or "_default"
    return list(_soft_steer_buffers.pop(tid, []) or [])


# ── Hard steer ─────────────────────────────────────────────────────────


def push_hard_steer(thread_id: str, text: str) -> None:
    """Queue a hard-steer text for *thread_id* (triggers an interrupt)."""
    tid = thread_id or "_default"
    val = str(text or "").strip()
    if not val:
        return
    dq = _hard_steer_pending.setdefault(tid, deque())
    dq.append(val[:8000])
    logger.info("[steer] hard queued thread=%s pending=%d", tid[:12], len(dq))


def peek_hard_steer(thread_id: str) -> str | None:
    """Non-destructive read of the head hard-steer.

    The supervisor peeks before calling ``interrupt()`` so the payload can
    advertise the text to the resume poller.
    """
    tid = thread_id or "_default"
    dq = _hard_steer_pending.get(tid)
    if not dq:
        return None
    return dq[0]


def pop_hard_steer(thread_id: str) -> str | None:
    """Destructive pop of the head hard-steer.

    Called by the supervisor AFTER ``interrupt()`` returns (i.e. after the
    resume). Peek-then-pop keeps the text pending if the turn is aborted
    before resume — ``clear_all_steers`` then drops it.
    """
    tid = thread_id or "_default"
    dq = _hard_steer_pending.get(tid)
    if not dq:
        return None
    val = dq.popleft()
    if not dq:
        _hard_steer_pending.pop(tid, None)
    return val


def has_hard_steer(thread_id: str) -> bool:
    """Whether a hard steer is pending for *thread_id*."""
    tid = thread_id or "_default"
    return bool(_hard_steer_pending.get(tid))


def hard_steer_payload(text: str) -> dict[str, Any]:
    """Build the ``interrupt()`` payload for a hard steer.

    Shape mirrors the HITL ``hitl_approval`` payloads so the existing
    resume/detection plumbing can consume it; distinguished by ``type``.
    """
    return {
        "type": "hard_steer",
        "text": str(text or ""),
        "message": "The user paused this task to add a requirement.",
    }


def is_hard_steer_interrupt(snapshot: Any) -> str | None:
    """If *snapshot* is paused at a ``hard_steer`` interrupt, return its text.

    Mirrors the HITL ``_check_graph_interrupt`` discriminator (``hitl.py``):
    a non-empty ``snapshot.next`` plus a task carrying our interrupt payload.
    Used by the Web/gateway resume pollers to detect that the supervisor has
    reached the pause point.
    """
    if snapshot is None or not getattr(snapshot, "next", None):
        return None
    for task in getattr(snapshot, "tasks", []) or []:
        for intr in getattr(task, "interrupts", []) or []:
            payload = getattr(intr, "value", None)
            if payload is None and isinstance(intr, dict):
                payload = intr.get("value", intr)
            if isinstance(payload, (list, tuple)) and payload:
                payload = payload[0]
            if isinstance(payload, dict) and payload.get("type") == "hard_steer":
                return str(payload.get("text") or "")
    return None


# ── Shared ─────────────────────────────────────────────────────────────


def clear_all_steers(thread_id: str) -> None:
    """Drop all pending soft + hard steers for *thread_id* (called on abort)."""
    tid = thread_id or "_default"
    _soft_steer_buffers.pop(tid, None)
    _hard_steer_pending.pop(tid, None)


# ── Framing helpers ────────────────────────────────────────────────────
# NOT fenced — steer is trusted user input (like any user message), not an
# untrusted soul delta. Prefixes are for traceability/debug only.


def soft_steer_note(text: str) -> str:
    """Format a soft-steer note appended to messages for the next LLM call."""
    return (
        "[KAZMA STEER] The user added this context while your task is "
        "running. Incorporate it into your ongoing work — do NOT restart or "
        "discard progress already made:\n\n"
        f"{text}"
    )


def hard_steer_note(text: str) -> str:
    """Format a hard-steer note injected after the turn resumes from interrupt."""
    return (
        "[KAZMA STEER!] The user PAUSED this task to add a requirement you "
        "MUST address before continuing. Do not restart finished steps; fold "
        "this into the remaining work:\n\n"
        f"{text}"
    )


def abort_marker(ts: str | None = None) -> str:
    """System marker written to the checkpoint on /abort (suppresses resume)."""
    return (
        "[KAZMA ABORT] System note: the user cancelled this task "
        f"at {ts or _now_iso()}. Do NOT resume or continue it — treat it as "
        "abandoned. Only revisit the work if the user explicitly asks you to "
        "redo it."
    )


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
