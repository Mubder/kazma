"""Tests for Tantivy Search Backend.

Comprehensive tests for the TantivySearchBackend including
indexing, searching, and performance metrics.
"""

import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# Check if tantivy is available
try:
    import tantivy

    TANTIVY_AVAILABLE = True
except ImportError:
    TANTIVY_AVAILABLE = False

from kazma_memory.tantivy_backend import (
    IndexStats,
    Memory,
    SearchResult,
    TantivySearchBackend,
)


@pytest.fixture
def sample_memory():
    """Create a sample memory for testing."""
    return Memory(
        id="test_memory_1",
        content="This is a test memory with some content",
        metadata=json.dumps({"source": "test", "type": "note"}),
        timestamp=int(time.time()),
        source="test_source",
        relevance=0.95,
        division="engineering",
    )


@pytest.fixture
def sample_memories():
    """Create multiple sample memories for batch testing."""
    memories = []
    for i in range(10):
        memories.append(
            Memory(
                id=f"test_memory_{i}",
                content=f"Test memory content {i} with various topics",
                metadata=json.dumps({"index": i}),
                timestamp=int(time.time()),
                source="test_source",
                relevance=0.5 + (i * 0.05),
                division="engineering" if i % 2 == 0 else "finance",
            )
        )
    return memories


@pytest.fixture
def temp_index_path():
    """Create a temporary directory for the index."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.mark.skipif(not TANTIVY_AVAILABLE, reason="tantivy-py not installed")
class TestTantivySearchBackend:
    """Test suite for TantivySearchBackend."""

    def test_init_creates_index_directory(self, temp_index_path):
        """Test that initialization creates the index directory."""
        backend = TantivySearchBackend(temp_index_path)
        assert Path(temp_index_path).exists()
        backend._writer = None  # Cleanup

    def test_create_schema(self, temp_index_path):
        """Test schema creation."""
        backend = TantivySearchBackend(temp_index_path)
        schema = backend._create_schema()
        assert schema is not None
        backend._writer = None

    def test_index_memory(self, temp_index_path, sample_memory):
        """Test indexing a single memory."""
        backend = TantivySearchBackend(temp_index_path)

        # Run async test
        import asyncio

        doc_id = asyncio.run(backend.index_memory(sample_memory))

        assert doc_id == sample_memory.id
        backend._writer = None

    def test_index_batch(self, temp_index_path, sample_memories):
        """Test batch indexing."""
        backend = TantivySearchBackend(temp_index_path)

        import asyncio

        doc_ids = asyncio.run(backend.index_batch(sample_memories))

        assert len(doc_ids) == len(sample_memories)
        assert all(doc_id.startswith("test_memory_") for doc_id in doc_ids)
        backend._writer = None

    def test_search(self, temp_index_path, sample_memories):
        """Test searching indexed memories."""
        backend = TantivySearchBackend(temp_index_path)

        import asyncio

        # Index memories
        asyncio.run(backend.index_batch(sample_memories))

        # Search
        results = asyncio.run(backend.search("test", limit=5))

        assert isinstance(results, list)
        assert len(results) <= 5

        for result in results:
            assert isinstance(result, SearchResult)
            assert result.id
            assert result.content
        backend._writer = None

    def test_delete_memory(self, temp_index_path, sample_memory):
        """Test deleting a memory."""
        backend = TantivySearchBackend(temp_index_path)

        import asyncio

        # Index memory
        asyncio.run(backend.index_memory(sample_memory))

        # Delete
        success = asyncio.run(backend.delete_memory(sample_memory.id))

        assert success is True
        backend._writer = None

    def test_optimize(self, temp_index_path, sample_memories):
        """Test index optimization."""
        backend = TantivySearchBackend(temp_index_path)

        import asyncio

        # Index memories
        asyncio.run(backend.index_batch(sample_memories))

        # Optimize
        asyncio.run(backend.optimize())

        # Verify index is still accessible
        stats = asyncio.run(backend.get_stats())
        assert stats.total_documents == len(sample_memories)
        backend._writer = None

    def test_get_stats(self, temp_index_path, sample_memories):
        """Test getting index statistics."""
        backend = TantivySearchBackend(temp_index_path)

        import asyncio

        # Index memories
        asyncio.run(backend.index_batch(sample_memories))

        # Get stats
        stats = asyncio.run(backend.get_stats())

        assert isinstance(stats, IndexStats)
        assert stats.total_documents == len(sample_memories)
        assert stats.index_size_bytes > 0
        assert stats.total_searches == 0  # No searches yet
        backend._writer = None

    def test_search_latency_tracking(self, temp_index_path, sample_memories):
        """Test that search latency is tracked."""
        backend = TantivySearchBackend(temp_index_path)

        import asyncio

        # Index memories
        asyncio.run(backend.index_batch(sample_memories))

        # Perform searches
        for _ in range(5):
            asyncio.run(backend.search("test"))

        # Check stats
        stats = asyncio.run(backend.get_stats())
        assert stats.total_searches == 5
        assert stats.avg_search_latency_ms >= 0
        backend._writer = None


class TestTantivySearchBackendImportError:
    """Test behavior when tantivy is not available."""

    @patch("kazma_memory.tantivy_backend.TANTIVY_AVAILABLE", False)
    def test_init_raises_import_error(self, temp_index_path):
        """Test that initialization raises ImportError when tantivy not available."""
        with pytest.raises(ImportError) as excinfo:
            TantivySearchBackend(temp_index_path)

        assert "tantivy-py is required" in str(excinfo.value)


class TestMemoryDataclass:
    """Test Memory dataclass."""

    def test_memory_creation(self):
        """Test creating a Memory instance."""
        memory = Memory(
            id="test_id",
            content="test content",
            metadata="{}",
            timestamp=1234567890,
            source="test",
            relevance=0.9,
            division="engineering",
        )

        assert memory.id == "test_id"
        assert memory.content == "test content"
        assert memory.metadata == "{}"
        assert memory.timestamp == 1234567890
        assert memory.source == "test"
        assert memory.relevance == 0.9
        assert memory.division == "engineering"

    def test_memory_defaults(self):
        """Test Memory with default values."""
        memory = Memory(id="test_id", content="test content")

        assert memory.metadata == ""
        assert memory.timestamp == 0
        assert memory.source == ""
        assert memory.relevance == 1.0
        assert memory.division == ""


class TestSearchResultDataclass:
    """Test SearchResult dataclass."""

    def test_search_result_creation(self):
        """Test creating a SearchResult instance."""
        result = SearchResult(
            id="test_id",
            content="test content",
            score=0.85,
            metadata="{}",
            timestamp=1234567890,
            source="test",
            relevance=0.9,
            division="engineering",
        )

        assert result.id == "test_id"
        assert result.content == "test content"
        assert result.score == 0.85
        assert result.metadata == "{}"
        assert result.timestamp == 1234567890
        assert result.source == "test"
        assert result.relevance == 0.9
        assert result.division == "engineering"

    def test_search_result_defaults(self):
        """Test SearchResult with default values."""
        result = SearchResult(id="test_id", content="test content", score=0.85)

        assert result.metadata == ""
        assert result.timestamp == 0
        assert result.source == ""
        assert result.relevance == 1.0
        assert result.division == ""


class TestIndexStatsDataclass:
    """Test IndexStats dataclass."""

    def test_index_stats_creation(self):
        """Test creating an IndexStats instance."""
        stats = IndexStats(
            total_documents=1000,
            index_size_bytes=1024000,
            last_optimized="2024-01-01T00:00:00",
            avg_search_latency_ms=0.5,
            total_searches=100,
        )

        assert stats.total_documents == 1000
        assert stats.index_size_bytes == 1024000
        assert stats.last_optimized == "2024-01-01T00:00:00"
        assert stats.avg_search_latency_ms == 0.5
        assert stats.total_searches == 100

    def test_index_stats_defaults(self):
        """Test IndexStats with default values."""
        stats = IndexStats()

        assert stats.total_documents == 0
        assert stats.index_size_bytes == 0
        assert stats.last_optimized is None
        assert stats.avg_search_latency_ms == 0.0
        assert stats.total_searches == 0
