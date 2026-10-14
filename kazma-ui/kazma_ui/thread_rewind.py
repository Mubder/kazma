"""Restore and fork a conversation from a Time Travel snapshot: one service.

Time Travel audit (2026-10-13) Stage 1 and 2. A restore was a raw
``aupdate_state``: it wrote nothing to the gate registry, the transcript, the
turn journal or the scratchpad, and did not coordinate with a running turn.
On a thread paused at an approval it left the snapshot's tool calls runnable,
and an Approve on the still-live card ran one of them -- a call the human had
denied earlier (TT-02). A restore during a running turn was overwritten by
the turn (TT-04). The web chat fed the "rewound" turns back to the model on
the next message (TT-03).

Every surface -- the Time Travel page and its HTTP routes (the TUI uses
them), the chat apps' ``/replay <step>`` and ``/fork <step>`` -- calls
:func:`rewind_thread` or :func:`fork_thread`. Under the thread's mutation
lock (``kazma_ui.thread_locks``), a restore:

1. refuses while a turn runs on the thread or another task holds the lock
   (409 ``turn_running``), and while an approval is pending (409
   ``approval_pending``, naming the card: decision D11 -- the human answers
   or the card expires first; nothing is settled behind their back);
2. writes a turn-boundary state only (``time_travel.turn_boundary_payload``:
   nothing pending, nothing runnable, the scratchpad replaced);
3. takes the rewound turns back from memory (``memory.forget.rewind_memory``,
   Memory audit M09), from the transcript (the chat-store rows after the
   restored point), and from Time Travel itself (the steps captured after
   the restored one);
4. journals a ``thread_rewound`` frame so every open tab reloads the chat;
5. writes an audit row (``kazma_core.time_travel_audit``), the refusals too.

What is not rewound, and the pages and docs say so: files the agent wrote,
commands it ran, messages and posts it sent.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "RewindRefused",
    "chat_keys_for",
    "clear_thread_snapshots",
    "fork_thread",
    "rewind_thread",
]


class RewindRefused(Exception):
    """A restore or fork that did not happen, and why (an HTTP status, a code
    the pages word in their language, and the cards it waits on)."""

    def __init__(self, code: str, detail: str, *, status: int = 409, gates: list[dict[str, Any]] | None = None):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status
        self.gates = list(gates or [])

    def body(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": False, "error": self.code, "detail": self.detail}
        if self.gates:
            out["gates"] = self.gates
        return out


def _metric(action: str, outcome: str) -> None:
    from kazma_core.metrics import record_time_travel_rewind

    record_time_travel_rewind(action, outcome)  # a no-op without prometheus; never raises


async def _audit(action: str, outcome: str, **fields: Any) -> None:
    from kazma_core.time_travel_audit import write_audit_row

    await asyncio.to_thread(write_audit_row, action, outcome, **fields)  # never raises


@contextlib.asynccontextmanager
async def _outcome(action: str, audit: dict[str, Any]) -> AsyncIterator[None]:
    """Count and audit a restore or fork that did not complete: a refusal
    under its code, anything else as ``error`` (then raised to the caller,
    whose route answers with ``safe_error``)."""
    try:
        yield
    except RewindRefused as refused:
        _metric(action, refused.code)
        await _audit(action, refused.code, detail={"gates": refused.gates} if refused.gates else None, **audit)
        logger.info("[rewind] %s of %s refused: %s", action, str(audit.get("thread_id"))[:12], refused.code)
        raise
    except Exception:  # noqa: BLE001 - recorded here, raised to the caller
        _metric(action, "error")
        await _audit(action, "error", **audit)
        raise


def _best_effort(what: str, fn: Callable[..., Any], *args: Any, default: Any = None) -> tuple[bool, Any]:
    """Run one side step of a restore or fork that has already happened
    (blocking; called in a thread). A failure is a WARNING naming *what* was
    not done, never a failed restore: the conversation was already rewritten."""
    try:
        return True, fn(*args)
    except Exception:  # noqa: BLE001 - the restore/fork is done; say what is not
        logger.warning("[rewind] %s was NOT done", what, exc_info=True)
        return False, default


async def _tell_tabs(
    thread_id: str, *, session_id: str, snapshot_id: str, turn_index: int, message_count: int,
) -> bool:
    """Journal the ``thread_rewound`` frame (open tabs reload the chat). The
    frame is written here as a literal: the SSE contract gate reads it."""
    try:
        from kazma_ui.delivery import get_turn_broker

        await get_turn_broker().emit(thread_id, {
            "type": "thread_rewound",
            "data": {
                "thread_id": thread_id,
                "session_id": session_id,
                "snapshot_id": snapshot_id,
                "turn_index": turn_index,
                "message_count": message_count,
            },
        })
        return True
    except Exception:  # noqa: BLE001 - tabs reload on their next visit
        logger.warning("[rewind] open tabs of %s were not told of the restore", thread_id, exc_info=True)
        return False


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}


async def _load(recorder: Any, thread_id: str, snapshot_id: str) -> Any:
    """The snapshot, or a refusal (not found, unreadable store, truncated)."""
    from kazma_core.time_travel import SnapshotStoreError

    try:
        record = await asyncio.to_thread(recorder.get, snapshot_id, thread_id=thread_id)
    except SnapshotStoreError as exc:
        raise RewindRefused("store_unavailable", str(exc), status=503) from exc
    if record is None:
        raise RewindRefused("not_found", "No such step of this chat.", status=404)
    if record.truncated:
        raise RewindRefused(
            "snapshot_truncated",
            "This step was too large to keep whole. It can be read, but not restored or forked.",
        )
    return record


async def _open_questions(graph: Any, thread_id: str) -> tuple[list[dict[str, Any]], bool]:
    """The approval cards waiting on *thread_id*, and whether a decision is
    being carried out. The checkpoint (a live interrupt) and the gate
    registry (decision truth, §30) both answer; either one is enough."""
    from kazma_core.safety.hitl_gates import gate_registry_enabled, live_gates_async

    gates: list[dict[str, Any]] = []
    in_flight = False
    try:
        if gate_registry_enabled():
            for row in await live_gates_async(thread_id):
                if row.state == "pending":
                    gates.append({"gate_id": row.gate_id, "tool": row.tool, "kind": row.kind})
                else:
                    in_flight = True
    except (sqlite3.Error, OSError):
        logger.warning("[rewind] gate registry unreadable; the checkpoint alone answers", exc_info=True)
    # The checkpoint is read unguarded: a restore that cannot see whether a
    # question waits does not happen (the error reaches the caller).
    snapshot = await graph.aget_state(_config(thread_id))
    if snapshot is not None and getattr(snapshot, "next", None):
        for task in getattr(snapshot, "tasks", None) or ():
            for intr in getattr(task, "interrupts", None) or ():
                value = getattr(intr, "value", None)
                iid = str(getattr(intr, "id", "") or "")
                if isinstance(value, dict) and value.get("type") == "hitl_approval":
                    if not any(g["gate_id"] == iid for g in gates):
                        gates.append({"gate_id": iid, "tool": str(value.get("tool") or ""),
                                      "kind": str(value.get("kind") or "security")})
                else:
                    in_flight = True  # a /steer! pause: the turn is not over
    return gates, in_flight


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") in (None, "text"):
                parts.append(str(part.get("text") or ""))
            elif isinstance(part, str):
                parts.append(part)
        return "".join(parts)
    return "" if content is None else str(content)


def _norm(text: str) -> str:
    return " ".join(str(text or "").split())


def _transcript_cut(rows: list[Any], restored: list[Any]) -> int:
    """How many transcript rows a restore to *restored* (the checkpoint's
    messages) keeps.

    The restored state's questions are found in the transcript in order (an
    instant command, ``/yolo`` or ``/replay``, sits in the transcript only).
    When the restored state answered its last question, the transcript keeps
    that answer and stops before the next question; when it did not (a step
    in the middle of a turn), it keeps the question and drops its answer.
    A question the transcript does not hold as written (an attachment's
    rewrite) falls back to counting questions.
    """
    def role(m: Any) -> str:
        return str((m.get("role") if isinstance(m, dict) else "") or "")

    asked = [_norm(_text(m.get("content"))) for m in restored if role(m) == "user"]
    if not asked:
        return 0
    last_user = max(i for i, m in enumerate(restored) if role(m) == "user")
    answered = any(role(m) == "assistant" and _text(m.get("content")).strip()
                   for m in restored[last_user + 1:])
    users = [i for i, m in enumerate(rows) if role(m) == "user"]
    pos, j = -1, 0
    for i in users:
        if j < len(asked) and _norm(_text(rows[i].get("content"))) == asked[j]:
            pos, j = i, j + 1
    if j < len(asked):
        # Count instead: the len(asked)-th question of the transcript.
        if len(users) < len(asked):
            return len(rows)
        pos = users[len(asked) - 1]
    if not answered:
        return pos + 1
    later = [i for i in users if i > pos]
    return later[0] if later else len(rows)


def _rewind_transcript(thread_id: str, restored: list[Any]) -> tuple[str, int]:
    """Cut the chat-store transcript of *thread_id* to the restored point.
    Returns ``(session_id, rows removed)``; ``("", 0)`` with no chat row."""
    from kazma_ui.session_manager import get_session_manager

    store = get_session_manager()
    session = store.get_by_thread_id(thread_id)
    if session is None:
        return "", 0
    removed = 0
    with store.transact(session.session_id) as sess:
        rows = list(sess.messages or [])
        cut = _transcript_cut(rows, restored)
        removed = len(rows) - cut
        if removed:
            sess.messages = rows[:cut]
    return session.session_id, removed


async def rewind_thread(
    graph: Any,
    thread_id: str,
    snapshot_id: str,
    *,
    actor: str,
    surface: str,
    recorder: Any = None,
) -> dict[str, Any]:
    """Restore *thread_id* to its snapshot *snapshot_id* (see the module
    docstring). Raises :class:`RewindRefused`; every outcome is audited."""
    from kazma_core.time_travel import apply_snapshot_to_thread, process_recorder
    from kazma_ui.active_turns import is_turn_running
    from kazma_ui.thread_locks import bump_rewind_generation, thread_busy, thread_mutation

    rec = recorder if recorder is not None else await asyncio.to_thread(process_recorder)
    audit = {"actor": actor, "surface": surface, "thread_id": thread_id, "snapshot_id": snapshot_id}
    async with _outcome("restore", audit):
        record = await _load(rec, thread_id, snapshot_id)
        if thread_busy(thread_id):
            raise RewindRefused("turn_running", "A turn is running in this chat. Restore it when it ends.")
        async with thread_mutation(thread_id):
            if is_turn_running(thread_id):
                raise RewindRefused("turn_running", "A turn is running in this chat. Restore it when it ends.")
            gates, in_flight = await _open_questions(graph, thread_id)
            if gates:
                tools = ", ".join(sorted({g["tool"] for g in gates if g["tool"]})) or "a tool"
                raise RewindRefused(
                    "approval_pending",
                    f"An approval is waiting in this chat ({tools}). Approve or deny it first.",
                    gates=gates,
                )
            if in_flight:
                raise RewindRefused("turn_running", "A decision is being carried out in this chat.")
            before = await graph.aget_state(_config(thread_id))
            prior = str(((getattr(before, "config", None) or {}).get("configurable") or {})
                        .get("checkpoint_id") or "")
            payload = await apply_snapshot_to_thread(graph, record.get_state(), thread_id)
            bump_rewind_generation(thread_id)
            # The conversation is rewritten: what follows completes the
            # rewind, and a step that fails is said, never a failed restore.
            restored = list(payload.get("messages") or [])
            _ok, (session_id, removed) = await asyncio.to_thread(
                _best_effort, f"cutting the transcript of {thread_id}", _rewind_transcript, thread_id, restored,
                default=("", -1),
            )
            _ok, dropped = await asyncio.to_thread(
                _best_effort, f"deleting the later steps of {thread_id}", rec.forget_after, thread_id, record.seq,
                default=-1,
            )
            await _tell_tabs(thread_id, session_id=session_id, snapshot_id=snapshot_id,
                             turn_index=record.turn_index, message_count=len(restored))
    out = {
        "ok": True,
        "thread_id": thread_id,
        "snapshot_id": snapshot_id,
        "turn_index": record.turn_index,
        "iteration": record.iteration,
        "message_count": len(restored),
        "transcript_rows_removed": removed,
        "later_steps_removed": dropped,
        "prior_checkpoint_id": prior,
    }
    _metric("restore", "ok")
    await _audit("restore", "ok", prior_checkpoint_id=prior,
                 detail={"turn_index": record.turn_index, "transcript_rows_removed": removed,
                         "later_steps_removed": dropped}, **audit)
    logger.info("[rewind] %s restored %s to step %s (turn %d): %d transcript row(s), %d later step(s) removed",
                surface, thread_id[:12], snapshot_id[:12], record.turn_index, removed, dropped)
    return out


def _projected_transcript(messages: list[Any]) -> list[dict[str, Any]]:
    """The rows a fork's chat shows: the questions and the answers.

    The web fork wrote the snapshot's raw graph messages into the chat row --
    the system prompt, "About me", recall blocks, tool-call shells and tool
    results (file contents, keys) -- and the gateway projected its own way
    (TT-07). One projection now.
    """
    out: list[dict[str, Any]] = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "")
        if role not in ("user", "assistant"):
            continue
        text = _text(m.get("content")).strip()
        if not text:
            continue  # a tool-call shell
        out.append({"role": role, "content": text})
    return out


def _save_fork_chat(session_id: str, title: str, messages: list[dict[str, Any]], tenant_id: str | None = None) -> None:
    """The fork's chat row, so it shows in the sidebar (blocking: a chat-store write)."""
    from kazma_ui.session_manager import ChatSession, get_session_manager

    get_session_manager().put(ChatSession(
        session_id=session_id, thread_id=session_id, title=title, messages=messages, tenant_id=tenant_id,
    ))


def _fork_title(thread_id: str, turn_index: int, lang: str) -> str:
    """"Fork of <the chat's title> (turn N)", in the page's language."""
    from kazma_ui.i18n import t

    from kazma_ui.session_manager import get_session_manager

    _ok, session = _best_effort(f"reading the title of {thread_id}",
                                lambda: get_session_manager().get_by_thread_id(thread_id))
    source = str(getattr(session, "title", "") or "")
    if not source:
        source = t("replay.fork_untitled", lang)
    return t("replay.fork_title", lang, title=source[:80], turn=turn_index)


async def fork_thread(
    graph: Any,
    thread_id: str,
    snapshot_id: str,
    *,
    actor: str,
    surface: str,
    recorder: Any = None,
    new_thread_id: str | None = None,
    owner: str = "",
    lang: str | None = None,
) -> dict[str, Any]:
    """Branch *thread_id*'s snapshot into a new conversation.

    The original is not touched. The new thread gets the snapshot as a
    turn-boundary state; its chat row gets the projected transcript and a
    catalog title. *owner* (a chat app's sender) is recorded before anything
    is written: a fork nobody owns refuses every later approval on the chat
    apps, and the old code recorded it on the event loop and swallowed a
    failure at DEBUG.
    """
    from kazma_core.time_travel import apply_snapshot_to_thread, process_recorder
    from kazma_ui.thread_locks import thread_busy, thread_mutation

    rec = recorder if recorder is not None else await asyncio.to_thread(process_recorder)
    new_id = new_thread_id or f"fork-{uuid.uuid4().hex[:12]}"
    audit = {"actor": actor, "surface": surface, "thread_id": thread_id, "snapshot_id": snapshot_id,
             "target_thread_id": new_id}
    async with _outcome("fork", audit):
        record = await _load(rec, thread_id, snapshot_id)
        if thread_busy(thread_id):
            raise RewindRefused("turn_running", "A turn is running in this chat. Fork it when it ends.")
        if owner:
            from kazma_core.sessions.directory import record_thread_owner

            if not await asyncio.to_thread(record_thread_owner, new_id, owner):
                raise RewindRefused("owner_unavailable", "The new chat's owner could not be recorded.",
                                    status=503)
        async with thread_mutation(new_id):
            payload = await apply_snapshot_to_thread(graph, record.get_state(), new_id)
    if lang is None:
        from kazma_ui.i18n import current_language

        lang = current_language()
    messages = _projected_transcript(list(payload.get("messages") or []))
    title = await asyncio.to_thread(_fork_title, thread_id, record.turn_index, lang)
    chat_saved, _ = await asyncio.to_thread(
        _best_effort, f"saving the chat of fork {new_id} (it will not show in the chat list)",
        _save_fork_chat, new_id, title, messages,
    )
    _metric("fork", "ok")
    await _audit("fork", "ok", detail={"turn_index": record.turn_index, "chat_saved": chat_saved}, **audit)
    logger.info("[rewind] %s forked %s step %s into %s", surface, thread_id[:12], snapshot_id[:12], new_id)
    return {
        "ok": True,
        "new_thread_id": new_id,
        "session_id": new_id,
        "title": title,
        "turn_index": record.turn_index,
        "message_count": len(messages),
        "chat_saved": chat_saved,
    }


async def clear_thread_snapshots(
    thread_id: str, *, actor: str, surface: str, recorder: Any = None,
) -> int:
    """Delete every saved step of a conversation, under each of its keys
    (``/replay clear``, the page's Delete). Raises ``SnapshotStoreError``."""
    from kazma_core.time_travel import forget_thread_snapshots, process_recorder

    rec = recorder if recorder is not None else await asyncio.to_thread(process_recorder)
    keys = await asyncio.to_thread(chat_keys_for, thread_id)
    n = await asyncio.to_thread(forget_thread_snapshots, keys, recorder=rec)
    await _audit("clear", "ok", actor=actor, surface=surface, thread_id=thread_id, detail={"deleted": n})
    return n


def chat_keys_for(thread_id: str) -> list[str]:
    """Every key the conversation is stored under (session id and thread id)."""
    from kazma_core.memory.forget import chat_keys

    _ok, keys = _best_effort(f"finding every key of {thread_id}", chat_keys, thread_id, default=None)
    return list(keys or [thread_id])
