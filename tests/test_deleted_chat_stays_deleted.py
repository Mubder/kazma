"""A chat that was deleted is not brought back by what was still on its way to
it: a closing turn's reply, a message being sent, a tab left open, a voice
utterance, a chat app that had taken it over (Spaces S2-12's move deletes the
chat where it was; live, 2026-11-24, deleting a chat as its turn closed also
paged a "Reply NOT persisted" that lost nothing).

The session manager remembers the chats deleted in this process, and every
writer of a chat asks it. An install with one account, through the real store,
reply sink, web stream, voice path and gateway handler; a store that forgets
its deletes is the control.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import sqlite3
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from kazma_core.spaces import scope, threads
from kazma_ui import reply_sink, session_manager, turn_runtime
from kazma_ui.session_manager import ChatDeleted, ChatSession, SessionManager

from tests._chat_move_space_cases import NoCardGraph


@pytest.fixture
def store(tmp_path):
    before = session_manager.peek_session_manager()
    made = SessionManager(db_path=str(tmp_path / "chats.db"))
    session_manager.set_session_manager(made)
    yield made
    session_manager.set_session_manager(before)
    made.close()


@pytest.fixture
def forgets(monkeypatch):
    """The store before: it remembers no delete."""
    monkeypatch.setattr(SessionManager, "mark_deleted", lambda *_a, **_k: None)
    monkeypatch.setattr(threads, "end_thread", lambda *_a, **_k: None)


def _chat(store: SessionManager, *, title: str = "a chat", thread: str = "") -> tuple[str, str]:
    session_id = f"s-{uuid4().hex[:8]}"
    thread_id = thread or f"t-{uuid4().hex[:8]}"
    held = store.get_or_create(session_id, thread_id=thread_id)
    held.messages = [{"role": "user", "content": "question"}, {"role": "assistant", "content": "answer"}]
    held.title = title
    store.put(held)
    return session_id, thread_id


def _stored(store: SessionManager, session_id: str) -> int:
    """Rows of the chat in the database file itself."""
    with sqlite3.connect(store.db_path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM sessions WHERE session_id = ?", (session_id,)).fetchone()[0])


def _gone(store: SessionManager, session_id: str, thread_id: str) -> bool:
    return (store.get(session_id) is None and store.get_by_thread_id(thread_id) is None
            and session_id not in {s.session_id for s in store.list_all()} and _stored(store, session_id) == 0)


# ── the store ─────────────────────────────────────────────────────────────


def test_no_writer_of_the_store_brings_it_back(store) -> None:
    session_id, thread_id = _chat(store)
    held = copy.deepcopy(store.get(session_id))
    store.delete(session_id)
    assert store.chat_deleted(session_id) and store.chat_deleted(thread_id=thread_id)
    store.put(held)
    for call in (lambda: store.get_or_create(session_id), lambda: store.get_or_create(session_id, thread_id="t-new"),
                 lambda: store.get_or_create("s-new", thread_id=thread_id),
                 lambda: store.update_from_dict(session_id, {"messages": held.messages})):
        with pytest.raises(ChatDeleted):
            call()
    with pytest.raises(ChatDeleted), store.transact(session_id):
        pytest.fail("a transaction on a deleted chat")
    assert _gone(store, session_id, thread_id)


def test_control_a_store_that_forgets_its_deletes_stores_it_again(store, forgets) -> None:
    session_id, thread_id = _chat(store)
    held = copy.deepcopy(store.get(session_id))
    store.delete(session_id)
    store.put(held)
    assert not _gone(store, session_id, thread_id)


def test_the_other_chats_and_new_ones_are_as_they_were(store) -> None:
    session_id, _thread = _chat(store)
    other, other_thread = _chat(store)
    store.delete(session_id)
    assert not store.chat_deleted(other, other_thread) and store.get(other) is not None
    made = store.get_or_create(None)
    assert not store.chat_deleted(made.session_id, made.thread_id)
    with store.transact(other) as held:
        held.add_message("user", "more")
    assert len(store.get(other).messages) == 3


def test_a_chat_of_the_same_id_in_another_space_is_not_deleted(store) -> None:
    session_id, _thread = _chat(store)
    with scope.bind_scope("elsewhere", "someone"):
        held = store.get_or_create(session_id, thread_id="t-elsewhere")
        store.put(held)
    store.delete(session_id)
    with scope.bind_scope("elsewhere", "someone"):
        assert not store.chat_deleted(session_id) and store.get(session_id) is not None
        # Named as the deleted chat's thread is: still another space's.
        store.put(store.get_or_create(_thread, thread_id="t-there"))
        assert not store.chat_deleted(_thread) and store.get(_thread) is not None


def test_a_deleted_chat_whose_row_is_still_there_is_not_found(store) -> None:
    """A move whose delete waits: nothing lists it, nor finds it by its
    thread, from the cache or the database."""
    session_id, thread_id = _chat(store)
    store.mark_deleted(session_id, thread_id)
    for _cached in (True, False):
        assert store.get(session_id) is None and store.get_by_thread_id(thread_id) is None
        assert session_id not in {s.session_id for s in store.list_all()}
        with pytest.raises(ChatDeleted):
            store.rename(session_id, "back")
        store._sessions.clear()
    assert _stored(store, session_id) == 1


def test_what_is_remembered_is_bounded(store, monkeypatch) -> None:
    monkeypatch.setattr(session_manager, "DELETED_CHATS_KEPT", 3)
    monkeypatch.setattr(threads, "_ENDED_KEPT", 3)
    chats = [_chat(store) for _ in range(5)]
    for session_id, _thread in chats:
        store.delete(session_id)
    assert len(store._deleted) == len(threads._ended) == 3
    kept = [False, False, True, True, True]
    assert [store.chat_deleted(s) for s, _t in chats] == kept, "the oldest are dropped"
    assert [threads.thread_ended(t) for _s, t in chats] == kept


def test_a_delete_the_database_refused_is_not_one(store, monkeypatch) -> None:
    """The chat is still there, so it is not remembered as deleted."""
    session_id, _thread = _chat(store)

    def refuse(*_a: Any, **_k: Any) -> None:
        raise sqlite3.OperationalError("database is locked")

    with monkeypatch.context() as patch:
        patch.setattr(store, "_delete_row", refuse)
        with pytest.raises(sqlite3.OperationalError):
            store.delete(session_id)
    assert not store.chat_deleted(session_id) and store.get(session_id) is not None


def test_an_empty_chat_pruned_from_the_list_may_still_be_used(store) -> None:
    """Pruning is no delete: a tab open on an empty chat sends its first
    message yet."""
    empty = store.get_or_create(None)
    empty.created_at = empty.updated_at = "2020-01-01T00:00:00+00:00"
    store.put(empty)
    assert store.prune_empty_web_sessions(max_age_seconds=0) == 1
    assert store.get_or_create(empty.session_id, thread_id=empty.thread_id).session_id == empty.session_id


# ── the reply of a turn that was closing ──────────────────────────────────


def _alerts(monkeypatch) -> list[str]:
    raised: list[str] = []
    from kazma_core.observability import ops_alerts

    monkeypatch.setattr(ops_alerts, "alert", lambda key, *_a, **_k: raised.append(key))
    return raised


def test_a_closing_turns_reply_is_not_saved_and_pages_nobody(store, monkeypatch, caplog) -> None:
    session_id, thread_id = _chat(store)
    turn = reply_sink.open_reply_turn(thread_id, session_id)
    store.delete(session_id)
    raised = _alerts(monkeypatch)
    with caplog.at_level(logging.INFO):
        assert not turn_runtime.persist_reply(session_id, turn, "the answer", interrupted=False, thread_id=thread_id)
        assert not reply_sink.upsert_reply(session_id, turn, "the answer, again")
        reply_sink.record_instant_turn(session_id, thread_id, "/cost", "free")
    assert raised == [], "a reply to a deleted chat is no lost reply"
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []
    assert any("its chat was deleted" in r.getMessage() for r in caplog.records)
    assert _gone(store, session_id, thread_id)


def test_a_reply_the_store_refuses_is_still_said(store, monkeypatch, caplog) -> None:
    """Only a deleted chat is quiet."""
    session_id, thread_id = _chat(store)
    raised = _alerts(monkeypatch)

    def refuse(*_a: Any, **_k: Any) -> None:
        raise sqlite3.OperationalError("database is locked")

    with monkeypatch.context() as patch, caplog.at_level(logging.INFO):
        patch.setattr(SessionManager, "_write_durably", refuse)
        assert not turn_runtime.persist_reply(session_id, "turn-1", "the answer", interrupted=False,
                                              thread_id=thread_id)
    assert raised == ["reply.persist_failed"]
    assert any(r.levelno >= logging.WARNING and "Reply NOT persisted" in r.getMessage() for r in caplog.records)


def test_an_approvals_resume_mints_no_chat_for_it(store) -> None:
    session_id, thread_id = _chat(store)
    store.delete(session_id)
    assert turn_runtime.ensure_session_for_thread(thread_id) == ""
    assert _gone(store, session_id, thread_id)


def test_control_the_reply_of_a_deleted_chat_was_paged(store, forgets, monkeypatch) -> None:
    """Without the memory of it, a write that finds no row is the store's
    refusal: the false alert seen on live."""
    session_id, thread_id = _chat(store)
    turn = reply_sink.open_reply_turn(thread_id, session_id)
    store.delete(session_id)
    raised = _alerts(monkeypatch)
    assert not turn_runtime.persist_reply(session_id, turn, "the answer", interrupted=False, thread_id=thread_id)
    assert raised == ["reply.persist_failed"]


# ── the web chat ──────────────────────────────────────────────────────────


@pytest.fixture
def web(store, monkeypatch):
    from fastapi.testclient import TestClient
    from kazma_ui import dashboard
    from kazma_ui.sse_chat import create_sse_chat_router

    class Saved:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        async def adelete_thread(self, thread_id: str) -> None:
            self.deleted.append(thread_id)

    saved = Saved()
    monkeypatch.setattr(dashboard, "_checkpoint_manager", saved)
    app = FastAPI()
    app.include_router(create_sse_chat_router(graph_getter=NoCardGraph))
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, saved=saved)


def _send(client: Any, session_id: str, text: str = "more") -> str:
    return client.post("/api/chat/stream", json={"session_id": session_id, "message": text}).text


@pytest.mark.parametrize("lang, word", [("en", "deleted"), ("ar", "حُذفت")])
def test_a_message_to_a_deleted_chat_is_told_and_not_run(store, web, monkeypatch, lang, word) -> None:
    from kazma_ui import i18n

    monkeypatch.setattr(i18n, "current_language", lambda *_a, **_k: lang)
    session_id, thread_id = _chat(store)
    assert web.client.delete(f"/api/chat/sessions/{session_id}").json() == {"status": "ok"}
    assert web.saved.deleted == [thread_id]
    answer = _send(web.client, session_id)
    assert '"code": "chat_deleted"' in answer and "event: error" in answer
    told = json.loads(answer.split("data: ", 1)[1].split("\n", 1)[0])
    assert word in told["content"], told
    assert web.client.post("/api/chat/sessions", json={"session_id": session_id}).status_code == 404
    assert _gone(store, session_id, thread_id)


def test_a_turn_prepared_while_its_chat_was_deleted_does_not_run(store, web, monkeypatch) -> None:
    """Deleted while the message was being prepared: asked again under the
    thread's lock, right before the graph would start."""
    from kazma_ui.sse_chat import _streaming

    session_id, thread_id = _chat(store)
    ran: list[str] = []

    async def drive(*_a: Any, **_k: Any) -> None:
        ran.append(thread_id)

    monkeypatch.setattr(_streaming, "_drive_graph_to_journal", drive)
    import kazma_ui.sse_chat as sse_chat

    monkeypatch.setattr(sse_chat, "_drive_graph_to_journal", drive, raising=False)
    real = reply_sink.open_reply_turn

    def deleted_here(*args: Any, **kwargs: Any) -> str:
        store.delete(session_id)
        return real(*args, **kwargs)

    monkeypatch.setattr(reply_sink, "open_reply_turn", deleted_here)
    answer = _send(web.client, session_id, "a question")
    assert '"code": "chat_deleted"' in answer
    assert ran == [] and _gone(store, session_id, thread_id)


def test_a_message_whose_chat_goes_as_it_is_saved_prepares_no_turn(store, web, monkeypatch) -> None:
    """Refused where the message would be saved: no reply is opened, no other
    tab is told of the message, and nothing of it is prepared."""
    import kazma_ui.sse_chat as sse_chat

    session_id, thread_id = _chat(store)
    real, opened = sse_chat._mutate_session, []

    def deleted_first(sid: str, *args: Any) -> Any:
        store.delete(sid)
        return real(sid, *args)

    monkeypatch.setattr(sse_chat, "_mutate_session", deleted_first)
    monkeypatch.setattr(reply_sink, "open_reply_turn", lambda *a, **_k: opened.append(a) or "turn-x")
    assert '"code": "chat_deleted"' in _send(web.client, session_id, "a question")
    assert opened == [] and _gone(store, session_id, thread_id)


def test_a_turn_restored_while_prepared_is_told_the_restore(store, web, monkeypatch) -> None:
    from kazma_ui.i18n import t
    from kazma_ui.thread_locks import bump_rewind_generation

    session_id, thread_id = _chat(store)
    real = reply_sink.open_reply_turn

    def restored_here(*args: Any, **kwargs: Any) -> str:
        bump_rewind_generation(thread_id)
        return real(*args, **kwargs)

    monkeypatch.setattr(reply_sink, "open_reply_turn", restored_here)
    answer = _send(web.client, session_id, "a question")
    assert t("replay.turn_after_restore", "en") in answer and "chat_deleted" not in answer


# ── voice ─────────────────────────────────────────────────────────────────


def _speak(session: ChatSession, session_id: str, thread_id: str) -> Any:
    from kazma_ui.voice_turn import run_voice_user_turn

    return asyncio.run(run_voice_user_turn(graph=NoCardGraph(), session=session, thread_id=thread_id,
                                           session_id=session_id, user_text="and also this"))


def test_an_utterance_after_the_delete_runs_nothing(store, monkeypatch) -> None:
    from kazma_ui.sse_chat import _streaming

    ran: list[str] = []

    async def drive(*_a: Any, **_k: Any) -> None:
        ran.append("ran")

    monkeypatch.setattr(_streaming, "_drive_graph_to_journal", drive)
    session_id, thread_id = _chat(store)
    held = copy.deepcopy(store.get(session_id))  # what the socket has held since it opened
    store.delete(session_id)
    opened: list[Any] = []
    monkeypatch.setattr(reply_sink, "open_reply_turn", lambda *a, **_k: opened.append(a) or "turn-x")
    result = _speak(held, session_id, thread_id)
    assert result.error and "deleted" in result.error.lower()
    assert ran == [] and opened == [], "nothing is prepared for it"
    assert _gone(store, session_id, thread_id)


def test_an_utterance_prepared_while_its_chat_was_deleted_runs_nothing(store, monkeypatch) -> None:
    from kazma_ui.sse_chat import _streaming

    ran: list[str] = []

    async def drive(*_a: Any, **_k: Any) -> None:
        ran.append("ran")

    monkeypatch.setattr(_streaming, "_drive_graph_to_journal", drive)
    session_id, thread_id = _chat(store)
    real = reply_sink.open_reply_turn

    def deleted_here(*args: Any, **kwargs: Any) -> str:
        store.delete(session_id)
        return real(*args, **kwargs)

    monkeypatch.setattr(reply_sink, "open_reply_turn", deleted_here)
    result = _speak(store.get(session_id), session_id, thread_id)
    assert result.error and ran == [] and _gone(store, session_id, thread_id)


def test_an_utterance_keeps_what_was_said_since_its_socket_opened(store, monkeypatch) -> None:
    """The line joins the chat as it is, never the copy the socket holds."""
    from kazma_ui.sse_chat import _streaming

    async def drive(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(_streaming, "_drive_graph_to_journal", drive)
    session_id, thread_id = _chat(store)
    held = copy.deepcopy(store.get(session_id))
    with store.transact(session_id) as now:
        now.add_message("user", "typed since")

    async def attached(*_a: Any, **_k: Any):
        if False:
            yield ""

    monkeypatch.setattr(_streaming, "_sse_attach_stream", attached)
    _speak(held, session_id, thread_id)
    said = [m["content"] for m in store.get(session_id).messages]
    assert "typed since" in said and "and also this" in said


# ── a chat app that had taken the chat over ───────────────────────────────


class _Graph(NoCardGraph):
    def __init__(self) -> None:
        self.runs: list[str] = []
        self.values: dict[str, Any] = {}
        self.before: Any = None

    async def aget_state(self, config: Any) -> Any:
        return SimpleNamespace(values=self.values, next=(), tasks=())

    async def ainvoke(self, state: Any, config: Any) -> Any:
        if self.before is not None:
            await self.before()
        self.runs.append(config["configurable"]["thread_id"])
        return {**state, "messages": [*state["messages"], {"role": "assistant", "content": "ok"}]}


@pytest.fixture
def telegram(store, monkeypatch):
    from kazma_core.config_store import get_config_store
    from kazma_gateway.agent_handler.graph import create_graph_handler
    from kazma_gateway.agent_handler.store import _InMemoryStore

    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "0")
    graph, sent = _Graph(), []

    async def send(out: Any) -> bool:
        sent.append(out.text)
        return True

    handler = create_graph_handler(graph=graph, manager=SimpleNamespace(send=AsyncMock(side_effect=send), adapters=[]),
                                   store=_InMemoryStore())
    uid = f"8{uuid4().int % 10**8}"
    sender = f"telegram:{uid}"

    def message(text: str) -> Any:
        from kazma_gateway.gateway import IncomingMessage

        return IncomingMessage("telegram", sender, text,
                               context_metadata={"chat_id": int(uid), "user_id": int(uid), "chat_type": "private"})

    yield SimpleNamespace(graph=graph, sent=sent, handler=handler, sender=sender, message=message,
                          say=lambda text: asyncio.run(handler(message(text))),
                          take_over=lambda thread: get_config_store().set(
                              f"active_thread.{sender}", thread, category="session"))
    for key in list((get_config_store().get_category("session") or {}).keys()):
        if key.startswith("active_thread.telegram:8"):
            get_config_store().delete(key)


def test_a_chat_app_whose_web_chat_was_deleted_starts_its_own(store, telegram) -> None:
    session_id, thread_id = _chat(store)
    telegram.take_over(thread_id)
    telegram.say("hello there, plan the beds")
    assert telegram.graph.runs == [thread_id], "it had taken the web chat over"
    store.delete(session_id)
    telegram.say("hello there, plan the beds again")
    assert len(telegram.graph.runs) == 2 and telegram.graph.runs[1].startswith("gw-telegram")
    assert _gone(store, session_id, thread_id)
    telegram.say("and the paths")
    assert telegram.graph.runs[2] == telegram.graph.runs[1], "and goes on there"


def test_a_chat_apps_own_chat_begins_again(store, telegram) -> None:
    """Named by its sender, it is the same id: a delete on the web takes the
    chat that was, and the next message starts it."""
    telegram.say("hello there, plan the beds")
    own = telegram.graph.runs[0]
    assert own.startswith("gw-telegram")
    for held in store.list_all():
        if held.thread_id == own:
            store.delete(held.session_id)
    store.delete(own)
    assert store.chat_deleted(thread_id=own)
    telegram.say("hello there, the paths now")
    assert telegram.graph.runs == [own, own] and not store.chat_deleted(thread_id=own)


@pytest.mark.parametrize("text, word", [("what is next?", "not run"), ("ما التالي؟", "لم تُنفَّذ")])
def test_a_message_waiting_on_the_lock_when_its_chat_is_deleted_is_told(store, telegram, text, word) -> None:
    session_id, thread_id = _chat(store)
    telegram.take_over(thread_id)

    async def run() -> None:
        started, release = asyncio.Event(), asyncio.Event()

        async def hold() -> None:
            started.set()
            await release.wait()

        telegram.graph.before = hold
        first = asyncio.create_task(telegram.handler(telegram.message("plan the beds for spring")))
        await asyncio.wait_for(started.wait(), 10)
        telegram.graph.before = None
        second = asyncio.create_task(telegram.handler(telegram.message(text)))
        for _ in range(30):
            await asyncio.sleep(0.01)
        await asyncio.to_thread(store.delete, session_id)
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), 20)

    asyncio.run(run())
    assert telegram.graph.runs == [thread_id], "the second message ran nothing"
    assert any(word in text for text in telegram.sent), telegram.sent
    assert _gone(store, session_id, thread_id)


# ── the delete itself ─────────────────────────────────────────────────────


def test_a_delete_that_leaves_something_keeps_its_thread_record(store, monkeypatch) -> None:
    """``chat_delete.remove_chat_remains``: the thread's space record goes
    last, and only when nothing of the chat is left."""
    from kazma_core.spaces import threads
    from kazma_ui import chat_delete

    threads.record_thread("t-left", scope.OWNER_SPACE_ID, scope.OWNER_ACCOUNT_ID)

    class Refuses:
        async def adelete_thread(self, thread_id: str) -> None:
            raise sqlite3.OperationalError("database is locked")

    class Takes:
        async def adelete_thread(self, thread_id: str) -> None:
            return None

    for manager in (Refuses(), None):
        assert asyncio.run(chat_delete.remove_chat_remains("s-left", "t-left", manager)) is False
        assert threads.record_of("t-left") is not None
    assert asyncio.run(chat_delete.remove_chat_remains("s-left", "t-left", Takes())) is True
    assert threads.record_of("t-left") is None
