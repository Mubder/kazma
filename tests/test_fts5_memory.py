"""Tests for FTS5 Memory."""

import pytest
from kazma_core.memory.fts5 import FTS5Memory


@pytest.fixture
def memory(tmp_path):
    """Create a temporary FTS5 memory instance."""
    db_path = str(tmp_path / "test_memory.db")
    mem = FTS5Memory(db_path=db_path)
    yield mem
    mem.close()


def test_add_and_count(memory):
    """Test adding documents and counting them."""
    assert memory.count() == 0
    memory.add("Test document 1")
    assert memory.count() == 1
    memory.add("Test document 2")
    assert memory.count() == 2


def test_search_basic(memory):
    """Test basic keyword search."""
    memory.add("User prefers dark mode", {"topic": "preferences"})
    memory.add("User likes Python programming", {"topic": "coding"})
    memory.add("Dark theme is easier on eyes", {"topic": "preferences"})

    results = memory.search("dark")
    assert len(results) >= 1
    assert any("dark" in r["text"].lower() for r in results)


def test_search_with_metadata(memory):
    """Test that metadata is preserved and returned."""
    memory.add("Test text", {"topic": "test", "importance": "high"})
    results = memory.search("test")
    assert len(results) >= 1
    assert results[0]["metadata"]["topic"] == "test"


def test_search_no_results(memory):
    """Test search with no matching results."""
    memory.add("Python is great")
    results = memory.search("nonexistent")
    assert len(results) == 0


def test_delete(memory):
    """Test deleting a document."""
    doc_id = memory.add("Document to delete")
    assert memory.count() == 1
    memory.delete(doc_id)
    assert memory.count() == 0


def test_clear(memory):
    """Test clearing all documents."""
    memory.add("Doc 1")
    memory.add("Doc 2")
    memory.add("Doc 3")
    assert memory.count() == 3
    memory.clear()
    assert memory.count() == 0


def test_search_limit(memory):
    """Test search result limit."""
    for i in range(10):
        memory.add(f"Document number {i}")
    results = memory.search("document", limit=3)
    assert len(results) <= 3


def test_arabic_search(memory):
    """Test Arabic text search."""
    memory.add("المستخدم يفضل الوضع المظلم", {"lang": "ar"})
    # FTS5 may not tokenize Arabic perfectly, so test with full text
    results = memory.search("المستخدم")
    assert len(results) >= 1


def test_custom_doc_id(memory):
    """Test adding with custom document ID."""
    doc_id = memory.add("Test", doc_id="custom-id")
    assert doc_id == "custom-id"


def test_score_threshold(memory):
    """Test minimum score threshold."""
    memory.add("Exact match for testing")
    memory.add("Something else entirely")
    results = memory.search("exact match", min_score=0.0)
    assert len(results) >= 1
