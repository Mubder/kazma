"""The turn row must be confluent: order, duplication and loss cannot change it.

Every chat transport writes the same turn — SSE stream, SSE approve-resume, the
WebSocket bus, the HITL bridge, the unanswered-turn backfill. Fifteen call
sites across six modules each decide, independently, whether the turn is still
open. They run concurrently, over a network, against one row.

`reply_sink` already fixed *which row* (identity, not position) and *content*
(`allow_shrink=False` refuses a shorter overwrite; `merge_parts` never deletes).
Both of those are **join rules**: whatever order the writes arrive in, the
result is the same. The lifecycle flag never got one —

    if open_turn:  row["open"] = True
    else:          row.pop("open", None)

— so a write that arrives late can reopen a finished turn. Measured on a live
install 2026-09-13:

    22:24:41  persist chars=3362 interrupted=False   -> turn CLOSED
    22:24:42  persist chars=3362 interrupted=True    -> turn REOPENED, forever

The UI then showed "Action required" on a turn that had already answered, and
the reply was never presented.

**What this file asserts.** Not "the bug we saw is gone" — that is an anecdote,
and the same class has come back thirteen times in sixty days. It asserts the
property that makes the class impossible:

    for every permutation of a turn's writes,
    with any write duplicated,
    with any non-final write dropped
        the stored row is identical.

That is confluence. A system whose merge is commutative, associative and
idempotent cannot be corrupted by interleaving, retries or replay — which is
the only honest basis for saying concurrent writers are safe here.

The sequences below are the real shapes, taken from the log above: a plain
turn, and a HITL turn that pauses and resumes.
"""

from __future__ import annotations

import itertools
from contextlib import contextmanager
from typing import Any

import pytest


class _FakeSession:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []


class _FakeStore:
    """Minimal stand-in for SessionManager: one session, serialized writes."""

    def __init__(self) -> None:
        self.session = _FakeSession()

    @contextmanager
    def transact(self, session_id: str):
        yield self.session


@pytest.fixture
def sink(monkeypatch):
    from kazma_ui import reply_sink

    store = _FakeStore()
    monkeypatch.setattr(reply_sink, "_store", lambda: store)
    reply_sink.reset_reply_turns()
    return reply_sink, store


#: A turn that streams and finishes. Each entry is the kwargs of one
#: `upsert_reply` call, in the order the transports emit them.
_PLAIN_TURN: tuple[dict[str, Any], ...] = (
    {"content": "", "open_turn": True, "pending": True},
    {"content": "Hel", "open_turn": True},
    {"content": "Hello there", "open_turn": True},
    {"content": "Hello there, here is the answer.", "open_turn": False,
     "model": "deepseek-chat", "tokens": 52753, "cost": 0.0014},
)

#: A turn that pauses for approval and resumes — one turn, two HTTP requests.
_HITL_TURN: tuple[dict[str, Any], ...] = (
    {"content": "", "open_turn": True, "pending": True},
    {"content": "Checking the schedule", "open_turn": True},
    {"content": "Checking the schedule", "open_turn": True,
     "parts": [{"type": "hitl", "tool": "edit_scheduled", "state": "pending"}]},
    {"content": "Checking the schedule. Done — updated.", "open_turn": False,
     "model": "deepseek-chat"},
)


def _apply(sink_mod, store, calls, *, session="s1", turn="t1"):
    for kwargs in calls:
        sink_mod.upsert_reply(session, turn, kwargs.pop("content", ""), **dict(kwargs))
    rows = [m for m in store.session.messages if m.get("turn_id") == turn]
    assert len(rows) == 1, f"{len(rows)} rows for one turn"
    return rows[0]


def _run(sink_pair, calls):
    sink_mod, store = sink_pair
    store.session.messages = []
    sink_mod.reset_reply_turns()
    return _apply(sink_mod, store, [dict(c) for c in calls])


def _lifecycle(row: dict[str, Any]) -> tuple[bool, bool]:
    """The pair that decides whether the UI shows a finished answer."""
    return bool(row.get("open")), bool(row.get("pending"))


@pytest.mark.parametrize(
    "sequence", [_PLAIN_TURN, _HITL_TURN], ids=["plain", "hitl"]
)
class TestConfluence:
    def test_a_late_write_cannot_reopen_a_finished_turn(self, sink, sequence):
        """The exact live failure, as a property rather than an anecdote: the
        final close arrives, then a straggler flush from another transport
        lands. The turn must stay finished."""
        straggler = dict(sequence[-2])          # an in-flight write, delayed
        expected = _lifecycle(_run(sink, sequence))
        actual = _lifecycle(_run(sink, list(sequence) + [straggler]))
        assert actual == expected, (
            "a delayed in-flight write reopened a finished turn: "
            f"open/pending {expected} -> {actual}"
        )

    def test_any_duplicate_is_a_no_op(self, sink, sequence):
        """Transports retry. A retry must change nothing."""
        expected = _run(sink, sequence)
        for i in range(len(sequence)):
            doubled = list(sequence[: i + 1]) + [sequence[i]] + list(sequence[i + 1 :])
            got = _run(sink, doubled)
            assert _lifecycle(got) == _lifecycle(expected), (
                f"duplicating write {i} changed the lifecycle"
            )
            assert got.get("content") == expected.get("content"), (
                f"duplicating write {i} changed the content"
            )

    def test_any_order_gives_the_same_row(self, sink, sequence):
        """The strong claim, and the one worth making: two transports on one
        row means arrival order is not ours to choose, so it must not matter —
        for the lifecycle *and* for what the reader sees."""
        expected = _run(sink, sequence)
        total = 0
        failures = []
        for perm in itertools.permutations(range(len(sequence))):
            total += 1
            got = _run(sink, [sequence[i] for i in perm])
            if (_lifecycle(got), got.get("content")) != (
                _lifecycle(expected), expected.get("content")
            ):
                failures.append((perm, _lifecycle(got), got.get("content")))
        assert not failures, (
            f"{len(failures)} of {total} orderings produced a different row; "
            f"first: {failures[0]}"
        )

    def test_losing_a_non_final_write_is_survivable(self, sink, sequence):
        """A dropped interim frame must not change where the turn ends up."""
        expected = _lifecycle(_run(sink, sequence))
        for i in range(len(sequence) - 1):
            trimmed = [c for j, c in enumerate(sequence) if j != i]
            assert _lifecycle(_run(sink, trimmed)) == expected, (
                f"dropping write {i} changed the final lifecycle"
            )


class TestOnlyTheSinkOwnsLifecycle:
    """Confluence holds only while the join is the *sole* way lifecycle moves.

    Thirteen commits in sixty days touched these two modules, each fixing the
    field that happened to be corrupted that day. The join fixes the class —
    but only if no one reaches around it and sets the flags directly, which is
    exactly how the last one got in.
    """

    @staticmethod
    def _sources():
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent / "kazma-ui" / "kazma_ui"
        for path in root.rglob("*.py"):
            if path.name == "reply_sink.py":
                continue          # the sink is allowed to own it
            yield path

    def test_nothing_outside_the_sink_mutates_a_stored_row_lifecycle(self):
        """Scoped to writes against the *store*, deliberately.

        A first version of this flagged every `x["pending"] = True` and caught
        `sse_chat/__init__.py` building an API response DTO — a read path that
        copies the flag outward and cannot corrupt anything. The invariant is
        not "never name these keys"; it is "never change them on a row inside
        a store transaction, anywhere but the sink". That is the write that
        escapes the join.
        """
        import ast

        keys = {"open", "pending", "lifecycle"}
        offenders = []
        for path in self._sources():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.With, ast.AsyncWith)):
                    continue
                in_transact = any(
                    isinstance(item.context_expr, ast.Call)
                    and isinstance(item.context_expr.func, ast.Attribute)
                    and item.context_expr.func.attr == "transact"
                    for item in node.items
                )
                if not in_transact:
                    continue
                for inner in ast.walk(node):
                    targets: list[ast.expr] = []
                    if isinstance(inner, ast.Assign):
                        targets = list(inner.targets)
                    elif isinstance(inner, ast.AugAssign):
                        targets = [inner.target]
                    elif isinstance(inner, ast.Delete):
                        targets = list(inner.targets)
                    for target in targets:
                        if (
                            isinstance(target, ast.Subscript)
                            and isinstance(target.slice, ast.Constant)
                            and target.slice.value in keys
                        ):
                            offenders.append(f"{path.name}:{inner.lineno}")
        assert not offenders, (
            "a stored row's lifecycle is being changed outside reply_sink, "
            f"which escapes the join: {offenders}"
        )

    def test_the_join_is_a_total_order_with_an_absorbing_top(self):
        """The algebra the proof rests on: every pair has a least upper bound,
        and `closed` absorbs everything."""
        from kazma_ui.reply_sink import _CLOSED, _LIFECYCLE_RANK, _join_lifecycle

        states = list(_LIFECYCLE_RANK)
        for a in states:
            assert _join_lifecycle(a, a) == a, f"not idempotent at {a}"
            assert _join_lifecycle(_CLOSED, a) == _CLOSED, f"{a} escaped closed"
            assert _join_lifecycle(a, _CLOSED) == _CLOSED, f"{a} escaped closed"
            for b in states:
                assert _join_lifecycle(a, b) == _join_lifecycle(b, a), (
                    f"not commutative at {a}/{b}"
                )
                for c in states:
                    assert _join_lifecycle(_join_lifecycle(a, b), c) == _join_lifecycle(
                        a, _join_lifecycle(b, c)
                    ), f"not associative at {a}/{b}/{c}"

    def test_a_legacy_row_is_read_correctly(self):
        """Rows written before the lifecycle field exist in every live
        transcript, and misreading one would hang an old turn.

        A row still carrying `open`/`pending` is read as in-flight. A row with
        NO marker of any kind reads as *unknown*, not as closed, and the
        incoming write wins. That is deliberate: an unmarked row may have been
        appended by a writer that never went through the sink, and forcing it
        to `closed` would make such a turn born finished — unable to ever
        stream. Unknown is the safe bottom of the lattice.
        """
        from kazma_ui.reply_sink import _lifecycle_of

        assert _lifecycle_of({"content": "…", "open": True}) == "open"
        assert _lifecycle_of({"content": "", "open": True, "pending": True}) == "open"
        assert _lifecycle_of({"content": "done", "lifecycle": "closed"}) == "closed"
        assert _lifecycle_of({"content": "x", "pending": False}) == "closed"
        assert _lifecycle_of(None) is None
        assert _lifecycle_of({"content": "unmanaged"}) is None
