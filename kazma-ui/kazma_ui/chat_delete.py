"""Deleting a chat: one function, for the person's delete
(``DELETE /api/chat/sessions/{id}``) and for a move to another space, which
is a fork there and this delete here (Spaces S2-12, ``kazma_ui.chat_move``).

:func:`delete_chat` removes the row and marks the chat deleted (nothing
written late brings it back, and its thread runs no turn again:
``session_manager``, ``kazma_core.spaces.threads.end_thread``), bumps the
thread's rewind generation (a turn prepared from its state does not start),
ends the sockets and streams open on it, and removes what is left beside the
row: the thread's checkpoints, its Time Travel steps and, last, its space
record. What Kazma learned is memory and is kept (AGENTS.md section 15I).
Safe to run twice. A thread another chat of the space is on (a chat app's
archived twin, :meth:`SessionManager.another_chat_on`) stays that chat's: only
the row deleted goes -- and, when the chat that showed is deleted and only
archived ones are left, the thread's state with it (what Kazma answers from is
the conversation the person deleted), never ending the thread.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
from collections.abc import Callable
from typing import Any

__all__ = ["delete_chat", "end_chat", "remove_chat_remains"]

logger = logging.getLogger(__name__)


def _remove_row(session_id: str, thread_id: str, unchanged: Callable[[Any], bool] | None) -> bool:
    """The chat's row out of the store, and the chat marked deleted. With
    *unchanged* (a move's), only while its row is still that. Blocking."""
    from kazma_ui.session_manager import get_session_manager

    chats = get_session_manager()
    if unchanged is None:
        chats.delete(session_id)
        if thread_id and not chats.another_chat_on(thread_id, session_id):
            chats.mark_deleted(session_id, thread_id)  # a second run no longer holds the row that named it
        return True
    chats.mark_deleted(session_id, thread_id)
    return chats.delete_if(session_id, unchanged)


def _shows(session_id: str) -> bool:
    """Whether the chat is there and not archived. Raises when the store
    cannot be read. Blocking."""
    from kazma_ui.session_manager import get_session_manager

    chats = get_session_manager()
    row = chats.get(session_id) or chats.held(session_id)
    return row is not None and not row.archived


def _is_another_chats(thread_id: str, session_id: str, *, showing: bool = False) -> bool:
    """Whether the thread is another chat's too (with *showing*: one that is
    not archived). Blocking."""
    from kazma_ui.session_manager import get_session_manager

    return get_session_manager().another_chat_on(thread_id, session_id, showing=showing)


def _bump_generation(thread_id: str) -> None:
    """No turn prepared from the thread's state starts."""
    from kazma_ui.thread_locks import bump_rewind_generation

    bump_rewind_generation(thread_id)


def end_chat(session_id: str, thread_id: str, *, reason: str = "deleted") -> None:
    """Nothing prepared from the chat's state starts, and the sockets and
    streams open on it in the bound space end, told *reason*."""
    from kazma_core.spaces.scope import require_space_id

    from kazma_ui.live_connections import chat_gone

    if thread_id:
        _bump_generation(thread_id)
    chat_gone(session_id=session_id, thread_id=thread_id, space=require_space_id(), reason=reason)


async def delete_chat(
    session_id: str, thread_id: str, checkpoints: Any, *, scope: tuple[str, str] | None = None,
    reason: str = "deleted", unchanged: Callable[[Any], bool] | None = None,
) -> bool | None:
    """Delete the chat *session_id* (thread *thread_id*) of the bound space,
    or of *scope* (space, account) when a journal names it. *checkpoints* is
    the checkpoint manager, *reason* what its open sockets are told.

    True: nothing of it is left. False: a part could not be removed (a
    WARNING names it; run it again). None: *unchanged* says its row is not
    what a move copied, and nothing was removed."""
    from kazma_core.spaces import threads
    from kazma_core.spaces.scope import bind_scope

    with bind_scope(*scope) if scope else contextlib.nullcontext():
        shown = scope is None and await asyncio.to_thread(_shows, session_id)
        if not await asyncio.to_thread(_remove_row, session_id, thread_id, unchanged):
            return None
        if scope is None and await asyncio.to_thread(_is_another_chats, thread_id, session_id):
            # A person's delete of one of two chats on one thread.
            end_chat(session_id, "", reason=reason)
            if shown and not await asyncio.to_thread(_is_another_chats, thread_id, session_id, showing=True):
                # The archived one's rows stay; what the thread holds was the
                # conversation deleted. Its next turn starts from nothing.
                _bump_generation(thread_id)
                return await remove_chat_remains(session_id, thread_id, checkpoints, thread_ends=False)
            return await remove_chat_remains(session_id if session_id != thread_id else "", "", checkpoints)
        end_chat(session_id, thread_id, reason=reason)
        # The record goes last, so a thread recorded elsewhere, or nowhere,
        # has nothing left in the space the journal names.
        if scope and await asyncio.to_thread(threads.thread_home, thread_id) != scope[0]:
            return True
        return await remove_chat_remains(session_id, thread_id, checkpoints)


def _forget_thread_record(thread_id: str) -> None:
    """Remove a deleted chat's thread record. Blocking; never raises."""
    from kazma_core.spaces.threads import forget_thread

    try:
        forget_thread(thread_id)
    except (sqlite3.Error, OSError):
        logger.warning("[chat] the space record of thread %s was not removed", thread_id, exc_info=True)


async def remove_chat_remains(session_id: str, thread_id: str, checkpoints: Any, *,
                              thread_ends: bool = True) -> bool:
    """Remove what the deleted chat *session_id* (thread *thread_id*) leaves:
    its checkpoints (in *checkpoints*, the checkpoint manager), its saved
    steps under each of its keys, and its thread's record. True when nothing
    is left; a part that could not be removed is a WARNING, and the record
    stays with it -- as it does where the thread goes on (*thread_ends* False)."""
    leftovers = False
    if thread_id:
        try:
            from kazma_ui.thread_locks import thread_mutation

            if checkpoints and hasattr(checkpoints, "adelete_thread"):
                # The thread's checkpoint-mutation lock (TT-04).
                async with thread_mutation(thread_id):
                    await checkpoints.adelete_thread(thread_id)
            else:
                # No checkpoint store to ask: whatever it holds of
                # this chat is still there.
                leftovers = True
        except Exception as exc:  # noqa: BLE001 - whatever the store raised, the rest is still removed
            leftovers = True
            logger.warning("[chat] %s deleted, but its checkpoints were NOT (%s)",
                           session_id, type(exc).__name__, exc_info=True)
    # Its Time Travel steps go with it, under each of its keys (TT-05): they
    # are copies of the chat, like the checkpoints above. What Kazma learned
    # is memory, kept unless the dialog's "also forget" was ticked (the page
    # asks the memory route first).
    from kazma_core.time_travel import SnapshotStoreError, forget_thread_snapshots

    try:
        await asyncio.to_thread(forget_thread_snapshots, [k for k in (session_id, thread_id) if k])
    except (SnapshotStoreError, sqlite3.Error, OSError):
        leftovers = True
        logger.warning("[chat] %s deleted, but its saved steps were NOT", session_id, exc_info=True)
    if thread_id and thread_ends and not leftovers:
        # The thread's space record goes last, and only when nothing of the
        # chat is left: while a copy remains, the record is what keeps it in
        # its space.
        await asyncio.to_thread(_forget_thread_record, thread_id)
    return not leftovers
