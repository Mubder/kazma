"""A restore cuts a chat's transcript where the step stands, or not at all
(the review of the chat move's leftovers, M6, 25 Nov 2026).

``/compact`` on the web no longer rewrites the chat's row, so the state holds
its newest question and the row holds them all. The cut took the row's first
question of the same words, or counted the state's questions: "continue",
asked twice, kept 4 of 10 rows, and a question with an attachment 2 of 10. It
is found from the end now, among what was asked by the step's own time, and a
place that is not known cuts nothing and is said.
"""

from __future__ import annotations

import pytest
from kazma_core.agent.plan_fence import AUTO_CONTINUE_PROMPT
from kazma_core.memory.chat_history import epoch_of
from kazma_ui.thread_rewind import _norm, _text, _transcript_cut

from tests import _tt_graph as tt


def _at(minute: float) -> float:
    return epoch_of("2026-11-25T08:00:00+00:00") + minute * 60


def _rows(*texts: str, timed: bool = True) -> list[dict]:
    """A chat's rows: each question at its own minute (0, 1, 2, ...), and its
    answer half a minute later."""
    rows = []
    for n, text in enumerate(texts):
        stamp = {"ts": f"2026-11-25T08:{n:02d}:00+00:00"} if timed else {}
        rows += [{"role": "user", "content": text, **stamp},
                 {"role": "assistant", "content": f"answer {n} to {text}", **stamp}]
    return rows


def _state(*turns: tuple[int, str], answered: bool = True) -> list[dict]:
    """A checkpoint's messages: those turns (their number in the chat, their
    question as the state holds it)."""
    out: list[dict] = [{"role": "system", "content": "BASE PROMPT"}]
    for n, text in turns:
        out += [{"role": "user", "content": text}, {"role": "assistant", "content": f"answer {n} to {text}"}]
    return out if answered else out[:-1]


def _old_cut(rows, restored):
    """Before: the first row of the same words, or a count."""
    asked = [_norm(_text(m.get("content"))) for m in restored if m["role"] == "user"]
    if not asked:
        return 0
    last_user = max(i for i, m in enumerate(restored) if m["role"] == "user")
    answered = any(m["role"] == "assistant" for m in restored[last_user + 1:])
    users = [i for i, m in enumerate(rows) if m["role"] == "user"]
    pos, j = -1, 0
    for i in users:
        if j < len(asked) and _norm(rows[i]["content"]) == asked[j]:
            pos, j = i, j + 1
    if j < len(asked):
        if len(users) < len(asked):
            return len(rows)
        pos = users[len(asked) - 1]
    if not answered:
        return pos + 1
    later = [i for i in users if i > pos]
    return later[0] if later else len(rows)


FIVE = ("one", "continue", "three", "continue", "five")
CALLED_A = "the plan [attachment: plan.txt]"

#: (what it is, the rows, the state, the step's minute, what stays)
PLACES = [
    ("the whole state", _rows(*FIVE), _state((0, "one"), (1, "continue"), (2, "three")), 2.5, 6),
    ("the whole state, mid-turn", _rows(*FIVE), _state((0, "one"), (1, "continue"), (2, "three"), answered=False), 2.2, 5),
    ("a repeated question, the first", _rows(*FIVE), _state((0, "one"), (1, "continue")), 1.5, 4),
    ("a repeated question, the second", _rows(*FIVE),
     _state((0, "one"), (1, "continue"), (2, "three"), (3, "continue")), 3.5, 8),
    ("an ordinary trim", _rows(*FIVE), _state((2, "three"), (3, "continue")), 3.5, 8),
    ("/compact, a repeated question", _rows(*FIVE), _state((3, "continue")), 3.5, 8),
    ("/compact, a repeated question, mid-turn", _rows(*FIVE), _state((3, "continue"), answered=False), 3.2, 7),
    ("/compact, the first of the repeated", _rows(*FIVE), _state((1, "continue")), 1.5, 4),
    ("an attachment, whole", _rows("one", "the plan", "three"), _state((0, "one"), (1, CALLED_A)), 1.5, 4),
    ("an attachment, first", _rows("the plan", "two", "three"), _state((0, CALLED_A)), 0.5, 2),
    ("an attachment, /compact", _rows("one", "two", "three", "the plan", "five"), _state((3, CALLED_A)), 3.5, 8),
    ("an attachment, /compact, mid-turn", _rows("one", "two", "three", "the plan", "five"),
     _state((3, CALLED_A), answered=False), 3.2, 7),
    ("a steer", [*_rows("one", "two")[:3], {"role": "user", "content": "🔀 faster", "steer": True,
                                           "ts": "2026-11-25T08:01:10+00:00"}, *_rows("one", "two")[3:], *_rows("a", "b", "c")[4:]],
     [*_state((0, "one"), (1, "two"), answered=False), {"role": "user", "content": "[KAZMA STEER] ...\n\nfaster"},
      {"role": "assistant", "content": "answer 1 to two"}], 1.5, 5),
    ("Kazma's own prompt", _rows(*FIVE),
     [*_state((0, "one"), (1, "continue")), {"role": "user", "content": AUTO_CONTINUE_PROMPT},
      {"role": "assistant", "content": "done"}], 1.6, 4),
    ("an instant command between", _rows("one", "/yolo on", "three", "four"), _state((0, "one"), (2, "three")), 2.5, 6),
    ("a summary alone", _rows(*FIVE), [{"role": "system", "content": "## Summary"}], 2.5, 5),
]


@pytest.mark.parametrize(("rows", "state", "minute", "stays"), [p[1:] for p in PLACES], ids=[p[0] for p in PLACES])
def test_the_cut_is_where_the_step_stands(rows, state, minute, stays):
    assert _transcript_cut(rows, state, _at(minute)) == stays


#: The same with rows that name no time (a chat app's, a fork's).
UNTIMED = [(p[0], [{k: v for k, v in m.items() if k != "ts"} for m in p[1]], p[2], p[4]) for p in PLACES
           if p[0] not in ("a summary alone", "/compact, a repeated question, mid-turn", "a steer",
                           "an attachment, first", "an attachment, /compact", "an attachment, /compact, mid-turn")]


@pytest.mark.parametrize(("rows", "state", "stays"), [p[1:] for p in UNTIMED], ids=[p[0] for p in UNTIMED])
def test_rows_with_no_time_are_told_apart_by_what_was_said(rows, state, stays):
    assert _transcript_cut(rows, state, _at(99)) == stays
    assert _transcript_cut(rows, state) == stays


def test_an_instant_command_as_the_first_row():
    rows = _rows("/yolo on", "yes", "two", "yes", timed=False)
    for m in rows:
        if m["role"] == "assistant":
            m["content"] = "ok"  # the answers tell nothing
    state = [{"role": "user", "content": "yes"}, {"role": "assistant", "content": "ok"}]
    assert _transcript_cut(rows, state) == 4, "the state begins at the first question, not the command"


@pytest.mark.parametrize("at", [None, _at(3.5)])
def test_no_place_cuts_nothing(at):
    rows = _rows("one", "two", "three", "the plan", timed=False)
    assert _transcript_cut(rows, _state((7, CALLED_A)), at) is None
    assert _transcript_cut(rows, [{"role": "system", "content": "## Summary"}], at) is None
    assert _transcript_cut(_rows(*FIVE), _state((0, "one")), _at(-5)) is None, "every row is later than the step"


def test_the_start_of_a_chat_and_an_empty_transcript():
    assert _transcript_cut(_rows(*FIVE), [], _at(3)) == 0
    assert _transcript_cut([], _state((0, "one")), _at(3)) == 0


def test_never_less_than_before_stays_and_where_the_old_cut_lost_turns():
    """Control, and "not worse than before": on every case the old cut either
    agreed or was wrong."""
    wrong = [p[0] for p in PLACES if _old_cut(p[1], p[2]) != p[4]]
    assert {"/compact, a repeated question", "an attachment, /compact"} <= set(wrong)
    assert _old_cut(_rows(*FIVE), _state((3, "continue"))) == 4
    assert _old_cut(_rows("one", "two", "three", "the plan", "five"), _state((3, CALLED_A))) == 2
    for name, rows, state, minute, stays in PLACES:
        if name not in wrong:
            assert _transcript_cut(rows, state, _at(minute)) == _old_cut(rows, state), name


# ── Through the restore ───────────────────────────────────────────────────


@pytest.fixture()
def restored(tmp_path):
    """Restore a chat whose step holds *state*: (the answer, the rows left)."""
    import types

    from kazma_ui.session_manager import ChatSession, get_session_manager
    from kazma_ui.thread_rewind import rewind_thread
    from langgraph.checkpoint.memory import InMemorySaver

    def run(rows, state, at):
        thread = f"web-cut-{tt.__name__}-{len(rows)}-{at}"
        store = get_session_manager()
        store.put(ChatSession(session_id=thread, thread_id=thread, title="t", messages=[dict(m) for m in rows]))

        async def go():
            graph = tt.build_graph(None, [], InMemorySaver())
            await tt.run_turn(graph, thread, "hello")
            values = dict((await graph.aget_state(tt.config(thread))).values)
            record = types.SimpleNamespace(truncated=False, turn_index=1, seq=1, iteration=1, timestamp=at,
                                           get_state=lambda: {**values, "messages": state})
            recorder = types.SimpleNamespace(get=lambda *a, **k: record, forget_after=lambda *a: 0)
            return await rewind_thread(graph, thread, "step", actor="t", surface="web", recorder=recorder)

        try:
            return tt.run(go()), list(store.get(thread).messages)
        finally:
            store.delete(thread)

    return run


def test_a_restore_after_a_compact_keeps_what_came_before(restored):
    out, left = restored(_rows(*FIVE), _state((3, "continue")), "2026-11-25T08:03:30+00:00")
    assert left == _rows(*FIVE)[:8]
    assert out["transcript_rows_removed"] == 2 and out["transcript_kept"] is False


def test_a_restore_that_knows_no_place_keeps_the_rows_and_says_so(restored):
    rows = _rows("one", "two", "three", timed=False)
    out, left = restored(rows, _state((7, CALLED_A)), "2026-11-25T08:03:30+00:00")
    assert out["ok"] and out["transcript_kept"] is True and out["transcript_rows_removed"] == 0
    assert left == rows


def test_every_surface_says_the_rows_were_kept():
    from pathlib import Path

    from kazma_ui.i18n import t

    root = Path(__file__).resolve().parents[1]
    said = {lang: t("replay.transcript_kept", lang) for lang in ("en", "ar")}
    assert "none was removed" in said["en"] and "فلم يُحذف منها شيء" in said["ar"]
    for path in ("kazma-ui/kazma_ui/static/js/replay.js", "kazma-tui/kazma_tui/chat.py",
                 "kazma-gateway/kazma_gateway/agent_handler/graph.py"):
        assert "transcript_kept" in (root / path).read_text(encoding="utf-8"), path
