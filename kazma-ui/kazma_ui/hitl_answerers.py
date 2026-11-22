"""Who may answer a chat's approval card (Spaces S2-05), for the web routes.

``kazma_core.spaces.approvals`` holds the rule; this module finds what the
rule is asked about for one card of the chat surfaces: whose card it is, in
which space, about which tools.

* **whose**: the gate row's ``asker`` (the graph names the turn's asker on
  the card it raises), else the card's own ``asker``, else the chat's opener
  (its thread record), else nobody (the space's owners answer it);
* **where**: the chat's space (its thread record; a chat from before Spaces
  is the owner's), or a bridge gate's own tenant (``kazma mcp`` queues with
  no chat behind it);
* **about what**: the card's tools.

Nothing here acts unless Spaces is enforced. Blocking (the gate registry,
the thread record, the identity store): async callers use
``asyncio.to_thread``.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["WHO_FIELDS", "GateWhose", "answerable", "as_the_asker", "bell_items", "card_who", "clearable_thread", "refusal_for_card", "whose_card"]


#: What a card carries about who answers it (S2-05), from the graph's
#: interrupt to every copy of the card a surface keeps or sends.
WHO_FIELDS = ("asker", "answered_by", "answered_by_names")


def card_who(card: Any) -> dict[str, Any]:
    """The who-answers fields of *card* (:data:`WHO_FIELDS`), for a copy of
    the card: every place that rebuilds a card from the interrupt keeps them,
    or the page shows buttons to the wrong person. No I/O."""
    if not isinstance(card, dict):
        return {}
    return {key: card[key] for key in WHO_FIELDS if key in card}


@dataclass(frozen=True)
class GateWhose:
    """One card: its space, its asker and the tools it asks about."""

    space_id: str
    asker: str
    tools: tuple[str, ...]


def _row(gate_id: str) -> Any | None:
    if not gate_id:
        return None
    try:
        from kazma_core.safety.hitl_gates import gate_for, gate_registry_enabled

        if not gate_registry_enabled():
            return None
        return gate_for(gate_id)
    except (sqlite3.Error, OSError, ValueError):
        logger.warning("[HITL] the gate %s could not be read for who answers it", gate_id[:16],
                       exc_info=True)
        return None


def whose_card(thread_id: str, gate_id: str = "", payload: dict[str, Any] | None = None,
               *, row: Any = None) -> GateWhose:
    """Whose card *gate_id* (or the card *payload*) of *thread_id* is (the
    module's rules). Blocking."""
    from kazma_core.spaces import threads
    from kazma_core.spaces.approvals import tool_names
    from kazma_core.spaces.scope import space_of_record

    card = dict(payload or {})
    found = row if row is not None else _row(gate_id)
    mechanism = str(getattr(found, "mechanism", "") or card.get("mechanism") or "")
    record = threads.record_of(thread_id) if thread_id and mechanism != "bus_bridge" else None
    if record is not None:
        space = record.space_id
    elif mechanism == "bus_bridge":
        space = space_of_record(str(getattr(found, "tenant_id", "") or card.get("tenant_id") or ""))
    else:
        space = threads.thread_home(thread_id) if thread_id else space_of_record("")
    asker = (str(getattr(found, "asker", "") or "") or str(card.get("asker") or "")
             or (record.opener if record is not None else ""))
    tools = tool_names(card.get("tools"), str(card.get("tool") or card.get("tool_name") or ""))
    if found is not None and getattr(found, "tool", ""):
        tools = tool_names(tools, str(found.tool))
    return GateWhose(space_id=space, asker=asker, tools=tools)


def refusal_for_card(thread_id: str, gate_id: str = "", payload: dict[str, Any] | None = None,
                     *, caller: str | None = None, row: Any = None) -> tuple[str | None, GateWhose | None]:
    """Why *caller* (the account bound now when None) may not answer this
    card, with whose card it is: ``(None, whose)`` when they may. ``(None,
    None)`` while the install does not enforce Spaces (nothing is read).
    Blocking."""
    from kazma_core.spaces.approvals import answer_refusal
    from kazma_core.spaces.scope import spaces_enforced

    if not spaces_enforced():
        return None, None
    try:
        whose = whose_card(thread_id, gate_id, payload, row=row)
    except (sqlite3.Error, OSError):
        # Whose card it is cannot be read: nobody answers it for now, never
        # whoever asks.
        logger.warning("[HITL] whose card on %s is could not be read", str(thread_id)[:12],
                       exc_info=True)
        from kazma_core.spaces.approvals import IDENTITY_UNAVAILABLE

        return IDENTITY_UNAVAILABLE, None
    return answer_refusal(whose.space_id, whose.asker, whose.tools, caller=caller), whose


def answerable(items: list[dict[str, Any]], *, caller: str | None = None) -> list[dict[str, Any]]:
    """The pending cards of *items* the caller (the account bound now when
    None) may answer: all of them while the install does not enforce Spaces.
    Each item carries ``thread_id`` and, from the registry, ``gate_id``,
    ``asker``, ``tools`` and ``tool_name``. Blocking."""
    from kazma_core.spaces.scope import spaces_enforced

    if not spaces_enforced():
        return list(items)
    kept: list[dict[str, Any]] = []
    for item in items:
        refusal, _whose = refusal_for_card(
            str(item.get("thread_id") or ""), str(item.get("gate_id") or ""), item, caller=caller)
        if refusal is None:
            kept.append(item)
    return kept


def clearable_thread(thread_id: str, item: dict[str, Any] | None = None, *,
                     caller: str | None = None) -> bool:
    """Whether the caller may clear *thread_id*'s paused approvals: every
    pending card on it is one they may answer (Spaces S2-05). Always while
    the install does not enforce Spaces. A thread whose cards cannot be read
    is not cleared. Blocking."""
    from kazma_core.spaces.scope import spaces_enforced

    if not spaces_enforced():
        return True
    try:
        from kazma_core.safety.hitl_gates import gate_registry_enabled, live_gates

        rows = [r for r in live_gates(thread_id) if r.state == "pending"] if gate_registry_enabled() else []
    except (sqlite3.Error, OSError):
        logger.warning("[HITL] the cards of %s could not be read; not cleared", str(thread_id)[:12],
                       exc_info=True)
        return False
    if rows:
        return all(refusal_for_card(thread_id, r.gate_id, r.payload(), caller=caller, row=r)[0] is None
                   for r in rows)
    card = dict(item or {})
    return refusal_for_card(thread_id, str(card.get("gate_id") or ""), card, caller=caller)[0] is None


def bell_items(language: str = "en") -> list[dict[str, Any]]:
    """The cards waiting for the person looking, in the notification bell's
    shape (``/api/alerts/recent``, Spaces S2-05): the pending cards of the
    space bound to the page that they may answer, each linking to its chat.
    Nothing while the install does not enforce Spaces (the bell is as
    before), and nothing with no person bound. A card names its tool, never
    its arguments. Blocking."""
    from kazma_core.spaces.scope import current_scope, spaces_enforced

    if not spaces_enforced():
        return []
    bound = current_scope()
    if bound is None or not bound.asker:
        return []
    try:
        from kazma_core.safety.hitl_gates import gate_registry_enabled, pending_gates

        rows = pending_gates(tenant_id=bound.space_id) if gate_registry_enabled() else []
    except (sqlite3.Error, OSError):
        logger.warning("[HITL] the waiting cards could not be read for the bell", exc_info=True)
        return []
    ar = language == "ar"
    out: list[dict[str, Any]] = []
    for row in rows:
        if refusal_for_card(row.thread_id, row.gate_id, row.payload(), caller=bound.asker, row=row)[0]:
            continue
        link = f"link:/chat?s={row.session_id}" if row.session_id else "link:/dashboard"
        tool = str(row.tool or "")
        out.append({
            "id": f"approval-{row.gate_id}",
            "title": (f"موافقة بانتظارك: {tool}" if ar else f"Approval waiting for you: {tool}")
            if tool else ("موافقة بانتظارك" if ar else "Approval waiting for you"),
            "subsystem": "Approvals",
            "status": "pending",
            "reason": "",
            "callback_id": link,
            "button_text": "افتح المحادثة" if ar else "Open the chat",
            "timestamp": float(row.created_at or 0.0),
            "severity": "WARNING",
        })
    return out


@contextmanager
def as_the_asker(whose: GateWhose | None) -> Iterator[None]:
    """Bind the card's asker for the block, once Spaces is enforced and the
    card names one (S2-05): a resumed turn -- after a person's answer, an
    approver's, the admin's or the watchdog's timeout -- runs as the person
    whose turn it is, never as whoever answered. A task made in the block
    copies the binding. No I/O."""
    from kazma_core.spaces.scope import bind_scope, current_scope, spaces_enforced

    if whose is None or not spaces_enforced() or not whose.asker:
        yield
        return
    bound = current_scope()
    if bound is not None and (bound.space_id, bound.asker) == (whose.space_id, whose.asker):
        yield
        return
    with bind_scope(whose.space_id, whose.asker):
        yield
