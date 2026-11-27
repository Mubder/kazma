"""The three Lows of the review of the delete-chat fix (ec538d779).

L1 -- an Approve that won the race with a chat's going away ran its call on
the deleted chat, and a claimed gate the end took was recorded ``approve``
though its call never ran. Now a drive that carries out a recorded decision
starts only through ``chat_runs_end.begin_drive``, in the same step as it is
registered, and an end takes each gate's token before ending it: one of the
two wins, deterministically. The record keeps the person's decision and says
what became of the call (``hitl_gates.chat_gone_fate``): ``not_run`` when
the end took it first, ``cut`` (an error: it may have run) when its drive had
started. Driven here at the route itself (the delete lands in the step
between recording the decision and starting its drive) and at the end (the
person's Approve lands between the end's read and its write), for every way a
chat goes away.

L2 -- an ended resuming Deny read "while its approved call ran" and the
daily digest counted it as an approval that could not resume. Now every fate
is worded per decision in English and Arabic from the catalog
(``runs.gate_fate.<fate>``): the gate's message, Running work's line, and the
digest's key (a denial is counted as a denial, an expiry as an expiry).

L3 -- after archive and un-archive a cut turn's Continue did not come back
and nothing said why; a turn cut in a chat archived before the start was
skipped without a word. Now archiving ends the cut turn in its checkpoint
and the chat says why, in the reader's language; the start pass does the
same for an archived chat's cut turn, with a log line.

The gates read the product source: every function that records a decision
and starts its drive asks ``begin_drive`` first with no await in between,
and a gone chat's gates end only through ``end_gate``. Planted functions
are their negative controls.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import re
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from kazma_core.safety import hitl_gates
from kazma_core.safety.hitl_gates import CHAT_GONE_FATES, TIMEOUT_ACTOR, TransitionConflict
from kazma_ui import chat_runs_end, reply_sink, runs_chat_turn, turn_resume
from kazma_ui.chat_runs_end import begin_drive, chat_went_away, end_chat_runs
from kazma_ui.i18n import t

import tests.test_chat_gone_ends_runs as _gone
from tests._as_request import as_owner
from tests.test_chat_gone_ends_runs import _waiting_chat

#: The delete-chat tests' fixtures: a private chat store, the real chat and
#: Dashboard routes over it.
store = _gone.store
web = _gone.web

REPO = Path(__file__).resolve().parents[1]
_ARABIC = re.compile(r"[؀-ۿ]")


def _gate(gate_id: str) -> Any:
    row = hitl_gates.gate_for(gate_id)
    assert row is not None
    return row


def _fate(gate_id: str) -> str:
    row = _gate(gate_id)
    return hitl_gates.ended_fate(row.state, row.decision, row.actor, row.ended_reason)


def _claim(gate_id: str, decision: str = "approve", actor: str = "owner", *, resuming: bool = False) -> None:
    """A person's answer, as ``record_gate_decision`` records it."""
    hitl_gates.claim_gate(gate_id, decision, actor)
    if resuming:
        hitl_gates.mark_resuming(gate_id)


# ── L1 at the route: the delete lands between the decision and its drive ──


def _approve_route(monkeypatch: pytest.MonkeyPatch, chat: Any, *, in_the_window: Any = None) -> Any:
    """The real ``/api/approve`` over the real gate registry: the decision is
    recorded as ``record_gate_decision`` records it (claim, resuming), and
    *in_the_window* runs in the step between that and the drive (the scope
    grants, off the loop). The drive itself is recorded, never run."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import auth, hitl_decision, hitl_status, turn_runtime
    from kazma_ui.routes_direct import misc
    from kazma_ui.sse_chat import _streaming as streaming

    payload = {"type": "hitl_approval", "tool": "python_exec", "args": {"code": "print(1)"},
               "interrupt_id": chat.gate_id}
    snapshot = SimpleNamespace(next=("tool_worker",), values={},
                               tasks=[SimpleNamespace(interrupts=[SimpleNamespace(id=chat.gate_id, value=payload)])])
    graph = SimpleNamespace(aget_state=AsyncMock(return_value=snapshot))
    builder = SimpleNamespace(app=FastAPI(), _hitl_state={"graph": graph, "checkpointer": object()},
                              _graph_holder={"graph": graph}, session_store=None)

    async def record(thread_id: str, *, decision: str, interrupt_id: str = "", **_: Any) -> str:
        await asyncio.to_thread(_claim, interrupt_id, hitl_decision.DECISIONS[decision][1], resuming=True)
        return interrupt_id

    def grants(thread_id: str, approved: bool, scope: Any, *_: Any) -> Any:
        if in_the_window is not None:
            in_the_window()
        return scope

    drive = AsyncMock()
    monkeypatch.setattr(auth, "get_kazma_secret", lambda: "")
    monkeypatch.setattr(misc, "_gate_not_pending", AsyncMock(return_value=""))
    monkeypatch.setattr(misc, "_attach_hitl_view", lambda body, *a, **k: body)
    monkeypatch.setattr(hitl_status, "persisted_hitl_for_thread", lambda _: None)
    monkeypatch.setattr(turn_runtime, "resolve_session_id", lambda _: chat.session_id)
    monkeypatch.setattr(reply_sink, "resolve_reply_turn", lambda *a: chat.turn_id)
    monkeypatch.setattr(hitl_decision, "record_gate_decision", record)
    monkeypatch.setattr(misc, "_apply_scope_grants", grants)
    monkeypatch.setattr(streaming, "_drive_graph_to_journal", drive)
    misc.register_misc_routes(builder)
    with TestClient(builder.app) as client:
        answer = client.post(f"/api/approve/{chat.thread_id}",
                             json={"action": "approve", "interrupt_id": chat.gate_id, "scope": "once"})
    misc._resume_inflight.discard(chat.thread_id)
    return SimpleNamespace(answer=answer, drive=drive)


def test_an_approve_whose_chat_is_deleted_before_its_drive_never_runs(store, monkeypatch) -> None:
    """L1, the second window: the delete lands after the decision is
    recorded and before its drive starts. The call does not run, the person
    is told why, and the record keeps their Approve and says it did not run."""
    chat = _waiting_chat(store)
    seen = _approve_route(monkeypatch, chat,
                          in_the_window=lambda: chat_went_away(chat.thread_id, reason="chat_deleted"))
    assert seen.answer.status_code == 409, seen.answer.text
    assert seen.answer.json()["reason"] == "chat_gone"
    assert seen.answer.json()["error"] == t("runs.gate_fate.not_run", "en")
    seen.drive.assert_not_called()
    gate = _gate(chat.gate_id)
    assert (gate.state, gate.decision, gate.actor, gate.ended_reason) == ("settled", "approve", "owner",
                                                                           "chat_deleted"), gate
    assert _fate(chat.gate_id) == "not_run"
    assert gate.message == t("runs.gate_fate.not_run", "en")
    assert hitl_gates.gate_outcomes_since(0) == {"not_run": 1}
    assert runs_chat_turn._ended_turns()[chat.thread_id].approval == "not_run"


def test_control_the_same_approve_with_its_chat_there_runs(store, monkeypatch) -> None:
    """Control: nothing lands in the window -- the drive starts, once, and
    the gate is resuming (the harness sees a drive when there is one)."""
    chat = _waiting_chat(store)
    seen = _approve_route(monkeypatch, chat)
    assert seen.answer.status_code == 200, seen.answer.text
    assert seen.drive.call_count == 1
    assert _gate(chat.gate_id).state == "resuming"


# ── L1 at the end: the Approve lands between the end's read and its write ──


def _answer_after_the_read(monkeypatch: pytest.MonkeyPatch, gate_id: str, *, resuming: bool) -> list[bool]:
    """The person's Approve lands right after the end has read the gate
    pending, before it writes: the race, deterministically."""
    real = hitl_gates.live_gates
    fired: list[bool] = []

    def read_then_answer(thread_id: str) -> list[Any]:
        rows = real(thread_id)
        if not fired:
            fired.append(True)
            _claim(gate_id, resuming=resuming)
        return rows

    monkeypatch.setattr(hitl_gates, "live_gates", read_then_answer)
    return fired


def _delete(web_: Any, chat: Any) -> None:
    assert web_.client.delete(f"/api/chat/sessions/{chat.session_id}").json() == {"status": "ok"}


def _archive(web_: Any, chat: Any) -> None:
    assert web_.client.post(f"/api/chat/sessions/{chat.session_id}/archive").json()["archived"] is True


def _clear_all(web_: Any, chat: Any) -> None:
    assert web_.client.post("/api/sessions/clear-all").json()["deleted"] is True


def _store_limit(web_: Any, chat: Any) -> None:
    chat_went_away(chat.thread_id, reason="chat_store_full")


def _reset(web_: Any, chat: Any) -> None:
    assert "event: done" in web_.client.post("/api/chat/stream",
                                             json={"session_id": chat.session_id, "message": "/reset"}).text


#: Every way in to the end: the delete route, an archive, clear-all, /reset
#: (on the loop) and the store's limit (from a thread).
WAYS = {"deleted": (_delete, "chat_deleted"), "archived": (_archive, "chat_archived"),
        "clear_all": (_clear_all, "chat_cleared"), "reset": (_reset, "chat_cleared"),
        "store_limit": (_store_limit, "chat_store_full")}


@pytest.mark.parametrize("resuming", [False, True], ids=["claimed", "resuming"])
@pytest.mark.parametrize("way", sorted(WAYS))
def test_an_approve_landing_inside_the_end_is_taken_and_never_runs(store, web, monkeypatch, way,
                                                                   resuming) -> None:
    """L1, the first window, every way a chat goes away: the end read the
    gate pending, the person's Approve landed, the end's write missed. The
    end reads it again and takes it: the decision is kept, the call is
    recorded as not run, and the route's next step can neither resume it nor
    start its drive."""
    chat = _waiting_chat(store)
    fired = _answer_after_the_read(monkeypatch, chat.gate_id, resuming=resuming)
    ender, reason = WAYS[way]
    ender(web, chat)
    assert fired
    gate = _gate(chat.gate_id)
    assert not gate.is_live, gate
    assert (gate.decision, gate.actor, gate.ended_reason) == ("approve", "owner", reason), gate
    assert _fate(chat.gate_id) == "not_run"
    if not resuming:
        with pytest.raises(TransitionConflict):
            hitl_gates.mark_resuming(chat.gate_id)         # the route's next step
    assert begin_drive(chat.thread_id, chat.gate_id) is False


def test_control_the_end_that_wrote_once_leaves_the_approve_to_run(store, web, monkeypatch) -> None:
    """Control: the end as it was on ec538d779 (one write, no second read)
    leaves the claimed gate live; the route resumes it and its drive starts."""
    from kazma_core.safety.hitl_gates import end_gate

    def once(row: Any, outcome: str, reason: str, *, waiting_only: bool) -> str:
        return "not_needed" if end_gate(row.gate_id, row.state, outcome=outcome) is not None else ""

    monkeypatch.setattr(chat_runs_end, "_end_one", once)
    chat = _waiting_chat(store)
    _answer_after_the_read(monkeypatch, chat.gate_id, resuming=False)
    _delete(web, chat)
    assert _gate(chat.gate_id).state == "claimed"
    hitl_gates.mark_resuming(chat.gate_id)                 # the route goes on


def test_a_drive_that_started_first_is_cut_and_its_call_may_have_run(store, web) -> None:
    """L1: the drive started before the chat went away. The end leaves no
    "not run" claim: the gate is an error with the decision kept, worded
    "whether it ran is not known"."""
    chat = _waiting_chat(store)
    _claim(chat.gate_id, resuming=True)
    assert begin_drive(chat.thread_id, chat.gate_id) is True
    _delete(web, chat)
    gate = _gate(chat.gate_id)
    assert (gate.state, gate.decision, gate.ended_reason) == ("error", "approve", "chat_deleted"), gate
    assert _fate(chat.gate_id) == "cut"
    assert gate.message == t("runs.gate_fate.cut", "en")
    assert hitl_gates.gate_outcomes_since(0) == {"cut": 1}


def test_control_the_same_gate_with_no_drive_is_not_run(store, web) -> None:
    """Control: the same resuming gate whose drive never started."""
    chat = _waiting_chat(store)
    _claim(chat.gate_id, resuming=True)
    _delete(web, chat)
    assert (_gate(chat.gate_id).state, _fate(chat.gate_id)) == ("settled", "not_run")


def test_no_drive_starts_while_its_chat_is_being_ended(store, monkeypatch) -> None:
    """L1: from the moment an end begins, a drive on its thread is refused --
    asked while the end reads the gates, deterministically."""
    chat = _waiting_chat(store)
    real = hitl_gates.live_gates
    asked: list[bool] = []

    def read(thread_id: str) -> list[Any]:
        asked.append(begin_drive(chat.thread_id, "another-gate"))
        return real(thread_id)

    monkeypatch.setattr(hitl_gates, "live_gates", read)
    asyncio.run(end_chat_runs(chat.thread_id, reason="chat_archived", waiting_only=True))
    assert asked == [False]
    # Control: once it is over (archived, not deleted), a new gate's drive starts.
    assert begin_drive(chat.thread_id, "a-later-gate") is True


# ── L2: what happened, per decision, said and counted ───────────────────


@pytest.mark.parametrize("drove", [False, True], ids=["not-driven", "driven"])
@pytest.mark.parametrize(("decision", "actor", "fate", "key"), [
    ("deny", "owner", "denied", "deny"),
    ("deny", TIMEOUT_ACTOR, "expired", "timeout"),
], ids=["deny", "expiry"])
def test_an_ended_denial_is_said_and_counted_as_what_it_was(store, web, decision, actor, fate, key,
                                                           drove) -> None:
    """L2: a resuming Deny (or expiry) whose chat went away is no failed
    resume and no approved call: settled, its decision kept, worded per
    decision, counted as a denial or an expiry."""
    chat = _waiting_chat(store)
    _claim(chat.gate_id, decision, actor, resuming=True)
    if drove:
        assert begin_drive(chat.thread_id, chat.gate_id)
    _delete(web, chat)
    gate = _gate(chat.gate_id)
    assert (gate.state, gate.decision, gate.actor) == ("settled", decision, actor), gate
    assert _fate(chat.gate_id) == fate
    assert gate.message == t(f"runs.gate_fate.{fate}", "en")
    assert "approved" not in gate.message
    assert hitl_gates.gate_outcomes_since(0) == {key: 1}


def test_control_an_ended_driven_approve_is_the_one_cut(store, web) -> None:
    """Control: the same with an Approve whose drive started -- the one
    fate that is an error, said differently from a denial."""
    chat = _waiting_chat(store)
    _claim(chat.gate_id, resuming=True)
    begin_drive(chat.thread_id, chat.gate_id)
    _delete(web, chat)
    assert _gate(chat.gate_id).state == "error"
    assert t("runs.gate_fate.cut", "en") != t("runs.gate_fate.denied", "en")


def test_every_fate_is_worded_in_english_and_arabic_and_counted() -> None:
    """L2: each fate has its words in both languages, and the digest a word
    for each key a fate is counted under."""
    from kazma_core.observability.daily_digest import _OUTCOME_WORDS
    from kazma_core.safety.hitl_gates import _DIGEST_KEY

    for fate in CHAT_GONE_FATES:
        en, ar = t(f"runs.gate_fate.{fate}", "en"), t(f"runs.gate_fate.{fate}", "ar")
        assert en and en != f"runs.gate_fate.{fate}", fate
        assert _ARABIC.search(ar) and ar != en, fate
    for key in {*_DIGEST_KEY.values(), "not_needed", "cleared"}:
        assert key in _OUTCOME_WORDS, key
    assert "approve" not in _DIGEST_KEY.values()


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_running_work_says_what_became_of_the_approval(store, web, lang) -> None:
    """L2: Running work's line for the ended turn says why it ended and what
    became of its approval, in the reader's language."""
    from kazma_core.runs import registry
    from kazma_core.runs.text import ended_word, format_listing

    chat, plain = _waiting_chat(store), _waiting_chat(store, gate=False)
    _claim(chat.gate_id)
    _delete(web, chat)
    _delete(web, plain)
    listing = asyncio.run(registry.list_runs(kinds=("chat_turn",), now=time.time()))
    runs = {run.native_id: run for run in listing.runs}
    assert runs[chat.thread_id].ended_gate == "not_run"
    line = format_listing([runs[chat.thread_id]], {}, lang=lang, include_finished=True)
    assert t("runs.ended.chat_deleted", lang) in line and t("runs.gate_fate.not_run", lang) in line, line
    # Control: a turn that waited on no approval says only why it ended.
    assert runs[plain.thread_id].ended_gate == ""
    assert ended_word("chat_deleted", lang, runs[plain.thread_id].ended_gate) == t("runs.ended.chat_deleted", lang)


# ── L3: a cut turn in an archived chat ends, and the chat says why ───────


def _cut_turn(chat: Any) -> None:
    """A turn a restart cut while a tool ran (left to the owner), its reply
    row still open, as after a restart (no reply turn in memory)."""
    reply_sink.reset_reply_turns()
    turn_resume._left[chat.thread_id] = turn_resume.CutTurn(
        thread_id=chat.thread_id, at=time.time(), next=("tools",), unknown=["python_exec"])


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_an_archived_chats_cut_turn_ends_and_the_chat_says_why(store, web, lang) -> None:
    """L3: archiving ends the cut turn in its checkpoint (no start brings it
    back) and the chat says why, in the reader's language; un-archived, the
    line is there. Before, nothing was written and the turn came back at the
    next start."""
    from kazma_ui.i18n import set_install_language

    chat = _waiting_chat(store, gate=False)
    _cut_turn(chat)
    set_install_language(lang)
    try:
        _archive(web, chat)
    finally:
        set_install_language("en")
    assert turn_resume.cut_turns() == {}
    assert web.graph.updates and web.graph.updates[-1]["task_status"] == "abandoned"
    assert runs_chat_turn._ended_turns()[chat.thread_id].reason == "chat_archived"
    assert web.client.post(f"/api/chat/sessions/{chat.session_id}/unarchive").json()["status"] == "ok"
    with as_owner():
        rows = store.get(chat.session_id).messages
    assert rows[-1]["role"] == "assistant" and rows[-1]["content"] == t("chat.cut_turn_archived", lang), rows
    assert not any(row.get("open") for row in rows), rows      # the cut turn's row is closed
    if lang == "ar":
        assert _ARABIC.search(rows[-1]["content"])


def test_control_an_archive_with_no_cut_turn_says_nothing_of_one(store, web) -> None:
    chat = _waiting_chat(store, gate=False)
    reply_sink.reset_reply_turns()
    _archive(web, chat)
    with as_owner():
        rows = store.get(chat.session_id).messages
    assert all(row.get("content") != t("chat.cut_turn_archived", "en") for row in rows)
    assert web.graph.updates == []


def _cut_graph() -> tuple[Any, Any]:
    """A real graph whose turn a restart cut before its tools ran."""
    from typing import TypedDict

    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph

    class State(TypedDict, total=False):
        messages: list
        task_status: str
        auto_continue: bool

    builder = StateGraph(State)
    builder.add_node("think", lambda state: {})
    builder.add_node("tools", lambda state: {})
    builder.add_edge(START, "think")
    builder.add_edge("think", "tools")
    builder.add_edge("tools", END)
    saver = InMemorySaver()
    return builder.compile(checkpointer=saver, interrupt_before=["tools"]), saver


def test_a_start_ends_an_archived_chats_cut_turn_and_says_why(store, monkeypatch, caplog) -> None:
    """L3, the start pass: a turn cut in a chat archived before the start is
    logged, ended in its checkpoint, said in its chat and listed ended -- it
    was skipped without a word. The chat that shows is continued (control)."""
    graph, saver = _cut_graph()
    showing, archived = _waiting_chat(store, gate=False), _waiting_chat(store, gate=False)
    reply_sink.reset_reply_turns()
    with as_owner():
        store.set_archived(archived.session_id, True)
    continued: list[str] = []

    async def fake_continue(graph_: Any, turn: Any, **_: Any) -> tuple[bool, str]:
        continued.append(turn.thread_id)
        return True, "The turn continues."

    monkeypatch.setattr(turn_resume, "continue_turn", fake_continue)

    async def scenario() -> Any:
        for chat in (showing, archived):
            await graph.ainvoke({"messages": [{"role": "user", "content": "go"}]},
                                {"configurable": {"thread_id": chat.thread_id, "checkpoint_ns": ""}})
        await turn_resume.resume_cut_turns(lambda: graph, lambda: saver, delay_s=0)
        return await graph.aget_state({"configurable": {"thread_id": archived.thread_id, "checkpoint_ns": ""}})

    with as_owner(), caplog.at_level(logging.INFO, logger="kazma_ui.turn_resume"):
        snapshot = asyncio.run(scenario())
    assert continued == [showing.thread_id]
    assert snapshot.values.get("task_status") == "abandoned"
    assert any(archived.thread_id[:12] in r.getMessage() and "archived chat" in r.getMessage()
               for r in caplog.records), caplog.text
    with as_owner():
        rows = store.get(archived.session_id).messages
        shown = store.get(showing.session_id).messages
    assert rows[-1]["content"] == t("chat.cut_turn_archived", "en"), rows
    assert all(row.get("content") != t("chat.cut_turn_archived", "en") for row in shown)
    assert runs_chat_turn._ended_turns()[archived.thread_id].reason == "chat_archived"
    assert turn_resume.cut_turns() == {}


# ── the gates ───────────────────────────────────────────────────────────

#: What starts the drive that carries out a recorded decision.
DRIVE_STARTS = frozenset({"create_task", "_run_resume"})


def _product_sources() -> dict[str, str]:
    listed = subprocess.run(["git", "ls-files", "kazma-*/kazma_*/*.py", "kazma-*/kazma_*/**/*.py"],
                            cwd=REPO, capture_output=True, text=True, check=True).stdout.split()
    return {rel: (REPO / rel).read_text(encoding="utf-8", errors="ignore") for rel in listed
            if "_tests/" not in rel and "/tests/" not in rel and (REPO / rel).exists()}


def _name(call: ast.Call) -> str:
    func = call.func
    return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""


def _own(fn: ast.AST) -> list[ast.AST]:
    out, stack = [], list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        out.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return out


def _functions(source: str) -> list[tuple[str, ast.AST]]:
    tree = ast.parse(source)
    return [(node.name, node) for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _records_durably(nodes: list[ast.AST]) -> list[int]:
    lines = []
    for node in nodes:
        if isinstance(node, ast.Call) and _name(node) == "record_gate_decision" and any(
                k.arg == "require_durable" and isinstance(k.value, ast.Constant) and k.value.value is True
                for k in node.keywords):
            lines.append(node.lineno)
    return lines


def drives_without_a_token(sources: dict[str, str]) -> tuple[list[str], list[str]]:
    """(every function that records a decision and starts a drive after it,
    the ones whose drive start is not preceded by ``begin_drive`` with no
    await in between): ``module:function:line``."""
    deciders, bad = [], []
    for module, source in sources.items():
        if "record_gate_decision" not in source:
            continue
        for name, fn in _functions(source):
            nodes = _own(fn)
            recorded = _records_durably(nodes)
            if not recorded:
                continue
            starts = sorted(n.lineno for n in nodes if isinstance(n, ast.Call) and _name(n) in DRIVE_STARTS
                            and n.lineno > min(recorded))
            if not starts:
                continue
            deciders.append(f"{module}:{name}")
            tokens = sorted(n.lineno for n in nodes if isinstance(n, ast.Call) and _name(n) == "begin_drive")
            # What the refusal does (``if not begin_drive(...): ... return``)
            # starts no drive: its awaits are not between the token and one.
            refused = [(n.body[0].lineno, n.body[-1].end_lineno or n.body[-1].lineno) for n in nodes
                       if isinstance(n, ast.If) and any(isinstance(c, ast.Call) and _name(c) == "begin_drive"
                                                        for c in ast.walk(n.test))]
            awaits = sorted(n.lineno for n in nodes if isinstance(n, ast.Await)
                            and not any(lo <= n.lineno <= hi for lo, hi in refused))
            for start in starts:
                token = max((line for line in tokens if min(recorded) < line < start), default=None)
                if token is None or any(token < line < start for line in awaits):
                    bad.append(f"{module}:{name}:{start}")
    return sorted(deciders), sorted(bad)


def test_every_drive_of_a_recorded_decision_asks_begin_drive_first() -> None:
    deciders, bad = drives_without_a_token(_product_sources())
    # The enumeration is not empty: the web route, the watchdog, a chat app's button.
    for where in ("routes_direct/misc.py:", "kazma_ui/hitl_timeout.py:", "agent_handler/hitl.py:"):
        assert any(where in d for d in deciders), (where, deciders)
    assert not bad, (
        "A recorded decision's drive starts here without asking kazma_ui.chat_runs_end.begin_drive in the "
        "same step (no await in between): a chat deleted meanwhile runs the approved call (review L1): "
        + ", ".join(bad))


_PLANTED_NO_TOKEN = '''
async def approve(thread_id):
    gate = await record_gate_decision(thread_id, decision="approved", actor="a", require_durable=True)
    task = asyncio.create_task(drive(thread_id))
    register_turn(thread_id, task)
'''
_PLANTED_AWAIT_BETWEEN = '''
async def approve(thread_id):
    gate = await record_gate_decision(thread_id, decision="approved", actor="a", require_durable=True)
    if not begin_drive(thread_id, gate):
        return
    await asyncio.to_thread(grants, thread_id)
    task = asyncio.create_task(drive(thread_id))
'''
_PLANTED_GOOD = '''
async def approve(thread_id):
    gate = await record_gate_decision(thread_id, decision="approved", actor="a", require_durable=True)
    await asyncio.to_thread(grants, thread_id)
    if not begin_drive(thread_id, gate):
        return
    task = asyncio.create_task(drive(thread_id))
'''


@pytest.mark.parametrize("planted", [_PLANTED_NO_TOKEN, _PLANTED_AWAIT_BETWEEN], ids=["no-token", "await-between"])
def test_control_a_drive_without_its_token_is_caught(planted) -> None:
    deciders, bad = drives_without_a_token({"kazma-ui/kazma_ui/planted.py": planted})
    assert deciders == ["kazma-ui/kazma_ui/planted.py:approve"] and len(bad) == 1, (deciders, bad)


def test_control_a_drive_with_its_token_passes() -> None:
    assert drives_without_a_token({"kazma-ui/kazma_ui/planted.py": _PLANTED_GOOD})[1] == []


#: What ends a chat's runs, and what writes a gate's end other than end_gate.
ENDERS = frozenset({"end_chat_runs", "chat_went_away", "end_space_runs", "end_every_chat_run", "_chat_went_away"})
OTHER_GATE_ENDS = frozenset({"settle_gate", "settle_gate_async", "fail_gate", "timeout_gate", "supersede_gate",
                             "settle_thread_gates", "abort_thread_hitl", "expire_due_gates"})
ENDS_MODULE = "kazma-ui/kazma_ui/chat_runs_end.py"
#: Functions on a way out that end a gate otherwise in a branch that is no
#: way out, and why.
DECLARED: dict[tuple[str, str, str], str] = {
    ("kazma-gateway/kazma_gateway/agent_handler/graph.py", "_handler_body", "abort_thread_hitl"):
        "the chat-app command handler: /abort (Stop) releases the turn's cards there, in its own branch; "
        "its /reset branch is the way out, through end_chat_runs",
}


def gone_chat_gates_ended_otherwise(sources: dict[str, str],
                                    declared: dict[tuple[str, str, str], str] | None = None) -> list[str]:
    """Every function on a chat's way out (it calls an ender, or is in the
    end's own module) that ends a gate other than through ``end_gate``:
    ``module:function:line name``. Those rewrite a person's decision, or call
    an approval whose call never ran settled."""
    out = []
    for module, source in sources.items():
        for name, fn in _functions(source):
            calls = [n for n in _own(fn) if isinstance(n, ast.Call)]
            if module != ENDS_MODULE and not any(_name(c) in ENDERS for c in calls):
                continue
            out += [f"{module}:{name}:{c.lineno} {_name(c)}" for c in calls if _name(c) in OTHER_GATE_ENDS
                    and (module, name, _name(c)) not in (DECLARED if declared is None else declared)]
    return sorted(out)


def test_a_gone_chats_gates_end_only_through_end_gate() -> None:
    sources = _product_sources()
    found = gone_chat_gates_ended_otherwise(sources)
    assert not found, (
        "A chat's way out ends a gate here other than through hitl_gates.end_gate (via "
        "kazma_ui.chat_runs_end): it rewrites the person's decision, or records an approval whose call "
        "never ran as settled. " + ", ".join(found))
    # The enumeration reaches the ways out (the end's module), and every
    # declaration is still needed.
    assert "end_gate(" in sources[ENDS_MODULE]
    undeclared = gone_chat_gates_ended_otherwise(sources, declared={})
    for module, name, call in DECLARED:
        assert any(f.startswith(f"{module}:{name}:") and f.endswith(f" {call}") for f in undeclared), (
            module, name, call)


def test_control_a_way_out_that_settles_a_gate_itself_is_caught() -> None:
    planted = '''
async def clear(thread_id):
    await end_chat_runs(thread_id, reason="approvals_cleared")
    for row in await live_gates_async(thread_id):
        await settle_gate_async(row.gate_id, "cleared")
'''
    assert gone_chat_gates_ended_otherwise({"kazma-ui/kazma_ui/planted.py": planted}) == [
        "kazma-ui/kazma_ui/planted.py:clear:5 settle_gate_async"]
    # And the same without the ender is not a way out.
    assert gone_chat_gates_ended_otherwise(
        {"kazma-ui/kazma_ui/planted.py": planted.replace("end_chat_runs", "something_else")}) == []


def test_the_token_is_bounded() -> None:
    """The tokens are memory: a long-running install keeps a bounded number."""
    for _ in range(chat_runs_end._TOKENS_KEPT + 5):
        begin_drive("t-bound", uuid4().hex)
    assert len(chat_runs_end._tokens) <= chat_runs_end._TOKENS_KEPT
