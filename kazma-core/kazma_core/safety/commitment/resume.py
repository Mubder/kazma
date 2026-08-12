"""Resume-value builder for the unified HITL bus (Commitment Layer §4.3).

Two resume-value contracts exist, discriminated by the interrupt payload's
``kind`` field:

- ``security`` (absent or ``"security"``): ``{"approved": bool, "scope": ...}``
- ``semantic_clarify`` / ``semantic_confirm``: ``{tool_call_id: option_id}``

This helper maps the EXISTING Approve/Deny buttons to the semantic contract:
Approve → the first non-cancel option (the "best" resolution); Deny → cancel.
So every platform's existing HITL UI works for semantic clarifies immediately,
without per-option buttons. Dedicated per-option rendering (chat.js /
gateway keyboards) is a follow-on UX refinement.
"""

from __future__ import annotations

from typing import Any

from langgraph.types import Command

__all__ = ["build_resume_value", "is_semantic_kind", "read_pending_interrupt", "build_resume_command"]


# Interrupt payload types we know how to resume. ``hitl_approval`` covers the
# security + semantic clarify/confirm gates; ``hard_steer`` covers the /steer!
# pause. Anything else is treated as "not pending".
_KNOWN_INTERRUPT_TYPES = {"hitl_approval", "hard_steer"}


async def read_pending_interrupt(
    graph: Any,
    config: dict[str, Any],
    *,
    snapshot: Any = None,
) -> dict[str, Any] | None:
    """Return the pending interrupt payload on the graph checkpoint, or None.

    Folds the five previously-duplicated ``aget_state -> tasks -> interrupts ->
    value`` scans (HTTP / WS / gateway / watchdog / supersede) into one. Returns
    the first payload whose ``type`` is a known interrupt kind, or ``None`` when
    the graph is idle/finished (uniform stale-card handling).

    Callers that already hold a snapshot (e.g. HTTP's ``pre``) may pass it via
    ``snapshot=`` to avoid a second ``aget_state`` round-trip.
    """
    try:
        snap = snapshot if snapshot is not None else await graph.aget_state(config)
    except Exception:
        return None
    if snap is None or not getattr(snap, "next", None):
        return None
    for task in getattr(snap, "tasks", []) or []:
        for intr in getattr(task, "interrupts", []) or []:
            val = getattr(intr, "value", None)
            if val is None and isinstance(intr, dict):
                val = intr.get("value", intr)
            if isinstance(val, (list, tuple)) and val:
                val = val[0]
            if isinstance(val, dict) and val.get("type") in _KNOWN_INTERRUPT_TYPES:
                return val
    return None


def build_resume_command(
    payload: dict[str, Any] | None = None,
    *,
    approved: bool | None = None,
    choices: dict[str, Any] | None = None,
    scope: str = "once",
    reason: str = "",
    semantic_option: str | None = None,
    approved_ids: list[Any] | None = None,
    action: str | None = None,
    extra: dict[str, Any] | None = None,
) -> Command | None:
    """Build the single LangGraph resume ``Command`` for any interrupt kind.

    This is THE chokepoint — the only place production code constructs
    ``Command(resume=…)``. An architectural test (tests/test_resume_chokepoint.py)
    asserts no other production module does, so a future transport cannot
    silently re-create the WS drift bug (incident 2026-08-12).

    Dispatch on the pending payload's ``kind``/``type``:

    * ``action`` set (hard_steer / /steer!) → ``{"action": action}`` (constant;
      no payload needed).
    * ``semantic_clarify`` / ``semantic_confirm`` → ``{tool_call_id: option_id}``
      (explicit ``semantic_option``, or client ``choices``, or
      ``build_resume_value``'s best-option/cancel mapping).
    * ``security`` (default) → ``{"approved", "reason", "scope"[, "approved_ids",
      ...extra]}``.

    Returns ``None`` only for a stale HITL card (a HITL resume was requested but
    no payload is pending). Hard_steer always returns a Command.
    """
    # Hard steer: constant resume value; payload not required.
    if action is not None:
        return Command(resume={"action": action})

    if payload is None:
        return None  # stale HITL card — nothing to resume

    if is_semantic_kind(payload):
        items = payload.get("items", [])
        tcid = items[0].get("tool_call_id", "") if items else ""
        if semantic_option is not None:
            return Command(resume={tcid: semantic_option})
        if choices:
            return Command(resume=dict(choices))
        return Command(resume=build_resume_value(payload, bool(approved)))

    # security (default)
    rv: dict[str, Any] = {"approved": bool(approved), "reason": reason, "scope": scope}
    if approved_ids:
        rv["approved_ids"] = list(approved_ids)
    if extra:
        rv.update(extra)
    return Command(resume=rv)


def is_semantic_kind(payload: dict | None) -> bool:
    """True if the interrupt payload is a semantic clarify/confirm."""
    return bool(payload and payload.get("kind") in ("semantic_clarify", "semantic_confirm"))


def build_resume_value(
    payload: dict | None,
    approved: bool,
    **extra: Any,
) -> dict[str, Any]:
    """Build the ``Command(resume=...)`` value based on the interrupt kind.

    Args:
        payload: the interrupt payload (from aget_state → tasks → interrupts).
        approved: True for Approve, False for Deny.
        **extra: additional fields for the security resume (scope, reason, etc.).

    Returns:
        The resume-value dict matching the interrupt's kind.
    """
    kind = (payload or {}).get("kind", "security")
    if kind in ("semantic_clarify", "semantic_confirm"):
        items = (payload or {}).get("items", [])
        if not items:
            return {}  # malformed; the gate will treat as unresolved
        tcid = items[0].get("tool_call_id", "")
        if approved:
            opts = items[0].get("options", [])
            # pick the first non-cancel option (the resolver's "best" resolution)
            opt_id = next((o["id"] for o in opts if o.get("id") != "cancel"), "cancel")
            return {tcid: opt_id}
        return {tcid: "cancel"}
    # security (default): existing shape
    rv: dict[str, Any] = {"approved": approved}
    rv.update(extra)
    return rv
