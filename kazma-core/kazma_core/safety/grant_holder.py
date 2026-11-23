"""Whose standing approval it is (Spaces S2-06).

A standing approval -- "Allow tool" (``safety.hitl_grants``), "Approve for
task" (``safety.task_grants``), a YOLO window (``safety.yolo``) -- skips the
approval cards of a chat until it ends. Once Spaces is enforced it belongs
to the person and the chat: a grant one member of a shared chat made never
skips another member's card. Each store keys its rows by :func:`holder`:

* while the install does not enforce Spaces, the chat's thread id, as
  before (nothing moves, and nothing written before reads differently);
* once it does, the thread id and the person (``<thread>~<account>``): the
  person named, else the one the work is bound to -- the turn's asker in the
  chat graph, the task's in a swarm worker. Work with no person bound has no
  holder: nothing is granted for it, and nothing it asks is skipped.

An account id holds no ``~`` and no ``.`` (``spaces.scope.valid_account_id``),
so a holder is one key segment, and a prefix of one person's rows never
matches another's. No I/O.
"""

from __future__ import annotations

__all__ = ["SEPARATOR", "holder", "thread_of"]

#: Between the chat's thread id and the person, once Spaces is enforced.
SEPARATOR = "~"


def holder(thread_id: str | None, person: str | None = None) -> str:
    """The key a standing approval of *thread_id* is kept under, for
    *person* (the person bound now when None); ``""`` for none (no chat, or
    Spaces enforced and nobody bound)."""
    from kazma_core.spaces.scope import bound_asker, spaces_enforced

    thread = str(thread_id or "")
    if not thread or not spaces_enforced():
        return thread
    who = str(person or bound_asker() or "")
    return f"{thread}{SEPARATOR}{who}" if who else ""


def thread_of(key_part: str) -> str:
    """The thread id a holder names (the part before :data:`SEPARATOR`)."""
    return str(key_part or "").split(SEPARATOR, 1)[0]
