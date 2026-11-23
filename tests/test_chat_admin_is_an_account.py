"""Spaces S3-08: a chat app's admin is the admin's linked account.

Once Spaces is enforced, ``allowlists.is_gateway_admin`` -- the one admin
rule of the chat apps: admin-only commands, install buttons, a swarm bus
card's press -- asks whether the sender's chat-app account is linked to the
install's admin, enabled (``chat_links.linked_admin``). ``KAZMA_GATEWAY_ADMINS``
and the allowlists retire: listing someone grants nothing; the owner's known
ids became the owner's links when Spaces was switched on (S3-02).

Through the real handler: a member listed in ``KAZMA_GATEWAY_ADMINS`` sends
an admin-only command and is refused, in their language, told how one
becomes able to; the owner's linked chat runs it. Before Spaces the
environment list decides, as always.

Negative control: the environment list -- a listed member passes the admin
gate.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.spaces import chat_apps, chat_links, identity, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

from kazma_gateway import allowlists
from kazma_gateway.gateway import IncomingMessage

PASSWORD = "a-long-enough-password"
#: An admin-only command (``changes_global_config``) that changes only a
#: setting when it runs.
ADMIN_COMMAND = "/swarm config clear"


@pytest.fixture
def world(monkeypatch):
    from kazma_gateway.agent_handler.graph import create_graph_handler
    from kazma_gateway.agent_handler.store import _InMemoryStore

    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "0")
    monkeypatch.setattr(scope, "_enforced", True)
    store = get_config_store()
    for key in (identity.REGISTRY_KEY, chat_apps.BINDINGS_KEY, chat_links.CODES_KEY):
        store.delete(key)
    dana = people.create_person(f"dana-{uuid4().hex[:6]}", PASSWORD, by=OWNER_ACCOUNT_ID)
    uids = {"dana": f"6{uuid4().int % 10**8}", "owner": f"7{uuid4().int % 10**8}",
            "stranger": f"5{uuid4().int % 10**8}"}

    def link(reg: dict) -> None:
        reg["links"].append({"platform": "telegram", "user_id": uids["dana"], "account": dana["id"]})
        reg["links"].append({"platform": "telegram", "user_id": uids["owner"], "account": OWNER_ACCOUNT_ID})

    identity.update_registry(link)
    chat_links.refresh_links()
    # The list of before names Dana and a stranger: it grants nothing now.
    monkeypatch.setenv("KAZMA_GATEWAY_ADMINS", f"telegram:{uids['dana']},{uids['stranger']}")

    class _Graph:
        async def aget_state(self, config):
            return SimpleNamespace(values={}, next=(), tasks=())

        async def ainvoke(self, state, config):
            return {**state, "messages": [*state["messages"], {"role": "assistant", "content": "ok"}]}

    sent: list[str] = []

    async def send(out):
        sent.append(out.text)
        return True

    manager = SimpleNamespace(send=AsyncMock(side_effect=send), adapters=[])
    handler = create_graph_handler(graph=_Graph(), manager=manager, store=_InMemoryStore())
    yield SimpleNamespace(dana=dana, uids=uids, sent=sent, handler=handler)
    for key in (identity.REGISTRY_KEY, chat_apps.BINDINGS_KEY, chat_links.CODES_KEY):
        store.delete(key)
    chat_links._forget_links_for_tests()


def _say(w: Any, who: str, text: str) -> str:
    uid = w.uids[who]
    meta = {"chat_id": int(uid), "user_id": int(uid), "chat_type": "private", "username": who}
    asyncio.run(w.handler(IncomingMessage("telegram", f"telegram:{uid}", text, context_metadata=meta)))
    return w.sent[-1] if w.sent else ""


# ── the rule ────────────────────────────────────────────────────────────


def test_the_admin_is_the_admin_s_linked_account(world) -> None:
    assert allowlists.is_gateway_admin(f"telegram:{world.uids['owner']}", "telegram")


def test_the_environment_list_grants_nothing(world) -> None:
    assert not allowlists.is_gateway_admin(f"telegram:{world.uids['dana']}", "telegram"), (
        "a linked member listed in KAZMA_GATEWAY_ADMINS is not the admin")
    assert not allowlists.is_gateway_admin(f"telegram:{world.uids['stranger']}", "telegram")


def test_the_admin_on_discord_is_the_user_never_the_channel(world) -> None:
    def link(reg: dict) -> None:
        reg["links"].append({"platform": "discord", "user_id": "4242", "account": OWNER_ACCOUNT_ID})

    identity.update_registry(link)
    assert allowlists.is_gateway_admin("discord:4242:9001", "discord")
    assert not allowlists.is_gateway_admin("discord:9001:4242", "discord")


def test_an_unlinked_admin_chat_is_no_admin(world) -> None:
    assert chat_links.unlink(OWNER_ACCOUNT_ID, "telegram", world.uids["owner"])
    assert not allowlists.is_gateway_admin(f"telegram:{world.uids['owner']}", "telegram")


# ── through the handler ─────────────────────────────────────────────────


def test_a_listed_member_is_refused_an_admin_command(world) -> None:
    said = _say(world, "dana", ADMIN_COMMAND)
    assert "admin-only" in said and "linked to the admin" in said, said
    assert "KAZMA_GATEWAY_ADMINS" not in said, "the retired list is not offered"


def test_the_refusal_is_in_the_sender_s_language(world) -> None:
    said = _say(world, "dana", "/swarm config clear امسح إعدادات السرب من فضلك الآن")
    assert "للمسؤول وحده" in said and "مربوط بحساب المسؤول" in said, said


def test_the_owner_s_linked_chat_runs_it(world) -> None:
    said = _say(world, "owner", ADMIN_COMMAND)
    assert "admin-only" not in said, said


def test_before_spaces_the_environment_list_decides(world, monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", False)
    assert allowlists.is_gateway_admin(f"telegram:{world.uids['stranger']}", "telegram")
    assert "KAZMA_GATEWAY_ADMINS" in allowlists.admin_hint()


# ── negative control ────────────────────────────────────────────────────


def test_negative_control_the_environment_list(world, monkeypatch) -> None:
    """Planted: the rule of before -- the environment list decides whatever
    the install. The listed member passes the admin gate: the check above
    fails on its own assertion."""
    monkeypatch.setattr(allowlists, "_admin_by_link", allowlists._admin_by_lists)
    with pytest.raises(AssertionError, match="not the admin"):
        test_the_environment_list_grants_nothing(world)
