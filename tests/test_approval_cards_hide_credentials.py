"""No approval card posts a credential argument to a chat platform.

Audit 2026-11-10: the swarm bus's card (``swarm.approval_card``) showed every
argument verbatim and went to every configured chat platform; the chat graph's
card (``agent_handler.hitl``) hid an exact list of names that missed compound
ones (``access_token``, ``client_secret``, ``bot_token``,
``aws_secret_access_key``) and ``vault_store``'s ``value``. Both now use one
redactor (``security.approval_args``): every argument NAME stays on the card
(SW-S03) and the sha256 footer still hashes the REAL arguments.

Each card is built by its real builder; the negative controls rebuild the old
redaction and show the secret.
"""

from __future__ import annotations

import json

import pytest

from kazma_core.security import approval_args
from kazma_core.swarm import approval_card
from kazma_core.swarm.approval_card import arguments_digest, render_arguments

#: Planted fake secrets, built at run time.
SECRET = "plant" + "ed-" + "s" * 12

#: (tool, arguments) -- each argument that holds SECRET must be hidden.
CALLS = [
    ("http_request", {"url": "https://api.example/x", "access_token": SECRET}),
    ("mcp__acme__connect", {"client_id": "abc", "client_secret": SECRET}),
    ("send_message", {"text": "hi", "bot_token": SECRET}),
    ("shell_exec", {"command": "aws s3 ls", "aws_secret_access_key": SECRET}),
    ("vault_store", {"name": "my_key", "value": SECRET, "category": "general"}),
    ("http_request", {"url": "https://api.example/x", "headers": {"X-Api": SECRET}}),
    ("mcp__acme__run", {"config": {"nested": {"refresh_token": SECRET}}}),
    ("shell_exec", {"command": f"git clone https://user:{SECRET}@example.com/r.git"}),
]


def _chat_card(tool: str, args: dict) -> str:
    from kazma_gateway.agent_handler.hitl import _build_approval_prompt

    return _build_approval_prompt({"tool": tool, "args": args}, "th-1", platform="telegram")["text"]


@pytest.mark.parametrize(("tool", "args"), CALLS)
def test_the_swarm_card_hides_each_secret(tool, args):
    raw = json.dumps(args)
    card = render_arguments(raw, tool=tool)
    assert SECRET not in card.text, card.text
    for name in args:
        assert f"{name}: " in card.text  # every name stays (SW-S03)
    assert card.digest == arguments_digest(raw)  # the hash is of the real call
    assert f"arguments sha256: {card.digest}" in card.text


@pytest.mark.parametrize(("tool", "args"), CALLS)
def test_the_chat_card_hides_each_secret(tool, args):
    text = _chat_card(tool, args)
    assert SECRET not in text, text
    for name in args:
        assert name in text


def test_the_swarm_page_shows_the_call_without_its_secrets():
    shown = approval_args.redacted_arguments_json("vault_store", json.dumps(CALLS[4][1]))
    assert SECRET not in shown and json.loads(shown)["name"] == "my_key"
    assert approval_args.redacted_arguments_json("x", None) is None


def test_ordinary_arguments_are_shown_as_they_are():
    args = {"path": "notes/plan.md", "content": "max_tokens: 400", "pattern": "a*b"}
    assert approval_args.redact_arguments("file_write", args) == args
    assert "notes/plan.md" in render_arguments(json.dumps(args), tool="file_write").text


def test_negative_control_the_old_swarm_card_posted_the_secret(monkeypatch):
    """The bus card before 2026-11-10: no redaction at all."""
    monkeypatch.setattr(approval_args, "redact_arguments", lambda tool, args: args)
    for tool, args in CALLS[:5]:
        assert SECRET in render_arguments(json.dumps(args), tool=tool).text


def test_negative_control_the_old_chat_card_list_missed_compound_names():
    """The chat card's old exact-name set, reproduced."""
    old = {"authorization", "token", "api_key", "apikey", "password", "secret",
           "header", "headers", "env", "cookie", "private_key"}

    def old_redact(obj):
        if isinstance(obj, dict):
            return {k: ("***" if k.lower() in old else old_redact(v)) for k, v in obj.items()}
        return obj

    leaked = [tool for tool, args in CALLS[:5] if SECRET in json.dumps(old_redact(args))]
    assert leaked == ["http_request", "mcp__acme__connect", "send_message", "shell_exec",
                      "vault_store"]


@pytest.mark.parametrize("platform", ("telegram", "discord", "slack"))
def test_the_card_the_bus_posts_hides_a_per_tool_secret(platform, monkeypatch):
    """Through the real middleware and each platform's bus adapter: the
    middleware names the tool, so ``vault_store``'s ``value`` is hidden."""
    import asyncio

    from tests.test_swarm_bus_approvals import _Card, _middleware

    card = _Card(platform, monkeypatch)
    mw = _middleware(monkeypatch, card.adapter, timeout=0.3)
    args = json.dumps(CALLS[4][1])
    assert asyncio.run(mw.check("vault_store", args)) is False  # nobody answered
    assert card.posts and SECRET not in card.posts[0]
    assert "value: [hidden]" in card.posts[0]
    assert f"arguments sha256: {arguments_digest(args)}" in card.posts[0]
    assert approval_card.render_arguments is render_arguments
