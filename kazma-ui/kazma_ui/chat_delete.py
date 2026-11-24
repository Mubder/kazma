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
Safe to run twice.
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
        if thread_id:  # a second run no longer holds the row that named it
            chats.mark_deleted(session_id, thread_id)
        return True
    chats.mark_deleted(session_id, thread_id)
    return chats.delete_if(session_id, unchanged)


def end_chat(session_id: str, thread_id: str, *, reason: str = "deleted") -> None:
    """Nothing prepared from the chat's state starts, and the sockets and
    streams open on it in the bound space end, told *reason*."""
    from kazma_core.spaces.scope import require_space_id

    from kazma_ui.live_connections import chat_gone
    from kazma_ui.thread_locks import bump_rewind_generation

    if thread_id:
        bump_rewind_generation(thread_id)
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
        if not await asyncio.to_thread(_remove_row, session_id, thread_id, unchanged):
            return None
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


async def remove_chat_remains(session_id: str, thread_id: str, checkpoints: Any) -> bool:
    """Remove what the deleted chat *session_id* (thread *thread_id*) leaves:
    its checkpoints (in *checkpoints*, the checkpoint manager), its saved
    steps under each of its keys, and its thread's record. True when nothing
    is left; a part that could not be removed is a WARNING, and the record
    stays with it."""
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
    if thread_id and not leftovers:
        # The thread's space record goes last, and only when nothing of the
        # chat is left: while a copy remains, the record is what keeps it in
        # its space.
        await asyncio.to_thread(_forget_thread_record, thread_id)
    return not leftovers
