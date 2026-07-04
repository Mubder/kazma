"""Semantic capability router for the swarm engine.

Replaces keyword-only matching with local semantic similarity using
sentence-transformers embeddings stored in ChromaDB.  Falls back to
keyword matching when the embedding model is unavailable (e.g., on
machines without the `rag` extra).

Architecture:
    Registry → build profiles → ChromaDB collection
    Task query → embed → ChromaDB cosine similarity → ranked workers
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────

_DEFAULT_MODEL = "all-MiniLM-L6-v2"
_DEFAULT_COLLECTION = "swarm_worker_profiles"
_EMBEDDING_DIM = 384  # all-MiniLM-L6-v2 output dimension


class SemanticRoutingUnavailableError(RuntimeError):
    """Raised when semantic routing is requested but embeddings are unavailable."""


def _tokenize(text: str) -> set[str]:
    """Lowercase and split text into word tokens, normalizing underscores/hyphens."""
    normalized = text.lower().replace("_", " ").replace("-", " ")
    return set(re.findall(r"[a-z0-9]+", normalized))


# ── Keyword scoring (fallback) ────────────────────────────────────────────


def keyword_match_score(task_description: str, expertise_tags: list[str]) -> int:
    """Score a worker's expertise against a task description using keyword overlap.

    Returns an integer score; higher is better.
    """
    desc_lower = task_description.lower()
    score = 0
    for tag in expertise_tags:
        if tag.lower() in desc_lower:
            score += 10
    # Bonus for word-level overlap
    for kw in desc_lower.split():
        for tag in expertise_tags:
            if kw in tag.lower() or tag.lower() in kw:
                score += 2
    return score


# ── Semantic routing ──────────────────────────────────────────────────────


class SemanticRouter:
    """Routes tasks to workers using semantic similarity.

    Stores worker expertise profiles as embeddings in a ChromaDB
    collection.  Queries embed the task description and rank workers
    by cosine similarity.

    Falls back to keyword matching when sentence-transformers or
    ChromaDB are unavailable.
    """

    def __init__(
        self,
        model_name: str = _DEFAULT_MODEL,
        collection_name: str = _DEFAULT_COLLECTION,
        persist_dir: str | None = None,
    ) -> None:
        self._model_name = model_name
        self._collection_name = collection_name
        self._persist_dir = persist_dir
        self._model: Any = None
        self._collection: Any = None
        self._available: bool = False
        self._initialized: bool = False
        # Guard profile rebuilds so concurrent route() calls (e.g. from
        # multiple threads) cannot interleave delete/add and corrupt the
        # collection.  ``_profile_sig`` caches the worker-set signature
        # so we only rebuild when the profiles actually change.
        self._profile_lock = threading.Lock()
        self._profile_sig: tuple[tuple[str, str], ...] = ()

    # ── Lazy initialization ────────────────────────────────────────────

    def _ensure_model(self) -> bool:
        """Lazy-load the sentence-transformers model via shared singleton.

        Uses ``get_encoder()`` from ``swarm.memory.vector`` so the model
        is never double-loaded across the system.
        """
        if self._model is not None:
            return True
        try:
            from kazma_core.swarm.memory.vector import get_encoder

            self._model = get_encoder(self._model_name)
            if self._model is not None:
                logger.info("[SemanticRouter] Using shared encoder: %s", self._model_name)
                return True
            return False
        except Exception as exc:
            logger.warning("[SemanticRouter] Shared encoder unavailable: %s", exc)
            return False

    def _ensure_collection(self) -> bool:
        """Lazy-load the ChromaDB collection.  Returns True on success."""
        if self._collection is not None:
            return True
        try:
            import chromadb
            from chromadb.config import Settings

            client_kwargs: dict[str, Any] = {}
            if self._persist_dir:
                client_kwargs["settings"] = Settings(
                    persist_directory=self._persist_dir,
                    anonymized_telemetry=False,
                )
            client = chromadb.Client(
                chromadb.config.Settings(
                    anonymized_telemetry=False,
                    **(client_kwargs.get("settings", {}).__dict__ if "settings" in client_kwargs else {}),
                )
            ) if not self._persist_dir else chromadb.PersistentClient(path=self._persist_dir)

            # Get or create the collection
            try:
                self._collection = client.get_collection(self._collection_name)
            except Exception:
                self._collection = client.create_collection(
                    name=self._collection_name,
                    metadata={"description": "Swarm worker expertise profiles"},
                )
            logger.info("[SemanticRouter] ChromaDB collection ready: %s", self._collection_name)
            return True
        except ImportError:
            logger.warning(
                "[SemanticRouter] chromadb not installed — keyword fallback active"
            )
            return False
        except Exception as exc:
            logger.warning("[SemanticRouter] ChromaDB init failed: %s", exc)
            return False

    @property
    def available(self) -> bool:
        """Whether semantic routing is available."""
        if self._initialized:
            return self._available
        self._initialized = True
        self._available = self._ensure_model() and self._ensure_collection()
        return self._available

    # ── Profile building ────────────────────────────────────────────────

    def build_profiles(self, workers: list[dict[str, Any]]) -> None:
        """Build/update ChromaDB profiles from a list of worker dicts.

        Each dict should have keys: name, expertise (list of str),
        system_prompt (str), and optionally roles (list of str).

        This clears the existing collection and rebuilds it.
        """
        if not self.available:
            return

        if not workers:
            return

        # Content signature: rebuild only when the worker profiles change,
        # not on every route() call.  Sorted for stable comparison.
        sig = tuple(sorted(
            (str(w.get("name", "")), ",".join(sorted(w.get("expertise", []))))
            for w in workers
        ))
        with self._profile_lock:
            if sig == self._profile_sig and self._collection.count() == len(workers):
                return  # already up to date
            self._profile_sig = sig

            # Build text profiles for embedding
            ids: list[str] = []
            documents: list[str] = []
            metadatas: list[dict[str, Any]] = []

            for w in workers:
                wid = w.get("name", "")
                expertise = w.get("expertise", [])
                system_prompt = w.get("system_prompt", "")
                roles = w.get("roles", [])

                # Build a rich text profile for embedding
                profile_text = f"Worker: {wid}\nExpertise: {', '.join(expertise)}\n"
                if system_prompt:
                    profile_text += f"Description: {system_prompt[:200]}\n"

                ids.append(wid)
                documents.append(profile_text)
                metadatas.append({
                    "name": wid,
                    "expertise": ",".join(expertise),
                    "roles": ",".join(roles),
                })

            try:
                # Clear and rebuild
                existing = self._collection.get()
                if existing and existing.get("ids"):
                    self._collection.delete(ids=existing["ids"])
                self._collection.add(
                    ids=ids,
                    documents=documents,
                    metadatas=metadatas,
                )
                logger.info("[SemanticRouter] Built profiles for %d workers", len(workers))
            except Exception as exc:
                logger.warning("[SemanticRouter] Profile build failed: %s", exc)
                # Invalidate the cache so the next call retries.
                self._profile_sig = ()

    def _embed(self, text: str) -> list[float] | None:
        """Embed a query text.  Returns None on failure."""
        if not self.available or self._model is None:
            return None
        try:
            embedding = self._model.encode(text, convert_to_numpy=False)
            if isinstance(embedding, list):
                return embedding
            return list(embedding)  # type: ignore[arg-type]
        except Exception as exc:
            logger.warning("[SemanticRouter] Embedding failed: %s", exc)
            return None

    def query(
        self,
        task_description: str,
        top_n: int = 5,
    ) -> list[tuple[str, float]]:
        """Query workers by semantic similarity.

        Returns a list of (worker_name, similarity_score) tuples,
        sorted by highest similarity first.
        """
        if not self.available:
            return []

        embedding = self._embed(task_description)
        if embedding is None:
            return []

        try:
            results = self._collection.query(
                query_embeddings=[embedding],
                n_results=min(top_n, self._collection.count()),
            )
            if not results or not results.get("ids") or not results["ids"][0]:
                return []

            scored: list[tuple[str, float]] = []
            ids_list = results["ids"][0]
            distances = results.get("distances", [[0.0] * len(ids_list)])[0]
            for i, wid in enumerate(ids_list):
                dist = distances[i] if i < len(distances) else 0.0
                scored.append((wid, 1.0 - float(dist)))
            scored.sort(key=lambda x: x[1], reverse=True)
            return scored
        except Exception as exc:
            logger.warning("[SemanticRouter] Query failed: %s — falling back", exc)
            return []

    # ── High-level routing (semantic → keyword fallback) ──────────────────

    def route(
        self,
        task_description: str,
        workers: list[dict[str, Any]],
        top_n: int = 5,
    ) -> list[str]:
        """Route a task to the best workers using semantic + fallback.

        1. Try semantic similarity via ChromaDB.
        2. If unavailable or empty, fall back to keyword matching.
        3. Return worker names sorted by relevance.
        """
        # 1 — Semantic
        if self.available:
            self.build_profiles(workers)
            scored = self.query(task_description, top_n=top_n)
            if scored:
                return [name for name, _score in scored]

        # 2 — Keyword fallback
        logger.info("[SemanticRouter] Using keyword fallback for routing")
        keyword_scored: list[tuple[str, int]] = []
        for w in workers:
            score = keyword_match_score(task_description, w.get("expertise", []))
            keyword_scored.append((w["name"], score))
        keyword_scored.sort(key=lambda x: x[1], reverse=True)
        return [name for name, s in keyword_scored[:top_n] if s > 0]


# ── Module-level singleton ────────────────────────────────────────────────

_semantic_router: SemanticRouter | None = None


def get_semantic_router(model_name: str = _DEFAULT_MODEL) -> SemanticRouter:
    """Return the shared SemanticRouter instance."""
    global _semantic_router
    if _semantic_router is None:
        _semantic_router = SemanticRouter(model_name=model_name)
    return _semantic_router
