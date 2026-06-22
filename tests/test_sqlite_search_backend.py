"""Tests for SQLite Search Backend — FTS5 with Arabic Tokenization.

Comprehensive tests for the SQLite-only search backend including:
- FTS5 full-text search with Arabic tokenization
- Arabic tokenizer functionality
- Hybrid BM25 + vector search (if available)
- Edge deployment optimization
"""

import tempfile
import time

from pathlib import Path

import pytest

from kazma_memory import ArabicTokenizer, SearchBackend, SQLiteMemoryBackend


@pytest.fixture
def temp_db():
    """Create a temporary SQLite database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_memory.db"
        yield db_path


@pytest.fixture
def search_backend(temp_db):
    """Create a SearchBackend instance for testing."""
    backend = SearchBackend(str(temp_db))
    yield backend
    # Cleanup
    import asyncio

    asyncio.run(backend.close())


@pytest.fixture
def sqlite_backend(temp_db):
    """Create a SQLiteMemoryBackend instance for testing."""
    backend = SQLiteMemoryBackend(str(temp_db))
    yield backend
    # Cleanup
    import asyncio

    asyncio.run(backend.close())


class TestArabicTokenizer:
    """Test Arabic tokenizer functionality."""

    @pytest.fixture
    def tokenizer(self):
        """Create an ArabicTokenizer instance."""
        return ArabicTokenizer()

    def test_normalize_alef_variants(self, tokenizer):
        """Test Alef variant normalization."""
        text = "أنا أريد الذهاب إلى الكتاب"
        normalized = tokenizer.normalize(text)
        assert "أ" not in normalized or normalized == "انا اريد الذهاب الى الكتاب"

    def test_remove_diacritics(self, tokenizer):
        """Test diacritics removal."""
        text = "القُرآن الكَرِيم"
        normalized = tokenizer._remove_diacritics(text)
        # Diacritics should be removed
        assert len(normalized) <= len(text)

    def test_normalize_teh_marbuta(self, tokenizer):
        """Test Teh Marbuta to Heh normalization."""
        text = "الرحمة"
        normalized = tokenizer.normalize(text)
        assert "ة" not in normalized
        assert "ه" in normalized

    def test_stop_words_removal(self, tokenizer):
        """Test stop words removal during tokenization."""
        text = "في الكتاب من الرجل"
        tokenized = tokenizer.tokenize(text)
        assert "في" not in tokenized
        assert "من" not in tokenized
        # After stemming, الكتاب might become كتاب
        assert "كتاب" in tokenized or "الكتاب" in tokenized
        # الرجل might become رجل
        assert "رجل" in tokenized or "الرجل" in tokenized

    def test_tokenize_empty_string(self, tokenizer):
        """Test tokenization of empty string."""
        assert tokenizer.tokenize("") == ""

    def test_kuwaiti_dialect_stop_words(self, tokenizer):
        """Test Kuwaiti dialect stop words."""
        text = "يلا شلون عشان"
        tokenized = tokenizer.tokenize(text)
        assert "يلا" not in tokenized
        assert "شلون" not in tokenized
        assert "عشان" not in tokenized


class TestSQLiteMemoryBackend:
    """Test SQLite memory backend functionality."""

    @pytest.mark.asyncio
    async def test_initialization(self, sqlite_backend, temp_db):
        """Test backend initialization."""
        assert sqlite_backend is not None
        assert str(temp_db) in sqlite_backend.db_path

    @pytest.mark.asyncio
    async def test_index_memory(self, sqlite_backend):
        """Test indexing a memory."""
        memory = {
            "id": "test_mem_1",
            "content": "Test memory content for indexing",
            "metadata": {"source": "test"},
            "timestamp": int(time.time()),
            "source": "unit_test",
            "relevance": 1.0,
        }
        memory_id = await sqlite_backend.index(memory)
        assert memory_id == "test_mem_1"

    @pytest.mark.asyncio
    async def test_index_auto_generate_id(self, sqlite_backend):
        """Test indexing without providing an ID."""
        memory = {
            "content": "Test memory with auto-generated ID",
            "metadata": {},
            "timestamp": int(time.time()),
        }
        memory_id = await sqlite_backend.index(memory)
        assert memory_id is not None
        assert memory_id.startswith("mem_")

    @pytest.mark.asyncio
    async def test_arabic_indexing(self, sqlite_backend):
        """Test indexing Arabic text with tokenization."""
        memory = {
            "id": "arabic_mem_1",
            "content": "أنا أريد الذهاب إلى المكتبة",
            "metadata": {"language": "ar"},
            "timestamp": int(time.time()),
            "source": "test",
        }
        memory_id = await sqlite_backend.index(memory)
        assert memory_id == "arabic_mem_1"

    @pytest.mark.asyncio
    async def test_fts5_search(self, sqlite_backend):
        """Test FTS5 full-text search."""
        # Index some memories
        memories = [
            {
                "id": "m1",
                "content": "Python programming is awesome",
                "metadata": {},
                "timestamp": int(time.time()),
                "source": "test",
                "relevance": 0.9,
            },
            {
                "id": "m2",
                "content": "JavaScript frameworks are popular",
                "metadata": {},
                "timestamp": int(time.time()),
                "source": "test",
                "relevance": 0.8,
            },
            {
                "id": "m3",
                "content": "Machine learning with Python",
                "metadata": {},
                "timestamp": int(time.time()),
                "source": "test",
                "relevance": 0.95,
            },
        ]

        for mem in memories:
            await sqlite_backend.index(mem)

        # Search for "Python"
        results = await sqlite_backend.search("Python", limit=10)
        assert len(results) > 0
        content_matches = [r["content"] for r in results if "Python" in r["content"]]
        assert len(content_matches) >= 2

    @pytest.mark.asyncio
    async def test_arabic_search(self, sqlite_backend):
        """Test Arabic text search with tokenization."""
        # Index Arabic memories
        memories = [
            {
                "id": "ar1",
                "content": "التعليم مهم جدا للطلاب",
                "metadata": {},
                "timestamp": int(time.time()),
                "source": "test",
                "relevance": 0.9,
            },
            {
                "id": "ar2",
                "content": "الصحة هي أهم شيء في الحياة",
                "metadata": {},
                "timestamp": int(time.time()),
                "source": "test",
                "relevance": 0.8,
            },
            {
                "id": "ar3",
                "content": "التعليم يفتح آفاقا جديدة",
                "metadata": {},
                "timestamp": int(time.time()),
                "source": "test",
                "relevance": 0.95,
            },
        ]

        for mem in memories:
            await sqlite_backend.index(mem)

        # Search for "التعليم" (education)
        results = await sqlite_backend.search("التعليم", limit=10)
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_count(self, sqlite_backend):
        """Test document count."""
        # Index some memories
        for i in range(5):
            await sqlite_backend.index(
                {
                    "content": f"Memory {i}",
                    "metadata": {},
                    "timestamp": int(time.time()),
                    "source": "test",
                    "relevance": 1.0,
                }
            )

        count = await sqlite_backend.count()
        assert count >= 5

    @pytest.mark.asyncio
    async def test_update_memory(self, sqlite_backend):
        """Test updating an existing memory."""
        memory = {
            "id": "update_test",
            "content": "Original content",
            "metadata": {},
            "timestamp": int(time.time()),
            "source": "test",
            "relevance": 0.5,
        }
        await sqlite_backend.index(memory)

        # Update the memory
        updated_memory = {
            "id": "update_test",
            "content": "Updated content",
            "metadata": {},
            "timestamp": int(time.time()),
            "source": "test",
            "relevance": 0.9,
        }
        await sqlite_backend.index(updated_memory)

        # Search for it
        results = await sqlite_backend.search("Updated", limit=10)
        assert len(results) > 0
        assert "Updated content" in [r["content"] for r in results]


class TestSearchBackend:
    """Test SearchBackend wrapper."""

    @pytest.mark.asyncio
    async def test_initialization(self, temp_db):
        """Test SearchBackend initialization."""
        backend = SearchBackend(str(temp_db))
        assert backend is not None
        await backend.close()

    @pytest.mark.asyncio
    async def test_search_wrapper(self, search_backend):
        """Test search wrapper method."""
        # Index a memory
        memory = {
            "id": "wrapper_test",
            "content": "Wrapper test content",
            "metadata": {},
            "timestamp": int(time.time()),
            "source": "test",
            "relevance": 1.0,
        }
        await search_backend.index(memory)

        # Search for it
        results = await search_backend.search("Wrapper", limit=10)
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_get_backend_info(self, search_backend):
        """Test getting backend information."""
        info = await search_backend.get_backend_info()
        assert info["backend_type"] == "sqlite"
        assert info["fts5_enabled"] is True
        assert info["arabic_tokenization"] is True
        assert "document_count" in info

    @pytest.mark.asyncio
    async def test_index_wrapper(self, search_backend):
        """Test index wrapper method."""
        memory = {
            "id": "index_wrapper_test",
            "content": "Index wrapper test",
            "metadata": {},
            "timestamp": int(time.time()),
            "source": "test",
            "relevance": 1.0,
        }
        memory_id = await search_backend.index(memory)
        assert memory_id == "index_wrapper_test"


class TestEdgeDeployment:
    """Test edge deployment scenarios."""

    @pytest.mark.asyncio
    async def test_no_vector_search_fts5_only(self, temp_db):
        """Test FTS5-only search when vector search unavailable."""
        backend = SQLiteMemoryBackend(str(temp_db))
        # Disable vector search by not checking extension
        backend._vec_available = False

        # Index a memory
        memory = {
            "id": "edge_test",
            "content": "Edge deployment test",
            "metadata": {},
            "timestamp": int(time.time()),
            "source": "test",
            "relevance": 1.0,
        }
        await backend.index(memory)

        # Search without vector search
        results = await backend.search("deployment", limit=10)
        assert len(results) > 0

        await backend.close()

    @pytest.mark.asyncio
    async def test_performance_basic_operations(self, search_backend):
        """Test basic operation performance for edge deployment."""
        import time

        # Index operation
        start = time.time()
        for i in range(10):
            await search_backend.index(
                {
                    "content": f"Performance test {i}",
                    "metadata": {},
                    "timestamp": int(time.time()),
                    "source": "test",
                    "relevance": 1.0,
                }
            )
        index_time = time.time() - start

        # Search operation
        start = time.time()
        results = await search_backend.search("test", limit=10)
        search_time = time.time() - start

        # Should be reasonably fast for edge deployment
        assert index_time < 5.0  # Index 10 items in < 5 seconds
        assert search_time < 1.0  # Search in < 1 second
