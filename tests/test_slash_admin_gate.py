"""A chat command that changes Kazma for every user and platform needs an admin.

With an empty allowlist anyone in a chat may talk to Kazma, and none of them
may install or remove code, change the model or the configuration (audit H-8,
``kazma_gateway.allowlists.is_gateway_admin``). ``/config model|memory|tools``
and ``/skill install`` were gated; until 2026-10-01 the rest of the class was
not: the personality switch, ``/skill uninstall``, ``/ide repo switch|clone``
and ``/swarm config``. ``slash_commands.changes_global_config`` is the one rule
the gateway handler asks before every slash command.
"""

from __future__ import annotations

from typing import Any

import pytest

from kazma_gateway.agent_handler.commands import _try_skill_command
from kazma_gateway.gateway import IncomingMessage
from kazma_gateway.slash_commands import changes_global_config


def test_only_admins_change_kazma_for_everyone():
    """Showing and listing stay open; a change every user and platform gets
    needs an admin."""
    for text in (
        "/config model deepseek-chat",
        "/config memory off",
        "/config tools toggle github",
        "/config personality concise",
        "/personality concise",
        "/personality@KazmaBot teacher",
        "/skill install shadcn/improve",
        "/skill uninstall improve",
        "/ide repo switch ws-123",
        "/ide repo clone Mubder/kazma",
        "/ide repo Mubder/kazma",
        "/swarm config group -1001234",
        "/swarm config clear",
        "/_models_select deepseek-chat",
    ):
        assert changes_global_config(text), text
    for text in (
        "/config", "/config show", "/config personality", "/config export",
        "/personality", "/personality list", "/status", "/help",
        "/skill", "/skill list", "/skill activate improve", "/skill info improve",
        "/ide", "/ide repo", "/ide repo list", "/ide ls", "/ide open README.md",
        "/swarm", "/swarm config", "/swarm status", "/kb list",
    ):
        assert not changes_global_config(text), text


class _Store:
    async def get(self, _thread: str) -> dict[str, Any]:
        return {"channel": "C1"}


class _Manager:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, outbound: Any) -> bool:
        self.sent.append(outbound.text)
        return True


@pytest.fixture
def uninstalls(monkeypatch):
    """Records each skill the command really uninstalls."""
    from kazma_core.agent_skills import tools

    done: list[str] = []

    async def fake_uninstall(name: str) -> str:
        done.append(name)
        return f"Uninstalled {name}."

    monkeypatch.setattr(tools, "uninstall_agent_skill", fake_uninstall)
    monkeypatch.delenv("KAZMA_GATEWAY_ADMINS", raising=False)
    return done


def _uninstall_msg() -> IncomingMessage:
    return IncomingMessage(platform="slack", sender_id="slack:U42", text="/skill uninstall improve")


async def test_a_chat_member_cannot_uninstall_a_skill(uninstalls):
    """It deleted the skill for everyone, open to any chat member."""
    manager = _Manager()
    assert await _try_skill_command(_uninstall_msg(), _Store(), manager, "gw-slack-u42")
    assert uninstalls == []
    assert any("Uninstalling skills is admin-only" in text for text in manager.sent), manager.sent


async def test_an_admin_uninstalls_a_skill(uninstalls, monkeypatch):
    monkeypatch.setenv("KAZMA_GATEWAY_ADMINS", "slack:U42")
    manager = _Manager()
    assert await _try_skill_command(_uninstall_msg(), _Store(), manager, "gw-slack-u42")
    assert uninstalls == ["improve"]
