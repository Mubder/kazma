"""Agent-facing knowledge create / ingest / list tools."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kazma_core.agent.tool_registry import LocalToolRegistry


@pytest.fixture
def registry() -> LocalToolRegistry:
    return LocalToolRegistry(include_builtins=True)


@pytest.mark.asyncio
async def test_knowledge_list_empty(registry: LocalToolRegistry):
    with patch("kazma_core.stores.knowledge.get_knowledge_store") as gs:
        store = MagicMock()
        store.list_libraries.return_value = []
        gs.return_value = store
        out = await registry.execute("knowledge_list_libraries", {})
        assert "No knowledge libraries" in out["content"]
        assert "knowledge_create_library" in out["content"]


@pytest.mark.asyncio
async def test_knowledge_create_and_ingest_url(registry: LocalToolRegistry):
    store = MagicMock()
    store.get_library.side_effect = [None, {"id": "smoke_kb", "name": "Smoke", "chunk_count": 0}, {"id": "smoke_kb", "chunk_count": 3}]
    store.create_library.return_value = {"id": "smoke_kb", "name": "Smoke", "chunk_count": 0}

    ingest_result = SimpleNamespace(
        pages_fetched=1,
        pages_failed=0,
        chunks_new=3,
        chunks_skipped=0,
        errors=[],
    )

    with (
        patch("kazma_core.stores.knowledge.get_knowledge_store", return_value=store),
        patch(
            "kazma_core.stores.knowledge_ingest.ingest_url",
            new=AsyncMock(return_value=ingest_result),
        ),
    ):
        created = await registry.execute(
            "knowledge_create_library",
            {"library_id": "smoke_kb", "name": "Smoke"},
        )
        assert "Created knowledge library" in created["content"]

        ingested = await registry.execute(
            "knowledge_ingest_url",
            {"library_id": "smoke_kb", "url": "https://example.com/docs"},
        )
        assert "chunks_new=3" in ingested["content"]
        assert "knowledge_search" in ingested["content"]


@pytest.mark.asyncio
async def test_knowledge_search_points_to_ingest_when_empty(registry: LocalToolRegistry):
    store = MagicMock()
    store.list_libraries.return_value = []
    with (
        patch("kazma_core.stores.knowledge.get_knowledge_store", return_value=store),
        patch("kazma_core.stores.knowledge_index.get_knowledge_index"),
    ):
        out = await registry.execute("knowledge_search", {"query": "Supervisor"})
        assert "knowledge_create_library" in out["content"]
        assert "knowledge_ingest_url" in out["content"]
