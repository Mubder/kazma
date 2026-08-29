"""Tests for the /documents chat command handler (Phase 8).

Verifies the command is detected and handled (skipping the graph), that it
delegates to the shared DocumentIngestionService singleton, and that
non-/documents messages fall through.
"""

from __future__ import annotations

import types

import pytest


def _fake_message(text: str, platform: str = "slack") -> types.SimpleNamespace:
    return types.SimpleNamespace(text=text, platform=platform, context_metadata={})


class _FakeManager:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, outbound) -> None:  # type: ignore[no-untyped-def]
        self.sent.append(outbound.text)


class _FakeStore:
    async def get(self, _tid: str):
        return None


@pytest.fixture
def wired_singleton(tmp_path):
    from kazma_core.documents.config import DocumentConfig
    from kazma_core.documents.ingestion import (
        DocumentIngestionService,
        set_ingestion_service,
    )

    svc = DocumentIngestionService(config=DocumentConfig(storage_root=tmp_path / "store"))
    set_ingestion_service(svc)
    yield svc
    set_ingestion_service(None)
    svc.close()


async def test_documents_help_is_handled(wired_singleton):
    from kazma_gateway.agent_handler.commands import _try_documents_command

    mgr = _FakeManager()
    handled = await _try_documents_command(_fake_message("/documents"), _FakeStore(), mgr, "t1")
    assert handled is True
    assert mgr.sent and "Kazma Documents" in mgr.sent[0]


async def test_documents_list_empty(wired_singleton):
    from kazma_gateway.agent_handler.commands import _try_documents_command

    mgr = _FakeManager()
    handled = await _try_documents_command(_fake_message("/documents list"), _FakeStore(), mgr, "t1")
    assert handled is True
    assert mgr.sent and "No documents" in mgr.sent[0]


async def test_documents_unknown_subcommand(wired_singleton):
    from kazma_gateway.agent_handler.commands import _try_documents_command

    mgr = _FakeManager()
    handled = await _try_documents_command(
        _fake_message("/documents frobnicate"), _FakeStore(), mgr, "t1"
    )
    assert handled is True
    assert mgr.sent and "Unknown" in mgr.sent[0]


async def test_non_documents_message_not_handled(wired_singleton):
    from kazma_gateway.agent_handler.commands import _try_documents_command

    mgr = _FakeManager()
    handled = await _try_documents_command(
        _fake_message("hello there"), _FakeStore(), mgr, "t1"
    )
    assert handled is False
    assert not mgr.sent
