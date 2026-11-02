"""Every tool that messages someone declares its recipients to the outbound allowlist (FA-117).

The 2026-11 functional audit: Settings → Safety says the outbound allowlist
restricts "email/channel targets", and §20C says an unknown target goes to
clarify. The resolver read only an argument named ``to``, ``target`` or
``recipient``, and only for tools profiled ``send_outbound``:
``dispatch_notification`` (any ``recipient_id`` on Telegram, Discord or Slack)
was audit-only, ``send_message`` was not outbound at all, and its manifest
said it replies "to the current conversation" while it requires a
``target_id``.

Every registered tool that takes a recipient, or whose name says it sends a
message, is profiled ``send_outbound`` with its recipient fields declared in
``side_effects.OUTBOUND_TARGET_FIELDS``; the resolver checks every address in
them. A message to the conversation the turn runs in stays exempt. The old
profile of ``dispatch_notification`` is the negative control.
"""

from __future__ import annotations

import inspect
import re

import pytest
from kazma_core.agent.tool_builtins import register_builtin_tools
from kazma_core.agent.tool_registry import LocalToolRegistry
from kazma_core.safety import side_effects
from kazma_core.safety.commitment.authorize import authorize_effect
from kazma_core.safety.side_effects import EffectKind, SemanticTier

#: Parameters that name who a message goes to.
_RECIPIENT_PARAMS = {"to", "cc", "bcc", "recipient", "recipient_id", "recipients", "target",
                     "target_id", "chat_id", "channel_id", "user_id"}
_SENDS_BY_NAME = re.compile(r"^send_|_send$|notification|message")

#: Tools with a recipient-like parameter that sends nothing to anyone, and why.
NOT_MESSAGES: dict[str, str] = {
    "memory_admin": "target is the memory entity an admin action applies to",
    "memory_merge_entities": "target_id is the entity a merge keeps",
    "git_diff": "target is the commit or range to diff",
}


def _registry() -> LocalToolRegistry:
    registry = LocalToolRegistry()
    register_builtin_tools(registry)
    return registry


def message_senders(registry: LocalToolRegistry, not_messages: dict[str, str] = NOT_MESSAGES) -> dict[str, set[str]]:
    """name -> its recipient parameters, for every tool that takes one or is
    named as a sender -- unless declared in *not_messages*."""
    found: dict[str, set[str]] = {}
    for name, tool in registry._tools.items():
        if name in not_messages:
            continue
        try:
            params = set(inspect.signature(tool.func).parameters)
        except (TypeError, ValueError):
            params = set()
        recipients = params & _RECIPIENT_PARAMS
        if recipients or _SENDS_BY_NAME.search(name):
            found[name] = recipients
    return found


def undeclared(senders: dict[str, set[str]]) -> dict[str, str]:
    problems: dict[str, str] = {}
    for name, recipients in senders.items():
        profile = side_effects.get_effect_profile(name)
        fields = side_effects.OUTBOUND_TARGET_FIELDS.get(name)
        if profile.act != "send_outbound":
            problems[name] = f"act {profile.act!r}, not send_outbound"
        elif fields is None:
            problems[name] = "no declared recipient fields"
        elif not set(fields) >= recipients:
            problems[name] = f"recipient parameters not declared: {sorted(recipients - set(fields))}"
    return problems


@pytest.fixture
def allowlist(tmp_path, monkeypatch):
    """A real settings store holding an allowlist, and a private ops database
    for the clarify cards."""
    from kazma_core.config_store import ConfigStore, peek_config_store, reset_config_store, set_config_store

    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "ops.db"))
    previous = peek_config_store()
    store = ConfigStore(db_path=str(tmp_path / "settings.db"))
    set_config_store(store)
    store.set("agent.commitment.outbound_allowed_targets", ["telegram:111", "owner@example.com"])
    try:
        yield ["telegram:111", "owner@example.com"]
    finally:
        reset_config_store()
        if previous is not None:
            set_config_store(previous)
        store.close()


def test_every_message_sender_declares_its_recipients():
    registry = _registry()
    senders = message_senders(registry)
    assert {"dispatch_notification", "send_message", "send_approval_request", "email_send",
            "send_file"} <= set(senders), sorted(senders)
    assert not undeclared(senders), undeclared(senders)
    assert set(NOT_MESSAGES) <= set(registry._tools), "NOT_MESSAGES names a tool that no longer exists"
    for name in NOT_MESSAGES:
        assert side_effects.get_effect_profile(name).effect != EffectKind.OUTBOUND, name
    for name, fields in side_effects.OUTBOUND_TARGET_FIELDS.items():
        params = set(inspect.signature(registry._tools[name].func).parameters)
        assert set(fields) <= params, (name, fields)


@pytest.mark.parametrize(("tool", "args"), [
    ("dispatch_notification", {"channel": "telegram", "recipient_id": "telegram:999", "text": "hi"}),
    ("send_message", {"target_id": "telegram:999", "text": "hi"}),
    ("send_approval_request", {"channel": "telegram", "recipient_id": "telegram:999", "title": "ok?"}),
    ("email_send", {"to": "owner@example.com", "cc": "stranger@example.com"}),
])
def test_a_recipient_off_the_allowlist_asks_first(allowlist, tool, args):
    decision = authorize_effect(tool, args)
    assert decision.decision == "clarify", (tool, decision.reason)
    assert ("999" in decision.clarify_question) or ("stranger" in decision.clarify_question)


@pytest.mark.parametrize(("tool", "args"), [
    ("dispatch_notification", {"channel": "telegram", "recipient_id": "telegram:111", "text": "hi"}),
    ("send_message", {"target_id": "telegram:111", "text": "hi"}),
    ("email_send", {"to": "owner@example.com"}),
])
def test_an_allowlisted_recipient_passes(allowlist, tool, args):
    assert authorize_effect(tool, args).decision == "allow"


def test_this_conversation_is_never_held_by_the_allowlist(allowlist, monkeypatch):
    """A message to the chat the turn runs in is the operator's own."""
    from kazma_core.tools import send_message as sm

    monkeypatch.setattr(sm, "get_current_delivery_target", lambda: "telegram:555")
    for tool, args in (
        ("send_message", {"target_id": "telegram:555", "text": "done"}),
        ("send_approval_request", {"channel": "telegram", "recipient_id": "", "title": "ok?"}),
        ("send_file", {"file_path": "report.pdf"}),
    ):
        assert authorize_effect(tool, args).decision == "allow", tool


def test_the_old_profile_let_any_recipient_through(allowlist, monkeypatch):
    """Negative control: ``dispatch_notification`` as profiled before FA-117
    (outbound, no act) is allowed to a chat id off the allowlist."""
    monkeypatch.setitem(side_effects._PROF, "dispatch_notification",
                        (EffectKind.OUTBOUND, SemanticTier.NONE, None, ()))
    decision = authorize_effect("dispatch_notification",
                                {"channel": "telegram", "recipient_id": "telegram:999", "text": "hi"})
    assert decision.decision == "allow"
    assert undeclared({"dispatch_notification": {"recipient_id"}})


def test_a_new_tool_with_a_recipient_is_caught():
    """A tool taking ``chat_id`` that nobody declared is a sender until it
    says otherwise."""
    registry = LocalToolRegistry()

    @registry.register(description="post to a chat", category="test")
    async def post_update(chat_id: str, text: str) -> str:  # pragma: no cover - never called
        return ""

    assert message_senders(registry)["post_update"] == {"chat_id"}
    assert "post_update" in undeclared(message_senders(registry))
