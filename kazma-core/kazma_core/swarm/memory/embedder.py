"""Pluggable embedding backend for Kazma's memory / RAG layer.

This module abstracts the embedding model so the system can use either:

  * **local** — ``sentence-transformers`` in-process (default, free, 384-dim)
  * **openai-compatible** — any OpenAI-compatible ``/embeddings`` endpoint
    (NVIDIA NIM / NeMo Retriever, OpenAI, self-hosted TEI, etc.)

The contract is a single ``Embedder`` protocol with ``encode(text) -> list[float]``.
All call sites that previously did ``model.encode(text, convert_to_numpy=False)``
now go through ``get_embedder().encode(text)`` — a mechanical swap that keeps
the return type identical (``list[float]``).

For ChromaDB (which requires an ``EmbeddingFunction`` returning ``numpy.ndarray``),
``ChromaEmbeddingFunctionWrapper`` adapts any ``Embedder`` to that interface.

Config is read from ``kazma.yaml`` under ``memory.embedding`` with env-var
fallbacks (``KAZMA_EMBED_*``). When no config is present the defaults are
identical to today: local + ``all-MiniLM-L6-v2`` + 384-dim.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol, runtime_checkable

__all__ = ["DEFAULT_DIM", "DEFAULT_MODEL", "DEFAULT_PROVIDER", "Embedder", "LocalSentenceTransformerEmbedder", "OpenAICompatibleEmbedder", "get_embedder", "get_embedding_dim", "make_chroma_embedding_function", "reset_embedder"]

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "all-MiniLM-L6-v2"
DEFAULT_DIM = 384
DEFAULT_PROVIDER = "local"


# ══════════════════════════════════════════════════════════════════════════
# Embedder protocol
# ══════════════════════════════════════════════════════════════════════════


@runtime_checkable
class Embedder(Protocol):
    """The embedding contract every backend must satisfy.

    ``encode`` returns ``list[float]`` (never raw numpy/torch tensors).
    ``dim`` is the vector dimensionality (used for sqlite-vec table DDL).
    """

    dim: int

    def encode(self, text: str) -> list[float]: ...

    def encode_batch(self, texts: list[str]) -> list[list[float]]: ...


# ══════════════════════════════════════════════════════════════════════════
# Providers
# ══════════════════════════════════════════════════════════════════════════


class LocalSentenceTransformerEmbedder:
    """Local in-process embedder backed by ``sentence-transformers``.

    Wraps the same ``SentenceTransformer(model_name)`` the codebase already
    used, preserving the lazy-singleton behavior. Returns ``list[float]``.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL, dim: int = DEFAULT_DIM) -> None:
        self._model_name = model_name
        self._dim = dim
        self._model: Any = None

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)
            logger.info("[Embedder:local] Loaded %s", self._model_name)
        except ImportError:
            logger.warning("[Embedder:local] sentence-transformers not installed")
        except Exception as exc:
            logger.warning("[Embedder:local] load failed: %s", exc)
        return self._model

    @property
    def dim(self) -> int:
        return self._dim

    def encode(self, text: str) -> list[float]:
        model = self._ensure_model()
        if model is None:
            return []
        emb = model.encode(text, convert_to_numpy=False)
        if isinstance(emb, list):
            return emb
        if hasattr(emb, "tolist"):
            return emb.tolist()
        return list(emb)

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        model = self._ensure_model()
        if model is None:
            return [[] for _ in texts]
        embs = model.encode(texts, convert_to_numpy=False)
        result: list[list[float]] = []
        for emb in embs:
            if isinstance(emb, list):
                result.append(emb)
            elif hasattr(emb, "tolist"):
                result.append(emb.tolist())
            else:
                result.append(list(emb))
        return result


class OpenAICompatibleEmbedder:
    """Remote embedder for any OpenAI-compatible ``/embeddings`` endpoint.

    Covers NVIDIA NIM (NeMo Retriever / nv-embed-v1), OpenAI, self-hosted
    Text Embeddings Inference (TEI), etc. Uses a synchronous ``httpx.Client``
    because the swarm callers are sync; the supervisor's retrieval path goes
    through ``AsyncMemoryAdapter`` which already offloads to a thread executor.

    Includes a small in-memory cache so identical queries aren't re-embedded.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        dim: int,
        *,
        timeout: float = 60.0,
        cache_size: int = 512,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._dim = dim
        self._timeout = timeout
        self._client: Any = None
        # Simple LRU-ish cache (dict + size cap; good enough for query dedup).
        self._cache: dict[str, list[float]] = {}
        self._cache_size = cache_size

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        import httpx

        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=httpx.Timeout(self._timeout),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        return self._client

    @property
    def dim(self) -> int:
        return self._dim

    def encode(self, text: str) -> list[float]:
        """Embed a single text via the remote endpoint.

        Retries once on failure (NIM endpoints can be slow / rate-limited).
        Returns ``[]`` if both attempts fail. Results are cached so identical
        queries don't re-hit the API.
        """
        # Cache lookup
        if text in self._cache:
            return self._cache[text]
        client = self._ensure_client()
        emb: list[float] = []  # initialized to prevent UnboundLocalError
        # Retry once on failure (NIM endpoints can be slow / rate-limited).
        for attempt in range(2):
            try:
                resp = client.post(
                    "/embeddings",
                    json={"model": self._model, "input": text},
                )
                resp.raise_for_status()
                data = resp.json()
                emb = data["data"][0]["embedding"]
                break
            except Exception as exc:
                if attempt == 0:
                    logger.debug("[Embedder:openai-compatible] retrying after: %s", exc)
                else:
                    logger.warning("[Embedder:openai-compatible] encode failed: %s", exc)
                    return []
        # Cache store (evict oldest if over capacity)
        if len(self._cache) >= self._cache_size:
            self._cache.pop(next(iter(self._cache)))
        self._cache[text] = emb
        return emb

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts in one API call. Returns ``[[]]`` on failure."""
        client = self._ensure_client()
        results: list[list[float]] = []
        try:
            resp = client.post(
                "/embeddings",
                json={"model": self._model, "input": texts},
            )
            resp.raise_for_status()
            data = resp.json()
            # The API returns one embedding per input, in order.
            for item in sorted(data["data"], key=lambda d: d.get("index", 0)):
                results.append(item["embedding"])
        except Exception as exc:
            logger.warning("[Embedder:openai-compatible] batch encode failed: %s", exc)
            results = [[] for _ in texts]
        return results


# ══════════════════════════════════════════════════════════════════════════
# ChromaDB embedding-function adapter
# ══════════════════════════════════════════════════════════════════════════


def make_chroma_embedding_function(embedder: Embedder) -> Any:
    """Create a ChromaDB-compatible EmbeddingFunction wrapping any Embedder.

    ChromaDB's ``EmbeddingFunction.__call__`` must return a ``numpy.ndarray``
    (not a list). This adapter centralizes the numpy coercion so
    ``vector_store.py`` stays provider-agnostic. Falls back to ChromaDB's
    built-in ``SentenceTransformerEmbeddingFunction`` when the embedder is a
    local SentenceTransformer (so ChromaDB can use its own optimized path).
    """
    # Fast path: for the local ST embedder, let ChromaDB use its native EF
    # (avoids double-loading the model).
    if isinstance(embedder, LocalSentenceTransformerEmbedder):
        try:
            from chromadb.utils import embedding_functions

            return embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=embedder._model_name,
            )
        except Exception:
            pass  # fall through to the generic wrapper

    # Generic wrapper for remote providers.
    try:
        import numpy as np
        from chromadb.api.types import EmbeddingFunction, Documents, Embeddings
    except ImportError:
        logger.warning("[Embedder] chromadb not installed; ChromaDB EF unavailable")
        return None

    class _Wrapper(EmbeddingFunction):
        def __call__(self, input: Documents) -> Embeddings:
            embeddings = []
            for doc in input:
                emb = embedder.encode(doc)
                if not emb:
                    # Remote endpoint returned nothing (timeout / rate limit).
                    # Retry once — if still empty, fall back to a zero vector
                    # so ChromaDB doesn't crash (the doc just won't match well).
                    emb = embedder.encode(doc)
                if not emb:
                    logger.warning(
                        "[Embedder] embedding failed after retry — using zero vector "
                        "(recall quality degraded for this document)"
                    )
                    emb = [0.0] * embedder.dim
                embeddings.append(emb)
            return np.array(embeddings, dtype=np.float32)

        # ChromaDB 1.5.x calls name() and default_space() as methods
        # (not properties). Must return strings, not be properties.
        def name(self) -> str:
            return "kazma_embedder_wrapper"

        def default_space(self) -> str:
            return "cosine"

    return _Wrapper()


# ══════════════════════════════════════════════════════════════════════════
# Config + factory
# ══════════════════════════════════════════════════════════════════════════


def _read_embedding_config() -> dict[str, Any]:
    """Read the embedding config from kazma.yaml with env-var fallbacks.

    Returns a dict with keys: provider, model, dim, base_url, api_key.
    """
    cfg: dict[str, Any] = {}

    # From kazma.yaml (memory.embedding block)
    try:
        import yaml
        from pathlib import Path

        cfg_path = Path("kazma.yaml")
        if cfg_path.exists():
            with open(cfg_path) as f:
                full = yaml.safe_load(f) or {}
            cfg = full.get("memory", {}).get("embedding", {}) or {}
    except Exception:
        pass

    provider = (
        os.environ.get("KAZMA_EMBED_PROVIDER", "")
        or cfg.get("provider", DEFAULT_PROVIDER)
    ).strip().lower()
    model = os.environ.get("KAZMA_EMBED_MODEL", "") or cfg.get("model", DEFAULT_MODEL)
    dim = cfg.get("dim", DEFAULT_DIM)
    try:
        dim = int(os.environ.get("KAZMA_EMBED_DIM", "") or dim)
    except (ValueError, TypeError):
        dim = DEFAULT_DIM
    base_url = os.environ.get("KAZMA_EMBED_BASE_URL", "") or cfg.get("base_url", "")
    # api_key: read from the env var named in config (api_key_env), then
    # common aliases (NVIDIA NIM / OpenAI). Never inline the key in yaml.
    api_key_env = cfg.get("api_key_env", "KAZMA_EMBED_API_KEY")
    api_key = os.environ.get(str(api_key_env), "") if api_key_env else ""
    if not api_key:
        for _alias in ("KAZMA_EMBED_API_KEY", "NVIDIA_API_KEY", "NGC_API_KEY", "OPENAI_API_KEY"):
            api_key = os.environ.get(_alias, "")
            if api_key:
                break

    return {
        "provider": provider,
        "model": model,
        "dim": dim,
        "base_url": base_url,
        "api_key": api_key,
    }


_embedder: Embedder | None = None


def get_embedder() -> Embedder | None:
    """Return the shared Embedder singleton.

    Reads config once, instantiates the right provider, caches it. All
    vector backends MUST use this function so the model/endpoint is never
    loaded twice. Returns None if the provider can't initialize (the
    callers already handle None gracefully).

    When a remote provider is configured but missing ``base_url``/API key,
    fall back to the **default local model** (``all-MiniLM-L6-v2`` / 384-d),
    not the remote model name. Previously we kept ``nvidia/nv-embed-v1`` and
    tried to load it via sentence-transformers, which fails (gated HF repo)
    and silently kills store + per-turn recall.
    """
    global _embedder
    if _embedder is not None:
        return _embedder

    cfg = _read_embedding_config()
    provider = cfg["provider"]
    remote_providers = ("openai-compatible", "openai", "nim", "remote")
    fell_back_from_remote = False

    if provider in remote_providers:
        # Allow common NVIDIA/OpenAI env aliases when api_key_env is unset.
        api_key = (cfg.get("api_key") or "").strip()
        if not api_key:
            for env_name in (
                "KAZMA_EMBED_API_KEY",
                "NVIDIA_API_KEY",
                "NGC_API_KEY",
                "OPENAI_API_KEY",
            ):
                api_key = os.environ.get(env_name, "").strip()
                if api_key:
                    break
        base_url = (cfg.get("base_url") or "").strip()
        if not base_url:
            logger.warning(
                "[Embedder] provider=%s but base_url is empty — falling back to local %s",
                provider,
                DEFAULT_MODEL,
            )
            fell_back_from_remote = True
        elif not api_key:
            logger.warning(
                "[Embedder] provider=%s but api_key is empty "
                "(set %s or NVIDIA_API_KEY) — falling back to local %s",
                provider,
                cfg.get("api_key_env") or "KAZMA_EMBED_API_KEY",
                DEFAULT_MODEL,
            )
            fell_back_from_remote = True
        else:
            _embedder = OpenAICompatibleEmbedder(
                base_url=base_url,
                api_key=api_key,
                model=cfg["model"],
                dim=cfg["dim"],
            )
            logger.info(
                "[Embedder] Using openai-compatible: %s (dim=%d, base=%s)",
                cfg["model"], cfg["dim"], base_url,
            )
            return _embedder

    # Default / fallback: local sentence-transformers.
    if provider not in ("local", "") and provider not in remote_providers:
        logger.warning(
            "[Embedder] Unknown provider '%s' — falling back to local. "
            "Valid: local, openai-compatible", provider,
        )
        fell_back_from_remote = True

    # Critical: when leaving a remote config, do NOT keep the remote model
    # name (e.g. nvidia/nv-embed-v1) — ST will try to download a gated HF
    # model and every encode returns [].
    if fell_back_from_remote or provider in remote_providers:
        local_model = DEFAULT_MODEL
        local_dim = DEFAULT_DIM
    else:
        local_model = cfg.get("model") or DEFAULT_MODEL
        local_dim = int(cfg.get("dim") or DEFAULT_DIM)

    _embedder = LocalSentenceTransformerEmbedder(
        model_name=local_model, dim=local_dim,
    )
    logger.info("[Embedder] Using local: %s (dim=%d)", local_model, local_dim)
    return _embedder


def get_embedding_dim() -> int:
    """Return the configured embedding dimension (without loading the model).

    Used by sqlite-vec table DDL and semantic_router before the embedder is
    instantiated. Reads from config; defaults to 384.
    """
    return _read_embedding_config()["dim"]


def reset_embedder() -> None:
    """Drop the singleton reference (used by test teardown)."""
    global _embedder
    _embedder = None
