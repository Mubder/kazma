"""Turn-keyed reply sink — the single durable writer for assistant replies.

These tests pin the contract that replaced five positional writers (live
incidents 2026-08-27/28: duplicate bubbles, lost approve-resume finals,
clobbered history). Every case here is a shape that actually reached the
user's Postgres session store.
"""

from __future__ import annotations

import pytest
from kazma_ui import reply_sink
from kazma_ui.reply_sink import (
    close_reply_turn,
    current_reply_turn,
    open_reply_turn,
    reset_reply_turns,
    resolve_reply_text,
    resolve_reply_turn,
    upsert_reply,
)


class _Session:
    def __init__(self, session_id="s1", messages=None):
        self.session_id = session_id
        self.thread_id = "t1"
        self.messages = messages if messages is not None else []


class _Transact:
    def __init__(self, store):
        self._store = store

    def __enter__(self):
        return self._store.session

    def __exit__(self, *exc):
        self._store.puts += 1
        return False


class _FakeStore:
    def __init__(self, session):
        self.session = session
        self.puts = 0

    def transact(self, session_id):
        return _Transact(self)

    def get(self, session_id):
        return self.session


@pytest.fixture(autouse=True)
def _clean():
    reset_reply_turns()
    yield
    reset_reply_turns()


@pytest.fixture
def store(monkeypatch):
    st = _FakeStore(_Session())
    monkeypatch.setattr(reply_sink, "_store", lambda: st)
    return st


def _assistants(store):
    return [m for m in store.session.messages if m.get("role") == "assistant"]


# ── identity ──────────────────────────────────────────────────────────


def test_open_and_current_turn_roundtrip():
    tid = open_reply_turn("thread-a")
    assert tid
    assert current_reply_turn("thread-a") == tid
    assert current_reply_turn("thread-b") == ""


def test_close_clears_identity_and_open_marker(store):
    tid = open_reply_turn("thread-a")
    upsert_reply("s1", tid, "answer", open_turn=True)
    assert _assistants(store)[0]["open"] is True

    close_reply_turn("thread-a", "s1")
    assert current_reply_turn("thread-a") == ""
    assert "open" not in _assistants(store)[0]


# ── the duplicate-bubble incident ─────────────────────────────────────


def test_two_writers_same_turn_converge_on_one_row(store):
    """The 2026-08-28 duplicate: detached callback + live final both wrote.

    Stored history held two identical 125-char assistant rows 36 ms apart.
    Same turn id must mean one row.
    """
    store.session.messages = [{"role": "user", "content": "why not sending them now?"}]
    tid = open_reply_turn("thread-a")

    upsert_reply("s1", tid, "narration")  # detached callback
    upsert_reply("s1", tid, "narration")  # live final persist

    assert len(_assistants(store)) == 1
    assert _assistants(store)[0]["content"] == "narration"


def test_incremental_then_final_updates_in_place(store):
    store.session.messages = [{"role": "user", "content": "q"}]
    tid = open_reply_turn("thread-a")

    upsert_reply("s1", tid, "par", pending=True)
    upsert_reply("s1", tid, "partial ans")
    upsert_reply("s1", tid, "partial answer complete")

    rows = _assistants(store)
    assert len(rows) == 1
    assert rows[0]["content"] == "partial answer complete"
    assert "pending" not in rows[0]


# ── the clobbered-history incident ────────────────────────────────────


def test_writer_never_touches_another_turns_row(store):
    """A late write for turn 1 must not overwrite turn 2's answer.

    This is the 1,080 → 225 → 151 char shrink: an interim narration landed
    while the trailing row belonged to the previous turn.
    """
    t1 = open_reply_turn("thread-a")
    upsert_reply("s1", t1, "the good long answer from the first turn")
    close_reply_turn("thread-a", "s1", t1)

    store.session.messages.append({"role": "user", "content": "second question"})
    t2 = open_reply_turn("thread-a")
    upsert_reply("s1", t2, "second answer")

    # Straggler from turn 1 arrives late.
    upsert_reply("s1", t1, "interim fragment")

    rows = _assistants(store)
    assert len(rows) == 2
    assert rows[0]["content"] == "interim fragment"  # own row only
    assert rows[1]["content"] == "second answer"  # untouched


def test_allow_shrink_false_refuses_to_trade_answer_for_fragment(store):
    tid = open_reply_turn("thread-a")
    upsert_reply("s1", tid, "a complete and long final answer")
    upsert_reply("s1", tid, "frag", allow_shrink=False)
    assert _assistants(store)[0]["content"] == "a complete and long final answer"


# ── the lost approve-resume final ─────────────────────────────────────


def test_resume_adopts_in_memory_turn_so_pause_and_answer_share_a_row(store):
    """prompt → HITL pause → approve → final answer is ONE bubble."""
    store.session.messages = [{"role": "user", "content": "post the tweets"}]
    tid = open_reply_turn("thread-a")
    upsert_reply("s1", tid, "let me pull the texts…", open_turn=True)

    # Separate HTTP request (POST /api/approve) resumes the same turn.
    resumed = resolve_reply_turn("thread-a", "s1")
    assert resumed == tid

    upsert_reply("s1", resumed, "Posted all 4 Arabic tweets.")
    close_reply_turn("thread-a", "s1", resumed)

    rows = _assistants(store)
    assert len(rows) == 1
    assert rows[0]["content"] == "Posted all 4 Arabic tweets."


def test_resume_adopts_open_row_after_process_restart(store):
    """In-memory identity is gone; the row's own marker carries it."""
    store.session.messages = [
        {"role": "user", "content": "post the tweets"},
        {
            "role": "assistant",
            "content": "let me pull the texts…",
            "turn_id": "turn-xyz",
            "open": True,
        },
    ]
    reset_reply_turns()  # simulate restart

    assert resolve_reply_turn("thread-a", "s1") == "turn-xyz"
    upsert_reply("s1", "turn-xyz", "Posted all 4.")
    assert len(_assistants(store)) == 1


def test_resume_does_not_adopt_a_closed_row(store):
    """No open marker → the resume is a genuinely new reply, not a join."""
    store.session.messages = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "finished answer", "turn_id": "turn-old"},
    ]
    assert resolve_reply_turn("thread-a", "s1") != "turn-old"


def test_resume_does_not_adopt_across_a_newer_user_message(store):
    store.session.messages = [
        {
            "role": "assistant",
            "content": "stale",
            "turn_id": "turn-stale",
            "open": True,
        },
        {"role": "user", "content": "new question"},
    ]
    assert resolve_reply_turn("thread-a", "s1") != "turn-stale"


# ── pending bubbles / empty turns ─────────────────────────────────────


def test_pending_bubble_then_text_resolves_same_row(store):
    tid = open_reply_turn("thread-a")
    upsert_reply("s1", tid, "", pending=True)
    assert _assistants(store)[0]["pending"] is True

    upsert_reply("s1", tid, "done")
    rows = _assistants(store)
    assert len(rows) == 1 and "pending" not in rows[0]


def test_empty_content_without_pending_creates_nothing(store):
    tid = open_reply_turn("thread-a")
    assert upsert_reply("s1", tid, "") is False
    assert _assistants(store) == []


def test_missing_ids_are_a_noop(store):
    assert upsert_reply("", "t", "x") is False
    assert upsert_reply("s1", "", "x") is False
    assert _assistants(store) == []


def test_store_failure_is_reported_not_swallowed(monkeypatch, caplog):
    class _Boom:
        def transact(self, session_id):
            raise RuntimeError("db down")

    monkeypatch.setattr(reply_sink, "_store", lambda: _Boom())
    with caplog.at_level("WARNING"):
        assert upsert_reply("s1", "t1", "text") is False
    assert any("upsert FAILED" in r.message for r in caplog.records)


# ── text selection ────────────────────────────────────────────────────


def test_longer_streamed_text_beats_truncated_checkpoint():
    """2026-08-27: a stopped turn left a 158-char fragment in the checkpoint
    while 2,272 streamed chars were discarded."""
    ckpt = "short fragment"
    streamed = "a much longer streamed accumulation " * 10
    assert resolve_reply_text(ckpt, streamed).startswith("a much longer")


def test_checkpoint_used_when_nothing_streamed():
    assert resolve_reply_text("checkpoint answer", "") == "checkpoint answer"


def test_no_candidates_yields_empty():
    assert resolve_reply_text("", "") == ""
