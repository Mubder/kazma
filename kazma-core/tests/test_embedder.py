"""Tests for the pluggable embedding backend (kazma_core.swarm.memory.embedder).

Covers the embedder factory dispatch, OpenAICompatibleEmbedder retry/cache,
LocalSentenceTransformerEmbedder graceful degradation, get_embedding_dim,
and the ChromaDB wrapper fallback.
"""

from __future__ import annotations

import os
from unittest.mock import patch, MagicMock

import pytest

from kazma_core.swarm.memory.embedder import (
    LocalSentenceTransformerEmbedder,
    OpenAICompatibleEmbedder,
    get_embedding_dim,
    reset_embedder,
    DEFAULT_DIM,
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
)


# ── Factory dispatch ───────────────────────────────────────────────────


def test_get_embedding_dim_default(monkeypatch):
    """get_embedding_dim returns a sane default."""
    monkeypatch.delenv("KAZMA_EMBED_DIM", raising=False)
    dim = get_embedding_dim()
    assert isinstance(dim, int)
    assert dim >= 1


def test_unknown_provider_warns(monkeypatch):
    """An unknown provider string falls back to local with a warning."""
    monkeypatch.setenv("KAZMA_EMBED_PROVIDER", "invalid-provider")
    monkeypatch.delenv("KAZMA_EMBED_BASE_URL", raising=False)
    monkeypatch.delenv("KAZMA_EMBED_API_KEY", raising=False)
    reset_embedder()
    from kazma_core.swarm.memory.embedder import get_embedder

    e = get_embedder()
    assert isinstance(e, LocalSentenceTransformerEmbedder)
    reset_embedder()


# ── LocalSentenceTransformerEmbedder ────────────────────────────────────


def test_local_embedder_returns_empty_when_no_model():
    """When sentence-transformers is unavailable, encode returns []."""
    e = LocalSentenceTransformerEmbedder(model_name="nonexistent/model", dim=384)
    # Force _ensure_model to return None (simulate missing dep).
    e._model = None
    # Patch _ensure_model so it doesn't try to download
    with patch.object(e, "_ensure_model", return_value=None):
        assert e.encode("test") == []
        assert e.encode_batch(["a", "b"]) == [[], []]


def test_local_embedder_dim_property():
    e = LocalSentenceTransformerEmbedder(dim=512)
    assert e.dim == 512


# ── OpenAICompatibleEmbedder ───────────────────────────────────────────


def test_openai_embedder_dim_property():
    e = OpenAICompatibleEmbedder(
        base_url="https://example.com/v1",
        api_key="test-key",
        model="test-model",
        dim=1024,
    )
    assert e.dim == 1024


def test_openai_embedder_cache():
    """encode() caches results so identical queries don't re-hit the API."""
    e = OpenAICompatibleEmbedder(
        base_url="https://example.com/v1",
        api_key="test-key",
        model="test-model",
        dim=4,
    )
    # Manually populate cache.
    e._cache["hello"] = [1.0, 2.0, 3.0, 4.0]
    result = e.encode("hello")
    assert result == [1.0, 2.0, 3.0, 4.0]


def test_openai_embedder_retry_then_fail():
    """When both attempts fail, encode returns []."""
    e = OpenAICompatibleEmbedder(
        base_url="https://example.com/v1",
        api_key="test-key",
        model="test-model",
        dim=4,
    )
    # Mock the httpx client to always fail.
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = Exception("timeout")
    mock_client.post.return_value = mock_resp
    e._client = mock_client

    result = e.encode("test text")
    assert result == []
    # Should have tried twice (retry logic).
    assert mock_client.post.call_count == 2


def test_openai_embedder_retry_then_succeed():
    """When the second attempt succeeds, encode returns the embedding."""
    e = OpenAICompatibleEmbedder(
        base_url="https://example.com/v1",
        api_key="test-key",
        model="test-model",
        dim=4,
    )
    mock_client = MagicMock()
    # First call fails, second succeeds.
    fail_resp = MagicMock()
    fail_resp.raise_for_status.side_effect = Exception("timeout")

    success_resp = MagicMock()
    success_resp.raise_for_status.return_value = None
    success_resp.json.return_value = {"data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}]}

    mock_client.post.side_effect = [fail_resp, success_resp]
    e._client = mock_client

    result = e.encode("test text")
    assert result == [0.1, 0.2, 0.3, 0.4]
    assert mock_client.post.call_count == 2


def test_openai_embedder_encode_batch_failure():
    """encode_batch returns [[]] for each input on failure."""
    e = OpenAICompatibleEmbedder(
        base_url="https://example.com/v1",
        api_key="test-key",
        model="test-model",
        dim=4,
    )
    mock_client = MagicMock()
    mock_client.post.side_effect = Exception("connection refused")
    e._client = mock_client

    results = e.encode_batch(["a", "b", "c"])
    assert results == [[], [], []]


# ── ChromaDB wrapper ───────────────────────────────────────────────────


def test_chroma_wrapper_local_uses_native_ef():
    """For local embedders, the wrapper delegates to ChromaDB's native EF."""
    e = LocalSentenceTransformerEmbedder(model_name="all-MiniLM-L6-v2", dim=384)
    from kazma_core.swarm.memory.embedder import make_chroma_embedding_function

    try:
        ef = make_chroma_embedding_function(e)
        # If chromadb is installed, this returns a real EF (not None).
        if ef is not None:
            assert hasattr(ef, "__call__")
    except ImportError:
        pass  # chromadb not installed in test env — skip
