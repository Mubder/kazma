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

The class gate: every product function that builds a resume command
(``build_resume_command``) or continues a turn from its checkpoint (a drive
with no input) binds the asker -- ``as_the_asker`` or ``bind_scope`` in the
same function -- or is declared here with why. A planted unbound resume is
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
    "kazma-core/kazma_core/agent/hitl_supersede.py::cancel_pending_hitl._deny_resume_cmd":
        "a deny of the chat's leftover card before the same person's new turn, in that person's "
        "request: another member's message is refused before it (sse_chat._card_waits_on_someone_else)",
    "kazma-core/kazma_core/cli/ask.py::_invoke_supervisor":
        "kazma ask on the machine, bound as the admin by spaces.machine before the turn starts",
    "kazma-gateway/kazma_gateway/agent_handler/graph.py::create_graph_handler._handler_body":
        "a chat app's hard steer, inside the handler's own scope (the owner's until S3-04 binds "
        "the card's asker)",
    "kazma-gateway/kazma_gateway/agent_handler/hitl.py::_handle_hitl_resume":
        "a chat app's /hitl decision, in the owner's scope until S3-04 binds the card's asker",
}

_BINDERS = {"as_the_asker", "bind_scope"}


def _resumes(tree: ast.AST) -> dict[str, bool]:
    """Every function holding a resume, and whether it binds a scope."""
    found: dict[str, bool] = {}
    stack: list[ast.AST] = []

    def binds(fn: ast.AST) -> bool:
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
                if name in _BINDERS:
                    return True
        return False

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.AST) -> None:
            stack.append(node)
            self.generic_visit(node)
            stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node: ast.Call) -> None:
            f = node.func
            name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
            resumes = name == "build_resume_command" or (
                name in ("_drive_graph_to_journal", "drive") and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant) and node.args[1].value is None)
            if resumes and stack:
                key = ".".join(getattr(fn, "name", "?") for fn in stack)
                found[key] = found.get(key, False) or binds(stack[-1])
            self.generic_visit(node)

    Visitor().visit(tree)
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
    with as_the_asker(whose):
        cmd = build_resume_command(action="apply")
'''
    assert sorted(_unbound_resumes({"planted.py": planted})) == [
        "planted.py::continue_it", "planted.py::resume_for_someone"]


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
