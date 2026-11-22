"""A chat app's message works in its sender's scope once Spaces is enforced.

Since stage 3 each sender is a linked account (S3-02) and the message works
in the place ``chat_apps.chat_place`` says (S3-03; its own tests are
``tests/test_chat_space_binding.py``): here the senders are the owner's own
linked accounts, so the turn runs as the owner in the owner's space. The
gateway's handler binds that scope before it reads or builds anything of
the chat; a chat recorded in another space (a member's web chat a sender
switched to) is refused with a reply, and the graph never runs.

Through the real handler on Telegram, Discord, Slack and WhatsApp: the turn
runs in the owner's scope and the answer is delivered. ``/x list`` reads in
the bound scope; with none bound it refuses, never the owner's by default.

Controls: the handler with no scope bound fails the turn (``NoSpaceBound``)
and sends nothing; the ``/x`` rule of before reads as the owner whatever is
bound.

Gate: every tenant or scope a chat-app path binds in the gateway comes from
this rule, or is declared.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from kazma_core.spaces import scope, threads
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID, NoSpaceBound
from kazma_gateway.gateway import IncomingMessage

ROOT = Path(__file__).resolve().parents[1]
ANSWER = "the answer " + uuid4().hex[:6]


class _Graph:
    """A graph that answers at once and records the scope it ran in."""

    def __init__(self) -> None:
        self.values: dict = {}
        self.scopes: list = []

    async def aget_state(self, config):
        return SimpleNamespace(values=self.values, next=(), tasks=())

    async def ainvoke(self, state, config):
        self.scopes.append(scope.current_scope())
        self.values = {**state, "messages": [*state["messages"],
                       {"role": "assistant", "content": ANSWER}]}
        return self.values


#: The owner's own account on each chat app (linked in ``enforced``).
OWNER_USER = "4242"


def _message(platform: str, text: str = "hello there") -> IncomingMessage:
    return IncomingMessage(platform, f"{platform}:{uuid4().hex}", text,
                           context_metadata={"chat_id": 42, "channel_id": "D42", "user_id": OWNER_USER,
                                             "chat_type": "private"})


async def _run(platform: str, graph: _Graph, text: str = "hello there"):
    from kazma_gateway.agent_handler.graph import create_graph_handler
    from kazma_gateway.agent_handler.store import _InMemoryStore

    manager = SimpleNamespace(send=AsyncMock(), adapters=[])
    handler = create_graph_handler(graph=graph, manager=manager, store=_InMemoryStore())
    await handler(_message(platform, text))
    return manager


@pytest.fixture
def enforced(monkeypatch):
    """Spaces on, and the owner's account linked on every chat app (S3-02)."""
    from kazma_core.config_store import get_config_store
    from kazma_core.spaces import chat_links, identity

    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "0")
    monkeypatch.setattr(scope, "_enforced", True)
    store = get_config_store()
    store.delete(identity.REGISTRY_KEY)

    def link(reg):
        for platform in ("telegram", "discord", "slack", "whatsapp"):
            reg["links"].append({"platform": platform, "user_id": OWNER_USER, "account": OWNER_ACCOUNT_ID})

    identity.update_registry(link)
    chat_links.refresh_links()
    yield
    store.delete(identity.REGISTRY_KEY)
    chat_links._forget_links_for_tests()


@pytest.mark.parametrize("platform", ["telegram", "discord", "slack", "whatsapp"])
async def test_a_chat_app_turn_runs_as_the_owner_in_the_owners_space(enforced, platform) -> None:
    graph = _Graph()
    manager = await _run(platform, graph)
    assert graph.scopes and graph.scopes[0] == scope.Scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID)
    assert graph.values.get("tenant_id") in (None, OWNER_SPACE_ID)
    sent = [call.args[0].text for call in manager.send.await_args_list]
    assert any(ANSWER in text for text in sent), sent


async def test_a_chat_of_another_space_is_refused_with_a_reply(enforced, monkeypatch) -> None:
    thread = "chatapp-other-" + uuid4().hex
    threads.record_thread(thread, "sotherspace01", "pmemberacct01")
    try:
        from kazma_core.sessions import directory

        monkeypatch.setattr(directory, "find_mouth_thread", lambda *a, **k: thread)
        graph = _Graph()
        manager = await _run("telegram", graph)
    finally:
        threads.forget_thread(thread)
    assert graph.scopes == []  # the graph never ran
    sent = [call.args[0].text for call in manager.send.await_args_list]
    assert len(sent) == 1 and "another space" in sent[0]


@pytest.mark.parametrize(("text", "said"), [("hello there", "cannot read this chat's record"),
                                            ("مرحبا كيف الحال", "لا يستطيع Kazma قراءة سجل")])
async def test_a_record_that_cannot_be_read_is_said_for_what_it_is(enforced, monkeypatch,
                                                                    text, said) -> None:
    """The record store failing is refused, and said as a failure to read --
    never "this chat belongs to another space" over a chat that may be the
    sender's own. Control: the chat of another space keeps its own words."""
    import sqlite3

    def _broken(thread_id):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(threads, "record_of", _broken)
    graph = _Graph()
    manager = await _run("telegram", graph, text)
    assert graph.scopes == []  # the graph never ran
    sent = [call.args[0].text for call in manager.send.await_args_list]
    assert len(sent) == 1 and said in sent[0], sent
    assert "another space" not in sent[0] and "مساحة أخرى" not in sent[0]


async def test_not_enforced_the_turn_runs_as_before(monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "0")
    graph = _Graph()
    manager = await _run("telegram", graph)
    sent = [call.args[0].text for call in manager.send.await_args_list]
    assert any(ANSWER in text for text in sent), sent


async def test_control_with_no_scope_bound_the_turn_fails(enforced, monkeypatch) -> None:
    """The handler of before bound nothing: the turn's state asks for the
    space and gets ``NoSpaceBound``; nothing is answered."""
    from kazma_core.spaces import chat_apps

    monkeypatch.setattr(chat_apps, "chat_app_scope", lambda thread_id, place=None: None)
    graph = _Graph()
    with pytest.raises(NoSpaceBound):
        await _run("telegram", graph)
    assert graph.scopes == []


# ── /x reads in the bound scope ──────────────────────────────────────────


def test_x_work_reads_in_the_bound_scope_and_never_by_default(enforced) -> None:
    from kazma_core.spaces.chat_apps import chat_app_work
    from kazma_core.tenant_context import get_current_tenant_id

    with pytest.raises(NoSpaceBound):
        with chat_app_work():
            pass
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        with chat_app_work():
            assert scope.current_scope() == scope.Scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID)
            assert get_current_tenant_id() == OWNER_SPACE_ID


def test_x_work_before_spaces_is_the_owners_tenant() -> None:
    from kazma_core.spaces.chat_apps import chat_app_work
    from kazma_core.tenant_context import get_current_tenant_id

    with chat_app_work():
        assert get_current_tenant_id() == OWNER_SPACE_ID


async def test_x_list_with_no_scope_bound_refuses(enforced, monkeypatch) -> None:
    from kazma_gateway.agent_handler import commands

    sent: list[str] = []
    message = _message("telegram", "/x list")
    monkeypatch.setattr("kazma_gateway.agent_handler.graph._sender_is_gateway_admin", lambda msg: True)
    with pytest.raises(NoSpaceBound):
        await commands._try_x_command(message, AsyncMock(), _manager(sent), "t-" + uuid4().hex)


def _manager(sent: list[str]):
    async def _send(out):
        sent.append(out.text)

    return SimpleNamespace(send=_send, adapters=[])


def test_control_the_x_rule_of_before_read_as_the_owner_whatever_was_bound(enforced) -> None:
    from kazma_core.tenant_context import get_current_tenant_id, tenant_scope

    # What /x bound before: the owner's tenant, with no space at all bound.
    with tenant_scope("default"):
        assert get_current_tenant_id() == "default" and scope.current_scope() is None


# ── Gate: every tenant or scope a gateway path binds comes from the rule ──

#: (file, function) -> why it binds a tenant or scope itself.
DECLARED = {
    ("kazma-gateway/kazma_gateway/agent_handler/graph.py", "_handler_body_in_its_space"):
        "binds what kazma_core.spaces.chat_apps.chat_app_scope answers",
    ("kazma-gateway/kazma_gateway/agent_handler/commands.py", "_chat_document_scope"):
        "before Spaces only (enforced: surface_scope reads the bound scope)",
}


def _binders(source: str) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    stack: list[str] = []

    class _Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            name = getattr(node.func, "id", getattr(node.func, "attr", ""))
            if name in ("tenant_scope", "bind_scope", "set_current_tenant_id"):
                out.append((stack[-1] if stack else "<module>", node.lineno))
            self.generic_visit(node)

    _Visitor().visit(ast.parse(source))
    return out


def _problems(files: dict[str, str]) -> list[str]:
    return [f"{rel}:{line} {fn} binds a tenant or scope itself"
            for rel, source in files.items() for fn, line in _binders(source)
            if (rel, fn) not in DECLARED]


def test_every_gateway_binding_comes_from_the_chat_app_rule() -> None:
    files = {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8")
             for p in (ROOT / "kazma-gateway" / "kazma_gateway").rglob("*.py")}
    assert _problems(files) == []
    found = {(rel, fn) for rel, source in files.items() for fn, _ in _binders(source)}
    assert set(DECLARED) <= found, sorted(set(DECLARED) - found)


def test_gate_control_the_x_binding_of_before_fails() -> None:
    planted = {"kazma-gateway/kazma_gateway/agent_handler/commands.py": (
        "async def _try_x_command(msg):\n"
        "    from kazma_core.tenant_context import tenant_scope\n"
        "    with tenant_scope('default'):\n"
        "        pass\n"
    )}
    assert len(_problems(planted)) == 1
