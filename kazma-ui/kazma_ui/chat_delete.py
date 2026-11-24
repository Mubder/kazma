"""What a deleted chat leaves beside its row: one place removes it.

Deleting a chat (``DELETE /api/chat/sessions/{id}``) and moving one to
another space, which is a fork there and this delete here (Spaces S2-12,
``kazma_ui.chat_move``), remove the same things: the thread's checkpoints,
its Time Travel steps and, last, its space record. What Kazma learned is
memory and is kept (AGENTS.md section 15I).
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import Any

__all__ = ["remove_chat_remains"]

logger = logging.getLogger(__name__)


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
