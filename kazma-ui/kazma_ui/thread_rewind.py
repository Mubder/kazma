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
import itertools
import logging
import sqlite3
import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

from kazma_core.memory.chat_history import epoch_of

logger = logging.getLogger(__name__)

__all__ = [
    "RewindRefused",
    "carry_conversation",
    "chat_keys_for",
    "clear_thread_snapshots",
    "fork_thread",
    "projected_rows",
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
        # Its text carries SQLite's: logged, never sent (the pages word the code).
        logger.warning("[time-travel] snapshot store unavailable: %s", exc)
        raise RewindRefused("store_unavailable", "The Time Travel store could not be read.",
                            status=503) from exc
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


def _role(message: Any) -> str:
    return str((message.get("role") if isinstance(message, dict) else "") or "")


def _said(message: Any) -> str:
    return _norm(_text(message.get("content"))) if isinstance(message, dict) else ""


def _transcript_cut(rows: list[Any], restored: list[Any], at: float | None = None) -> int | None:
    """How many transcript rows a restore to *restored* (the checkpoint's
    messages) keeps, or None where its place in the transcript is not known:
    nothing is cut then.

    The place is the restored state's last question, found from the end (a
    state that was trimmed or compacted holds the newest questions only)
    among the questions asked by *at*, the step's own time. Rows with no time
    are told apart by the answer the state gave, then by the state's other
    questions. One the transcript does not hold as written (an attachment's
    rewrite, a steer) is the newest asked by then. An instant command
    (``/yolo``, ``/replay``) sits in the transcript only.

    When the restored state answered its last question, the transcript keeps
    that answer and stops before the next question; when it did not (a step
    in the middle of a turn), it keeps the question and drops its answer.
    """
    from kazma_core.agent.plan_fence import is_graph_nudge

    if not restored:
        return 0
    users = [i for i, m in enumerate(rows) if _role(m) == "user"]
    if not users:
        return len(rows)
    times = [epoch_of(rows[i].get("ts")) for i in users]
    late = next((k for k, t in enumerate(times) if at is not None and t is not None and t > at), len(users))
    asked_by_then = users[:late]
    timed = at is not None and None not in times
    asked = [i for i, m in enumerate(restored) if _role(m) == "user" and not is_graph_nudge(_text(m.get("content")))]
    last = max((i for i, m in enumerate(restored) if _role(m) == "user"), default=-1)
    answers = [_said(m) for m in restored[last + 1:] if _role(m) == "assistant" and _said(m)]

    pos = _place(rows, restored, asked, asked_by_then, answers, timed=timed, users=users)
    if pos is None:
        return None
    if not answers:
        return pos + 1
    later = [i for i in users if i > pos and not (timed and i in asked_by_then)]
    return later[0] if later else len(rows)


def _place(rows: list[Any], restored: list[Any], asked: list[int], candidates: list[int], answers: list[str],
           *, timed: bool, users: list[int]) -> int | None:
    """The row of the restored state's last question, among *candidates*."""
    same = [i for i in candidates if asked and _said(rows[i]) == _said(restored[asked[-1]])]
    if timed:  # the newest asked by then, as written or not
        return (same or candidates or [None])[-1]
    if len(same) == 1:
        return same[0]
    # The question whose answer is the one the state gave.
    if answers:
        answered = [i for i in (same or candidates)
                    if any(_said(m) in answers for m in itertools.takewhile(
                        lambda m: _role(m) != "user", rows[i + 1:]) if _role(m) == "assistant")]
        if len(answered) == 1 or (answered and not same):
            return answered[-1]
    # The state's questions are the transcript's first ones, in order.
    found: list[int] = []
    for i in candidates:
        if len(found) < len(asked) and _said(rows[i]) == _said(restored[asked[len(found)]]):
            found.append(i)
    if asked and len(found) == len(asked) and found[0] == users[0]:
        return found[-1]
    if same:
        return same[-1]
    # Counted, when the state begins where the transcript does.
    if len(asked) > 1 and found[:1] == users[:1] and len(asked) <= len(candidates):
        return candidates[len(asked) - 1]
    return None


def _rewind_transcript(thread_id: str, restored: list[Any], at: float | None = None) -> tuple[str, int | None]:
    """Cut the chat-store transcript of *thread_id* to the restored point.
    Returns ``(session_id, rows removed)``; None removed: the point is not
    known and every row was kept; ``("", 0)`` with no chat row."""
    from kazma_ui.session_manager import get_session_manager

    store = get_session_manager()
    session = store.get_by_thread_id(thread_id)
    if session is None:
        return "", 0
    with store.transact(session.session_id) as sess:
        rows = list(sess.messages or [])
        cut = _transcript_cut(rows, restored, at)
        if cut is not None and cut < len(rows):
            sess.messages = rows[:cut]
    return session.session_id, None if cut is None else len(rows) - cut


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
        await asyncio.to_thread(_in_this_space, thread_id)
        await asyncio.to_thread(_theirs_to_change, thread_id)
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
                epoch_of(record.timestamp), default=("", -1),
            )
            if removed is None:
                logger.warning("[rewind] %s: the step's place in its transcript is not known; its rows were kept",
                               thread_id[:12])
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
        "transcript_rows_removed": removed or 0,
        # Its place is not known (or the cut failed): the chat still shows the
        # later turns, and every surface says so.
        "transcript_kept": removed is None or removed < 0,
        "later_steps_removed": dropped,
        "prior_checkpoint_id": prior,
    }
    _metric("restore", "ok")
    await _audit("restore", "ok", prior_checkpoint_id=prior,
                 detail={"turn_index": record.turn_index, "transcript_rows_removed": removed or 0,
                         "transcript_kept": out["transcript_kept"],
                         "later_steps_removed": dropped}, **audit)
    logger.info("[rewind] %s restored %s to step %s (turn %d): %d transcript row(s), %d later step(s) removed",
                surface, thread_id[:12], snapshot_id[:12], record.turn_index, removed or 0, dropped)
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


#: What of a chat row's message a moved chat keeps beside its text (Spaces
#: S2-12): who said it, when, and the model that answered.
_CARRIED_FIELDS = ("ts", "author", "steer", "model", "run_model", "closed_at")


def projected_rows(messages: list[Any]) -> list[dict[str, Any]]:
    """A chat row's questions and answers alone (:func:`_projected_transcript`,
    with :data:`_CARRIED_FIELDS`): no memory parts, no tool results, no
    reasoning, no approval cards."""
    out: list[dict[str, Any]] = []
    for m in messages or []:
        for row in _projected_transcript([m]):
            out.append({**row, **{k: m[k] for k in _CARRIED_FIELDS if m.get(k)}})
    return out


def _conversation_of(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The model's messages of a chat row's questions and answers: each
    question as it is shown, and one assistant message a turn, its final
    answer (a turn that paused for an approval may hold more)."""
    out: list[dict[str, Any]] = []
    for row in rows:
        message = {"role": row["role"], "content": row["content"]}
        if out and message["role"] == "assistant" and out[-1]["role"] == "assistant":
            out[-1] = message
        else:
            out.append(message)
    return out


async def carry_conversation(
    graph: Any, rows: list[dict[str, Any]], new_thread_id: str, *, space: str, by: str,
) -> int:
    """The state of a moved chat's copy (Spaces S2-12): *rows*, its row's
    questions and answers (:func:`projected_rows`), as the new thread's
    turn-boundary state in *space*, for *by*. From the row, so it is what the
    chat shows; nothing of the source's state goes -- no turn notes, no
    "About me", no summary, nothing recalled, no tool results, documents or
    images, drafts or plans, and nothing pending -- and memory is not told of
    a rewind: the new thread was never anything else. Returns how many
    messages."""
    from kazma_core.spaces.scope import bind_scope
    from kazma_core.time_travel import write_turn_boundary

    from kazma_ui.thread_locks import thread_mutation

    messages = _conversation_of(rows)
    if not messages:
        return 0
    with bind_scope(space, by):
        async with thread_mutation(new_thread_id):
            await write_turn_boundary(graph, {"messages": messages, "tenant_id": space}, new_thread_id)
    return len(messages)


def _in_this_space(thread_id: str, new_thread_id: str | None = None) -> None:
    """Refuse a restore or a fork of a chat that belongs to another space, as
    a chat that is not there; and record a fork's new thread in this space
    before anything is written under it (Spaces S1-08). Blocking: the thread
    record store."""
    from kazma_core.spaces import threads
    from kazma_core.spaces.scope import NoSpaceBound

    try:
        refused = threads.thread_refusal(thread_id)
        if not refused and new_thread_id:
            # A fork of a chat that was copied here from another space (Spaces
            # S2-12) holds what was said there: this space's memory starts at
            # the fork, as it starts at the copy in the chat itself.
            source = threads.record_of(thread_id)
            copied = source is not None and source.copied_at is not None
            threads.record_new_thread(new_thread_id, copied_at=time.time() if copied else None)
    except (sqlite3.Error, OSError) as exc:
        # The record store is where a chat's space is kept: a restore or a
        # fork that cannot ask it, or cannot record the new chat, does not
        # happen. Nothing was written yet.
        logger.warning("[rewind] the space record of chat %s could not be read or written (%s); "
                       "nothing was changed", str(thread_id)[:12], type(exc).__name__, exc_info=True)
        raise RewindRefused("thread_record_unavailable",
                            "Where this chat lives could not be read or recorded.",
                            status=503) from exc
    except NoSpaceBound as exc:
        # Spaces is enforced and this request named no space (a chat app's
        # /fork before its turns are bound to one): the new chat would
        # belong to nobody.
        logger.warning("[rewind] a fork of chat %s was refused: the request is bound to no space",
                       str(thread_id)[:12])
        raise RewindRefused("no_space", "This request is not tied to a space, so nothing was forked.",
                            status=403) from exc
    if refused:
        raise RewindRefused("not_found", "That chat was not found.", status=404)


def _theirs_to_change(thread_id: str) -> None:
    """Refuse a restore or a fork of a chat the person asking may not change,
    once Spaces is enforced: the chat's opener or an owner of its space, the
    rule a chat app's ``/replay`` asks (``hitl_answerers.turn_control_refusal``,
    action ``rewind``; S2-05, the stage 3 review). The space check
    (:func:`_in_this_space`) let any member of a shared space restore or
    fork another member's chat. A chat whose opener is not recorded (from
    before Spaces) refuses nobody; a running turn is refused on its own
    (``turn_running``). Work with no person bound is left to
    :func:`_in_this_space` (it acts for the thread; a fork bound to no space
    is ``no_space`` there). Blocking: the thread record, the identity store."""
    from kazma_core.spaces.scope import bound_asker, spaces_enforced

    if not spaces_enforced() or not bound_asker():
        return
    from kazma_core.spaces.identity import IdentityUnavailable
    from kazma_ui.hitl_answerers import turn_control_refusal

    try:
        refused = turn_control_refusal(thread_id, "rewind", active=False)
    except (sqlite3.Error, OSError, IdentityUnavailable) as exc:
        logger.warning("[rewind] whose chat %s is could not be read (%s); nothing was changed",
                       str(thread_id)[:12], type(exc).__name__, exc_info=True)
        raise RewindRefused("identity_unavailable",
                            "Whose chat this is could not be read, so nothing was changed.",
                            status=503) from exc
    if refused is not None:
        code, name = refused
        logger.info("[rewind] chat %s refused: %s", str(thread_id)[:12], code)
        raise RewindRefused(code, f"Only {name}, who started this chat, or an owner of this space "
                                  "can restore it, fork it or delete its saved steps.", status=403)


def _save_fork_chat(session_id: str, title: str, messages: list[dict[str, Any]], tenant_id: str | None = None) -> None:
    """The fork's chat row, so it shows in the sidebar (blocking: a chat-store write)."""
    from kazma_ui.session_manager import ChatSession, get_session_manager

    get_session_manager().put(ChatSession(
        session_id=session_id, thread_id=session_id, title=title, messages=messages, tenant_id=tenant_id,
    ))


def _fork_rows(thread_id: str, state: list[Any], at: float | None = None) -> list[dict[str, Any]]:
    """The fork's chat rows: the step's questions and answers, each question
    naming who asked it in the source chat -- of the rows up to the step (of
    all, where its place is not known), the later ones are not the fork's.
    They named nobody, so a fork in a shared space could not be taken out
    (``others_wrote``) and its turns were the opener's. One whose asker is in
    doubt still names nobody (``reply_sink.carry_authors``). Blocking: the
    chat store."""
    from kazma_ui.reply_sink import carry_authors
    from kazma_ui.session_manager import get_session_manager

    rows = _projected_transcript(state)
    _ok, session = _best_effort(f"reading who asked what in {thread_id} (its fork names nobody)",
                                lambda: get_session_manager().get_by_thread_id(thread_id))
    said = list(getattr(session, "messages", None) or [])
    # By the step's own time alone: a place read from the texts is a guess.
    timed = at is not None and all(epoch_of(m.get("ts")) is not None for m in said if _role(m) == "user")
    cut = _transcript_cut(said, state, at) if timed else None
    return carry_authors(said if cut is None else said[:cut], rows, one_person=_one_person(said), among=said)


def _one_person(rows: list[Any]) -> bool:
    """Every question of the chat names the same person."""
    return len({str(m.get("author") or "") for m in rows
                if isinstance(m, dict) and m.get("role") == "user"}) == 1


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
        await asyncio.to_thread(_in_this_space, thread_id)
        await asyncio.to_thread(_theirs_to_change, thread_id)
        record = await _load(rec, thread_id, snapshot_id)
        if thread_busy(thread_id):
            raise RewindRefused("turn_running", "A turn is running in this chat. Fork it when it ends.")
        await asyncio.to_thread(_in_this_space, thread_id, new_id)
        if owner:
            from kazma_core.sessions.directory import record_thread_owner

            if not await asyncio.to_thread(record_thread_owner, new_id, owner):
                raise RewindRefused("owner_unavailable", "The new chat's owner could not be recorded.",
                                    status=503)
        # Before anything of the fork is written: its transcript holds the
        # source's turns, and a chat kept out of memory, or a turn the user
        # forgot, stays out in the fork too (FA-246).
        from kazma_core.memory.forget import inherit_ledger

        try:
            inherited = await asyncio.to_thread(inherit_ledger, thread_id, new_id)
        except (sqlite3.Error, OSError) as exc:
            logger.warning("[rewind] fork of %s refused: its memory ledger could not be copied (%s)",
                           thread_id[:12], type(exc).__name__, exc_info=True)
            raise RewindRefused("memory_ledger_unavailable",
                                "What this chat keeps out of memory could not be copied to the fork.",
                                status=503) from exc
        async with thread_mutation(new_id):
            payload = await apply_snapshot_to_thread(graph, record.get_state(), new_id)
    if lang is None:
        from kazma_ui.i18n import current_language

        lang = current_language()
    messages = await asyncio.to_thread(_fork_rows, thread_id, list(payload.get("messages") or []),
                                        epoch_of(record.timestamp))
    title = await asyncio.to_thread(_fork_title, thread_id, record.turn_index, lang)
    chat_saved, _ = await asyncio.to_thread(
        _best_effort, f"saving the chat of fork {new_id} (it will not show in the chat list)",
        _save_fork_chat, new_id, title, messages,
    )
    _metric("fork", "ok")
    await _audit("fork", "ok", detail={"turn_index": record.turn_index, "chat_saved": chat_saved,
                                       "memory_ledger": inherited}, **audit)
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
    (``/replay clear``, the page's Delete). Raises ``SnapshotStoreError``,
    and :class:`RewindRefused` for a chat the person asking may not change
    (:func:`_theirs_to_change`, once Spaces is enforced)."""
    from kazma_core.time_travel import forget_thread_snapshots, process_recorder

    await asyncio.to_thread(_theirs_to_change, thread_id)
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
