"""Transport recovery — ``docs/plans/UNIFIED_TURN_BLOCK.md`` §7, §10.

Acceptance matrix rows covered here:

    | Disconnect mid-token    | Reconnect converges without duplicated or
    |                         | missing content
    | Journal retention gap   | Full snapshot plus newer events converge
    |                         | correctly
    | Session switch during   | No content appears in the wrong session;
    | updates                 | return restores state

``tests/js/test_turn_convergence.js`` already proves the PROJECTOR
converges when it is handed a disconnect, a replay and duplicates. That
is a statement about a pure function fed synthetic frames. It cannot say
whether the server actually serves the frames the projector was tested
on, and the plan's oracle names live delivery and replay as two separate
sources for exactly that reason.

These drive the real routes: a stream really abandoned mid-token, a
cursor really outside retention, two sessions really interleaved.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

pytest.importorskip("httpx")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import (  # noqa: E402
    Harness,
    HITL_EVENTS,
    Script,
    api_client,
    attach_stream,
    four_gate_script,
    gate_from_frame,
    new_session_id,
    sse_frames,
    start_turn,
    submit_decision,
    unified_turn_server,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


#: Long enough to become many deltas. `scripted_provider` chunks the
#: model's text at 24 characters, and the shared four-gate script narrates
#: 19 -- one delta, four journaled frames for the whole leg, and nothing
#: to disconnect in the middle of. A recovery suite needs a stream.
_CHATTY = (
    "Reading the project layout before I touch anything. "
    "There is no package manifest, no source directory and no entry "
    "point, so the scaffold has to be created rather than repaired. "
    "I will start with the README so the repository explains itself, "
    "then add a source directory with an entry point, then a manifest "
    "that names them, and finally install the dependencies it lists. "
    "Each of those four steps touches the filesystem, so each will "
    "stop and ask before it runs."
)


def _streaming_script(tmp_dir: str) -> Script:
    """The shared four-gate script, with a first step that streams."""
    script = four_gate_script(tmp_dir)
    if script.steps:
        script.steps[0].narration = _CHATTY
    return script


@pytest.fixture
def harness() -> Iterator[Harness]:
    """One app per test — see ``test_unified_turn_concurrency.py``."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="utb-recov-") as tmp:
        with unified_turn_server(_streaming_script(tmp)) as h:
            yield h


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════

TEXT_EVENTS = ("llm_delta", "token", "delta", "message_delta")


def _text_of(frame: dict[str, Any]) -> str:
    """The visible text a frame contributes, or "".

    Deliberately generous about the key: the point of the test is that
    the SAME extraction over two deliveries yields the same string, not
    that it knows every producer's field name.
    """
    if frame.get("event") not in TEXT_EVENTS:
        return ""
    data = frame.get("data")
    if not isinstance(data, dict):
        return str(data or "")
    for key in ("content", "text", "delta", "token", "chunk"):
        val = data.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def _run_to_first_gate(
    harness: Harness, session_id: str, client: Any, *, stop_after: int = 0
) -> tuple[str, str, int, list[dict[str, Any]]]:
    """Drive one turn until its first gate, or until *stop_after* frames.

    Returns ``(thread_id, gate_id, last_seq, frames)``. A ``stop_after``
    of N abandons the response after N frames WITHOUT reading the rest —
    which is what a closed laptop lid does, and is why the seq of the
    last frame actually read is the cursor the client would hold.
    """
    frames: list[dict[str, Any]] = []
    thread_id = session_id
    gate = ""
    last_seq = 0
    with start_turn(harness.base, session_id, "Set up the project scaffold.",
                    client) as resp:
        assert resp.status_code == 200
        for frame in sse_frames(resp, limit_seconds=120):
            frames.append(frame)
            data = frame["data"] if isinstance(frame["data"], dict) else {}
            seq = int(data.get("seq") or 0)
            if seq:
                last_seq = max(last_seq, seq)
            if data.get("thread_id"):
                thread_id = str(data["thread_id"])
            # stop_after is checked FIRST. Checked second, it never fired:
            # the gate arrives on frame four, so the "disconnect" read the
            # whole leg and the re-attach had nothing left to replay.
            if stop_after and len(frames) >= stop_after:
                break
            if frame["event"] in HITL_EVENTS:
                found = gate_from_frame(frame)
                if found and str(data.get("state") or "pending") == "pending":
                    gate = found
                    break
    return thread_id, gate, last_seq, frames


def _drain_attach(
    harness: Harness, session_id: str, last_seq: int, client: Any,
    *, limit_seconds: float = 120.0,
) -> list[dict[str, Any]]:
    """Read one attach to its end of transmission.

    Four things end it, and only one of them closes the socket:

    - a terminal frame (``done`` / ``turn_complete`` / ``capacity``);
    - ``resync``, after which the server closes anyway;
    - a PENDING gate arriving live;
    - a keepalive, once the handshake has said the turn is not running.

    The last two are the ones worth naming. ``_sse_attach_stream``
    deliberately holds the connection open while the thread is paused --
    "a HITL pause is not nothing: the graph is parked and approve will
    journal into this same tail". Right for a browser, which is waiting
    for the human; a deadline for a test, which IS the human.

    So: a live question is the end of this leg (what follows is another
    leg with its own attach), and ten seconds of silence on a turn that
    is not running means the replay has been delivered and the graph is
    parked. Without the second rule the only way to observe that state
    is to time out, which reports a stall where there is none -- it cost
    this suite 189s across three deadline waits before it was added.
    """
    out: list[dict[str, Any]] = []
    quiet_after_replay = False
    with attach_stream(harness.base, session_id, last_seq, client) as resp:
        assert resp.status_code == 200
        for frame in sse_frames(resp, limit_seconds=limit_seconds,
                                keepalives=True):
            if frame["event"] == "keepalive":
                # Ten seconds of nothing on a turn the handshake said is
                # not running: the replay is delivered and the graph is
                # parked at a question. Nothing further arrives until a
                # human answers, so this is the end of transmission even
                # though the socket stays open.
                if quiet_after_replay:
                    break
                continue
            out.append(frame)
            if frame["event"] in ("done", "turn_complete", "capacity"):
                break
            data = frame["data"] if isinstance(frame["data"], dict) else {}
            if frame["event"] == "resumed" and not data.get("running"):
                quiet_after_replay = True
            if frame["event"] == "status_update" and data.get("status") == "resync":
                break
            if (
                frame["event"] in HITL_EVENTS
                and str(data.get("state") or "pending") == "pending"
            ):
                break
    return out


# ══════════════════════════════════════════════════════════════════════
# Disconnect mid-token
# ══════════════════════════════════════════════════════════════════════


def test_a_stream_abandoned_mid_turn_reconnects_without_gap_or_repeat(
    harness: Harness,
) -> None:
    """Close the stream early, re-attach from the cursor, converge.

    The failure this exists to catch has two directions and they look
    identical from the server's side: replay ``> cursor`` loses the frame
    at the cursor, replay ``>= cursor`` serves it twice. Both produce a
    turn that "worked". Only comparing the reassembled text against the
    uninterrupted one tells them apart.

    What is checked is the delivery, not the text: sequence numbers past
    the cursor, strictly increasing and never repeated, plus a seam with
    no duplicated prefix. Comparing against a second live run would prove
    nothing — a scripted provider makes two runs equal by construction.
    """
    session_id = new_session_id()
    with api_client(harness.base) as client:
        # Abandon two frames in. `_CHATTY` makes the first leg stream
        # ~14 deltas, so two frames is genuinely mid-token and there is a
        # real window for the replay to get wrong.
        thread_id, _gate, cursor, first = _run_to_first_gate(
            harness, session_id, client, stop_after=2
        )
        assert first, "the turn produced nothing to disconnect from"
        assert cursor > 0, (
            "no frame carried a seq, so a reconnecting client has no cursor "
            "to reconnect FROM; that is the bug, not a test setup problem"
        )

        second = _drain_attach(harness, session_id, cursor, client)
        assert second, "re-attaching returned nothing at all"

        resumed = [f for f in second if f["event"] == "resumed"]
        assert resumed, "the attach handshake never identified itself"
        head = resumed[0]["data"]
        assert int(head.get("from") or 0) == cursor, head
        assert not head.get("gap"), (
            f"a two-frame-old cursor was declared outside retention: {head}"
        )

        # No frame is served twice: seqs strictly increase past the cursor.
        seqs = [
            int((f["data"] or {}).get("seq") or 0)
            for f in second
            if isinstance(f.get("data"), dict)
        ]
        seqs = [s for s in seqs if s]
        assert seqs == sorted(seqs), f"replayed frames arrived out of order: {seqs}"
        assert len(seqs) == len(set(seqs)), (
            f"the same frame was delivered twice on reconnect: {seqs}"
        )
        assert all(s > cursor for s in seqs), (
            f"the reconnect re-served frames the client already had: {seqs}"
        )

        # And the text splices: prefix from leg one, suffix from leg two,
        # with nothing repeated across the seam.
        head_text = "".join(_text_of(f) for f in first)
        tail_text = "".join(_text_of(f) for f in second)
        if head_text and tail_text:
            assert not tail_text.startswith(head_text[-40:] or head_text), (
                "the reconnect replayed text the client already rendered; "
                "the reader sees it twice"
            )
        assert thread_id


# ══════════════════════════════════════════════════════════════════════
# Journal retention gap
# ══════════════════════════════════════════════════════════════════════


def test_a_cursor_outside_retention_is_told_to_resync_not_served_a_hole(
    harness: Harness,
) -> None:
    """Retention is a bound, and a bound is eventually crossed.

    Shrinking ``_max_events`` is the honest way to reach it: the same
    deque, the same eviction on append, the same ``replay()`` arithmetic
    — only the number is small enough to hit inside one turn. Waiting for
    2000 real frames would test the same branch far more slowly.

    What must NOT happen is a silent partial replay. A client handed
    frames 40..60 with 1..39 missing renders a turn that begins in the
    middle and looks complete. The server owes it ``gap`` instead, and
    the client rebuilds from the durable store.
    """
    from kazma_ui.delivery import get_turn_broker

    broker = get_turn_broker()
    journal = broker._journal
    original = journal._max_events
    journal._max_events = 2
    try:
        session_id = new_session_id()
        with api_client(harness.base) as client:
            thread, gate, _cursor, _frames = _run_to_first_gate(
                harness, session_id, client
            )
            assert gate, "the turn never paused, so there is no journal to gap"

            # The cursor comes from the window the journal ACTUALLY kept,
            # not from a guess. A first attempt used cursor 1 against a
            # bound of 3 and got gap=False -- correctly, because with
            # head=4 the retained window started at seq 2 and a client at
            # 1 had missed nothing. The bound has to be crossed by more
            # than one frame before there is a hole to report.
            bucket = journal._events.get(thread) or journal._events.get(
                session_id
            )
            assert bucket, (
                f"nothing was journaled for thread {thread!r}; the keys "
                f"present are {sorted(journal._events)}"
            )
            first_kept = int(bucket[0]["seq"])
            head = int(bucket[-1]["seq"])
            assert first_kept > 2, (
                f"retention never evicted anything (window {first_kept}.."
                f"{head}); there is no gap to detect and this test would "
                "pass without asserting the behavior it names"
            )
            stale = first_kept - 2

            second = _drain_attach(harness, session_id, stale, client)
            resumed = [f for f in second if f["event"] == "resumed"]
            assert resumed, "the attach handshake never identified itself"
            assert resumed[0]["data"].get("gap") is True, (
                "a cursor outside retention was served as if it were "
                f"current: {resumed[0]['data']}"
            )

            told = [
                f for f in second
                if f["event"] == "status_update"
                and isinstance(f.get("data"), dict)
                and f["data"].get("status") == "resync"
            ]
            assert told, (
                "the gap was reported in the handshake but the client was "
                "never told to resync; a client that does not read the "
                "handshake sits on a truncated turn forever"
            )

            # Nothing content-bearing may be served alongside the gap.
            served = [
                f["event"] for f in second
                if f["event"] not in ("resumed", "status_update", "keepalive")
            ]
            assert not served, (
                f"a gapped attach served content anyway: {served}"
            )
    finally:
        journal._max_events = original


def test_the_durable_store_still_answers_after_a_gapped_attach(
    harness: Harness,
) -> None:
    """The other half: "full snapshot plus newer events converge".

    Being told to resync is only useful if the snapshot exists. One gate
    is approved -- enough to prove the store is written on the resume
    path, not only on the first leg -- and then the hydration endpoint
    the client falls back to must carry the answer plus the two fields
    the projector merges newer events against: ``rev`` and ``schema``.

    Driving all four gates would assert nothing further and would cost
    three more pauses.
    """
    session_id = new_session_id()
    with api_client(harness.base) as client:
        thread_id, gate, _cursor, _frames = _run_to_first_gate(
            harness, session_id, client
        )
        assert gate
        submit_decision(
            harness.base, thread_id, approve=True, interrupt_id=gate,
            client=client,
        )
        _drain_attach(harness, session_id, 0, client, limit_seconds=120.0)

        r = client.get(
            f"{harness.base}/api/chat/sessions/{session_id}/messages", timeout=60.0
        )
        assert r.status_code == 200, r.text[:200]
        payload = r.json()
        rows = payload.get("messages") if isinstance(payload, dict) else payload
        assistant = [
            row for row in (rows or [])
            if isinstance(row, dict) and row.get("role") == "assistant"
        ]
        assert assistant, (
            "the client was told to rebuild from the durable store and the "
            "durable store has no assistant row to rebuild from"
        )
        last = assistant[-1]
        assert int(last.get("rev") or 0) > 0, (
            f"the snapshot carries no revision, so the projector cannot tell "
            f"whether a newer event supersedes it: {sorted(last)}"
        )
        assert int(last.get("schema") or 0) >= 2, (
            f"the snapshot predates the turn contract: {sorted(last)}"
        )


# ══════════════════════════════════════════════════════════════════════
# Session switch
# ══════════════════════════════════════════════════════════════════════


def test_a_second_session_never_receives_the_first_sessions_frames(
    harness: Harness,
) -> None:
    """Plan: "No content appears in the wrong session."

    Two sessions are paused at once, then one is approved. Every frame
    the other session's attach produces must name the other session.
    Cross-talk here is the worst failure in the plan: a reader watching
    session B sees session A's tool output and cannot tell.
    """
    a_id, b_id = new_session_id(), new_session_id()
    with api_client(harness.base) as client:
        a_thread, a_gate, _ac, _af = _run_to_first_gate(harness, a_id, client)
        b_thread, b_gate, _bc, _bf = _run_to_first_gate(harness, b_id, client)
        assert a_gate and b_gate
        assert a_thread != b_thread, "two sessions shared a thread id"
        assert a_gate != b_gate, (
            "two independent turns were handed the same gate id; one "
            "approval would settle both"
        )

        # Advance A only.
        submit_decision(
            harness.base, a_thread, approve=True, interrupt_id=a_gate,
            client=client,
        )
        a_frames = _drain_attach(harness, a_id, 0, client, limit_seconds=120.0)
        b_frames = _drain_attach(harness, b_id, 0, client, limit_seconds=60.0)

    def _sessions(frames: list[dict[str, Any]]) -> set[str]:
        seen = set()
        for f in frames:
            data = f.get("data")
            if isinstance(data, dict):
                for key in ("session_id", "thread_id"):
                    val = data.get(key)
                    if val:
                        seen.add(str(val))
        return seen

    a_seen, b_seen = _sessions(a_frames), _sessions(b_frames)
    assert b_id not in a_seen, (
        f"session A's stream carried session B's id: {sorted(a_seen)}"
    )
    assert a_id not in b_seen, (
        f"session B's stream carried session A's id: {sorted(b_seen)}"
    )
    # B is still parked on its own question, untouched by A's approval.
    b_text = json.dumps(b_frames)
    assert a_gate not in b_text, (
        "session A's gate id appeared in session B's stream"
    )
