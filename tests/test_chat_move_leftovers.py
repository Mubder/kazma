"""What a chat taken out of a shared space left behind (Spaces S2-12; found
by its last review, 25 Nov 2026).

* The web searches it made stayed on the space's Research page: another
  member read the person's questions, and what was found. A report stays, a
  file of the space (N32), and the dialog and the guide say so.
* The finished swarm tasks it started went, and their workers' whole output
  stayed in ``pipeline_logs.db``, which names a task by its id alone: no later
  erase could find it. The Research page's delete of a task had the same fault.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path

import pytest
from kazma_core.safety.hitl import reset_current_thread_id, set_current_thread_id
from kazma_core.spaces import scope
from kazma_core.swarm.memory import pipeline_logger
from kazma_core.tools import research_session

from tests._chat_move_world import world  # noqa: F401 - a fixture

ROOT = Path(__file__).resolve().parents[1]
TOPICS = {"en": "how to train a falcon", "ar": "كيف يدرب الصقر"}


@pytest.fixture(autouse=True)
def stores(monkeypatch, tmp_path):
    monkeypatch.setattr(research_session, "_db_path", lambda: tmp_path / "research_sessions.db")
    monkeypatch.setattr(pipeline_logger, "_DEFAULT_DB", str(tmp_path / "pipeline_logs.db"))
    monkeypatch.setattr(pipeline_logger, "_conn", None)
    monkeypatch.setattr(pipeline_logger, "_pipeline_logger", None)
    yield tmp_path
    pipeline_logger.close_pipeline_logger()


# ── the web searches ──────────────────────────────────────────────────────


@contextlib.contextmanager
def _in_chat(thread: str):
    token = set_current_thread_id(thread or None)
    try:
        yield
    finally:
        reset_current_thread_id(token)


def _search(w, who: str, space: str, thread: str, topic: str) -> None:
    with scope.bind_scope(space, w.ids[who]), _in_chat(thread):
        research_session.record_chat_research(topic, result_text=f"found about {topic}", result_count=3)


def _report(w, who: str, space: str, thread: str, topic: str) -> None:
    with scope.bind_scope(space, w.ids[who]), _in_chat(thread):
        made = research_session.create_session(topic)
        research_session.update_session(made.id, status="done", report_path="research/reports/r.md")


def _seen(w, who: str, space: str) -> set[str]:
    with scope.bind_scope(space, w.ids[who]):
        return {s.topic for s in research_session.list_sessions()}


@pytest.mark.parametrize("lang", TOPICS)
def test_a_chat_taken_out_takes_its_searches_and_nothing_else(world, lang):
    w, i, topic = world, world.ids, TOPICS[lang]
    session, thread = w.chat("reem", i["family"], mark="out")
    _other, other = w.chat("reem", i["family"], mark="another chat")
    _hers, omars = w.chat("omar", i["family"], mark="omar's")
    _search(w, "reem", i["family"], thread, topic)
    _search(w, "reem", i["family"], other, "her other chat's")
    _search(w, "omar", i["family"], omars, "omar's search")
    _search(w, "reem", i["family"], "", "searched from no chat")
    _report(w, "reem", i["family"], thread, "the report")
    assert topic in _seen(w, "omar", i["family"])
    w.moved("reem", i["family"], session, i["mine"])
    assert _seen(w, "omar", i["family"]) == {
        "her other chat's", "omar's search", "searched from no chat", "the report"}


def test_a_question_asked_in_two_chats_goes_with_the_last(world):
    """One question is one row of the Research page, whatever chats asked it."""
    w, i = world, world.ids
    one, first = w.chat("reem", i["family"], mark="one")
    two, second = w.chat("reem", i["family"], mark="two")
    for thread in (first, second, first):
        _search(w, "reem", i["family"], thread, "the tent")
    w.moved("reem", i["family"], one, i["mine"])
    assert _seen(w, "omar", i["family"]) == {"the tent"}, "her other chat there asked it too"
    w.moved("reem", i["family"], two, i["mine"])
    assert _seen(w, "omar", i["family"]) == set()


def test_the_same_question_of_another_member_is_theirs(world):
    w, i = world, world.ids
    session, thread = w.chat("reem", i["family"], mark="one")
    _omars, omars = w.chat("omar", i["family"], mark="two")
    _search(w, "reem", i["family"], thread, "the tent")
    _search(w, "omar", i["family"], omars, "the tent")
    w.moved("reem", i["family"], session, i["mine"])
    assert _seen(w, "omar", i["family"]) == {"the tent"}


def test_a_share_removes_nothing(world):
    w, i = world, world.ids
    session, thread = w.chat("reem", i["mine"], mark="share")
    _search(w, "reem", i["mine"], thread, "the tent")
    w.moved("reem", i["mine"], session, i["family"])
    assert _seen(w, "reem", i["mine"]) == {"the tent"}
    assert _seen(w, "omar", i["family"]) == set()


def test_a_row_from_before_is_not_known_to_be_one_chat_s(world):
    """It names no chat: another of hers may have asked it."""
    w, i = world, world.ids
    session, thread = w.chat("reem", i["family"], mark="old")
    _search(w, "reem", i["family"], thread, "the tent")
    with contextlib.closing(sqlite3.connect(research_session._db_path())) as conn, conn:
        conn.execute("UPDATE research_sessions SET thread_ids = ''")
    _search(w, "reem", i["family"], thread, "the tent")
    w.moved("reem", i["family"], session, i["mine"])
    assert _seen(w, "omar", i["family"]) == {"the tent"}


def test_the_table_of_an_install_from_before_takes_the_column(world):
    path = research_session._db_path()
    with contextlib.closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("CREATE TABLE research_sessions (id TEXT PRIMARY KEY, topic TEXT NOT NULL, depth TEXT, "
                     "status TEXT, stage TEXT, message TEXT, log_json TEXT, report_path TEXT, summary TEXT, "
                     "error TEXT, sources INTEGER, max_sources INTEGER, created_at REAL, updated_at REAL, "
                     "meta_json TEXT)")
    w, i = world, world.ids
    session, thread = w.chat("reem", i["family"], mark="old")
    _search(w, "reem", i["family"], thread, "the tent")
    w.moved("reem", i["family"], session, i["mine"])
    assert _seen(w, "omar", i["family"]) == set()


def test_the_chats_of_a_session_are_never_shown(world):
    w, i = world, world.ids
    _session, thread = w.chat("reem", i["family"], mark="shown")
    _search(w, "reem", i["family"], thread, "the tent")
    with scope.bind_scope(i["family"], i["omar"]):
        assert thread not in json.dumps([s.to_dict() for s in research_session.list_sessions()])


def test_a_row_of_very_many_chats_is_no_take_out_s(world, monkeypatch):
    monkeypatch.setattr(research_session, "_CHATS_NAMED", 2)
    w, i = world, world.ids
    chats = [w.chat("reem", i["family"], mark=str(n)) for n in range(3)]
    for _session, thread in chats:
        _search(w, "reem", i["family"], thread, "the tent")
    for session, _thread in chats:
        w.moved("reem", i["family"], session, i["mine"])
    assert _seen(w, "omar", i["family"]) == {"the tent"}


def test_control_rows_without_the_stamp_stay(world, monkeypatch):
    monkeypatch.setattr(research_session, "_with_chat", lambda _held: "")
    w, i = world, world.ids
    session, thread = w.chat("reem", i["family"], mark="out")
    _search(w, "reem", i["family"], thread, "the tent")
    w.moved("reem", i["family"], session, i["mine"])
    assert _seen(w, "omar", i["family"]) == {"the tent"}


def test_the_dialog_and_the_guide_say_what_stays():
    from kazma_ui.i18n import t

    assert "web searches" in t("chat.move_take_out_note", "en")
    assert "reports stay" in t("chat.move_take_out_note", "en")
    assert "البحث على الويب" in t("chat.move_take_out_note", "ar")
    assert "تقارير البحث العميق" in t("chat.move_take_out_note", "ar")
    guide = (ROOT / "docs/docs/guide/spaces.md").read_text(encoding="utf-8")
    assert "web searches it made" in " ".join(guide.split())
    assert "deep research report stays" in " ".join(guide.split())


# ── the log of its tasks ──────────────────────────────────────────────────


def _task(w, space: str, thread: str, mark: str) -> str:
    from kazma_core.swarm.task import SwarmTask
    from kazma_core.swarm.task_store import TaskStore

    task = SwarmTask(prompt=f"question {mark}")
    task.status = "completed"
    task.metadata = {"source_thread_id": thread, "tenant_id": space, "asker": w.ids["reem"]}
    store = TaskStore()
    try:
        with scope.bind_scope(space, w.ids["reem"]):
            store.persist_task(task)
    finally:
        store.close()
    log = pipeline_logger.get_pipeline_logger()
    log.log_output(task.id, "writer", "writer", f"answer {mark}")
    log.log_step(task.id, "orchestrator", "pipeline", "info", "pipeline_complete", mark)
    return task.id


def _logged(task_id: str) -> int:
    return len(pipeline_logger.get_pipeline_logger().query_by_correlation(task_id))


def _task_there(task_id: str) -> bool:
    from kazma_core.swarm.task_store import TaskStore

    store = TaskStore()
    try:
        return store.get_task(task_id) is not None
    finally:
        store.close()


def test_a_take_out_takes_its_tasks_log(world):
    w, i = world, world.ids
    session, thread = w.chat("reem", i["family"], mark="log")
    _other, other = w.chat("reem", i["family"], mark="other")
    mine, hers = _task(w, i["family"], thread, "mine"), _task(w, i["family"], other, "hers")
    assert _logged(mine) == 2
    w.moved("reem", i["family"], session, i["mine"])
    assert _logged(mine) == 0 and not _task_there(mine)
    assert _logged(hers) == 2 and _task_there(hers)


def test_a_log_that_cannot_be_written_keeps_the_task_to_be_tried_again(world, monkeypatch):
    w, i = world, world.ids
    session, thread = w.chat("reem", i["family"], mark="fail")
    task = _task(w, i["family"], thread, "fail")
    real = pipeline_logger.forget_pipeline_logs

    def locked(_task_id):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(pipeline_logger, "forget_pipeline_logs", locked)
    w.move("reem", i["family"], session, i["mine"])
    assert _task_there(task), "its row is what the log is found by"
    monkeypatch.setattr(pipeline_logger, "forget_pipeline_logs", real)
    w.settle()
    assert _logged(task) == 0 and not _task_there(task)


def test_the_research_page_s_delete_takes_the_log_too(world, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.swarm.task_store import TaskStore
    from kazma_ui.research_panel import routes

    w, i = world, world.ids
    _session, thread = w.chat("reem", i["family"], mark="page")
    task = _task(w, i["family"], thread, "page")
    store = TaskStore()
    monkeypatch.setattr(routes, "_get_store", lambda: store, raising=False)
    monkeypatch.setattr(routes, "_task_to_change", lambda task_id: (store, store.get_task(task_id), ""))
    app = FastAPI()
    app.include_router(routes.create_research_router())
    try:
        with TestClient(app) as client:
            answer = client.delete(f"/api/research/tasks/{task}")
        assert answer.status_code == 200 and answer.json()["ok"], answer.text
    finally:
        store.close()
    assert _logged(task) == 0 and not _task_there(task)


def test_no_log_is_made_to_delete_from(stores):
    from kazma_core.swarm.task_store import TaskStore

    store = TaskStore()
    try:
        assert store.delete_task("never-there") is False
    finally:
        store.close()
    assert not (stores / "pipeline_logs.db").exists()


def test_control_a_delete_of_the_row_alone_leaves_the_log(world, monkeypatch):
    monkeypatch.setattr(pipeline_logger, "forget_pipeline_logs", lambda _task_id: 0)
    w, i = world, world.ids
    session, thread = w.chat("reem", i["family"], mark="log")
    task = _task(w, i["family"], thread, "mine")
    w.moved("reem", i["family"], session, i["mine"])
    assert _logged(task) == 2 and not _task_there(task), "the fault: nothing finds it any more"
