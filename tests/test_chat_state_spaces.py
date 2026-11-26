"""Chat state follows the space, and messages name their author (Spaces S1-08).

The matrix (``tests/_chat_space_cases.py``, run by ``tests/test_space_matrix.py``)
drives every chat route and chat tool as a member of one space against the
ids of another. This file holds what the matrix does not:

* **negative controls**: each guard is taken out and the matrix case that
  rests on it must fail -- a case that passes without its guard checks
  nothing;
* **the checkpoint store on one shared table** (Postgres's layout: one table,
  keyed by thread, for every space): the thread's record is what keeps two
  spaces apart there. Run everywhere on a stand-in (a SQLite file used as
  the one table), and on a real Postgres where one is given
  (``@pytest.mark.postgres``). Control: without the thread guard the shared
  table reads across;
* **messages name their author**;
* **an install that does not enforce Spaces behaves as before**: no guard
  refuses, names and rooms are the ones it always had.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from kazma_core.spaces import identity, scope, threads
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

from tests import _tt_graph as tt
from tests._chat_space_cases import ITEM, World, recorded_space, world
from tests._space_matrix import Matrix, load_cases

CASES = load_cases()

#: Every route and tool S1-08 wrote a case for.
ROUTES = (
    "DELETE /api/chat/sessions/{session_id}", "DELETE /api/replay/threads/{thread_id}",
    "DELETE /api/sessions/{thread_id}", "GET /api/chat/capacity", "GET /api/chat/sessions",
    "GET /api/chat/sessions/archived", "GET /api/chat/sessions/{session_id}/messages",
    "GET /api/chat/sessions/{session_id}/status", "GET /api/replay/snapshots/{thread_id}",
    "GET /api/replay/snapshots/{thread_id}/{snapshot_id}", "GET /api/replay/threads",
    "GET /api/sessions", "GET /chat", "GET /replay", "PATCH /api/chat/sessions/{session_id}",
    "POST /api/a2a", "POST /api/chat/abort", "POST /api/chat/sessions",
    "POST /api/chat/sessions/{session_id}/archive", "POST /api/chat/sessions/{session_id}/pin",
    "POST /api/chat/sessions/{session_id}/unarchive", "POST /api/chat/sessions/{session_id}/unpin",
    "POST /api/chat/steer", "POST /api/chat/stop", "POST /api/chat/stream",
    "POST /api/replay/compare", "POST /api/replay/fork", "POST /api/replay/restore",
    "POST /api/sessions/clear-all", "POST /api/voice/livekit/token", "POST /api/voice/stt",
    "POST /api/voice/tts", "WEBSOCKET /ws/chat/{session_id}", "WEBSOCKET /ws/voice",
)
TOOLS = (
    "tool:context_info", "tool:discard_proposal", "tool:export_session", "tool:list_proposals",
    "tool:save_proposal", "tool:task_ledger_update", "tool:update_scratchpad",
)


@pytest.fixture
def matrix(monkeypatch, request) -> Matrix:
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    with Matrix() as made:
        # A test marked postgres keeps its checkpoints on the real Postgres;
        # every other one in its world's SQLite file (``world``).
        made.database = "postgres" if request.node.get_closest_marker("postgres") else "sqlite"
        yield made


@pytest.fixture
def enforced(monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", True)


def _fails(target: str, matrix: Matrix) -> None:
    """The case of *target* fails on one of its own assertions: its guard is
    out, and the case saw another space's data shown or changed. A case that
    stops for any other reason (a KeyError, a TypeError: a broken stand-in)
    proves nothing, and is an error here. ``pytest.fail.Exception`` is the
    case's own "the refusal did not come"."""
    with pytest.raises((AssertionError, pytest.fail.Exception)):
        CASES[target].run(matrix)


def test_the_item_wrote_a_case_for_every_chat_route_and_tool() -> None:
    mine = sorted(target for target, registered in CASES.items() if registered.item == ITEM)
    assert mine == sorted([*ROUTES, *TOOLS])


# ── negative controls: a guard out, its case fails ───────────────────────


def test_control_a_chat_store_with_one_space_shows_every_chat(matrix, monkeypatch) -> None:
    """The chat store kept every chat under one key (the fallback this item
    removed): the list shows another space's chats."""
    from kazma_ui import session_manager

    CASES["GET /api/chat/sessions"].run(matrix)
    monkeypatch.setattr(session_manager, "_space", lambda: OWNER_SPACE_ID)
    _fails("GET /api/chat/sessions", matrix)


def test_control_without_the_refusal_a_chat_delete_takes_anothers_steps(matrix, monkeypatch) -> None:
    """Time Travel steps are kept by id alone. A delete of an id the space's
    store does not hold went on to delete the steps saved under it."""
    from kazma_ui import sse_chat

    CASES["DELETE /api/chat/sessions/{session_id}"].run(matrix)
    monkeypatch.setattr(sse_chat, "_not_this_spaces_chat", lambda session_id: False)
    _fails("DELETE /api/chat/sessions/{session_id}", matrix)


def test_control_without_the_plan_guard_a_plan_is_read_and_replaced(matrix, monkeypatch) -> None:
    from kazma_core.agent import task_ledger

    CASES["tool:task_ledger_update"].run(matrix)
    monkeypatch.setattr(task_ledger, "thread_refusal", lambda thread_id: None)
    monkeypatch.setattr(task_ledger, "require_thread", lambda thread_id: None)
    _fails("tool:task_ledger_update", matrix)


def test_control_a_task_store_with_no_space_answers_anothers_task(matrix, monkeypatch) -> None:
    from kazma_ui import a2a_api

    CASES["POST /api/a2a"].run(matrix)
    monkeypatch.setattr(a2a_api._Store, "task_of",
                        lambda self, task_id, space: self.tasks.get(str(task_id or "")))
    _fails("POST /api/a2a", matrix)


def test_control_a_room_named_by_the_chat_alone_is_shared(matrix, monkeypatch) -> None:
    from kazma_ui import routes_voice

    CASES["POST /api/voice/livekit/token"].run(matrix)
    monkeypatch.setattr(routes_voice, "_livekit_room",
                        lambda session_id, sanitize: f"kazma-{sanitize(session_id)}")
    _fails("POST /api/voice/livekit/token", matrix)


def test_control_a_socket_bound_by_its_sign_in_alone(matrix, monkeypatch) -> None:
    """The handshake as it was: the sign-in's tenant, no space named, no
    membership checked. A member's own chat is then out of reach, and a
    socket that names another's space is not told so."""
    from kazma_core.tenant_isolation import principal_tenant_id
    from kazma_ui import space_binding

    CASES["WEBSOCKET /ws/chat/{session_id}"].run(matrix)
    monkeypatch.setattr(
        space_binding, "space_for_socket",
        lambda principal, websocket: space_binding.Binding(
            space_id=principal_tenant_id(principal) or OWNER_SPACE_ID, asker=None),
    )
    _fails("WEBSOCKET /ws/chat/{session_id}", matrix)
    _fails("WEBSOCKET /ws/voice", matrix)


# ── restore and fork, called as the chat apps call them (no route check) ─


def _fork(w: World, who: str, thread_id: str, snapshot_id: str) -> dict[str, Any]:
    from kazma_ui.thread_rewind import fork_thread

    async def run() -> dict[str, Any]:
        with w.as_(who):
            return await fork_thread(w.graph, thread_id, snapshot_id, actor="test",
                                     surface="test", recorder=w.recorder)

    return tt.run(run())


def test_a_fork_or_restore_of_another_spaces_chat_is_refused(matrix) -> None:
    from kazma_ui.thread_rewind import RewindRefused, rewind_thread

    with world(matrix) as w:
        mine, theirs = w.pair("direct")
        their_step = w.step(theirs)
        with pytest.raises(RewindRefused) as refused:
            _fork(w, "a", theirs.thread_id, their_step)
        assert refused.value.status == 404
        # No copy of B's chat was made in A's space.
        assert theirs.mark not in w.call("a", "GET", "/api/chat/sessions").text

        async def restore() -> Any:
            with w.as_("a"):
                return await rewind_thread(w.graph, theirs.thread_id, their_step,
                                           actor="test", surface="test", recorder=w.recorder)

        with pytest.raises(RewindRefused) as refused:
            tt.run(restore())
        assert refused.value.status == 404
        # A's own fork is a chat of A's space from its first write.
        forked = _fork(w, "a", mine.thread_id, w.step(mine))
        assert recorded_space(forked["new_thread_id"]) == Matrix.SPACE_A


def _restore(w: World, who: str, thread_id: str, snapshot_id: str) -> dict[str, Any]:
    from kazma_ui.thread_rewind import rewind_thread

    async def run() -> dict[str, Any]:
        with w.as_(who):
            return await rewind_thread(w.graph, thread_id, snapshot_id, actor="test",
                                       surface="test", recorder=w.recorder)

    return tt.run(run())


def _no_rewind_guard(monkeypatch) -> None:
    from kazma_ui import thread_rewind

    monkeypatch.setattr(thread_rewind, "_in_this_space", lambda thread_id, new_thread_id=None: None)
    # The service's per-person guard (the stage 3 review) is part of it too.
    monkeypatch.setattr(thread_rewind, "_theirs_to_change", lambda thread_id: None)


def test_the_checkpoint_store_refuses_a_fork_and_a_restore_the_rewind_guard_let_through(
    matrix, monkeypatch,
) -> None:
    """Time Travel writes through the product's own ``CheckpointManager``
    (the world's graph is built on it). With the rewind service's guard out,
    the manager's thread guard is what a fork or a restore of another
    space's chat meets: nothing is written."""
    _no_rewind_guard(monkeypatch)
    with world(matrix) as w:
        _mine, theirs = w.pair("second-line")
        w.checkpoint("b", theirs)
        their_step = w.step(theirs)
        with pytest.raises(threads.ThreadInAnotherSpace):
            _fork(w, "a", theirs.thread_id, their_step)
        with pytest.raises(threads.ThreadInAnotherSpace):
            _restore(w, "a", theirs.thread_id, their_step)
        # B's thread holds what it held; no chat of A's shows B's text.
        state = tt.run(w.graph.aget_state(tt.config(theirs.thread_id)))
        assert theirs.mark not in str(state.values)
        assert theirs.mark not in w.call("a", "GET", "/api/chat/sessions").text


def test_control_with_neither_guard_a_fork_copies_anothers_chat(matrix, monkeypatch) -> None:
    """Both guards out (the rewind service's, and the checkpoint store's):
    the fork goes through and holds another space's text."""
    _no_rewind_guard(monkeypatch)
    _no_thread_guard(monkeypatch)
    with world(matrix) as w:
        _mine, theirs = w.pair("copied")
        forked = _fork(w, "a", theirs.thread_id, w.step(theirs))
        state = tt.run(w.graph.aget_state(tt.config(forked["new_thread_id"])))
        assert theirs.mark in str(state.values)


# ── a fork that cannot be recorded in a space is refused, never a 500 ────


def test_a_fork_whose_new_chat_cannot_be_recorded_is_refused(matrix, monkeypatch, caplog) -> None:
    """The fork records its new chat's space before it writes anything. A
    record store that cannot be written is a refusal with its own code and a
    WARNING; it was a raw ``sqlite3.Error`` (a 500)."""
    import sqlite3

    from kazma_ui.thread_rewind import RewindRefused

    def _cannot(thread_id: str, **_: Any) -> None:
        raise sqlite3.OperationalError("unable to open database file")

    with world(matrix) as w:
        mine, _theirs = w.pair("unrecorded")
        step = w.step(mine)
        before = w.call("a", "GET", "/api/chat/sessions").text
        monkeypatch.setattr(threads, "record_new_thread", _cannot)
        with caplog.at_level("WARNING", logger="kazma_ui.thread_rewind"):
            with pytest.raises(RewindRefused) as refused:
                _fork(w, "a", mine.thread_id, step)
        assert refused.value.code == "thread_record_unavailable" and refused.value.status == 503
        assert any("could not be read or written" in r.getMessage() for r in caplog.records)
        answered = w.call("a", "POST", "/api/replay/fork",
                          json={"thread_id": mine.thread_id, "snapshot_id": step})
        assert answered.status_code == 503, answered.text
        assert answered.json()["error"] == "thread_record_unavailable"
        # Nothing was forked.
        assert w.call("a", "GET", "/api/chat/sessions").text == before


def test_a_restore_that_cannot_ask_the_record_store_is_refused(matrix, monkeypatch) -> None:
    import sqlite3

    from kazma_ui.thread_rewind import RewindRefused

    def _cannot(thread_id: str, **_: Any) -> None:
        raise sqlite3.OperationalError("database is locked")

    with world(matrix) as w:
        mine, _theirs = w.pair("unasked")
        step = w.step(mine)
        monkeypatch.setattr(threads, "thread_refusal", _cannot)
        with pytest.raises(RewindRefused) as refused:
            _restore(w, "a", mine.thread_id, step)
        assert refused.value.code == "thread_record_unavailable"


def test_a_fork_bound_to_no_space_is_refused_once_spaces_is_enforced(matrix) -> None:
    """A chat app's /fork binds no scope. With Spaces enforced the new chat
    would be nobody's: refused as a rewind refusal (it was ``NoSpaceBound``,
    which the chat apps answered as an unknown error)."""
    from kazma_ui.thread_rewind import RewindRefused, fork_thread

    with world(matrix) as w:
        mine, _theirs = w.pair("unbound")
        step = w.step(mine)

        async def unbound() -> Any:
            return await fork_thread(w.graph, mine.thread_id, step, actor="telegram:1",
                                     surface="telegram", recorder=w.recorder,
                                     new_thread_id="gw-telegram-1-unbound")

        with pytest.raises(RewindRefused) as refused:
            tt.run(unbound())
        assert refused.value.code == "no_space" and refused.value.status == 403
        assert recorded_space("gw-telegram-1-unbound") is None


def test_control_the_old_guard_raised_what_the_record_store_raised(matrix, monkeypatch) -> None:
    """The guard as S1-08 first wrote it: the record store's own error, and
    ``NoSpaceBound``, reached the caller."""
    import sqlite3

    from kazma_ui import thread_rewind

    def _old(thread_id: str, new_thread_id: str | None = None) -> None:
        if threads.thread_refusal(thread_id):
            raise thread_rewind.RewindRefused("not_found", "That chat was not found.", status=404)
        if new_thread_id:
            threads.record_new_thread(new_thread_id)

    def _cannot(thread_id: str, **_: Any) -> None:
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(thread_rewind, "_in_this_space", _old)
    with world(matrix) as w:
        mine, _theirs = w.pair("old-guard")
        step = w.step(mine)

        async def unbound() -> Any:
            return await thread_rewind.fork_thread(w.graph, mine.thread_id, step, actor="t",
                                                   surface="t", recorder=w.recorder)

        with pytest.raises(scope.NoSpaceBound):
            tt.run(unbound())
        monkeypatch.setattr(threads, "record_new_thread", _cannot)
        with pytest.raises(sqlite3.OperationalError):
            _fork(w, "a", mine.thread_id, step)


def test_every_rewind_refusal_has_words_on_the_chat_apps_and_the_page() -> None:
    """A refusal code the service raises is worded by the chat apps and by
    the Time Travel page (a code with no words shows as a load error)."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    service = (root / "kazma-ui/kazma_ui/thread_rewind.py").read_text(encoding="utf-8")
    codes = set(re.findall(r'RewindRefused\(\s*"([a-z_]+)"', service))
    assert {"thread_record_unavailable", "no_space", "not_found"} <= codes
    gateway = (root / "kazma-gateway/kazma_gateway/agent_handler/graph.py").read_text(encoding="utf-8")
    page = (root / "kazma-ui/kazma_ui/static/js/replay.js").read_text(encoding="utf-8")
    #: The page never forks for a chat-app sender, so it never sees this one.
    not_on_the_page = {"owner_unavailable"}
    assert sorted(c for c in codes if f'"{c}"' not in gateway) == []
    assert sorted(c for c in codes - not_on_the_page if f"'{c}'" not in page) == []


# ── the checkpoint store on one shared table ─────────────────────────────

_SHARED_TABLE_CASES = (
    "GET /api/sessions", "DELETE /api/sessions/{thread_id}", "POST /api/sessions/clear-all",
    "DELETE /api/chat/sessions/{session_id}",
)


def _one_table(monkeypatch) -> None:
    """Every space's checkpoints in one table keyed by thread, as on Postgres."""
    from kazma_gateway.stores.checkpoint import CheckpointManager

    monkeypatch.setattr(CheckpointManager, "_shared_table", lambda self: True)


def _no_thread_guard(monkeypatch) -> None:
    """The store as a shared table alone would behave: whoever asks reads
    and writes the thread they name."""
    from kazma_gateway.stores.checkpoint import CheckpointManager

    async def _opened(self: Any, thread_id: Any, *, write: bool = False) -> Any:
        return self._saver, None

    monkeypatch.setattr(CheckpointManager, "_opened", _opened)
    monkeypatch.setattr(CheckpointManager, "_filters_by_thread", lambda self: False)


@pytest.mark.parametrize("target", _SHARED_TABLE_CASES)
def test_on_one_shared_table_the_threads_record_keeps_spaces_apart(target, matrix, monkeypatch) -> None:
    _one_table(monkeypatch)
    CASES[target].run(matrix)


def test_on_one_shared_table_a_named_thread_is_not_read_or_written(matrix, monkeypatch) -> None:
    _one_table(monkeypatch)
    with world(matrix) as w:
        mine, theirs = w.pair("named")
        w.checkpoint("a", mine)
        w.checkpoint("b", theirs)
        assert w.checkpoints("a", mine) and w.checkpoints("b", theirs)
        assert not w.checkpoints("a", theirs) and not w.checkpoints("b", mine)
        with pytest.raises(threads.ThreadInAnotherSpace):
            w.call("a", "POST", "/_matrix/checkpoint", json={"thread_id": theirs.thread_id})


@pytest.mark.parametrize("target", ("GET /api/sessions", "POST /api/sessions/clear-all"))
def test_control_the_shared_table_reads_across(target, matrix, monkeypatch) -> None:
    _one_table(monkeypatch)
    _no_thread_guard(monkeypatch)
    _fails(target, matrix)


def test_control_the_shared_table_answers_a_named_thread(matrix, monkeypatch) -> None:
    _one_table(monkeypatch)
    _no_thread_guard(monkeypatch)
    with world(matrix) as w:
        _mine, theirs = w.pair("across")
        w.checkpoint("b", theirs)
        assert w.checkpoints("a", theirs)


@pytest.mark.postgres
@pytest.mark.parametrize("target", _SHARED_TABLE_CASES)
def test_real_postgres_the_threads_record_keeps_spaces_apart(target, matrix) -> None:
    if tt.postgres_dsn() is None:
        pytest.skip("needs a real Postgres (KAZMA_DATABASE_URL + KAZMA_TEST_ALLOW_REAL_DB=1)")
    with world(matrix) as w:
        assert w.manager._shared_table(), "the world's checkpoint store is not on Postgres"
    CASES[target].run(matrix)


@pytest.mark.postgres
def test_real_postgres_control_the_shared_table_reads_across(matrix, monkeypatch) -> None:
    if tt.postgres_dsn() is None:
        pytest.skip("needs a real Postgres (KAZMA_DATABASE_URL + KAZMA_TEST_ALLOW_REAL_DB=1)")
    _no_thread_guard(monkeypatch)
    _fails("GET /api/sessions", matrix)


# ── the thread record ────────────────────────────────────────────────────


def test_a_thread_is_refused_only_to_another_space_and_only_when_enforced(matrix, monkeypatch) -> None:
    recorded = "t-recorded-in-b"
    threads.record_thread(recorded, Matrix.SPACE_B, matrix.account_b)
    with matrix.as_a():
        assert threads.thread_refusal(recorded) is None  # not enforced: as before
    monkeypatch.setattr(scope, "_enforced", True)
    with matrix.as_a():
        assert threads.thread_refusal(recorded)
        with pytest.raises(threads.ThreadInAnotherSpace):
            threads.require_thread(recorded)
        # A chat from before Spaces is the owner's (decision N19).
        assert threads.thread_refusal("t-from-before-spaces")
    with matrix.as_b():
        assert threads.thread_refusal(recorded) is None
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        assert threads.thread_refusal("t-from-before-spaces") is None
        assert threads.thread_refusal(recorded)
    # Work that bound nothing (a sweep, a start-up pass) is not a request.
    assert threads.thread_refusal(recorded) is None


def test_a_new_thread_is_recorded_for_the_bound_scope(matrix, monkeypatch) -> None:
    assert threads.record_new_thread("t-unbound-fork") is None
    assert recorded_space("t-unbound-fork") is None
    with matrix.as_a():
        made = threads.record_new_thread("t-bound-fork")
    assert made is not None and made.space_id == Matrix.SPACE_A
    monkeypatch.setattr(scope, "_enforced", True)
    with pytest.raises(scope.NoSpaceBound):
        threads.record_new_thread("t-unbound-enforced")


def test_a_steps_space_comes_from_the_state_until_spaces_is_enforced(matrix, monkeypatch) -> None:
    assert scope.space_of_state({}) == OWNER_SPACE_ID
    assert scope.space_of_state({"tenant_id": "team-x"}) == "team-x"
    with matrix.as_a():
        # Not enforced: the state's own value, as the graph always read it.
        assert scope.space_of_state({"tenant_id": "team-x"}) == "team-x"
    monkeypatch.setattr(scope, "_enforced", True)
    with matrix.as_a():
        assert scope.space_of_state({"tenant_id": "team-x"}) == Matrix.SPACE_A
    with pytest.raises(scope.NoSpaceBound):
        scope.space_of_state({})


def test_a_deleted_chats_record_stays_while_a_copy_of_the_chat_remains(matrix, monkeypatch) -> None:
    from kazma_core import time_travel
    from kazma_ui import sse_chat

    def _cannot(keys: list[str], **_: Any) -> int:
        raise time_travel.SnapshotStoreError("the step store cannot be reached")

    with world(matrix) as w:
        mine, _theirs = w.pair("leftover")
        w.step(mine)
        monkeypatch.setattr(time_travel, "forget_thread_snapshots", _cannot)
        assert w.call("a", "DELETE", f"/api/chat/sessions/{mine.session_id}").json() == {"status": "ok"}
        # The steps are still there, so the record that keeps them in A's space is too.
        assert w.steps(mine) == 1
        assert recorded_space(mine.thread_id) == Matrix.SPACE_A
    assert sse_chat._not_this_spaces_chat("an-id-nobody-holds") is False  # owner's space: as before


def test_a_deleted_chats_record_stays_when_its_checkpoints_were_not_deleted(
    matrix, monkeypatch, caplog,
) -> None:
    """The chat page's delete: no checkpoint store to ask, or one that fails,
    leaves the chat's checkpoints where they are. The record that keeps them
    in their space then stays, and the failure is a WARNING (it was DEBUG,
    and the record went)."""
    from kazma_ui import dashboard

    async def _cannot(thread_id: str) -> None:
        raise OSError("the checkpoint store cannot be reached")

    with world(matrix) as w:
        unasked, _ = w.pair("no-manager")
        failing, _ = w.pair("failing-manager")
        for chat in (unasked, failing):
            w.checkpoint("a", chat)
        manager = w.manager
        monkeypatch.setattr(dashboard, "_checkpoint_manager", None)
        assert w.call("a", "DELETE", f"/api/chat/sessions/{unasked.session_id}").json() == {"status": "ok"}
        assert recorded_space(unasked.thread_id) == Matrix.SPACE_A
        monkeypatch.setattr(dashboard, "_checkpoint_manager", manager)
        monkeypatch.setattr(manager, "adelete_thread", _cannot)
        with caplog.at_level("WARNING", logger="kazma_ui.sse_chat"):
            assert w.call("a", "DELETE", f"/api/chat/sessions/{failing.session_id}").json() == {"status": "ok"}
        assert recorded_space(failing.thread_id) == Matrix.SPACE_A
        assert any("its checkpoints were NOT" in r.getMessage() for r in caplog.records)
        monkeypatch.undo()
        # Both chats' checkpoints are still A's to read.
        assert w.checkpoints("a", unasked) and w.checkpoints("a", failing)


def test_the_dashboards_delete_keeps_the_record_when_the_chat_was_not_deleted(
    matrix, monkeypatch, caplog,
) -> None:
    from kazma_ui import dashboard

    def _cannot(thread_id: str) -> list[str]:
        raise OSError("the chat store cannot be reached")

    with world(matrix) as w:
        mine, _theirs = w.pair("chat-left")
        w.checkpoint("a", mine)
        monkeypatch.setattr(dashboard, "_delete_chats_of_thread", _cannot)
        with caplog.at_level("WARNING", logger="kazma_ui.dashboard"):
            done = w.call("a", "DELETE", f"/api/sessions/{mine.thread_id}")
        assert done.status_code == 200, done.text
        # The chat is still in A's space, and so is its record.
        assert w.stored("a", mine) is not None
        assert recorded_space(mine.thread_id) == Matrix.SPACE_A
        assert any("its chat was NOT" in r.getMessage() for r in caplog.records)


# ── the scope's space, not the tenant an older value names ───────────────


@contextmanager
def _resumed(space: str, account: str, *, older: str) -> Iterator[None]:
    """A step bound to *space* while the tenant ContextVar still names
    *older*: the shape of a turn resumed from a checkpoint that an older
    build, or the older multi-user mode, wrote under another tenant."""
    from kazma_core.tenant_context import tenant_scope

    with tenant_scope(older):
        token = scope.set_scope_only(scope.Scope(space, account))
        try:
            yield
        finally:
            scope.reset_scope_only(token)


def _drafts_and_notes_follow_the_scope(matrix: Matrix) -> None:
    from kazma_core.agent.artifacts import get_artifact_store

    from tests._chat_space_cases import _call, _thread_of, _turn

    marks = matrix.plant("resumed-draft")
    thread_a = _thread_of(matrix, "a")
    with _resumed(Matrix.SPACE_A, matrix.account_a, older=Matrix.SPACE_B), _turn(thread_a):
        _call("save_proposal", kind="notes", items=[f"draft {marks.a}"])
        _call("update_scratchpad", key="finding", finding=f"note {marks.a}")
    with matrix.as_b(), _turn(thread_a):
        theirs = _call("list_proposals") + str(get_artifact_store().list_scratchpad(thread_a))
    with matrix.as_a(), _turn(thread_a):
        mine = _call("list_proposals") + str(get_artifact_store().list_scratchpad(thread_a))
    assert marks.a not in theirs, "a draft or a note was filed under the older tenant"
    assert mine.count(marks.a) == 2


def test_a_draft_and_a_note_are_filed_in_the_bound_scopes_space(matrix, enforced) -> None:
    _drafts_and_notes_follow_the_scope(matrix)


def test_control_filed_by_the_tenant_value_a_draft_lands_in_the_older_tenant(
    matrix, enforced, monkeypatch,
) -> None:
    """The rule before S1-08: ``get_current_tenant_id() or "default"``."""
    from kazma_core.tenant_context import get_current_tenant_id

    monkeypatch.setattr(scope, "require_space_id", lambda: get_current_tenant_id() or OWNER_SPACE_ID)
    with pytest.raises(AssertionError, match="older tenant"):
        _drafts_and_notes_follow_the_scope(matrix)


def _a_receipt_follows_the_scope(matrix: Matrix) -> None:
    """Two turns reach the same call identity but for the space: the same
    thread, turn, iteration and call id, and a state that names B's tenant
    (the value an older checkpoint carries). A's step must run its own call,
    never answer with the result B's step recorded."""
    from kazma_core.agent.effect_journal import execute_effect

    marks = matrix.plant("receipt")

    class _Runs:
        def __init__(self, mark: str) -> None:
            self.mark, self.calls = mark, 0

        async def execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
            self.calls += 1
            return {"content": f"wrote {self.mark}", "error": False}

    state = {"thread_id": f"t-receipt-{secrets.token_hex(6)}", "tenant_id": Matrix.SPACE_B,
             "created_at": "turn-1", "iteration": 0}
    call = {"id": "call-1", "name": "file_write"}
    theirs, mine = _Runs(marks.b), _Runs(marks.a)

    async def both() -> tuple[dict[str, Any], dict[str, Any]]:
        with matrix.as_b():
            first = await execute_effect(theirs, dict(state), call, {"path": "x", "content": "y"})
        with _resumed(Matrix.SPACE_A, matrix.account_a, older=Matrix.SPACE_B):
            second = await execute_effect(mine, dict(state), call, {"path": "x", "content": "y"})
        return first, second

    first, second = tt.run(both())
    assert marks.b in str(first)
    assert marks.b not in str(second), "A's step was answered with the result B's step recorded"
    assert mine.calls == 1 and marks.a in str(second)


def test_a_receipt_is_the_bound_scopes_whatever_the_state_says(matrix, enforced) -> None:
    _a_receipt_follows_the_scope(matrix)


def test_control_keyed_by_the_states_tenant_a_receipt_answers_another_space(
    matrix, enforced, monkeypatch,
) -> None:
    """The rule before S1-08: ``state.get("tenant_id") or "default"``."""
    from kazma_core.agent import effect_journal

    monkeypatch.setattr(effect_journal, "space_of_state",
                        lambda state: str(state.get("tenant_id") or OWNER_SPACE_ID))
    # One working folder for both, as before S1-19: since then each space
    # works in its own folder, which is in the request's hash, so the old
    # key withholds A's call instead of answering it with B's result.
    from kazma_core.workspace import binding

    one_folder = binding.default_sandbox_root()
    monkeypatch.setattr(binding, "resolve_active_root", lambda: one_folder)
    with pytest.raises(AssertionError, match="recorded"):
        _a_receipt_follows_the_scope(matrix)


# ── one shared table: a space's threads are asked once, in the statement ─


def _many_threads(w: World, who: str, n: int, word: str) -> list[Any]:
    made = [w.chat(who, f"{word}-{i}-{w.matrix.plant(word).a[-6:]}") for i in range(n)]
    for chat in made:
        w.checkpoint(who, chat)
    return made


def test_on_one_shared_table_an_older_spaces_threads_are_still_listed(matrix, monkeypatch) -> None:
    """The listing is held to the space's threads by its own statement,
    before the limit. Filtered after reading the newest ``limit * 5`` threads
    of the table, a space whose chats are older got a short or empty list."""
    from kazma_gateway.stores import checkpoint as store

    _one_table(monkeypatch)
    with world(matrix) as w:
        mine = _many_threads(w, "a", 2, "older")
        _many_threads(w, "b", 7, "newer")  # every one newer than A's
        listed = w.call("a", "GET", "/api/sessions?limit=1").json()["sessions"]
        assert [row["thread_id"] for row in listed] == [mine[-1].thread_id]
        both = w.call("a", "GET", "/api/sessions?limit=50").json()["sessions"]
        assert sorted(row["thread_id"] for row in both) == sorted(c.thread_id for c in mine)

        async def as_it_was(limit: int) -> list[str]:
            # The listing as S1-08 first wrote it: the newest limit*5 threads
            # of the table, then the space's own among them.
            cursor = await w.manager._saver.conn.execute(store._NEWEST_PER_THREAD_SQLITE, (limit * 5,))
            rows = await cursor.fetchall()
            return [str(r[0]) for r in rows if threads.thread_home(str(r[0])) == Matrix.SPACE_A][:limit]

        assert tt.run(as_it_was(1)) == []  # the control: A was shown nothing


def test_on_one_shared_table_the_owner_is_shown_threads_from_before_spaces(matrix, monkeypatch) -> None:
    """A thread with no record is the owner's (N19): the owner's listing is
    every thread no other space recorded."""
    _one_table(monkeypatch)
    with world(matrix) as w:
        _mine, theirs = w.pair("owner-view")
        w.checkpoint("b", theirs)

        async def plant_and_list() -> list[str]:
            from langgraph.checkpoint.base import empty_checkpoint

            with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
                await w.manager.aput(tt.config("t-from-before-spaces"), empty_checkpoint(),
                                     {"source": "input", "step": 0, "writes": {}}, {})
                return [row["thread_id"] for row in await w.manager.list_checkpoints(limit=50)]

        assert tt.run(plant_and_list()) == ["t-from-before-spaces"]


def test_a_clear_and_a_listing_ask_the_record_store_once(matrix, monkeypatch) -> None:
    """A clear-all and a listing over a shared table asked the record store
    thread by thread, and a thread with no record (never remembered) on every
    asking. They ask once, for the space's threads."""
    _one_table(monkeypatch)
    asked: list[int] = []
    real = threads._connection

    def counted() -> Any:
        asked.append(1)
        return real()

    with world(matrix) as w:
        _mine, theirs = w.pair("asked-once")
        w.checkpoint("b", theirs)

        async def run() -> tuple[int, int, int]:
            from langgraph.checkpoint.base import empty_checkpoint

            with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
                for i in range(6):
                    await w.manager.aput(tt.config(f"t-unrecorded-{i}"), empty_checkpoint(),
                                         {"source": "input", "step": 0, "writes": {}}, {})
                monkeypatch.setattr(threads, "_connection", counted)
                asked.clear()
                listed = [item async for item in w.manager.alist(None)]
                for_listing = len(asked)
                asked.clear()
                removed = await w.manager.adelete_all_threads()
                return len(listed), for_listing, removed

        listed, for_listing, removed = tt.run(run())
        assert listed == 6 and removed == 6
        assert for_listing == 1 and len(asked) == 1
        monkeypatch.setattr(threads, "_connection", real)
        assert w.checkpoints("b", theirs)  # B's thread was not cleared


def test_a_turns_thread_is_answered_from_memory_afterwards(matrix, enforced, monkeypatch) -> None:
    """Every turn reads its thread's record (``scope_for_turn``). The guards
    the turn's own code asks later, often on the event loop, then read no
    store: the record's space was remembered by that read."""
    import sqlite3

    recorded = "t-read-by-its-turn"
    threads.record_thread(recorded, Matrix.SPACE_A, matrix.account_a)
    monkeypatch.setattr(threads, "_spaces", {})
    assert threads.known_space(recorded) is None
    with matrix.as_a():
        threads.scope_for_turn(recorded)
    assert threads.known_space(recorded) == Matrix.SPACE_A

    def _no_store() -> Any:
        raise sqlite3.OperationalError("the record store was read")

    monkeypatch.setattr(threads, "_connection", _no_store)
    with matrix.as_a():
        assert threads.thread_refusal(recorded) is None
    with matrix.as_b():
        assert threads.thread_refusal(recorded)


# ── messages name their author ───────────────────────────────────────────

SHARED = "matrix-shared"


def _shared_space(matrix: Matrix) -> None:
    identity.create_space("Matrix shared", created_by=matrix.account_a, space_id=SHARED)
    identity.add_member(SHARED, matrix.account_b)


def test_a_row_names_the_account_that_wrote_it(matrix) -> None:
    from kazma_ui.reply_sink import CLIENT_ROW_FIELDS, authored

    assert authored({"role": "user", "content": "x"}) == {"role": "user", "content": "x"}
    with matrix.as_a():
        assert authored({"role": "user"})["author"] == matrix.account_a
    assert "author" in CLIENT_ROW_FIELDS


def test_history_and_the_live_frame_name_another_member_and_not_the_reader(matrix, enforced) -> None:
    from kazma_ui.reply_sink import authored
    from kazma_ui.session_manager import get_session_manager
    from kazma_ui.sse_chat import _author_of_row

    _shared_space(matrix)
    names = {a["id"]: str(a.get("display_name") or a.get("name") or a["id"])
             for a in identity.list_accounts()}
    with world(matrix) as w:
        session_id = "s-shared-chat"
        for who, account in (("a", matrix.account_a), ("b", matrix.account_b)):
            with scope.bind_scope(SHARED, account):
                store = get_session_manager()
                session = store.get_or_create(session_id, thread_id="t-shared-chat")
                session.messages.append(authored({"role": "user", "content": f"from {who}"}))
                store.put(session)
        for who, me, other in (("a", matrix.account_a, matrix.account_b),
                               ("b", matrix.account_b, matrix.account_a)):
            rows = w.call(who, "GET", f"/api/chat/sessions/{session_id}/messages", space=SHARED).json()
            by_author = {row["author"]: row for row in rows if row.get("role") == "user"}
            assert by_author[other]["author_name"] == names[other]
            assert "author_name" not in by_author[me]
    frame = _author_of_row({"author": matrix.account_a})
    assert frame == {"author": matrix.account_a, "author_name": names[matrix.account_a]}
    assert _author_of_row({"role": "user"}) == {}


def test_not_enforced_no_name_is_sent(matrix) -> None:
    from kazma_ui.space_binding import other_authors
    from kazma_ui.sse_chat import _author_of_row

    assert other_authors([matrix.account_a, matrix.account_b], None) == {}
    assert _author_of_row({"author": matrix.account_a}) == {"author": matrix.account_a}


# ── an install that does not enforce Spaces behaves as before ────────────


def test_not_enforced_the_checkpoint_store_takes_the_bound_tenants_file(matrix, tmp_path) -> None:
    from kazma_gateway.stores.checkpoint import create_checkpoint_manager

    recorded = "t-not-enforced"
    threads.record_thread(recorded, Matrix.SPACE_B, matrix.account_b)

    async def run() -> None:
        manager = await create_checkpoint_manager(str(tmp_path / "checkpoints.db"))
        try:
            # Whatever the thread's record says, the saver is the bound
            # tenant's, exactly as before Spaces; nothing is refused.
            assert await manager._open(recorded, write=True) is await manager._get_saver()
            with matrix.as_a():
                assert await manager._open(recorded, write=True) is await manager._get_saver()
                assert await manager._open(recorded) is not manager._saver
            assert not manager._filters_by_thread()
        finally:
            await manager.close()

    tt.run(run())


def test_the_owners_room_keeps_its_name(matrix) -> None:
    from kazma_core.voice.livekit import sanitize_room_name
    from kazma_ui.routes_voice import _livekit_room

    assert _livekit_room("abc", sanitize_room_name) == "kazma-abc"
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        assert _livekit_room("abc", sanitize_room_name) == "kazma-abc"
    with matrix.as_a():
        room = _livekit_room("abc", sanitize_room_name)
    assert room != "kazma-abc" and room.endswith("-abc")
