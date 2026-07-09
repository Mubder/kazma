"""LLM Semantic Cache using SQLite and pure-Python cosine similarity."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any

from kazma_core.swarm.memory.vector import get_encoder

from kazma_core.config_store import apply_sqlite_pragmas

logger = logging.getLogger(__name__)

_DEFAULT_DB = "kazma-data/semantic_cache.db"


class SemanticCache:
    """Thread-safe, portable LLM Semantic Cache using SQLite and pure-Python cosine similarity."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or _DEFAULT_DB
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._init_db()

    def close(self) -> None:
        """Close the SQLite database connection cleanly."""
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            apply_sqlite_pragmas(self._conn)
        return self._conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_conn()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS semantic_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prompt_hash TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    tools_json TEXT,
                    response_json TEXT NOT NULL,
                    embedding TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    scope TEXT NOT NULL DEFAULT '_global_'
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_hash ON semantic_cache(prompt_hash)")
            # Migration: add scope column on existing DBs (idempotent).
            try:
                conn.execute("ALTER TABLE semantic_cache ADD COLUMN scope TEXT NOT NULL DEFAULT '_global_'")
            except sqlite3.OperationalError:
                pass  # Column already exists.
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scope ON semantic_cache(scope)")
            conn.commit()

    def _compute_hash(self, prompt: str, tools: list[dict[str, Any]] | None, scope: str = "_global_") -> str:
        """Compute SHA256 of scope + prompt + tools_json for exact hash fallback.

        ``scope`` isolates cache entries by caller context (e.g. a user or
        session id). The default ``_global_`` scope is shared across all
        callers — only safe for prompts with no user-specific content.
        """
        tools_str = json.dumps(tools, sort_keys=True) if tools else ""
        payload = f"{scope}||{prompt}||{tools_str}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _cosine_similarity(self, v1: list[float], v2: list[float]) -> float:
        """Calculate cosine similarity in pure Python for absolute portability."""
        if not v1 or not v2 or len(v1) != len(v2):
            return 0.0
        dot_product = sum(a * b for a, b in zip(v1, v2))
        norm_v1 = sum(a * a for a in v1) ** 0.5
        norm_v2 = sum(b * b for b in v2) ** 0.5
        if norm_v1 == 0.0 or norm_v2 == 0.0:
            return 0.0
        return dot_product / (norm_v1 * norm_v2)

    def lookup(
        self, prompt: str, tools: list[dict[str, Any]] | None = None, threshold: float = 0.95,
        scope: str = "_global_",
    ) -> dict[str, Any] | None:
        """Look up prompt in the cache.

        First attempts semantic embedding match. Falls back to exact hash if
        embeddings fail or are unavailable.

        ``scope`` isolates entries by caller context (user/session id). The
        default ``_global_`` is shared across callers — only safe for prompts
        with no user-specific content.
        """
        # 1. Exact hash fast lookup (always available)
        prompt_hash = self._compute_hash(prompt, tools, scope)
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT response_json FROM semantic_cache WHERE prompt_hash = ? LIMIT 1",
                (prompt_hash,),
            ).fetchone()
            if row:
                logger.info("[SemanticCache] Exact hash HIT!")
                return json.loads(row["response_json"])

        # 2. Try Semantic similarity
        encoder = get_encoder()
        if encoder is None:
            # Encoder not available (no sentence-transformers)
            return None

        try:
            query_embedding = encoder.encode(prompt, convert_to_numpy=False)
            if not isinstance(query_embedding, list):
                query_embedding = list(query_embedding)
        except Exception as exc:
            logger.warning("[SemanticCache] Failed to encode query prompt: %s", exc)
            return None

        # Fetch all candidate entries with embeddings — scoped so a
        # semantically-similar prompt from a different caller cannot return
        # another caller's cached response.
        tools_json = json.dumps(tools, sort_keys=True) if tools else "{}"
        with self._lock:
            conn = self._get_conn()
            cursor = conn.execute(
                "SELECT response_json, embedding FROM semantic_cache "
                "WHERE tools_json = ? AND embedding IS NOT NULL AND scope = ?",
                (tools_json, scope),
            )
            rows = cursor.fetchall()

        best_score = -1.0
        best_response = None

        for row in rows:
            try:
                emb_list = json.loads(row["embedding"])
                score = self._cosine_similarity(query_embedding, emb_list)
                if score > best_score:
                    best_score = score
                    best_response = row["response_json"]
            except Exception as exc:
                logger.debug("[SemanticCache] Failed to parse/compare cached embedding: %s", exc)
                continue

        if best_score >= threshold and best_response:
            logger.info("[SemanticCache] Semantic similarity HIT! score=%.4f", best_score)
            return json.loads(best_response)

        return None

    def store(
        self, prompt: str, response: dict[str, Any], tools: list[dict[str, Any]] | None = None,
        scope: str = "_global_",
    ) -> None:
        """Store the prompt and response in the cache.

        ``scope`` isolates the entry by caller context (user/session id).
        """
        prompt_hash = self._compute_hash(prompt, tools, scope)
        tools_json = json.dumps(tools, sort_keys=True) if tools else "{}"
        response_json = json.dumps(response)

        embedding_json = None
        encoder = get_encoder()
        if encoder is not None:
            try:
                emb = encoder.encode(prompt, convert_to_numpy=False)
                if not isinstance(emb, list):
                    emb = list(emb)
                embedding_json = json.dumps(emb)
            except Exception as exc:
                logger.debug("[SemanticCache] Failed to generate embedding for storage: %s", exc)

        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """
                    INSERT INTO semantic_cache (prompt_hash, prompt, tools_json, response_json, embedding, scope)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (prompt_hash, prompt, tools_json, response_json, embedding_json, scope),
                )
                conn.commit()
                logger.debug("[SemanticCache] Successfully cached response")
            except Exception as exc:
                logger.warning("[SemanticCache] Failed to write cache entry: %s", exc)
