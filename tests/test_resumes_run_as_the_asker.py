"""Every resume runs as the turn's asker (the S2-05 review, finding 7).

A turn that goes on after a pause runs as the person whose turn it is --
never as whoever pressed, and never unbound (a turn bound to nobody runs as
its chat's opener). Two resumes did not:

* a turn a restart cut off (``turn_resume.continue_turn``: the start pass,
  and Continue on Running work) ran unbound: once Spaces was enforced the
  chat store refused it, and anywhere it ran it was its chat's opener's --
  in a shared chat, Omar's for Reem's turn;
* a hard steer resumed the turn in whoever steered's request: another member
  could run Reem's turn as themself.

Now ``continue_turn`` binds the turn's asker (``hitl_answerers.whose_turn``:
the author of the chat's newest message) and does not go on when that person
has left the space, and a hard steer by anyone but the asker is refused.

The class gate, per call site (the second review: a binding anywhere in
a function passed every resume in it): every call that runs a resume -- a
resume command (``build_resume_command``, or a function that builds and
returns one) or a turn continued from its checkpoint (``None``) given to
what runs a turn, or handed on to a chat app's resumer -- sits inside
``with as_the_asker(...)`` or ``with bind_scope(...)``, or its function is
declared here with why. Planted unbound resumes, one beside a binding, are
its control.
"""

from __future__ import annotations

import ast
import asyncio
import secrets
from pathlib import Path
from typing import Any

import pytest
from kazma_core.spaces import identity, scope, threads
from kazma_core.spaces.scope import bind_scope

from tests.test_approvals_reach_the_asker import team  # noqa: F401 -- the shared fixture

pytestmark = pytest.mark.real_features

REPO = Path(__file__).resolve().parents[1]
ROOTS = ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway")

#: Resumes that bind no asker in their own function, and why that is right.
DECLARED = {
    "kazma-core/kazma_core/agent/hitl_supersede.py::cancel_pending_hitl":
        "a deny of the chat's leftover card before the same person's new turn, in that person's "
        "request: another member's message is refused before it (sse_chat._card_waits_on_someone_else)",
    "kazma-core/kazma_core/cli/ask.py::_invoke_supervisor":
        "kazma ask on the machine, bound as the admin by spaces.machine before the turn starts",
    "kazma-gateway/kazma_gateway/agent_handler/graph.py::create_graph_handler._handler_body":
        "a chat app's hard steer, inside the handler's own scope (the owner's until S3-04 binds "
        "the card's asker)",
    "kazma-gateway/kazma_gateway/agent_handler/hitl.py::make_continue_resumer.continue_after_restart":
        "runs in the task turn_resume.continue_turn starts inside as_the_asker(whose_turn)",
    "kazma-gateway/kazma_gateway/agent_handler/hitl.py::_handle_hitl_resume":
        "a chat app's /hitl decision, in the owner's scope until S3-04 binds the card's asker",
}

_BINDERS = {"as_the_asker", "bind_scope"}
#: The calls that run a turn from a given input: a resume runs when its input
#: is a resume command, or None (a turn continued from its checkpoint).
_RUNNERS = {"_drive_graph_to_journal", "drive", "invoke_turn", "_run_resume", "_resume_into_chat"}
#: Graph calls whose first argument is the input.
_GRAPH_RUNNERS = {"ainvoke", "astream", "astream_events"}
#: Keywords that hand a resume on to whatever runs it (a chat app's
#: resumer; ``run_agent_turn``'s ``state``).
_RESUME_KEYWORDS = {"resume_cmd", "resume_input", "state"}
#: Calls that continue a cut turn, whatever they are given.
_CONTINUERS = {"_continuer"}


#: ``import ... as`` names of the module being read (``invoke_turn as
#: _invoke_steer``), set by :func:`_resumes`.
_ALIASES: dict[str, str] = {}
#: The module's functions that build a resume command and return it (a
#: call of one is a resume, ``hitl_supersede``'s ``_deny_resume_cmd``).
_BUILDERS: set[str] = set()


def _name_of(func: ast.AST) -> str:
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
    return _ALIASES.get(name, name)


def _own(fn: ast.AST):
    """*fn*'s own nodes: not those of a function defined inside it."""
    todo = list(ast.iter_child_nodes(fn))
    while todo:
        node = todo.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield node
        todo.extend(ast.iter_child_nodes(node))


def _resume_names(fn: ast.AST) -> set[str]:
    """The names *fn* binds to a resume command (``build_resume_command``)."""
    out: set[str] = set()
    for node in _own(fn):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Call)                 and _name_of(node.value.func) == "build_resume_command":
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            out |= {t.id for t in targets if isinstance(t, ast.Name)}
    return out


def _is_resume(value: ast.AST | None, names: set[str], *, none: bool = True) -> bool:
    if isinstance(value, ast.Await):
        value = value.value
    if none and isinstance(value, ast.Constant) and value.value is None:
        return True
    if isinstance(value, ast.Name) and value.id in names:
        return True
    return isinstance(value, ast.Call) and _name_of(value.func) in ({"build_resume_command"} | _BUILDERS)


def _runs_a_resume(call: ast.Call, names: set[str]) -> bool:
    """Whether *call* runs a resume (or hands one on to what runs it)."""
    name = _name_of(call.func)
    if name in _CONTINUERS:
        return True
    if name in _RUNNERS and len(call.args) >= 2 and _is_resume(call.args[1], names):
        return True
    if name in _GRAPH_RUNNERS and call.args and _is_resume(call.args[0], names):
        return True
    return any(k.arg in _RESUME_KEYWORDS and _is_resume(k.value, names, none=k.arg != "state")
               for k in call.keywords)


def _resumes(tree: ast.AST) -> dict[str, bool]:
    """Every function holding a call that runs a resume, and whether EACH
    such call sits inside an asker binding (``with as_the_asker(...)`` or
    ``with bind_scope(...)``) in that function: a binding elsewhere in the
    function does not bind it (the second review)."""
    found: dict[str, bool] = {}

    def visit(fn: ast.AST, key: str) -> None:
        names = _resume_names(fn)

        def walk(node: ast.AST, bound: bool) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    visit(child, f"{key}.{child.name}")
                    continue
                if isinstance(child, ast.Lambda):
                    continue
                inner = bound
                if isinstance(child, (ast.With, ast.AsyncWith)) and any(
                        isinstance(i.context_expr, ast.Call) and _name_of(i.context_expr.func) in _BINDERS
                        for i in child.items):
                    inner = True
                if isinstance(child, ast.Call) and _runs_a_resume(child, names):
                    found[key] = found.get(key, True) and inner
                walk(child, inner)

        walk(fn, False)

    _ALIASES.clear()
    _BUILDERS.clear()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            _ALIASES.update({a.asname: a.name for a in node.names if a.asname})
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", "")) == "build_resume_command"
                for n in _own(node)) and any(isinstance(n, ast.Return) and n.value is not None for n in _own(node)):
            _BUILDERS.add(node.name)
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            visit(node, node.name)
        elif isinstance(node, ast.ClassDef):
            for item in ast.iter_child_nodes(node):
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    visit(item, f"{node.name}.{item.name}")
    return found


def _unbound_resumes(sources: dict[str, str]) -> list[str]:
    out: list[str] = []
    for rel, text in sources.items():
        for function, bound in _resumes(ast.parse(text)).items():
            key = f"{rel}::{function}"
            if not bound and key not in DECLARED:
                out.append(key)
    return out


def _product_sources() -> dict[str, str]:
    out: dict[str, str] = {}
    for root in ROOTS:
        for path in (REPO / root).rglob("*.py"):
            if {"tests", "__pycache__", "_tests"} & set(path.parts):
                continue
            out[path.relative_to(REPO).as_posix()] = path.read_text(encoding="utf-8")
    return out


def test_every_resume_binds_the_asker_or_is_declared() -> None:
    sources = _product_sources()
    assert _unbound_resumes(sources) == []
    seen = {f"{rel}::{fn}" for rel, text in sources.items() for fn in _resumes(ast.parse(text))}
    assert set(DECLARED) <= seen, sorted(set(DECLARED) - seen)


def test_control_a_planted_unbound_resume_fails_the_gate() -> None:
    planted = '''
async def resume_for_someone(graph, config):
    cmd = build_resume_command(action="apply")
    await graph.ainvoke(cmd, config)

async def continue_it(graph, config):
    await _drive_graph_to_journal(graph, None, config, thread_id="t")

async def bound_resume(graph, config, whose):
    cmd = build_resume_command(action="apply")
    with as_the_asker(whose):
        await graph.ainvoke(cmd, config)

async def binds_elsewhere(graph, config, whose):
    with as_the_asker(whose):
        pass
    cmd = build_resume_command(action="apply")
    await _drive_graph_to_journal(graph, cmd, config, thread_id="t")

async def hands_it_on(graph, config, whose):
    cmd = build_resume_command(action="apply")
    _chat_app_resumer[1](resume_cmd=cmd, resume_config=config)

async def continues_unbound(graph, config):
    await _resume_into_chat(graph, None, None, resume_cmd=None, resume_config=config)
'''
    assert sorted(_unbound_resumes({"planted.py": planted})) == [
        "planted.py::binds_elsewhere", "planted.py::continue_it", "planted.py::continues_unbound",
        "planted.py::hands_it_on", "planted.py::resume_for_someone"]


def test_control_the_function_level_rule_missed_a_resume_beside_a_binding() -> None:
    """The gate before the second review passed a function holding any
    binder: a resume run outside it went unseen."""
    planted = ast.parse('''
async def binds_elsewhere(graph, config, whose):
    with as_the_asker(whose):
        pass
    cmd = build_resume_command(action="apply")
    await _drive_graph_to_journal(graph, cmd, config, thread_id="t")
''')
    function = planted.body[0]
    assert any(isinstance(n, ast.Call) and getattr(n.func, "id", "") in _BINDERS for n in ast.walk(function))
    assert _resumes(planted) == {"binds_elsewhere": False}


# ── continue_turn ──────────────────────────────────────────────────────


def _cut_turn_continued(team: Any, *, unbind: bool = False, reem_left: bool = False) -> tuple[bool, str, list[Any]]:
    """A turn of Reem's in a chat Omar opened, cut off by a restart: what
    ``continue_turn`` said, and as whom its drive ran."""
    from contextlib import nullcontext

    from kazma_ui import hitl_answerers
    from kazma_ui.session_manager import get_session_manager
    from kazma_ui.turn_resume import CutTurn, continue_turn

    reem, omar = team.accounts["reem"], team.accounts["omar"]
    thread, session_id = f"t{secrets.token_hex(6)}", f"s{secrets.token_hex(6)}"
    with bind_scope(team.space, omar):
        threads.record_thread(thread, team.space, omar)
        store = get_session_manager()
        session = store.get_or_create(session_id, thread_id=thread)
        session.messages.append({"role": "user", "content": "start it", "author": omar})
        session.messages.append({"role": "user", "content": "now my part", "author": reem})
        store.put(session)
    if reem_left:
        def leave(reg: dict[str, Any]) -> None:
            reg["memberships"] = [m for m in reg["memberships"]
                                  if not (m.get("account") == reem and m.get("space") == team.space)]

        identity.update_registry(leave)
    ran: list[Any] = []

    async def drive(*_a: Any, **_k: Any) -> None:
        bound = scope.current_scope()
        ran.append((bound.space_id, bound.asker) if bound else None)

    async def go() -> tuple[bool, str]:
        started, words = await continue_turn(object(), CutTurn(thread_id=thread, at=0.0, next=("supervisor",)),
                                             drive=drive)
        for _ in range(100):
            if ran or not started:
                break
            await asyncio.sleep(0.01)
        return started, words

    with pytest.MonkeyPatch.context() as patch:
        if unbind:
            patch.setattr(hitl_answerers, "as_the_asker", lambda _whose: nullcontext())
        started, words = asyncio.run(go())
    return started, words, ran


def test_a_cut_turn_continues_as_its_asker(team) -> None:
    started, _words, ran = _cut_turn_continued(team)
    assert started and ran == [(team.space, team.accounts["reem"])], ran


def test_control_a_cut_turn_continued_unbound(team) -> None:
    """Today: nothing bound. Once Spaces is enforced the chat store refused
    the unbound read and the turn never went on; before that (or past it)
    it ran as nobody, which ``turn_segment_budget`` makes the chat's opener
    (Omar)."""
    with pytest.raises(scope.NoSpaceBound):
        _cut_turn_continued(team, unbind=True)


def test_a_cut_turn_of_a_departed_asker_does_not_go_on(team) -> None:
    started, words, ran = _cut_turn_continued(team, reem_left=True)
    assert not started and "no longer in its space" in words and ran == [], (words, ran)


def test_with_spaces_off_a_cut_turn_continues_as_before(team, monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", False)
    started, _words, ran = _cut_turn_continued(team)
    assert started and ran == [None]


# ── the hard steer ─────────────────────────────────────────────────────


def _steer_whose(team: Any, who: str) -> Any:
    from kazma_ui.session_manager import get_session_manager
    from kazma_ui.sse_chat import _hard_steer_whose

    reem = team.accounts["reem"]
    thread, session_id = f"t{secrets.token_hex(6)}", f"s{secrets.token_hex(6)}"
    with bind_scope(team.space, reem):
        threads.record_thread(thread, team.space, reem)
        store = get_session_manager()
        session = store.get_or_create(session_id, thread_id=thread)
        session.messages.append({"role": "user", "content": "go", "author": reem})
        store.put(session)
    with bind_scope(team.space, team.accounts[who]):
        return asyncio.run(_hard_steer_whose(thread))


def test_only_the_turns_asker_hard_steers_it(team) -> None:
    refused = _steer_whose(team, "omar")
    assert refused["ok"] is False and refused["reason"] == "not_your_turn" and "omar" not in refused["message"]
    from kazma_ui.hitl_answerers import GateWhose

    whose = _steer_whose(team, "reem")
    assert isinstance(whose, GateWhose) and whose.asker == team.accounts["reem"], whose


def test_the_steer_route_binds_the_turns_asker_for_its_resume() -> None:
    """The route's resume sits in ``as_the_asker`` (the class gate above
    reads it); the refusal comes before anything is queued."""
    text = (REPO / "kazma-ui/kazma_ui/sse_chat/__init__.py").read_text(encoding="utf-8")
    steer = text[text.index("async def steer_chat_turn"):text.index("async def abort_chat_turn")]
    assert steer.index("_hard_steer_whose(thread_id, text)") < steer.index("push_hard_steer(thread_id, text)")
    assert "with as_the_asker(_steer_whose):" in steer
