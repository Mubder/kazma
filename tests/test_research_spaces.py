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
  every swarm task does (S1-15).

A research report and the other research files are the workspace folder's,
which S1-19 makes the space's; their routes and tools wait for it there.

Each guard is taken out as a control, and the control shows what it stops:
the cases that rest on it fail. The route and tool cases are in
``tests/_research_space_cases.py``.
"""

from __future__ import annotations

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
    """The chat search's row named by its question alone: the same question
    asked in two spaces is one row, and B's search is written into A's."""
    import hashlib

    from kazma_core.tools import research_session

    monkeypatch.setattr(research_session, "_chat_search_key",
                        lambda topic, space: hashlib.md5(topic.encode(), usedforsecurity=False).hexdigest()[:12])
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
    assert _chat_search_key("a question", Matrix.SPACE_A) != old
