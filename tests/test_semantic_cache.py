"""Unit tests for the LLM Semantic Cache (Option D)."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

from kazma_core.swarm.semantic_cache import SemanticCache
from kazma_core.llm_provider import LLMProvider, LLMConfig, LLMResponse, ToolCall


class TestSemanticCache(unittest.TestCase):

    def setUp(self) -> None:
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_cache.db")
        self.caches_to_close: list[SemanticCache] = []

    def tearDown(self) -> None:
        for cache in self.caches_to_close:
            cache.close()
        shutil.rmtree(self.test_dir)

    def test_init_and_db_creation(self) -> None:
        cache = SemanticCache(self.db_path)
        self.caches_to_close.append(cache)
        self.assertTrue(os.path.exists(self.db_path))

    def test_compute_hash(self) -> None:
        cache = SemanticCache(self.db_path)
        self.caches_to_close.append(cache)
        h1 = cache._compute_hash("hello world", [{"name": "tool1"}])
        h2 = cache._compute_hash("hello world", [{"name": "tool1"}])
        h3 = cache._compute_hash("hello world", [{"name": "tool2"}])
        self.assertEqual(h1, h2)
        self.assertNotEqual(h1, h3)

    def test_cosine_similarity(self) -> None:
        cache = SemanticCache(self.db_path)
        self.caches_to_close.append(cache)
        v1 = [1.0, 0.0, 0.0]
        v2 = [1.0, 0.0, 0.0]
        v3 = [0.0, 1.0, 0.0]
        v4 = [1.0, 1.0, 0.0]  # Normalised dot product will be 1/sqrt(2) approx 0.707

        self.assertAlmostEqual(cache._cosine_similarity(v1, v2), 1.0)
        self.assertAlmostEqual(cache._cosine_similarity(v1, v3), 0.0)
        self.assertAlmostEqual(cache._cosine_similarity(v1, v4), 0.70710678)

    @patch("kazma_core.swarm.semantic_cache.get_encoder")
    def test_exact_hash_hit(self, mock_get_encoder: MagicMock) -> None:
        mock_get_encoder.return_value = None  # Force hash fallback
        cache = SemanticCache(self.db_path)
        self.caches_to_close.append(cache)

        prompt = "Explain quantum computing"
        response = {"content": "Quantum computing uses qubits..."}

        # Store in cache
        cache.store(prompt, response, tools=None)

        # Look up - exact hit should work even with no encoder
        hit = cache.lookup(prompt, tools=None)
        self.assertEqual(hit, response)

        # Look up - miss
        miss = cache.lookup("different prompt", tools=None)
        self.assertIsNone(miss)

    @patch("kazma_core.swarm.semantic_cache.get_encoder")
    def test_semantic_similarity_hit(self, mock_get_encoder: MagicMock) -> None:
        # Mock SentenceTransformer model
        mock_transformer = MagicMock()
        mock_get_encoder.return_value = mock_transformer

        # Define mock embeddings
        emb_query = [1.0, 0.0]
        emb_cached = [0.98, 0.1]  # high similarity >= 0.95
        emb_different = [0.0, 1.0]  # low similarity

        cache = SemanticCache(self.db_path)
        self.caches_to_close.append(cache)

        # Store an entry
        mock_transformer.encode.return_value = emb_cached
        cache.store("Write a python script to count words", {"content": "import sys..."}, tools=None)

        # Look up with a semantically similar query (triggers semantic lookup)
        mock_transformer.encode.return_value = emb_query
        hit = cache.lookup("Create a python script that counts words", tools=None, threshold=0.95)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["content"], "import sys...")

        # Look up with a different query (triggers miss)
        mock_transformer.encode.return_value = emb_different
        miss = cache.lookup("How is the weather today?", tools=None, threshold=0.95)
        self.assertIsNone(miss)


@patch.dict(os.environ, {"KAZMA_SEMANTIC_CACHE": "true"})
class TestLLMProviderSemanticCaching(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self) -> None:
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_cache.db")
        # Direct the LLMProvider to use our test db path
        from kazma_core.swarm import semantic_cache
        self.orig_db = semantic_cache._DEFAULT_DB
        semantic_cache._DEFAULT_DB = self.db_path

        # Clean any singleton in llm_provider
        llm_prov_mod = sys.modules.get("kazma_core.llm_provider")
        if llm_prov_mod is not None and hasattr(llm_prov_mod, "_semantic_cache_singleton"):
            delattr(llm_prov_mod, "_semantic_cache_singleton")

    async def asyncTearDown(self) -> None:
        # Close any global singleton connection in llm_provider
        llm_prov_mod = sys.modules.get("kazma_core.llm_provider")
        if llm_prov_mod is not None and hasattr(llm_prov_mod, "_semantic_cache_singleton"):
            singleton = getattr(llm_prov_mod, "_semantic_cache_singleton")
            if singleton is not None:
                singleton.close()
            delattr(llm_prov_mod, "_semantic_cache_singleton")

        from kazma_core.swarm import semantic_cache
        semantic_cache._DEFAULT_DB = self.orig_db
        shutil.rmtree(self.test_dir)

    @patch("kazma_core.llm_provider.httpx.AsyncClient")
    async def test_llm_provider_cache_flow(self, mock_client_class: MagicMock) -> None:
        # Setup mock response with standard dict / raise_for_status methods
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "This is a response from the LLM API"
                },
                "finish_reason": "stop"
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            "model": "gpt-4o-mini"
        }
        mock_resp.raise_for_status = MagicMock()

        # Setup mock http client
        mock_client = MagicMock()
        mock_post = AsyncMock(return_value=mock_resp)
        mock_client.post = mock_post
        mock_client.is_closed = False
        mock_client_class.return_value = mock_client

        config = LLMConfig(base_url="http://localhost:1234/v1", api_key="test-key")
        provider = LLMProvider(config)
        provider._http = mock_client

        messages = [{"role": "user", "content": "What is 2+2?"}]

        # First call: Cache Miss -> calls httpx
        resp1 = await provider.chat(messages)
        self.assertEqual(resp1.content, "This is a response from the LLM API")
        self.assertEqual(mock_post.call_count, 1)

        # Second call: Cache Hit -> returns directly, call count stays 1
        resp2 = await provider.chat(messages)
        self.assertEqual(resp2.content, "This is a response from the LLM API")
        self.assertEqual(mock_post.call_count, 1)
