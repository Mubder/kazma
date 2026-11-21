"""The space a chat app's message works in (Spaces, until stage 3).

The chat apps -- Telegram, Discord, Slack, WhatsApp -- are the install's
bots, and every address they deliver to is the owner's. Until each person
links their own chat app (stage 3), an allowlisted sender acts as the owner,
in the owner's space. :func:`chat_app_scope` is the one answer, asked by the
gateway's handler before it reads or builds anything of the message's chat
(the turn's state, a slash command, an approval):

* an install that does not enforce Spaces binds nothing, as before;
* once it does, a chat with no record, or recorded in the owner's space,
  works as the owner in the owner's space -- the thread's own record then
  says the same;
* a chat recorded in another space (a member's web chat a sender switched
  to) is refused (:class:`ChatInAnotherSpace`): a chat app never acts as a
  member, nor in a member's space.

:func:`chat_app_work` is the same rule for a chat-app command that reads the
owner's connected accounts (``/x``): before Spaces the owner's tenant, as it
always was; once enforced the scope the handler bound, and nothing when none
is bound.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from kazma_core.spaces.scope import (
    OWNER_ACCOUNT_ID,
    OWNER_SPACE_ID,
    Scope,
    require_scope,
    spaces_enforced,
)
from kazma_core.spaces.threads import ThreadInAnotherSpace

__all__ = ["ChatInAnotherSpace", "ChatRecordUnreadable", "chat_app_scope", "chat_app_work"]

logger = logging.getLogger(__name__)


class ChatInAnotherSpace(ThreadInAnotherSpace):
    """A chat app's message reached a chat that belongs to another space."""


class ChatRecordUnreadable(ChatInAnotherSpace):
    """Whose a chat app's chat is cannot be read right now: refused like a
    chat of another space, and said for what it is (a retry may work)."""


def chat_app_scope(thread_id: str) -> Scope | None:
    """The scope a chat app's message on *thread_id* works in, or None when
    nothing is bound (an install that does not enforce Spaces).

    Raises :class:`ChatInAnotherSpace` for a chat recorded in another space,
    and when the chat's record cannot be read (never guessed). Blocking: the
    thread record store, on a first asking."""
    if not spaces_enforced():
        return None
    from kazma_core.spaces import threads

    try:
        record = threads.record_of(str(thread_id or ""))
    except (sqlite3.Error, OSError) as exc:
        logger.warning("[spaces] the chat %s's record cannot be read (%s); the message is refused",
                       str(thread_id)[:12], type(exc).__name__)
        raise ChatRecordUnreadable("The chat's record cannot be read right now.") from None
    if record is not None and record.space_id != OWNER_SPACE_ID:
        raise ChatInAnotherSpace(
            "This chat belongs to another space; chat apps work in the owner's space only."
        )
    return Scope(space_id=OWNER_SPACE_ID, asker=OWNER_ACCOUNT_ID)


@contextmanager
def chat_app_work() -> Iterator[None]:
    """Run a chat-app command that reads the owner's connected accounts.

    Before Spaces: in the owner's tenant, as the gateway always did (the
    gateway installs no tenant of its own). Once enforced: in the scope the
    handler bound (:func:`chat_app_scope`); with none bound it raises
    ``NoSpaceBound`` -- never the owner's by default."""
    if spaces_enforced():
        require_scope()
        yield
        return
    from kazma_core.tenant_context import tenant_scope

    with tenant_scope(OWNER_SPACE_ID):
        yield
