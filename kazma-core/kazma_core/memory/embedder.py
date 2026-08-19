"""Pluggable embedding backend for Kazma's memory / RAG layer.

This module abstracts the embedding model so the system can use either:

  * **local** — ``sentence-transformers`` in-process (default, free, 1024-dim)
  * **openai-compatible** — any OpenAI-compatible ``/embeddings`` endpoint
    (NVIDIA NIM / NeMo Retriever, OpenAI, self-hosted TEI, etc.)

The contract is a single ``Embedder`` protocol with ``encode(text) -> list[float]``.
All call sites that previously did ``model.encode(text, convert_to_numpy=False)``
now go through ``get_embedder().encode(text)`` — a mechanical swap that keeps
the return type identical (``list[float]``).

For ChromaDB (which requires an ``EmbeddingFunction`` returning ``numpy.ndarray``),
``ChromaEmbeddingFunctionWrapper`` adapts any ``Embedder`` to that interface.

Config precedence (highest wins):

1. Env vars (``KAZMA_EMBED_PROVIDER`` / ``KAZMA_EMBED_MODEL`` / ``KAZMA_EMBED_DIM`` /
   ``KAZMA_EMBED_BASE_URL``; ``KAZMA_VECTOR_MODEL`` is accepted as a legacy alias
   for the model name)
2. ConfigStore override keys ``embedding.*`` (written by the Web UI Embedder
   settings page — live, no restart needed to *save*; the embedder singleton
   is per-process so a server restart is required to *apply*)
3. ``kazma.yaml`` under ``memory.embedding``
4. Built-in defaults: local + ``BAAI/bge-m3`` + 1024-dim (multilingual).

The default model was upgraded from the 384-dim ``all-MiniLM-L6-v2`` to the
1024-dim multilingual ``BAAI/bge-m3``. Existing stores keep their rows; run
the Embedder rebuild action (Web UI) or ``scripts/reembed.py`` after switching
so every row lives in the same vector space.
"""

from __future__ import annotations

import logging
import os
import struct
import threading
import time
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "DEFAULT_DIM",
    "DEFAULT_MODEL",
    "DEFAULT_PROVIDER",
    "Embedder",
    "LocalSentenceTransformerEmbedder",
    "OpenAICompatibleEmbedder",
    "encode_text_to_blob",
    "get_embedder",
    "get_embedding_config",
    "get_embedding_dim",
    "get_embedding_model_name",
    "get_embedder_status",
    "make_chroma_embedding_function",
    "reset_embedder",
    "resolve_unix_timestamp",
    "serialize_f32_embedding",
]

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "BAAI/bge-m3"
DEFAULT_DIM = 1024
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

    ``allow_download`` (default True) controls whether constructing the
    model may pull it from HuggingFace when it is not in the local cache.
    FALLBACK embedders (unknown provider / broken remote config) get
    ``allow_download=False`` — a misconfiguration must not stall the
    process on a live ~2GB model download (deep-audit 2026-08-19; force
    with ``KAZMA_EMBED_ALLOW_DOWNLOAD=1``).
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        dim: int = DEFAULT_DIM,
        *,
        allow_download: bool = True,
    ) -> None:
        self._model_name = model_name
        self._dim = dim
        self._model: Any = None
        self._model_lock = threading.Lock()
        self._allow_download = allow_download

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        # Double-checked lock: concurrent first .encode() calls (e.g. a
        # fan-out dispatching workers simultaneously) would each construct
        # SentenceTransformer and double-load the ~2.3GB model without this.
        with self._model_lock:
            if self._model is not None:
                return self._model
            if not self._allow_download and not _model_in_hf_cache(self._model_name):
                logger.warning(
                    "[Embedder:local] %s is not in the local HF cache and "
                    "downloads are disabled for fallback embedders (set "
                    "KAZMA_EMBED_ALLOW_DOWNLOAD=1 to permit) — degrading to "
                    "no local embeddings",
                    self._model_name,
                )
                return None
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
        # Guards _cache — encode() is called from the consolidator's per-turn
        # threads AND recall threads concurrently (audit finding).
        self._cache_lock = threading.Lock()

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
        # Cache lookup (thread-safe)
        with self._cache_lock:
            cached = self._cache.get(text)
        if cached is not None:
            return cached
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
        # Cache store — only successful (non-empty) results, so a transient
        # empty/malformed response doesn't poison the cache permanently. The
        # httpx call above runs WITHOUT the lock (don't serialize encodes);
        # only dict mutation is guarded (audit finding).
        if emb:
            with self._cache_lock:
                if len(self._cache) >= self._cache_size:
                    self._cache.pop(next(iter(self._cache)), None)
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
    ``vector_store.py`` stays provider-agnostic.

    Always uses the generic wrapper — even for local SentenceTransformer
    — to avoid loading the model twice (the embedder already holds it).
    """
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


def _store_embedding_overrides() -> dict[str, Any]:
    """Read embedding overrides from the ConfigStore singleton, if initialized.

    Uses the module-level singleton directly (never constructs the store) so
    standalone scripts / tests that never boot the app are unaffected. The
    Web UI Embedder settings page writes these keys via ``SettingsManager``;
    they take effect on the next server start (the embedder is a per-process
    singleton).
    """
    overrides: dict[str, Any] = {}
    try:
        import kazma_core.config_store as _cs_mod

        store = getattr(_cs_mod, "_config_store", None)
        if store is None:
            return overrides
        for key in ("provider", "model", "dim", "base_url", "api_key_env"):
            try:
                val = store.get(f"embedding.{key}")
            except Exception:
                val = None
            if val is not None and str(val).strip() != "":
                overrides[key] = val
    except Exception:
        pass
    return overrides


def _read_embedding_config() -> dict[str, Any]:
    """Read the embedding config: env vars > ConfigStore > kazma.yaml > defaults.

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

    # From ConfigStore (Web UI Embedder settings page) — wins over yaml.
    cfg = {**cfg, **_store_embedding_overrides()}

    provider = (
        os.environ.get("KAZMA_EMBED_PROVIDER", "")
        or cfg.get("provider", DEFAULT_PROVIDER)
    ).strip().lower()
    model = (
        os.environ.get("KAZMA_EMBED_MODEL", "")
        or os.environ.get("KAZMA_VECTOR_MODEL", "")
        or cfg.get("model", DEFAULT_MODEL)
    )
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
        "api_key_env": api_key_env,
    }


def get_embedding_config() -> dict[str, Any]:
    """Return the resolved embedding config WITHOUT secrets.

    Same resolution as :func:`get_embedder` uses, minus the API key (the
    caller only learns whether one is present). Used by the Web UI Embedder
    settings page and health checks.
    """
    cfg = _read_embedding_config()
    cfg["api_key_present"] = bool(cfg.get("api_key"))
    cfg.pop("api_key", None)
    return cfg


def get_embedding_model_name() -> str:
    """Return the resolved embedding model name (no side effects).

    Used to stamp ``embedding_model_version`` on new rows so existing
    databases (whose column DEFAULT predates a model switch) stay accurate.
    """
    return _read_embedding_config()["model"]


def get_embedder_status() -> dict[str, Any]:
    """Return the embedder configuration + live-singleton state (no model load).

    ``active`` is None until the embedder has actually been instantiated in
    this process (config is not enough to know the class). The UI uses this
    to show the "restart required" banner when ``active`` disagrees with the
    resolved ``config``.
    """
    config = get_embedding_config()
    active: dict[str, Any] | None = None
    if _embedder is not None:
        active = {
            "class": type(_embedder).__name__,
            "dim": getattr(_embedder, "dim", None),
            "model": getattr(_embedder, "_model_name", None)
            or getattr(_embedder, "_model", None)
            or None,
        }
    return {"config": config, "active": active}


_embedder: Embedder | None = None


def _model_in_hf_cache(model_name: str) -> bool:
    """Whether *model_name* resolves from the local HF cache (no network).

    ``snapshot_download(local_files_only=True)`` raises when the model is
    not cached and never touches the network — the guard that lets
    fallback embedders degrade instead of stalling on a live download.
    """
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(repo_id=model_name, local_files_only=True)
        return True
    except Exception:
        return False


def get_embedder() -> Embedder | None:
    """Return the shared Embedder singleton.

    Reads config once, instantiates the right provider, caches it. All
    vector backends MUST use this function so the model/endpoint is never
    loaded twice. Returns None if the provider can't initialize (the
    callers already handle None gracefully).

    When a remote provider is configured but missing ``base_url``/API key,
    fall back to the **default local model** (``BAAI/bge-m3`` / 1024-d),
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

    # Fallback embedders (unknown provider / broken remote config) may NOT
    # surprise-download the ~2GB model — only a deliberate local config
    # may (deep-audit 2026-08-19). KAZMA_EMBED_ALLOW_DOWNLOAD force-allows.
    allow_download = not fell_back_from_remote
    if os.environ.get("KAZMA_EMBED_ALLOW_DOWNLOAD", "").strip().lower() in (
        "1", "true", "yes", "on",
    ):
        allow_download = True

    _embedder = LocalSentenceTransformerEmbedder(
        model_name=local_model, dim=local_dim, allow_download=allow_download,
    )
    logger.info("[Embedder] Using local: %s (dim=%d)", local_model, local_dim)
    return _embedder


def get_embedding_dim() -> int:
    """Return the configured embedding dimension (without loading the model).

    Used by sqlite-vec table DDL and semantic_router before the embedder is
    instantiated. Reads from config; defaults to 1024 (DEFAULT_DIM).
    """
    return _read_embedding_config()["dim"]


def reset_embedder() -> None:
    """Drop the singleton reference (used by test teardown)."""
    global _embedder
    _embedder = None


def serialize_f32_embedding(embedding: list[float]) -> bytes:
    """Pack a float list as little-endian float32 BLOB (L3 memories.embedding)."""
    if not embedding:
        return b""
    return struct.pack(f"<{len(embedding)}f", *embedding)


def encode_text_to_blob(text: str) -> bytes | None:
    """Encode *text* with the shared embedder → float32 BLOB, or None on failure.

    Used by L3 (memory.db) so semantic search can run without Chroma/L4.
    Empty / whitespace-only input returns None (no zero-vector pollution).
    """
    body = (text or "").strip()
    if not body:
        return None
    try:
        emb = get_embedder()
        if emb is None:
            return None
        vec = emb.encode(body)
        if not vec:
            return None
        blob = serialize_f32_embedding(list(vec))
        return blob or None
    except Exception:
        logger.debug("[Embedder] encode_text_to_blob failed", exc_info=True)
        return None


def resolve_unix_timestamp(metadata: dict[str, Any] | None = None) -> int:
    """Resolve a unix-seconds timestamp from metadata or *now*.

    Accepts ``timestamp`` / ``ts`` as int/float, ISO-8601 string, or numeric
    string. Never returns 0 for new writes (0 is reserved for "unknown/legacy").
    """
    meta = metadata if isinstance(metadata, dict) else {}
    for key in ("timestamp", "ts", "created_at", "time"):
        raw = meta.get(key)
        if raw is None or raw == "" or raw == 0:
            continue
        if isinstance(raw, bool):
            continue
        if isinstance(raw, (int, float)):
            val = int(raw)
            # ms → s if looks like epoch millis
            if val > 10_000_000_000:
                val = val // 1000
            if val > 0:
                return val
        if isinstance(raw, str):
            s = raw.strip()
            if not s:
                continue
            try:
                if s.isdigit() or (s[0] == "-" and s[1:].isdigit()):
                    val = int(s)
                    if val > 10_000_000_000:
                        val = val // 1000
                    if val > 0:
                        return val
            except ValueError:
                pass
            try:
                # Support trailing Z
                iso = s.replace("Z", "+00:00") if s.endswith("Z") else s
                dt = datetime.fromisoformat(iso)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                val = int(dt.timestamp())
                if val > 0:
                    return val
            except ValueError:
                pass
    return int(time.time())
