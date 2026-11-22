"""Spaces S1-16: research sessions and research tasks follow the space.

Guards act only where Spaces is enforced; an install with one account, and
the older multi-user mode, behave as before. Enforced:

* a research session carries its space (``tenant_id``, always stamped); a
  space's list holds its own sessions, the owner's space also the sessions
  from before Spaces; another space's session answers as an unknown one on
  every route and tool that names one;
* a chat's web search is kept as one session per question per space: the
  same question asked in two spaces is two rows (the owner's keeps its ids);
* a research task (a swarm task of kind research) keeps to its space, as
  every swarm task does (S1-15);
* (the follow-up) shared research is read by every member and changed by its
  author and the space's owner (N32); a chat search's id is unambiguous and
  a row of another space is a miss; work bound to no space makes no session.

A research report and the other research files are the workspace folder's,
which S1-19 makes the space's; their routes and tools wait for it there.

Each guard is taken out as a control, and the control shows what it stops:
the cases that rest on it fail. The route and tool cases are in
``tests/_research_space_cases.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from kazma_core.spaces import scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

from tests._research_space_cases import ITEM
from tests._space_matrix import Matrix, load_cases

CASES = load_cases()

ROUTES = (
    "DELETE /api/research/sessions/{session_id}", "DELETE /api/research/tasks/{task_id}",
    "GET /api/research/sessions", "GET /api/research/sessions/{session_id}",
    "GET /api/research/sessions/{session_id}/stream", "GET /api/research/tasks",
    "GET /api/research/tasks/{task_id}", "GET /research", "POST /api/research/compare",
    "POST /api/research/sessions", "POST /api/research/sessions/{session_id}/archive",
    "POST /api/research/sessions/{session_id}/cancel", "POST /api/research/sessions/{session_id}/export",
    "POST /api/research/sessions/{session_id}/resume", "POST /api/research/sessions/{session_id}/unarchive",
    "POST /api/research/tasks/{task_id}/archive", "POST /api/research/tasks/{task_id}/unarchive",
    "POST /api/research/{task_id}/export",
)
TOOLS = ("tool:cancel_research_session", "tool:research_session_status", "tool:start_deep_research",
         "tool:web_search", "tool:web_search_duckduckgo")


@pytest.fixture
def matrix(monkeypatch) -> Matrix:
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    with Matrix() as made:
        yield made


def _fails(target: str, matrix: Matrix) -> None:
    """The case of *target* fails on one of its own assertions."""
    with pytest.raises((AssertionError, pytest.fail.Exception)):
        CASES[target].run(matrix)


def test_the_item_wrote_a_case_for_every_research_route_and_tool() -> None:
    mine = sorted(target for target, registered in CASES.items() if registered.item == ITEM)
    assert mine == sorted([*ROUTES, *TOOLS])


# ── each guard taken out ─────────────────────────────────────────────────


@pytest.mark.parametrize("target", (
    "GET /api/research/sessions", "tool:research_session_status", "tool:start_deep_research",
))
def test_control_the_unfiltered_list(target, matrix, monkeypatch) -> None:
    """The plan's control: the session list with no space filter (and the
    lookup as it read before), as before S1-16."""
    from kazma_core.tools import research_session

    monkeypatch.setattr(research_session, "_spaces_shown", lambda: None)
    _fails(target, matrix)


@pytest.mark.parametrize("target", (
    "GET /api/research/sessions/{session_id}", "GET /api/research/sessions/{session_id}/stream",
    "DELETE /api/research/sessions/{session_id}", "POST /api/research/sessions/{session_id}/archive",
    "POST /api/research/sessions/{session_id}/unarchive", "POST /api/research/sessions/{session_id}/cancel",
    "POST /api/research/sessions/{session_id}/export", "POST /api/research/sessions/{session_id}/resume",
    "tool:cancel_research_session",
))
def test_control_a_session_of_any_space_is_found(target, matrix, monkeypatch) -> None:
    """``session_here`` reading as ``get_session`` did: any space's session."""
    from kazma_core.tools import research_session

    monkeypatch.setattr(research_session, "session_here", research_session.get_session)
    _fails(target, matrix)


@pytest.mark.parametrize("target", (
    "GET /api/research/tasks/{task_id}", "POST /api/research/compare", "POST /api/research/{task_id}/export",
    "POST /api/research/tasks/{task_id}/archive", "POST /api/research/tasks/{task_id}/unarchive",
    "DELETE /api/research/tasks/{task_id}",
))
def test_control_a_research_task_of_any_space_is_found(target, matrix, monkeypatch) -> None:
    import kazma_core.swarm.single_operator as single_operator

    monkeypatch.setattr(single_operator, "task_of_this_space", lambda task: True)
    _fails(target, matrix)


def test_control_the_task_list_with_no_space_filter(matrix, monkeypatch) -> None:
    from kazma_core.swarm.task_store import TaskStore

    every = TaskStore.list_tasks

    def as_before(self, **kwargs):
        return every(self, **{**kwargs, "metadata_filter": {**(kwargs.get("metadata_filter") or {}),
                                                            "tenant_id": "*"}})

    monkeypatch.setattr(TaskStore, "list_tasks", as_before)
    _fails("GET /api/research/tasks", matrix)


@pytest.mark.parametrize("target", ("tool:web_search", "tool:web_search_duckduckgo"))
def test_control_one_search_row_for_every_space(target, matrix, monkeypatch) -> None:
    """The chat search's row named by its question alone, and a row of
    another space under that id written as one's own (both guards of the
    follow-up taken out): the same question asked in two spaces is one row,
    and B's search is written into A's."""
    import hashlib

    from kazma_core.tools import research_session

    monkeypatch.setattr(research_session, "_chat_search_key",
                        lambda topic, *_who: hashlib.md5(topic.encode(), usedforsecurity=False).hexdigest()[:12])
    monkeypatch.setattr(research_session, "_row_is_this_spaces", lambda row, space: True)
    _fails(target, matrix)


# ── the stamp, and an install that does not enforce Spaces ──────────────


@pytest.fixture
def private_sessions(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))


def test_a_session_is_stamped_with_its_space_whatever_the_mode(private_sessions, matrix) -> None:
    from kazma_core.tools.research_session import create_session

    with scope.bind_scope(Matrix.SPACE_A, matrix.account_a):
        made = create_session("a topic")
    assert made.tenant_id == Matrix.SPACE_A  # stamped though Spaces is not enforced


def test_not_enforced_every_session_is_listed_as_before(private_sessions, matrix) -> None:
    from kazma_core.tools.research_session import create_session, list_sessions, session_here

    with scope.bind_scope(Matrix.SPACE_A, matrix.account_a):
        mine = create_session("mine")
    with scope.bind_scope(Matrix.SPACE_B, matrix.account_b):
        theirs = create_session("theirs")
    listed = {s.id for s in list_sessions(limit=10)}
    assert {mine.id, theirs.id} <= listed
    with scope.bind_scope(Matrix.SPACE_A, matrix.account_a):
        assert session_here(theirs.id) is not None


def test_enforced_the_owners_space_holds_the_sessions_from_before(private_sessions, matrix,
                                                                   monkeypatch) -> None:
    """A session from before Spaces has no tenant: it is the owner's."""
    from kazma_core.tools.research_session import create_session, list_sessions, session_here

    before = create_session("from before Spaces")  # nothing bound: no tenant
    assert before.tenant_id == ""
    monkeypatch.setattr(scope, "_enforced", True)
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        assert before.id in {s.id for s in list_sessions(limit=10)}
        assert session_here(before.id) is not None
    with scope.bind_scope(Matrix.SPACE_A, matrix.account_a):
        assert before.id not in {s.id for s in list_sessions(limit=10)}
        assert session_here(before.id) is None


def test_the_owners_chat_search_keeps_the_ids_it_had() -> None:
    import hashlib

    from kazma_core.tools.research_session import _chat_search_key

    old = hashlib.md5(b"a question", usedforsecurity=False).hexdigest()[:12]
    assert _chat_search_key("a question", "") == old
    assert _chat_search_key("a question", OWNER_SPACE_ID) == old
    assert _chat_search_key("a question", OWNER_SPACE_ID, OWNER_ACCOUNT_ID) == old
    assert _chat_search_key("a question", Matrix.SPACE_A) != old


# ── the follow-up: shared research is read by every member, changed by two ──
#
# Decision N32: in a shared space the author of a research session or task
# and the space's owner may change it; another member reads it.

SHARED = "matrix-research-shared"


@pytest.fixture
def shared(matrix) -> str:
    """A space person A made (its owner) and person B joined."""
    from kazma_core.spaces import identity

    identity.create_space("Shared research", created_by=matrix.account_a, space_id=SHARED)
    identity.add_member(SHARED, matrix.account_b)
    return SHARED


def _in_shared(w, who: str, method: str, path: str, **kwargs):
    from kazma_ui import space_binding

    return w.client.request(method, path, headers={"x-matrix-person": who, space_binding.SPACE_HEADER: SHARED},
                            **kwargs)


def _shared_session(w, who: str, mark: str):
    from kazma_core.tools.research_session import create_session, update_session

    with scope.bind_scope(SHARED, w.account_of(who)):
        made = create_session(f"topic {mark}", depth="deep")
    update_session(made.id, status="done", stage="complete", message="Done", summary=f"summary {mark}")
    return made


_CHANGES = (("POST", "/api/research/sessions/{}/archive"), ("POST", "/api/research/sessions/{}/unarchive"),
            ("POST", "/api/research/sessions/{}/cancel"), ("POST", "/api/research/sessions/{}/resume"),
            ("DELETE", "/api/research/sessions/{}"))


def _member_changes(matrix) -> tuple[dict[str, int], int, dict, dict]:
    """B's every change to A's session in the shared space, B's read of it,
    the session as it was and as it is after B's changes."""
    from tests._research_space_cases import world

    with world(matrix) as w:
        authored = _shared_session(w, "a", matrix.plant("sharedsession").a)
        before = w.held_session(authored)
        read = _in_shared(w, "b", "GET", f"/api/research/sessions/{authored.id}").status_code
        answers = {f"{m} {p}": _in_shared(w, "b", m, p.format(authored.id)).status_code for m, p in _CHANGES}
        return answers, read, before, w.held_session(authored)


def test_in_a_shared_space_a_member_reads_shared_research_and_changes_none(matrix, shared) -> None:
    answers, read, before, after = _member_changes(matrix)
    assert read == 200
    assert set(answers.values()) == {403}, answers
    assert after == before


def test_control_any_member_changes_shared_research(matrix, shared, monkeypatch) -> None:
    import kazma_core.spaces.shared_work as shared_work

    monkeypatch.setattr(shared_work, "may_change", lambda space_id, author: True)
    answers, _read, _before, _after = _member_changes(matrix)
    assert 403 not in answers.values(), answers  # B archived, cancelled... A's session


def test_the_author_and_the_spaces_owner_change_shared_research(matrix, shared) -> None:
    from tests._research_space_cases import world

    with world(matrix) as w:
        bs = _shared_session(w, "b", matrix.plant("authorb").b)
        assert _in_shared(w, "b", "POST", f"/api/research/sessions/{bs.id}/archive").status_code == 200
        assert _in_shared(w, "a", "POST", f"/api/research/sessions/{bs.id}/unarchive").status_code == 200
        assert _in_shared(w, "a", "DELETE", f"/api/research/sessions/{bs.id}").status_code == 200
        assert w.held_session(bs) == {}


def test_the_research_tools_refuse_a_member_who_may_only_read(matrix, shared) -> None:
    from tests._chat_space_cases import _registry
    from tests._research_space_cases import world

    with world(matrix) as w:
        authored = _shared_session(w, "a", matrix.plant("sharedtool").a)
        account_b = w.account_of("b")

        def as_b(name: str, **kwargs) -> str:
            tool = _registry().get_tool(name)

            async def run() -> str:
                with scope.bind_scope(SHARED, account_b):
                    return str(await tool.func(**kwargs))

            return w.client.portal.call(run)

        assert '"not_yours"' in as_b("cancel_research_session", session_id=authored.id)
        assert '"not_yours"' in as_b("start_deep_research", resume_session_id=authored.id)
        assert authored.id in as_b("research_session_status", session_id=authored.id)  # reads it


def _shared_task(w, who: str, mark: str):
    from kazma_core.swarm.task import SwarmTask, TaskResult, TaskStatus

    task = SwarmTask(prompt=f"research {mark}", status=TaskStatus.COMPLETED,
                     metadata={"kind": "research", "tenant_id": SHARED, "asker": w.account_of(who)},
                     completed_at="2026-11-18T08:00:00+00:00")
    task.result = TaskResult(task_id=task.id, status="success", aggregated_output=f"report {mark}")
    assert w.engine._task_store.persist_task(task), task.id
    return task


def _member_changes_a_task(matrix) -> tuple[list[int], dict, dict]:
    from tests._research_space_cases import world

    with world(matrix) as w:
        authored = _shared_task(w, "a", matrix.plant("sharedtask").a)
        before = w.held_task(authored)
        answers = [_in_shared(w, "b", "POST", f"/api/research/tasks/{authored.id}/archive").status_code,
                   _in_shared(w, "b", "POST", f"/api/research/tasks/{authored.id}/unarchive").status_code,
                   _in_shared(w, "b", "DELETE", f"/api/research/tasks/{authored.id}").status_code]
        return answers, before, w.held_task(authored)


def test_one_rule_for_a_research_tasks_archive_and_delete(matrix, shared) -> None:
    answers, before, after = _member_changes_a_task(matrix)
    assert answers == [403, 403, 403] and after == before, answers


def test_control_a_research_task_changed_by_any_member(matrix, shared, monkeypatch) -> None:
    import kazma_core.spaces.shared_work as shared_work

    monkeypatch.setattr(shared_work, "may_change", lambda space_id, author: True)
    answers, _before, after = _member_changes_a_task(matrix)
    assert 403 not in answers and after == {}, answers


def test_the_spaces_owner_changes_a_members_research_task(matrix, shared) -> None:
    from tests._research_space_cases import world

    with world(matrix) as w:
        theirs = _shared_task(w, "b", matrix.plant("ownertask").b)
        assert _in_shared(w, "a", "POST", f"/api/research/tasks/{theirs.id}/archive").status_code == 200
        assert _in_shared(w, "a", "DELETE", f"/api/research/tasks/{theirs.id}").status_code == 200
        assert w.held_task(theirs) == {}


# ── the S1-17 follow-up: one rule, by the task's kind, on both pages ─────
#
# A research task follows N32 on every route, the Swarm page's too; a task
# of another kind follows N4 on every route, the Research page's too
# (``task_actions.caller_may_act_on``).


def _plain_task(w, who: str, mark: str):
    """A swarm task of *who*'s in the shared space: not research."""
    from kazma_core.swarm.task import SwarmTask, TaskResult, TaskStatus

    task = SwarmTask(prompt=f"task {mark}", status=TaskStatus.COMPLETED,
                     metadata={"tenant_id": SHARED, "asker": w.account_of(who)},
                     completed_at="2026-11-18T08:00:00+00:00")
    task.result = TaskResult(task_id=task.id, status="success", aggregated_output=f"output {mark}")
    assert w.engine._task_store.persist_task(task), task.id
    return task


def _swarm_refusal(w, who: str, task) -> str:
    """What the Swarm page's actions (and the run supervisor's) answer *who*
    for *task*: '' when they may act on it."""
    from kazma_ui.swarm_panel.task_actions import task_refusal

    with scope.bind_scope(SHARED, w.account_of(who)):
        return (task_refusal(w.engine, task.id) or {}).get("code", "")


def _both_pages(matrix) -> dict[str, Any]:
    """The space's owner (A) on B's research task through the Swarm page, a
    member (B) on A's; the owner on B's swarm task through the Research
    page."""
    from tests._research_space_cases import world

    with world(matrix) as w:
        research_b = _shared_task(w, "b", matrix.plant("kindresearch").b)
        research_a = _shared_task(w, "a", matrix.plant("kindmine").a)
        plain_b = _plain_task(w, "b", matrix.plant("kindplain").b)
        before = w.held_task(plain_b)
        answers = {
            "swarm: owner on a member's research": _swarm_refusal(w, "a", research_b),
            "swarm: member on the owner's research": _swarm_refusal(w, "b", research_a),
            "research: owner archives a member's task": _in_shared(
                w, "a", "POST", f"/api/research/tasks/{plain_b.id}/archive").status_code,
            "research: owner deletes a member's task": _in_shared(
                w, "a", "DELETE", f"/api/research/tasks/{plain_b.id}").status_code,
        }
        answers["the member's task as it was"] = w.held_task(plain_b) == before
        return answers


def test_a_task_follows_its_kinds_rule_on_both_pages(matrix, shared) -> None:
    assert _both_pages(matrix) == {
        "swarm: owner on a member's research": "",
        "swarm: member on the owner's research": "not_yours",
        "research: owner archives a member's task": 403,
        "research: owner deletes a member's task": 403,
        "the member's task as it was": True,
    }


def test_control_each_page_with_its_own_rule_as_before(matrix, shared, monkeypatch) -> None:
    """The two route families as before: the Swarm page held every task to
    N4 (the owner could not cancel a member's research), the Research page
    every task to N32 (the owner archived and deleted a member's swarm
    task)."""
    import kazma_ui.swarm_panel.task_actions as task_actions
    from kazma_core.spaces.shared_work import may_change
    from kazma_core.swarm.single_operator import task_answered_by_caller

    def by_page(task: Any, *, answering: bool = False) -> bool:
        import inspect

        on_the_research_page = any("research_panel" in frame.filename for frame in inspect.stack())
        if on_the_research_page:
            meta = task.metadata or {}
            return may_change(str(meta.get("tenant_id") or ""), meta.get("asker"))
        return task_answered_by_caller(task)

    monkeypatch.setattr(task_actions, "caller_may_act_on", by_page)
    answers = _both_pages(matrix)
    assert answers["swarm: owner on a member's research"] == "not_yours"
    assert answers["research: owner archives a member's task"] == 200
    assert answers["the member's task as it was"] is False


# ── the follow-up: a chat search's id, the author, and no space ──────────


def test_no_topic_of_the_owners_shares_an_id_with_another_spaces_search() -> None:
    """The review's collision: the owner's key was the bare topic and another
    space's ``space|topic``, so the owner's search of "<B>|T" and B's "T"
    were one row."""
    import hashlib

    from kazma_core.tools.research_session import _chat_search_key

    def as_s1_16(topic: str, space: str) -> str:
        key = topic if space in ("", OWNER_SPACE_ID) else f"{space}|{topic}"
        return hashlib.md5(key.encode(), usedforsecurity=False).hexdigest()[:12]

    crafted = f"{Matrix.SPACE_B}|T"
    assert as_s1_16(crafted, OWNER_SPACE_ID) == as_s1_16("T", Matrix.SPACE_B)  # the control
    assert _chat_search_key(crafted, OWNER_SPACE_ID) != _chat_search_key("T", Matrix.SPACE_B, "acct-b")
    assert _chat_search_key("T", Matrix.SPACE_B, "acct-b").startswith("s")  # never 12 hex digits


def test_two_members_of_a_shared_space_keep_their_own_search(private_sessions, matrix, monkeypatch) -> None:
    from kazma_core.tools.research_session import get_session, record_chat_research

    monkeypatch.setattr(scope, "_enforced", True)
    with scope.bind_scope(SHARED, matrix.account_a):
        mine = record_chat_research("the same question", result_text="person A's results")
    with scope.bind_scope(SHARED, matrix.account_b):
        theirs = record_chat_research("the same question", result_text="person B's longer results here")
    assert mine.id != theirs.id
    assert get_session(mine.id).summary == "person A's results"
    assert (mine.asker, theirs.asker) == (matrix.account_a, matrix.account_b)


def _a_search_over_another_spaces_row(monkeypatch):
    from kazma_core.tools.research_session import get_session, record_chat_research

    monkeypatch.setattr(scope, "_enforced", True)
    with scope.bind_scope(Matrix.SPACE_B, "acct-b"):
        record_chat_research("t", session_id="rs_chat_shared", result_text="B's row")
    with scope.bind_scope(Matrix.SPACE_A, "acct-a"):
        written = record_chat_research("t", session_id="rs_chat_shared", result_text="A's longer results")
    return written, get_session("rs_chat_shared")


def test_a_row_of_another_space_under_the_id_is_a_miss(private_sessions, monkeypatch) -> None:
    written, b_row = _a_search_over_another_spaces_row(monkeypatch)
    assert written.id != "rs_chat_shared" and written.tenant_id == Matrix.SPACE_A
    assert b_row.summary == "B's row" and b_row.tenant_id == Matrix.SPACE_B


def test_control_a_row_of_another_space_written_as_ones_own(private_sessions, monkeypatch) -> None:
    from kazma_core.tools import research_session

    monkeypatch.setattr(research_session, "_row_is_this_spaces", lambda row, space: True)
    written, b_row = _a_search_over_another_spaces_row(monkeypatch)
    assert written.id == "rs_chat_shared" and b_row.summary == "A's longer results"


def test_a_session_records_its_author_whatever_the_mode(private_sessions, matrix) -> None:
    from kazma_core.tools.research_session import create_session, record_chat_research

    with scope.bind_scope(Matrix.SPACE_A, matrix.account_a):
        assert create_session("a topic").asker == matrix.account_a
        assert record_chat_research("a search").asker == matrix.account_a
    assert create_session("nobody's").asker == ""


def test_once_enforced_work_bound_to_no_space_makes_no_session(private_sessions, monkeypatch) -> None:
    from kazma_core.tools.research_session import create_session, list_sessions, record_chat_research

    monkeypatch.setattr(scope, "_enforced", True)
    with pytest.raises(scope.NoSpaceBound):
        create_session("for no one")
    with pytest.raises(scope.NoSpaceBound):
        record_chat_research("for no one")
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        assert list_sessions(limit=10) == []  # nothing became the owner's


def test_control_a_session_for_no_one_became_the_owners(private_sessions, monkeypatch) -> None:
    from kazma_core.tenant_context import get_current_tenant_id
    from kazma_core.tools import research_session

    def as_s1_16() -> str:
        bound = scope.current_scope()
        return bound.space_id if bound is not None else (get_current_tenant_id() or "")

    monkeypatch.setattr(scope, "_enforced", True)
    monkeypatch.setattr(research_session, "_session_space", as_s1_16)
    made = research_session.create_session("for no one")
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        assert made.id in {s.id for s in research_session.list_sessions(limit=10)}


def test_not_enforced_a_session_for_no_one_is_made_as_before(private_sessions) -> None:
    from kazma_core.tools.research_session import create_session

    assert create_session("as before").tenant_id == ""
