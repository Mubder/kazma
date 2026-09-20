"""Restart recovery — the fourth source of the convergence oracle.

``docs/plans/UNIFIED_TURN_BLOCK.md`` §10 compares four sources; three are
projector-level and live in ``tests/js/test_turn_convergence.js``. This is
the fourth, and it is the only one that needs a real process, because the
thing under test is what is NOT in memory:

* the delivery journal is explicitly "process-local memory only"
  (``delivery.py``), so it is gone;
* the client's TurnDocument is gone with the page;
* the LangGraph checkpoint holds messages, not presentation state.

What is left is whatever ``reply_sink`` committed. Phase 1c made that
include tool activity and checkpointed answer text; before it, a restart
mid-turn lost the partial answer, every tool row and every reasoning note,
and tool rows were never persisted at all.

"Restart" here is a COLD READER: a new ``SessionManager`` on the same
file, with an empty cache, its own connection and nothing inherited from
the running app. That is what a rebooted process gets, and it is the
durability boundary.

It is not a process kill. A real reboot — and rollback across it — is
Phase 5's rehearsal; this file says so rather than implying it covered
one. What it does cover is the question a kill would ask: with the
journal and every cache out of the picture, is the turn still there?
"""

from __future__ import annotations

import pathlib
from collections.abc import Iterator

import pytest

pytest.importorskip("httpx")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import (  # noqa: E402
    Harness,
    HITL_EVENTS,
    api_client,
    gate_from_frame,
    new_session_id,
    sse_frames,
    start_turn,
    unified_turn_server,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


@pytest.fixture
def harness() -> Iterator[Harness]:
    """One app per test, not per module — see ``tests/e2e/conftest.py``."""
    with unified_turn_server() as h:
        yield h


def _cold_read(session_id: str) -> dict:
    """The assistant row as a RESTARTED process would find it.

    A new ``SessionManager`` on the same file, with no shared state: an
    empty cache, its own connection, nothing inherited from the running
    app. That is exactly what a reboot gets, and it is the honest way to
    ask "what is durable" from inside one process.

    The path is asked of the LIVE manager rather than guessed from the
    harness data dir. ``reply_sink`` resolves the store through
    ``get_session_manager()`` on every write, and this repo's conftest
    installs a per-test SessionManager on its own tmp path
    (process-singleton isolation), so the file the app just wrote to is
    the current global's — not the harness's. Guessing the harness dir
    opened an empty database and "proved" that nothing is durable, which
    is the most misleading way a durability test can be wrong.
    """
    from kazma_ui.session_manager import SessionManager, get_session_manager

    db = str(getattr(get_session_manager(), "db_path", "") or "")
    assert db and db != ":memory:", (
        f"the live session store is {db!r}; there is nothing on disk to "
        f"recover from and this test would pass vacuously"
    )
    cold = SessionManager(db_path=db)
    sess = cold.get(session_id)
    assert sess is not None, (
        f"a restarted process found no session {session_id} in {db}"
    )
    rows = [m for m in (sess.messages or []) if isinstance(m, dict)]
    assistant = [m for m in rows if m.get("role") == "assistant"]
    assert assistant, "the recovered session has no assistant row"
    return assistant[-1]


def _live_read(base: str, session_id: str) -> dict:
    """The same row through the running app, for comparison."""
    with api_client(base) as client:
        resp = client.get(
            f"{base}/api/chat/sessions/{session_id}/messages", timeout=30.0
        )
        assert resp.status_code == 200, resp.status_code
        rows = resp.json()
    assistant = [m for m in rows if m.get("role") == "assistant"]
    assert assistant, "no assistant row in the transcript"
    return assistant[-1]


def _run_to_first_pause(harness: Harness) -> tuple[str, str]:
    """Drive one turn until the graph parks. Returns (session_id, gate_id)."""
    session_id = new_session_id()
    with api_client(harness.base) as client:
        with start_turn(
            harness.base, session_id, "Set up the project scaffold.", client
        ) as resp:
            assert resp.status_code == 200
            for frame in sse_frames(resp, limit_seconds=120):
                if frame["event"] in HITL_EVENTS:
                    gate = gate_from_frame(frame)
                    if gate and str((frame["data"] or {}).get("state")
                                    or "pending") == "pending":
                        return session_id, gate
    raise AssertionError("the turn never paused")


def test_a_paused_turn_survives_losing_every_cache(harness: Harness) -> None:
    """Mid-pause is the window Phase 0 §5.3 called out as lossy.

    Before Phase 1c the pause write was the FIRST durable write of the
    turn, so everything before it lived only in the journal. Now the tool
    rows are committed as they happen and the answer is checkpointed, so
    dropping every cache leaves a readable turn.
    """
    session_id, gate_id = _run_to_first_pause(harness)

    live = _live_read(harness.base, session_id)
    after = _cold_read(session_id)

    assert after.get("turn_id") == live.get("turn_id"), (
        "the turn lost its identity across the restart"
    )
    assert int(after.get("rev") or 0) > 0, (
        "the recovered row carries no revision, so a client cannot tell "
        "it from a stale one"
    )

    gates = [p for p in (after.get("parts") or []) if p.get("type") == "hitl"]
    assert any(p.get("interrupt_id") == gate_id for p in gates), (
        f"the gate the graph is parked on ({gate_id}) is not in the "
        f"recovered transcript — the reader would see a paused turn with "
        f"no question"
    )

    assert str(after.get("content") or "").strip(), (
        "the partial answer did not survive; a reader returning after a "
        "restart sees an empty bubble for work that happened"
    )


def test_a_finished_turn_reads_the_same_after_a_restart(harness: Harness) -> None:
    """Convergence source 4 against source 3.

    The oracle's rule is that a recovered turn and a freshly hydrated one
    normalize to the same document. Here the comparison is over the
    serialized row, which is what hydration consumes: identical row,
    identical projection.
    """
    from tests.e2e._unified_turn_harness import drive_turn

    run = drive_turn(
        harness,
        "Set up the project scaffold.",
        [True, True, True, True],
        leg_timeout=120.0,
    )
    assert run.finished

    before = _live_read(harness.base, run.session_id)
    after = _cold_read(run.session_id)

    def _comparable(row: dict) -> dict:
        return {
            "turn_id": row.get("turn_id"),
            "content": row.get("content"),
            "gates": [
                (p.get("interrupt_id"), p.get("state"), p.get("tool"))
                for p in (row.get("parts") or [])
                if p.get("type") == "hitl"
            ],
            "activity": [
                (r.get("id"), r.get("kind"), r.get("title"))
                for r in (row.get("activity") or [])
            ],
        }

    # The cold read is the STORED row; the live read is that row plus the
    # read-time gate-view stamp the serializer applies. Compare what is
    # durable: identity, answer, gate identities and activity.
    cold, warm = _comparable(after), _comparable(before)
    assert cold["turn_id"] == warm["turn_id"]
    assert cold["content"] == warm["content"], "the answer changed on restart"
    assert [g[0] for g in cold["gates"]] == [g[0] for g in warm["gates"]], (
        "a restart changed which gates the turn had"
    )
    assert cold["activity"] == warm["activity"], (
        "a restart changed the activity log"
    )

    recovered = cold
    assert len(recovered["gates"]) == 4, recovered["gates"]
    assert len({g[0] for g in recovered["gates"]}) == 4
    assert any(k == "tool" for _, k, _ in recovered["activity"]), (
        "tool activity did not survive the restart — this is what Phase 1c "
        "made durable, and it is the whole of 'thoughts remain available' "
        "for tools"
    )
    assert "Scaffold ready" in str(recovered["content"])


def test_recovery_does_not_read_the_journal(harness: Harness) -> None:
    """The recovery above is durable state, not a surviving cache.

    A cold reader could still be passing for the wrong reason if the
    content it finds had come from somewhere in memory. It cannot: a
    fresh ``TurnJournal`` has nothing, which is the whole reason
    ``delivery.py`` says "the journal bridges a disconnected *user*, not a
    server restart".
    """
    from kazma_ui.delivery import TurnJournal, get_turn_broker

    session_id, _gate = _run_to_first_pause(harness)
    thread = session_id  # the harness uses the session id as the thread id

    assert get_turn_broker().head_seq(thread) > 0, (
        "the turn produced no journal frames; the comparison below would "
        "prove nothing"
    )
    # What a restarted process has instead.
    assert TurnJournal().head_seq(thread) == 0, (
        "a fresh journal is not empty — the premise of this whole test is "
        "that a restart loses it"
    )

    row = _cold_read(session_id)
    assert str(row.get("content") or "").strip() or (row.get("parts") or []), (
        "nothing durable was left once the journal was out of the picture"
    )
