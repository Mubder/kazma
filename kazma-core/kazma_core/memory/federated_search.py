"""Federated search — memory + Knowledge Library (no store merge).

Returns a single ranked list of hits, each labeled with ``store``
(``memory`` | ``knowledge``) so operators and UIs never confuse personal
facts with doc corpus chunks.

This is Horizon A1: best product UX without collapsing KB into V2 schema.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["federated_search", "format_source_footer"]


def federated_search(
    query: str,
    *,
    tenant_id: str = "default",
    session_id: str | None = None,
    limit_memory: int = 5,
    limit_kb: int = 5,
    include_memory: bool = True,
    include_knowledge: bool = True,
) -> dict[str, Any]:
    """Search cognitive memory and/or Knowledge Libraries.

    Returns::

        {
          "ok": True,
          "query": str,
          "hits": [
            {
              "store": "memory"|"knowledge",
              "kind": "belief"|"episode"|"chunk",
              "id": str,
              "content": str,
              "score": float,
              "source": str,
              "sources": list[str]|None,
              "provenance": dict,  # library_id, url, title for KB; meta for memory
            },
            ...
          ],
          "summary": {"memory": int, "knowledge": int, "total": int},
        }
    """
    q = (query or "").strip()
    if not q:
        return {
            "ok": False,
            "error": "query required",
            "hits": [],
            "summary": {"memory": 0, "knowledge": 0, "total": 0},
        }

    hits: list[dict[str, Any]] = []
    mem_n = 0
    kb_n = 0

    if include_memory:
        try:
            from kazma_core.memory.recall import recall

            result = recall(
                q,
                limit=max(1, min(limit_memory, 20)),
                session_id=session_id,
                tenant_id=tenant_id,
                explain=True,
            )
            for h in result.beliefs:
                hits.append(
                    {
                        "store": "memory",
                        "kind": "belief",
                        "id": h.id,
                        "content": (h.content or "")[:500],
                        "score": float(h.score or 0),
                        "source": h.source or "belief",
                        "sources": (h.metadata or {}).get("sources"),
                        "provenance": {
                            "subject": (h.metadata or {}).get("subject"),
                            "predicate": (h.metadata or {}).get("predicate"),
                            "object": (h.metadata or {}).get("object"),
                        },
                    }
                )
                mem_n += 1
            for h in result.episodes:
                hits.append(
                    {
                        "store": "memory",
                        "kind": "episode",
                        "id": h.id,
                        "content": (h.content or "")[:500],
                        "score": float(h.score or 0),
                        "source": h.source or "episode",
                        "sources": (h.metadata or {}).get("sources"),
                        "provenance": {
                            "tier": (h.metadata or {}).get("tier"),
                            "session_boost": (h.metadata or {}).get("session_boost"),
                        },
                    }
                )
                mem_n += 1
        except Exception as exc:
            logger.debug("[federated] memory search failed: %s", exc, exc_info=True)

    if include_knowledge:
        try:
            kb_hits = _search_knowledge(q, limit=max(1, min(limit_kb, 20)))
            hits.extend(kb_hits)
            kb_n = len(kb_hits)
        except Exception as exc:
            logger.debug("[federated] knowledge search failed: %s", exc, exc_info=True)

    # Sort: knowledge BM25 is often negative — normalize for display ranking
    def _rank_key(h: dict[str, Any]) -> float:
        s = float(h.get("score") or 0)
        if h.get("store") == "knowledge" and s < 0:
            return -s  # more negative BM25 → higher rank
        return s

    hits.sort(key=_rank_key, reverse=True)
    # Cap total while preserving store diversity (don't let one store wipe the other)
    cap = max(limit_memory, 0) + max(limit_kb, 0)
    if len(hits) > cap > 0:
        hits = hits[:cap]

    return {
        "ok": True,
        "query": q,
        "hits": hits,
        "summary": {
            "memory": mem_n,
            "knowledge": kb_n,
            "total": len(hits),
        },
    }


def _search_knowledge(query: str, *, limit: int = 5) -> list[dict[str, Any]]:
    """Lexical KB search across active libraries (sync, no store merge)."""
    from kazma_core.stores.knowledge import get_knowledge_store

    store = get_knowledge_store()
    libraries = store.list_libraries(include_archived=False) or []
    if not libraries:
        return []

    # Pool FTS hits across libraries, then hydrate once
    pooled: list[tuple[str, float, str]] = []  # chunk_id, score, library_id
    per_lib = max(2, (limit * 2) // max(1, len(libraries)))
    for lib in libraries:
        lib_id = str(lib.get("id") or "")
        if not lib_id:
            continue
        try:
            rows = store.fts_search(query, lib_id, limit=per_lib)
        except Exception:
            rows = []
        for chunk_id, score in rows:
            pooled.append((chunk_id, float(score), lib_id))

    if not pooled:
        return []

    # Prefer stronger BM25 (more negative) then take top
    pooled.sort(key=lambda x: x[1])  # ascending: more negative first
    top = pooled[: limit * 2]
    chunk_ids = [c for c, _, _ in top]
    full = store.get_chunks_by_ids(chunk_ids)

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for chunk_id, score, lib_id in top:
        if chunk_id in seen:
            continue
        seen.add(chunk_id)
        row = full.get(chunk_id) or {}
        content = (row.get("content") or "")[:500]
        if not content:
            continue
        # Display score: invert BM25 for "higher is better" UI
        disp = -score if score < 0 else score
        out.append(
            {
                "store": "knowledge",
                "kind": "chunk",
                "id": chunk_id,
                "content": content,
                "score": disp,
                "source": "kb_fts",
                "sources": ["kb_fts"],
                "provenance": {
                    "library_id": row.get("library_id") or lib_id,
                    "source_url": row.get("source_url"),
                    "document_title": row.get("document_title"),
                    "section_header": row.get("section_header"),
                    "chunk_index": row.get("chunk_index"),
                },
            }
        )
        if len(out) >= limit:
            break
    return out


def format_source_footer(
    *,
    beliefs: int = 0,
    episodes: int = 0,
    knowledge: int = 0,
    procedural: int = 0,
) -> str:
    """One-line operator/chat footer describing what was injected."""
    parts: list[str] = []
    if beliefs:
        parts.append(f"{beliefs} belief{'s' if beliefs != 1 else ''}")
    if episodes:
        parts.append(f"{episodes} episode{'s' if episodes != 1 else ''}")
    if knowledge:
        parts.append(f"{knowledge} KB chunk{'s' if knowledge != 1 else ''}")
    if procedural:
        parts.append(f"{procedural} skill{'s' if procedural != 1 else ''}")
    if not parts:
        return ""
    return "Sources used: " + ", ".join(parts) + " (memory stack — untrusted observation)."
