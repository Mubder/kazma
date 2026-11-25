"""Whoever asked keeps being who asked: a fork, ``/compact`` and a hydrate
keep each question's author (Spaces S1-08; found by the chat move, 25 Nov 2026).

A fork's rows named nobody, so in a shared space its maker could not take it
out (``others_wrote``) and its turns were the opener's. ``/compact`` on the web
replaced the chat's row with the model's own messages, on every install: the
base prompt, "About me" and recalled memory were stored in the chat, and its
authors, times and parts were lost. ``reply_sink.carry_authors`` is the one
helper; the row is not touched by a compaction at all.
"""

from __future__ import annotations

import ast
import subprocess
import tempfile
import types
from pathlib import Path

import pytest
from kazma_ui import reply_sink, thread_rewind

from tests import _tt_graph as tt
from tests._chat_move_world import world  # noqa: F401 - a fixture

ROOT = Path(__file__).resolve().parents[1]

LANGUAGES = {"en": ("the blue tent", "the red tent"), "ar": ("الخيمة الزرقاء", "الخيمة الحمراء")}


def _rows(w, space, session, who="reem"):
    return [(m.get("role"), m.get("author", ""), m["content"]) for m in w.row(space, who, session).messages]


def _state_of(w, space, thread):
    async def read():
        return await w.held["graph"].aget_state(tt.config(thread))

    return dict(w.run(space, "reem", read).values)


def _fork(w, space, thread, messages, who="reem", at="2026-11-25T09:00:00+00:00"):
    """Fork the step whose state holds *messages*, as the routes do."""
    state = {**_state_of(w, space, thread), "messages": messages}
    record = types.SimpleNamespace(truncated=False, turn_index=1, seq=1, get_state=lambda: state,
                                   timestamp=at)
    recorder = types.SimpleNamespace(get=lambda *a, **k: record)

    async def fork():
        return await thread_rewind.fork_thread(
            w.held["graph"], thread, "step", actor=who, surface="web", recorder=recorder)

    return w.run(space, who, fork)["session_id"]


def _said(*texts):
    out = []
    for text in texts:
        out += [{"role": "user", "content": text}, {"role": "assistant", "content": f"answer to {text}"}]
    return out


def _chat(w, space, *asked):
    """A chat Reem opened, whose questions are (who, text)."""
    session, thread = w.chat("reem", space, mark="open")
    w.turn("reem", space, thread, "open")
    with_rows = []
    for n, (who, text) in enumerate(asked):
        with_rows += [{"role": "user", "content": text, "author": w.ids[who], "ts": f"2026-11-25T08:{n:02d}:00+00:00"},
                      {"role": "assistant", "content": f"answer to {text}"}]
    from kazma_core.spaces import scope
    from kazma_ui import session_manager

    with scope.bind_scope(space, w.ids["reem"]), session_manager.get_session_manager().transact(session) as row:
        row.messages = with_rows
    return session, thread


# ── A fork ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("lang", LANGUAGES)
def test_a_fork_keeps_who_asked_and_can_be_taken_out(world, lang):
    w, i = world, world.ids
    one, two = LANGUAGES[lang]
    _session, thread = _chat(w, i["family"], ("reem", one), ("reem", two))
    fork = _fork(w, i["family"], thread, _said(one, two))
    assert [r[1] for r in _rows(w, i["family"], fork) if r[0] == "user"] == [i["reem"], i["reem"]]
    w.moved("reem", i["family"], fork, i["mine"])


def test_two_authors_stay_two_and_the_turn_is_its_asker_s(world):
    from kazma_ui.hitl_answerers import _turn_author

    w, i = world, world.ids
    _session, thread = _chat(w, i["family"], ("reem", "yes"), ("omar", "yes"), ("omar", "the tent"))
    fork = _fork(w, i["family"], thread, _said("yes", "yes", "the tent"))
    assert [r[1] for r in _rows(w, i["family"], fork) if r[0] == "user"] == [i["reem"], i["omar"], i["omar"]]
    assert _turn_author(fork, i["family"], i["reem"]) == i["omar"]
    answer = w.move("reem", i["family"], fork, i["mine"])
    assert answer.status_code == 409 and "others_wrote" in answer.text, answer.text


def test_a_fork_of_an_early_step_reads_the_rows_up_to_it(world):
    """Omar said the same words later: they are not the fork's."""
    w, i = world, world.ids
    _session, thread = _chat(w, i["family"], ("reem", "yes"), ("reem", "the tent"), ("omar", "yes"))
    fork = _fork(w, i["family"], thread, _said("yes"), at="2026-11-25T08:00:30+00:00")
    assert [r[1] for r in _rows(w, i["family"], fork) if r[0] == "user"] == [i["reem"]]


@pytest.mark.parametrize("lang", LANGUAGES)
def test_a_rewritten_question_is_the_one_person_s_or_nobody_s(world, lang):
    """The state keeps an attachment's rewrite, the row what was typed."""
    w, i = world, world.ids
    one, two = LANGUAGES[lang]
    rewritten = _said(f"{one} [attachment: plan.txt]", two)
    _session, alone = _chat(w, i["family"], ("reem", one), ("reem", two))
    fork = _fork(w, i["family"], alone, rewritten)
    assert [r[1] for r in _rows(w, i["family"], fork) if r[0] == "user"] == [i["reem"], i["reem"]]

    _session, shared = _chat(w, i["family"], ("reem", one), ("omar", two))
    fork = _fork(w, i["family"], shared, rewritten)
    assert [r[1] for r in _rows(w, i["family"], fork) if r[0] == "user"] == ["", i["omar"]]


def test_the_fork_without_the_helper_names_nobody(world, monkeypatch):
    """Control: the old fork."""
    w, i = world, world.ids
    _session, thread = _chat(w, i["family"], ("reem", "the tent"))
    monkeypatch.setattr(thread_rewind, "_fork_rows", lambda _t, state, _at: thread_rewind._projected_transcript(state))
    fork = _fork(w, i["family"], thread, _said("the tent"))
    assert w.move("reem", i["family"], fork, i["mine"]).status_code == 409


# ── In doubt, nobody (review M4, 25 Nov 2026) ─────────────────────────────


@pytest.mark.parametrize("yes", ["yes", "نعم"])
def test_a_trimmed_state_does_not_make_anothers_question_mine(world, yes):
    """The state holds its newest question only: Omar's. The fork called it
    Reem's, who asked the same words once, and let her take it out."""
    w, i = world, world.ids
    _session, thread = _chat(w, i["family"], ("reem", yes), ("omar", "my secret plan"), ("omar", yes))
    fork = _fork(w, i["family"], thread, _said(yes))
    assert [r[1] for r in _rows(w, i["family"], fork) if r[0] == "user"] == [""]
    answer = w.move("reem", i["family"], fork, i["mine"])
    assert answer.status_code == 409 and "others_wrote" in answer.text, answer.text
    for who in ("omar", "reem"):  # nor is it a mover's own by any other way
        assert w.move(who, i["family"], fork, i["mine" if who == "reem" else "omars"]).status_code in (403, 409)


def test_a_trimmed_state_that_kept_later_questions_is_in_doubt_too(world):
    w, i = world, world.ids
    asked = (("reem", "yes"), ("omar", "yes"), ("omar", "the plan"), ("reem", "yes"))
    _session, thread = _chat(w, i["family"], *asked)
    fork = _fork(w, i["family"], thread, _said("the plan", "yes"))
    assert [r[1] for r in _rows(w, i["family"], fork) if r[0] == "user"] == [i["omar"], ""]
    # Control: the whole state, in order, is not in doubt.
    fork = _fork(w, i["family"], thread, _said("yes", "yes", "the plan", "yes"))
    assert [r[1] for r in _rows(w, i["family"], fork) if r[0] == "user"] == [i[who] for who, _t in asked]


def test_rows_with_no_time_are_in_doubt(world):
    """A chat app's rows name no time: where the step stands among them is a
    guess, so a text two asked is nobody's unless the state holds them all."""
    from kazma_core.spaces import scope
    from kazma_ui import session_manager

    w, i = world, world.ids
    session, thread = _chat(w, i["family"], ("reem", "yes"), ("reem", "the tent"), ("omar", "yes"))
    with scope.bind_scope(i["family"], i["reem"]), session_manager.get_session_manager().transact(session) as row:
        row.messages = [{k: v for k, v in m.items() if k != "ts"} for m in row.messages]
    fork = _fork(w, i["family"], thread, _said("yes"), at="2026-11-25T08:00:30+00:00")
    assert [r[1] for r in _rows(w, i["family"], fork) if r[0] == "user"] == [""]
    fork = _fork(w, i["family"], thread, _said("yes", "the tent", "yes"))
    assert [r[1] for r in _rows(w, i["family"], fork) if r[0] == "user"] == [i["reem"], i["reem"], i["omar"]]


def test_kazma_s_own_prompt_is_nobody_s(world):
    from kazma_core.agent.plan_fence import AUTO_CONTINUE_PROMPT, PLAN_EXECUTE_CONTINUE

    w, i = world, world.ids
    _session, thread = _chat(w, i["family"], ("reem", "the tent"))
    state = [*_said("the tent"), {"role": "user", "content": PLAN_EXECUTE_CONTINUE},
             {"role": "assistant", "content": "working"}, {"role": "user", "content": AUTO_CONTINUE_PROMPT},
             {"role": "assistant", "content": "done"}]
    fork = _fork(w, i["family"], thread, state)
    assert [r for r in _rows(w, i["family"], fork) if r[0] == "user"] == [("user", i["reem"], "the tent")]
    w.moved("reem", i["family"], fork, i["mine"])  # her own fork is hers to take out


def test_a_person_s_words_that_look_like_kazma_s_stay_in_the_fork(world):
    from kazma_core.agent.plan_fence import AUTO_CONTINUE_PROMPT

    w, i = world, world.ids
    _session, thread = _chat(w, i["family"], ("reem", "the tent"), ("omar", AUTO_CONTINUE_PROMPT))
    fork = _fork(w, i["family"], thread, _said("the tent", AUTO_CONTINUE_PROMPT))
    assert len([r for r in _rows(w, i["family"], fork) if r[0] == "user"]) == 2
    assert "others_wrote" in w.move("reem", i["family"], fork, i["mine"]).text


def test_a_question_the_rows_do_not_hold_is_not_the_one_of_the_same_words(world):
    """The state holds the words twice, the rows once: whose the other is, is
    not known -- nor which of the two the row is."""
    out = reply_sink.carry_authors(
        [{"role": "user", "content": "yes", "author": "r"}, {"role": "user", "content": "no", "author": "o"}],
        [{"role": "user", "content": "yes"}, {"role": "user", "content": "yes"}])
    assert [m.get("author") for m in out] == [None, None]


def test_a_hydrate_of_a_trimmed_state_names_nobody_in_doubt(world):
    from kazma_core.spaces import scope
    from kazma_ui import session_manager

    w, i = world, world.ids
    thread = "gw-telegram-hydrate2"
    w.chat("reem", i["family"], session_id=thread, thread_id=thread)
    with scope.bind_scope(i["family"], i["reem"]), session_manager.get_session_manager().transact(thread) as row:
        row.messages = [{"role": "user", "content": "yes", "author": i["reem"]},
                        {"role": "user", "content": "yes", "author": i["omar"]},
                        {"role": "user", "content": "yes", "author": i["omar"]}]

    async def state():
        await w.held["graph"].aupdate_state(tt.config(thread), {"messages": _said("yes", "yes")[:3]}, as_node="respond")

    w.run(i["family"], "reem", state)
    got = w.call("reem", "GET", f"/api/chat/sessions/{thread}/messages", space=i["family"])
    assert got.status_code == 200, got.text
    rows = _rows(w, i["family"], thread)
    assert [r[0] for r in rows] == ["user", "assistant", "user"], "it was hydrated"
    assert [r[1] for r in rows if r[0] == "user"] == ["", ""]


def test_control_the_k_th_newest_named_the_wrong_person(world, monkeypatch):
    """The matching as it was: the newest row of the same text in the rows cut
    by the first one."""
    w, i = world, world.ids

    def guessed(existing, out, author="", *, one_person=False, among=None):
        for m in out:
            m["author"] = next((r.get("author", "") for r in existing if r["content"] == m["content"]), "")
        return out

    monkeypatch.setattr(reply_sink, "carry_authors", guessed)
    _session, thread = _chat(w, i["family"], ("reem", "yes"), ("omar", "my secret plan"), ("omar", "yes"))
    fork = _fork(w, i["family"], thread, [m for m in _said("yes")])
    assert [r[1] for r in _rows(w, i["family"], fork) if r[0] == "user"] == [i["reem"]]
    w.moved("reem", i["family"], fork, i["mine"])  # the fault: Omar's words leave


# ── /compact ──────────────────────────────────────────────────────────────


def _compact(w, space, session, thread):
    """Run the web's /compact on a state as a real turn leaves it."""
    async def state():
        await w.held["graph"].aupdate_state(tt.config(thread), {
            "workspace_root": tempfile.gettempdir(),
            "messages": [{"role": "system", "content": "BASE PROMPT. About me: a private line"}, *_said("the tent")],
        }, as_node="respond")

    w.run(space, "reem", state)
    return w.call("reem", "POST", "/api/chat/stream", space=space,
                  json={"message": "/compact", "session_id": session, "thread_id": thread})


@pytest.mark.parametrize("where", ["family", "mine"])
def test_compact_leaves_the_chat_s_row_as_it_was_said(world, where):
    w, i = world, world.ids
    session, thread = _chat(w, i[where], ("reem", "الخيمة"), ("reem", "the tent"))
    before = [dict(m) for m in w.row(i[where], "reem", session).messages]
    answer = _compact(w, i[where], session, thread)
    assert answer.status_code == 200 and "compaction completed" in answer.text, answer.text
    after = w.row(i[where], "reem", session).messages
    assert after[: len(before)] == before, "authors, times and parts are kept"
    assert [m["role"] for m in after[len(before):]] == ["user", "assistant"], "and the command's own line"
    assert not [m for m in after if m["role"] == "system"]
    assert "private line" not in str(after)
    if where == "family":
        assert [m.get("author") for m in after if m["role"] == "user"] == [i["reem"]] * 3
        w.moved("reem", i["family"], session, i["mine"])


def test_compact_still_runs_the_compaction(world, monkeypatch):
    from kazma_ui import turn_runtime

    w, i = world, world.ids
    session, thread = _chat(w, i["family"], ("reem", "the tent"))
    ran = []
    real = turn_runtime.invoke_turn

    async def invoke(graph, values, config, **kwargs):
        ran.append(values.get("needs_compaction"))
        return await real(graph, values, config, **kwargs)

    monkeypatch.setattr(turn_runtime, "invoke_turn", invoke)
    _compact(w, i["family"], session, thread)
    assert ran == [True]


# ── A hydrate ─────────────────────────────────────────────────────────────


def test_a_hydrated_chat_app_s_chat_keeps_its_authors(world):
    """The row has the questions and no answer yet, the newest still running;
    the page fills it from the checkpoint."""
    from kazma_core.spaces import scope
    from kazma_ui import session_manager

    w, i = world, world.ids
    thread = "gw-telegram-hydrate1"
    w.chat("reem", i["family"], session_id=thread, thread_id=thread)
    with scope.bind_scope(i["family"], i["reem"]), session_manager.get_session_manager().transact(thread) as row:
        row.messages = [{"role": "user", "content": "yes", "author": i["reem"]},
                        {"role": "user", "content": "yes", "author": i["omar"]}]

    async def state():
        await w.held["graph"].aupdate_state(tt.config(thread), {"messages": _said("yes", "yes")[:3]}, as_node="respond")

    w.run(i["family"], "reem", state)
    got = w.call("reem", "GET", f"/api/chat/sessions/{thread}/messages", space=i["family"])
    assert got.status_code == 200, got.text
    rows = _rows(w, i["family"], thread)
    assert [r[0] for r in rows] == ["user", "assistant", "user"], "it was hydrated"
    assert [r[1] for r in rows if r[0] == "user"] == [i["reem"], i["omar"]]


# ── The gate: every replacement of a row's messages is declared ───────────

#: (file, function) -> how the authors of the rows it replaces are kept.
REPLACES = {
    ("kazma-ui/kazma_ui/sse_chat/__init__.py", "_clear"): "/reset: nothing is left to name anyone",
    ("kazma-gateway/kazma_gateway/agent_handler/graph.py", "_clear_web_chat"): "/reset on a chat app: nothing is left",
    ("kazma-ui/kazma_ui/sse_chat/__init__.py", "_hydrate"): "carry_authors",
    ("kazma-gateway/kazma_gateway/agent_handler/graph.py", "_sync_platform_session_to_web"): "carry_authors",
    ("kazma-ui/kazma_ui/thread_rewind.py", "_rewind_transcript"): "a prefix of the row's own messages",
    ("kazma-ui/kazma_ui/thread_rewind.py", "_save_fork_chat"): "the rows of _fork_rows: carry_authors",
    ("kazma-ui/kazma_ui/chat_move.py", "_hand_over"): "the rows of _rows_to_copy: each names its author",
    ("kazma-ui/kazma_ui/reply_sink.py", "<any>"): "the row's own messages, one reply changed or added",
    ("kazma-ui/kazma_ui/session_manager.py", "<any>"): "the store: what it was given or read",
}


def _replacements(source: str) -> list[str]:
    """The functions of *source* that set a session's messages as a whole."""
    found: list[str] = []

    def visit(node: ast.AST, inside: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            inside = node.name
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, (ast.AnnAssign, ast.AugAssign)) else [])
        for target in targets:
            if isinstance(target, ast.Subscript):
                target = target.value if isinstance(target.slice, ast.Slice) else target
            if isinstance(target, ast.Attribute) and target.attr == "messages" and not (
                isinstance(target.value, ast.Name) and target.value.id == "self"
            ):
                found.append(inside)
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", getattr(node.func, "attr", ""))
            if name == "ChatSession" and any(k.arg == "messages" for k in node.keywords):
                found.append(inside)
            if (name == "setattr" and len(node.args) == 3 and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value == "messages"):
                found.append(inside)
        for child in ast.iter_child_nodes(node):
            visit(child, inside)

    visit(ast.parse(source), "")
    return found


def _undeclared(sources: dict[str, str]) -> list[tuple[str, str]]:
    return [(path, name) for path, source in sources.items() for name in _replacements(source)
            if (path, name) not in REPLACES and (path, "<any>") not in REPLACES]


def _product() -> dict[str, str]:
    listed = subprocess.run(["git", "ls-files", "kazma-ui", "kazma-gateway", "kazma-tui", "kazma-cli", "kazma-core"],
                            cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    out = {}
    for path in listed:
        if path.endswith(".py") and "_tests/" not in path:
            text = (ROOT / path).read_text(encoding="utf-8")
            if "messages" in text and "ession" in text:
                out[path] = text
    return out


def test_every_place_that_replaces_a_row_s_messages_is_declared():
    sources = _product()
    assert not _undeclared(sources)
    real = {(path, name) for path, source in sources.items() for name in _replacements(source)}
    gone = [k for k in REPLACES if k not in real and not (k[1] == "<any>" and any(p == k[0] for p, _ in real))]
    assert not gone, "declared and not there"


def test_the_gate_sees_a_planted_rewrite():
    sources = _product()
    planted = {
        "kazma-ui/kazma_ui/planted.py": "def a(sess):\n    sess.messages = made\n",
        "kazma-ui/kazma_ui/sliced.py": "async def b(sess):\n    sess.messages[:] = made\n",
        "kazma-ui/kazma_ui/built.py": "def c():\n    return sm.ChatSession(session_id='x', messages=made)\n",
        "kazma-ui/kazma_ui/set.py": "def d(sess):\n    setattr(sess, 'messages', made)\n",
    }
    assert len(_undeclared({**sources, **planted})) == len(planted)


def test_those_declared_to_carry_authors_do():
    """A declared carrier calls the helper (the fork through _fork_rows)."""
    sources = _product()
    for (path, name), how in REPLACES.items():
        if "carry_authors" in how:
            tree = ast.parse(sources[path])
            wanted = "_fork_rows" if name == "_save_fork_chat" else name
            function = next(n for n in ast.walk(tree) if getattr(n, "name", "") == wanted)
            called = {getattr(c.func, "id", getattr(c.func, "attr", "")) for c in ast.walk(function)
                      if isinstance(c, ast.Call)}
            assert "carry_authors" in called, (path, name)


def test_carry_authors_reads_parts_and_blank_edges():
    out = reply_sink.carry_authors(
        [{"role": "user", "content": " the tent\n", "author": "a"}],
        [{"role": "user", "content": [{"type": "text", "text": "the tent"}]}])
    assert out[0]["author"] == "a"
