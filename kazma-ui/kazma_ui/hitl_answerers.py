"""Who may answer a chat's approval card (Spaces S2-05), for the web routes.

``kazma_core.spaces.approvals`` holds the rule; this module finds what the
rule is asked about for one card of the chat surfaces: whose card it is, in
which space, about which tools.

* **whose**: the card's own ``asker`` (the graph names the turn's asker on
  the live interrupt it raises), else the gate row's, else the chat's opener
  (its thread record), else nobody (the space's owners answer it). The card
  wins over the row: a caller asks about the LIVE interrupt (its id and its
  payload, never an id a client named), and a row is only what a writer
  recorded about it;
* **where**: the chat's space (its thread record; a chat from before Spaces
  is the owner's), or a bridge gate's own tenant (``kazma mcp`` queues with
  no chat behind it);
* **about what**: the card's tools -- its ``tools``, its ``tool``, and a
  semantic card's ``items[].tool`` (a clarify or confirm card asks about
  the calls its items name).

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

__all__ = ["WHO_FIELDS", "GateWhose", "answerable", "as_the_asker", "bell_items", "card_who", "cards_of_this_space", "clearable_thread", "waiting_for_someone_else", "whose_turn", "refusal_for_card", "whose_card"]


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
    asker = (str(card.get("asker") or "") or str(getattr(found, "asker", "") or "")
             or (record.opener if record is not None else ""))
    tools = tool_names(card.get("tools"), str(card.get("tool") or card.get("tool_name") or ""))
    item_tools = [str(item.get("tool") or "") for item in card.get("items") or ()
                  if isinstance(item, dict) and item.get("tool")]
    if item_tools:
        tools = tool_names([*tools, *item_tools])
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


def cards_of_this_space(items: list[dict[str, Any]], *, caller: str | None = None) -> list[dict[str, Any]]:
    """The cards of *items* that are cards of the space bound now and that
    the caller may answer: all of them while the install does not enforce
    Spaces, none with no space bound. For cards no chat store filters by
    space first -- an MCP server's sampling request is kept process-wide
    (``mcp.spec_client``), whatever space its turn ran in. Blocking."""
    from kazma_core.spaces.scope import current_scope, spaces_enforced

    if not spaces_enforced():
        return list(items)
    bound = current_scope()
    if bound is None:
        return []
    kept: list[dict[str, Any]] = []
    for item in items:
        refusal, whose = refusal_for_card(str(item.get("thread_id") or ""), "", item, caller=caller)
        if refusal is None and whose is not None and whose.space_id == bound.space_id:
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


def _is_steer_row(row: dict[str, Any]) -> bool:
    """A steer's row (``/steer``, ``/steer!``): a note to a running turn,
    never the message that opened one."""
    return bool(row.get("steer")) or str(row.get("content") or "").startswith(("/steer ", "/steer! "))


def _open_turn_id(rows: list[dict[str, Any]], thread_id: str) -> str:
    """The id of the turn running or waiting on the chat: this process's open
    reply turn, else a trailing assistant row still marked open (a pause that
    outlived a restart)."""
    from kazma_ui.reply_sink import current_reply_turn

    turn = current_reply_turn(thread_id)
    if turn:
        return turn
    for row in reversed(rows):
        if row.get("role") == "assistant":
            return str(row.get("turn_id") or "") if row.get("lifecycle") == "open" or row.get("open") else ""
    return ""


def _turn_author(thread_id: str, space_id: str, reader: str) -> str:
    """The author of the message that OPENED the turn running or waiting on
    *thread_id*'s chat (the chat store stamps it, ``reply_sink.authored``),
    or ``""``: the newest user message before that turn's reply row -- never
    a steer's row, nor a message after it (the S2-05 second review: the
    newest row was a steer, so another member's steer made the turn theirs).
    With no turn open, the newest user message that is not a steer. Read in
    the chat's own space (the chat store is each space's), as *reader*: a
    resume at start has nothing bound. Blocking."""
    from kazma_core.spaces.scope import bind_scope
    from kazma_ui.session_manager import get_session_manager

    with bind_scope(space_id, reader):
        session = get_session_manager().get_by_thread_id(thread_id)
    rows = [row for row in (getattr(session, "messages", None) or []) if isinstance(row, dict)]
    turn = _open_turn_id(rows, thread_id)
    end = len(rows)
    if turn:
        end = next((i for i, row in enumerate(rows)
                    if row.get("role") == "assistant" and str(row.get("turn_id") or "") == turn), end)
    for row in reversed(rows[:end]):
        if row.get("role") == "user" and not _is_steer_row(row):
            return str(row.get("author") or "")
    return ""


def whose_turn(thread_id: str) -> GateWhose:
    """Whose turn on *thread_id* is (the one running, paused or cut off): the
    thread's space (its record) and the turn's asker -- the author of the
    message that opened the turn (``_turn_author``), else the chat's opener. What a resume that
    is not an answer to a card binds (S2-05 review): a cut turn continued
    after a restart, a hard steer's resume. Blocking (the thread record, the
    chat store)."""
    from kazma_core.spaces import threads

    from kazma_core.spaces.identity import acting_account

    record = threads.record_of(thread_id) if thread_id else None
    space = record.space_id if record is not None else threads.thread_home(thread_id)
    opener = record.opener if record is not None else ""
    asker = _turn_author(thread_id, space, opener or acting_account(space)) or opener
    return GateWhose(space_id=space, asker=asker, tools=())


def waiting_for_someone_else(thread_id: str, card: dict[str, Any] | None, interrupt_id: str = "",
                             *, caller: str | None = None) -> str | None:
    """The name of the person *thread_id*'s pending *card* waits on, when
    that is not the caller (the account bound now when None): a new message
    from anyone else would drop the card (LangGraph discards a pending
    interrupt when a new turn starts), so it is refused instead (S2-05
    review). None when no card waits, the card is the caller's own, or the
    install does not enforce Spaces. A card whose person cannot be read is
    someone else's (never dropped on a guess). Blocking."""
    from kazma_core.spaces.scope import bound_asker, spaces_enforced

    if not spaces_enforced() or not card:
        return None
    person = caller or bound_asker() or ""
    try:
        asker = whose_card(thread_id, interrupt_id, card).asker
    except (sqlite3.Error, OSError):
        logger.warning("[HITL] whose card waits on %s could not be read", str(thread_id)[:12], exc_info=True)
        return "?"
    if not asker or asker == person:
        return None
    return _account_name(asker)


#: What a person may do to a chat's turn (Spaces S2-05 second review).
TURN_ACTIONS = ("message", "steer", "stop", "reset")


def _account_name(account_id: str) -> str:
    """An account's name for a refusal's line; ``"?"`` when it cannot be read."""
    from kazma_core.spaces.identity import IdentityUnavailable, read_registry

    try:
        account = (read_registry().get("accounts") or {}).get(account_id) or {}
    except IdentityUnavailable:
        return "?"
    return str(account.get("name") or "").strip() or "?"


def turn_control_refusal(thread_id: str, action: str, *, active: bool,
                         caller: str | None = None) -> tuple[str, str] | None:
    """Why the caller (the account bound now when None) may not do *action*
    to *thread_id*'s chat, as ``(code, name)``, or None when they may. Once
    Spaces is enforced (else None, nothing read); *active* says a turn runs
    or waits on the chat. The turn's asker is ``whose_turn``'s.

    * ``message`` -- a new message while a turn runs (it would cancel it):
      only its asker; anyone else is told the chat is answering them.
    * ``steer`` -- only the turn's asker, running or paused.
    * ``stop`` (and abort) -- its asker or an owner of the space
      (``shared_work.may_change``).
    * ``reset`` (and compact) -- the chat's opener or an owner of the space,
      and never while another member's turn runs or waits.

    A turn whose asker is not recorded (a chat from before Spaces, in a
    personal space) refuses nobody. Blocking (the thread record, the chat
    store, the identity store); ``IdentityUnavailable`` or a store error when
    it cannot say."""
    from kazma_core.spaces.scope import bound_asker, spaces_enforced

    if action not in TURN_ACTIONS:
        raise ValueError(f"not a turn action: {action!r}")
    if not spaces_enforced():
        return None
    person = caller or bound_asker() or ""
    whose = whose_turn(thread_id) if active else None
    asker = whose.asker if whose is not None else ""
    if action == "reset":
        if asker and asker != person:
            return "another_members_turn", _account_name(asker)
        from kazma_core.spaces import threads
        from kazma_core.spaces.identity import owns_space

        record = threads.record_of(thread_id)
        opener = record.opener if record is not None else ""
        if opener and opener != person and not owns_space(person, record.space_id):
            return "not_your_chat", _account_name(opener)
        return None
    if not asker or asker == person:
        return None
    if action == "stop":
        from kazma_core.spaces.identity import owns_space

        return None if person and owns_space(person, whose.space_id) else ("not_your_turn", _account_name(asker))
    return ("answering_someone_else" if action == "message" else "not_your_turn"), _account_name(asker)


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
