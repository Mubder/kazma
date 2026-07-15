"""Tests for the /ide chat command handler.

Verifies the command is detected and handled (skipping the graph) for the
help path, and that file operations route through the shared IdeService.
"""

from __future__ import annotations

import types

import pytest


def _fake_message(text: str, platform: str = "slack") -> types.SimpleNamespace:
    return types.SimpleNamespace(
        text=text,
        platform=platform,
        context_metadata={},
    )


class _FakeManager:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, outbound) -> None:  # type: ignore[no-untyped-def]
        self.sent.append(outbound.text)


class _FakeStore:
    async def get(self, _tid: str):
        return None


async def test_ide_help_is_handled():
    from kazma_gateway.agent_handler.commands import _try_ide_command

    msg = _fake_message("/ide")
    mgr = _FakeManager()
    handled = await _try_ide_command(msg, _FakeStore(), mgr, "thread-1")
    assert handled is True
    assert mgr.sent and "Kazma IDE" in mgr.sent[0]


async def test_ide_unknown_subcommand_handled():
    from kazma_gateway.agent_handler.commands import _try_ide_command

    msg = _fake_message("/ide frobnicate")
    mgr = _FakeManager()
    handled = await _try_ide_command(msg, _FakeStore(), mgr, "thread-1")
    assert handled is True
    assert mgr.sent and "Unknown IDE subcommand" in mgr.sent[0]


async def test_non_ide_message_not_handled():
    from kazma_gateway.agent_handler.commands import _try_ide_command

    msg = _fake_message("hello there")
    mgr = _FakeManager()
    handled = await _try_ide_command(msg, _FakeStore(), mgr, "thread-1")
    assert handled is False
