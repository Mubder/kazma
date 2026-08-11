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

__all__ = ["build_resume_value", "is_semantic_kind"]


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
