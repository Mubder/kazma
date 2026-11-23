"""Stage 3 review, M7: a member's message goes only to their own chat.

Once Spaces is enforced, a person other than the admin approved their own
``send_message(target_id="telegram:<anyone>")`` and Kazma's bot messaged
whoever that was (no outbound allowlist set). Now every tool that names a
chat-app recipient (``side_effects.OUTBOUND_TARGET_FIELDS``) is checked
before the card and at the call (``call_preflight.call_refusal``, asked by
the chat tool worker and by ``LocalToolRegistry.execute``): a member's
recipient must be their own address (``chat_links.is_their_address``: one
of their links, or a group bound to the space they are a member of) or this
conversation; anything else is refused before anything is sent. The admin
keeps today's rule. Email is the space's own mailbox, not Kazma's bot:
declared, never checked here.

Negative control: the preflight of before, which asked nothing of an
outbound target -- the member's message reaches the owner's chat.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.safety import call_preflight
from kazma_core.safety.side_effects import OUTBOUND_TARGET_FIELDS
from kazma_core.spaces import chat_apps, chat_links, identity, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, bind_scope

PASSWORD = "a-long-enough-password"


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(scope, "_enforced", True)
    store = get_config_store()
    for key in (identity.REGISTRY_KEY, chat_apps.BINDINGS_KEY, chat_links.CODES_KEY):
        store.delete(key)
    dana = people.create_person(f"dana-{uuid4().hex[:6]}", PASSWORD, by=OWNER_ACCOUNT_ID)
    garden = people.create_shared_space("Garden", by=OWNER_ACCOUNT_ID)
    people.add_member(garden["id"], dana["id"], by=OWNER_ACCOUNT_ID)

    def link(reg: dict) -> None:
        reg["links"].append({"platform": "telegram", "user_id": "6101", "account": dana["id"],
                             "target": "telegram:6101"})
        reg["links"].append({"platform": "telegram", "user_id": "7101", "account": OWNER_ACCOUNT_ID,
                             "target": "telegram:7101"})

    identity.update_registry(link)
    chat_links.refresh_links()
    yield dana, garden
    for key in (identity.REGISTRY_KEY, chat_apps.BINDINGS_KEY, chat_links.CODES_KEY):
        store.delete(key)
    chat_links._forget_links_for_tests()


def _refusal(space: str, person: str, tool: str, args: dict) -> str | None:
    async def run():
        with bind_scope(space, person):
            return await call_preflight.call_refusal(tool, args)

    return asyncio.run(run())


CHAT_TOOLS = {name: fields for name, fields in OUTBOUND_TARGET_FIELDS.items() if fields and name != "email_send"}


@pytest.mark.parametrize("tool", sorted(CHAT_TOOLS))
def test_a_member_s_message_to_someone_else_is_refused(world, tool) -> None:
    dana, garden = world
    field = CHAT_TOOLS[tool][0]
    args = {field: "telegram:7101", "text": "hi", "channel": "telegram", "title": "t"}
    refused = _refusal(garden["id"], dana["id"], tool, args)
    assert refused and "own" in refused, f"{tool} reached another person's chat: {refused!r}"


@pytest.mark.parametrize("tool", sorted(CHAT_TOOLS))
def test_a_member_s_message_to_their_own_chat_is_sent(world, tool) -> None:
    dana, garden = world
    field = CHAT_TOOLS[tool][0]
    args = {field: "telegram:6101", "text": "hi", "channel": "telegram", "title": "t"}
    assert _refusal(garden["id"], dana["id"], tool, args) is None


def test_this_conversation_is_always_theirs(world) -> None:
    from kazma_core.tools.send_message import reset_current_delivery_target, set_current_delivery_target

    dana, garden = world
    token = set_current_delivery_target("telegram:-1009090")
    try:
        assert _refusal(garden["id"], dana["id"], "send_message", {"target_id": "telegram:-1009090"}) is None
    finally:
        reset_current_delivery_target(token)


def test_the_admin_keeps_today_s_rule(world) -> None:
    assert _refusal("default", OWNER_ACCOUNT_ID, "send_message", {"target_id": "telegram:6101"}) is None


def test_before_spaces_nothing_is_asked(world, monkeypatch) -> None:
    dana, garden = world
    monkeypatch.setattr(scope, "_enforced", False)
    assert _refusal(garden["id"], dana["id"], "send_message", {"target_id": "telegram:7101"}) is None


def test_every_outbound_tool_is_checked_or_declared() -> None:
    for name in OUTBOUND_TARGET_FIELDS:
        assert name in call_preflight.PREFLIGHT_TOOLS or name in call_preflight.OUTBOUND_NOT_CHECKED, (
            f"{name} names a recipient and is neither checked nor declared")


def test_the_registry_refuses_it_at_the_call(world) -> None:
    from kazma_core.agent.tool_registry import LocalToolRegistry

    dana, garden = world
    sent: list[str] = []

    async def send_message(target_id: str, text: str) -> str:
        sent.append(target_id)
        return f"sent:{target_id}"

    registry = LocalToolRegistry()
    registry.register(description="Send a message.", name="send_message")(send_message)

    async def run():
        with bind_scope(garden["id"], dana["id"]):
            return await registry.execute("send_message", {"target_id": "telegram:7101", "text": "hi"})

    out = asyncio.run(run())
    assert sent == [] and out.get("is_error"), out


def test_negative_control_nothing_asked_of_a_target(world, monkeypatch) -> None:
    """Planted: the preflight of before, which asked nothing of an outbound
    target. Dana's message reaches the owner's chat."""
    async def nothing(name, arguments, *, user_text=None):
        return None

    monkeypatch.setattr(call_preflight, "call_refusal", nothing)
    with pytest.raises(AssertionError, match="reached another person's chat"):
        test_a_member_s_message_to_someone_else_is_refused(world, "send_message")
