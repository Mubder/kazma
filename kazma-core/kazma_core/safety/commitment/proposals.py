"""Proposal-backed outbound tools.

``x_post`` / ``x_schedule_post`` / ``book_x_post`` must not be invoked as
ordinary tool calls: the commitment resolver rewrites ``text`` from the
stored proposal.

This module did not exist until 2026-09-17. The tool-worker imported it
inside ``except Exception: pass`` from 2026-09-04, so the import raised
``ImportError`` on every turn with pending tools and the filter never
ran. Commitment-authorize was the only remaining choke.

A draft is published only after the user has seen it (2026-10-16). Asked to
"check the metrics", the agent saved a new draft and asked to post it two
seconds later, in the same turn; the owner approved the card believing it was
part of the reading, and the post went out. A draft written in a turn may not
be published in that turn: the agent shows it and stops, and a later message
from the user asks to post it. :func:`bind_turn` names the running turn
(thread + the turn's ``created_at``, the identity the effect journal uses);
``save_proposal`` stamps a draft with it; the resolver refuses a draft whose
stamp is the running turn (:func:`drafted_this_turn`).
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

__all__ = [
    "DRAFTED_THIS_TURN",
    "PROPOSAL_TOOLS",
    "bind_turn",
    "current_turn",
    "drafted_this_turn",
    "is_proposal_tool",
    "reset_turn",
]

PROPOSAL_TOOLS: frozenset[str] = frozenset(
    {
        "x_post",
        "x_schedule_post",
        "book_x_post",
    }
)

#: The running turn: ``"<thread>|<created_at>"``, or "" outside a graph turn.
_current_turn: ContextVar[str] = ContextVar("kazma_proposal_turn", default="")

#: What the agent is told when it tries to publish a draft it just wrote.
DRAFTED_THIS_TURN = (
    "Not posted: this draft was written in this same turn, so the user has "
    "not seen it yet. Show the user the full draft text in your reply and "
    "ask whether to publish it, then stop. Post it only after the user "
    "answers in a new message (the draft is saved; use the same proposal_id)."
)


def is_proposal_tool(name: str) -> bool:
    """True when *name* is an outbound publish that requires a proposal_id."""
    return str(name or "").strip() in PROPOSAL_TOOLS


def _turn_of(state: Any) -> str:
    """The turn *state* belongs to, or "" when it carries no turn scope."""
    try:
        thread = str(state.get("thread_id") or "")
        scope = str(state.get("created_at") or "")
    except AttributeError:
        return ""
    return f"{thread}|{scope}" if thread and scope else ""


def bind_turn(state: Any) -> Token[str]:
    """Name the running turn for the tools and gates the tool worker runs."""
    return _current_turn.set(_turn_of(state))


def reset_turn(token: Token[str]) -> None:
    _current_turn.reset(token)


def current_turn() -> str:
    return _current_turn.get()


def drafted_this_turn(info: dict[str, Any] | None) -> bool:
    """True when the draft *info* (``resolve_proposal``'s answer) was saved
    in the running turn, or when that cannot be ruled out.

    A draft with no stamp was saved by a page or an older build: the owner
    saw it there. A stamped draft is judged against the running turn; with
    no running turn to compare (a call outside a chat turn), it is refused --
    the user can post it from the chat.
    """
    saved = str((info or {}).get("saved_turn") or "")
    if not saved:
        return False
    now = current_turn()
    return not now or now == saved
