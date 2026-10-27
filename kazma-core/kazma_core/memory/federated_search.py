"""Federated search — memory + Knowledge Library (no store merge).

Returns a single ranked list of hits, each labeled with ``store``
(``memory`` | ``knowledge``) so operators and UIs never confuse personal
facts with doc corpus chunks.

This is Horizon A1: best product UX without collapsing KB into V2 schema.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "federated_search",
    "format_source_footer",
    "format_kb_hits_for_prompt",
    "resolve_kb_library_ids",
]


def resolve_kb_library_ids(
    user_message: str = "",
    *,
    mode: str = "all_active",
    store: Any = None,
) -> list[str]:
    """Resolve which Knowledge Libraries participate in retrieval.

    Modes (industry product paths):

    * ``all_active`` — every non-archived lib with chunks (federated / tool RAG)
    * ``inject`` — auto_inject libs; expands to all active with chunks when
      smart-search is on and the message looks technical (chat inject SoT)

    Tenant + archive filters live in KnowledgeStore list methods. *store*
    is the shared store unless given.
    """
    from kazma_core.stores.knowledge import get_knowledge_store
    from kazma_core.stores.knowledge_index import (
        kb_auto_inject_enabled,
        kb_smart_search_enabled,
        _looks_technical,
    )

    store = store if store is not None else get_knowledge_store()
    mode_l = (mode or "all_active").strip().lower()

    if mode_l == "inject":
        if not kb_auto_inject_enabled():
            return []
        libs = list(store.list_auto_inject_libraries() or [])
        msg = (user_message or "").strip()
        if kb_smart_search_enabled() and _looks_technical(msg):
            by_id = {str(l.get("id")): l for l in libs if l.get("id")}
            for lib in store.list_libraries(include_archived=False) or []:
                if int(lib.get("chunk_count") or 0) <= 0:
                    continue
                lid = str(lib.get("id") or "")
                if lid:
                    by_id.setdefault(lid, lib)
            libs = list(by_id.values())
        return [str(l["id"]) for l in libs if l.get("id")]

    # all_active (default federated / tool)
    out: list[str] = []
    for lib in store.list_libraries(include_archived=False) or []:
        if int(lib.get("chunk_count") or 0) <= 0:
            continue
        lid = str(lib.get("id") or "")
        if lid:
            out.append(lid)
    return out


def federated_search(
    query: str,
    *,
    tenant_id: str = "default",
    session_id: str | None = None,
    limit_memory: int = 5,
    limit_kb: int = 5,
    include_memory: bool = True,
    include_knowledge: bool = True,
    kb_mode: str = "all_active",
) -> dict[str, Any]:
    """Search cognitive memory and/or Knowledge Libraries.

    KB side uses the same **RRF** path as auto-inject / ``knowledge_search``
    (semantic Chroma + FTS5), not FTS-only — industry hybrid retrieval.

    The two stores are never ranked against each other: ``hits`` holds the
    memory hits (best first by memory's evidence score, at most
    ``limit_memory``) and then the Knowledge hits (best first by RRF, at
    most ``limit_kb``). ``include_memory`` / ``include_knowledge`` decide
    whether a store is searched at all.

    Returns::

        {
          "ok": True,
          "query": str,
          "hits": [...],
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
    # Why a store's answer is incomplete, or None. "No hits in either
    # store." over a search that failed is a false statement: the memory
    # side said nothing (an exception at DEBUG, `result.degraded` dropped)
    # and the response was still ok (audit 2026-10-26 AUD-057).
    memory_degraded: str | None = None
    knowledge_error: str | None = None

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
            memory_degraded = result.degraded
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
            for h in result.summaries:
                hits.append(
                    {
                        "store": "memory",
                        "kind": "weekly_summary",
                        "id": h.id,
                        "content": ((h.metadata or {}).get("display") or h.content or "")[:500],
                        "score": float(h.score or 0),
                        "source": h.source or "summary",
                        "sources": (h.metadata or {}).get("sources"),
                        "provenance": {"title": h.content},
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
            logger.warning("[federated] memory search failed: %s", exc, exc_info=True)
            from kazma_core.memory.recall import RECALL_FAILED

            memory_degraded = f"{RECALL_FAILED}federated_error"

    # Each store keeps its own ranking and its own budget (Knowledge audit
    # KB-S02): memory scores are evidence on 0..1, Knowledge scores are RRF
    # sums near 0.02, and the old merged sort-and-cut compared them -- a
    # fixed 0.4 score on the retired unified rows pushed every Knowledge hit
    # out of an inject while the turn still reported them as used.
    hits.sort(key=lambda h: float(h.get("score") or 0), reverse=True)  # memory's own scale
    hits = hits[:max(0, limit_memory)] if include_memory else []
    mem_n = len(hits)

    if include_knowledge and limit_kb > 0:
        try:
            kb_hits = _search_knowledge(
                q,
                limit=max(1, min(limit_kb, 20)),
                mode=kb_mode,
                user_message=q,
            )
            kb_hits = kb_hits[:limit_kb]
            hits.extend(kb_hits)
            kb_n = len(kb_hits)
        except Exception as exc:
            logger.warning("[federated] knowledge search failed: %s", exc)
            knowledge_error = "knowledge_search_failed"

    return {
        "ok": True,
        "query": q,
        "hits": hits,
        "summary": {
            "memory": mem_n,
            "knowledge": kb_n,
            "total": len(hits),
        },
        # The page shows each as a banner instead of "no hits" (AUD-057).
        "degraded": memory_degraded,
        "knowledge_error": knowledge_error,
    }


def _search_knowledge(
    query: str,
    *,
    limit: int = 5,
    mode: str = "all_active",
    user_message: str = "",
    store: Any = None,
) -> list[dict[str, Any]]:
    """Hybrid RRF KB search (semantic + FTS) via KnowledgeIndex — single SoT.
    *store* is the shared store unless given (the retrieval benchmark's own)."""
    lib_ids = resolve_kb_library_ids(user_message or query, mode=mode, store=store)
    if not lib_ids:
        return []

    from kazma_core.stores.knowledge_index import get_knowledge_index, knowledge_index_for

    index = knowledge_index_for(store) if store is not None else get_knowledge_index()
    khits = index.search_all_sync(query, lib_ids, top_k=max(1, min(limit, 20)))
    out: list[dict[str, Any]] = []
    for h in khits:
        content = (h.content or "")[:2000]
        if not content:
            continue
        out.append(
            {
                "store": "knowledge",
                "kind": "chunk",
                "id": h.chunk_id,
                "content": content,
                "score": float(h.score or 0),
                "strength": h.strength,
                "source": "kb_rrf",
                "sources": ["kb_semantic", "kb_fts"],
                "provenance": {
                    "library_id": h.library_id,
                    "source_url": h.source_url,
                    "document_title": h.document_title,
                    "section_header": h.section_header,
                    "chunk_index": h.chunk_index,
                    "has_code": h.has_code,
                },
            }
        )
    return out


def format_source_footer(
    *,
    beliefs: int = 0,
    episodes: int = 0,
    knowledge: int = 0,
    procedural: int = 0,
    summaries: int = 0,
) -> str:
    """One-line operator/chat footer describing what was injected."""
    parts: list[str] = []
    if beliefs:
        parts.append(f"{beliefs} belief{'s' if beliefs != 1 else ''}")
    if summaries:
        parts.append(f"{summaries} weekly summar{'ies' if summaries != 1 else 'y'}")
    if episodes:
        parts.append(f"{episodes} episode{'s' if episodes != 1 else ''}")
    if knowledge:
        parts.append(f"{knowledge} KB chunk{'s' if knowledge != 1 else ''}")
    if procedural:
        parts.append(f"{procedural} skill{'s' if procedural != 1 else ''}")
    if not parts:
        return ""
    return "Sources used: " + ", ".join(parts) + " (memory stack — untrusted observation)."


def format_kb_hits_for_prompt(hits: list[dict[str, Any]], *, max_hits: int = 3) -> str:
    """Render knowledge hits as a compact markdown block (raw, unfenced):
    what the pages wrote -- each hit's source, section, title and text --
    and nothing of Kazma's. The caller fences it and adds its own lines
    after the fence (Knowledge audit KB-S01)."""
    kb = [h for h in hits if h.get("store") == "knowledge"][:max_hits]
    if not kb:
        return ""
    lines: list[str] = []
    for i, h in enumerate(kb, start=1):
        prov = h.get("provenance") or {}
        cite = prov.get("source_url") or prov.get("library_id") or h.get("id") or ""
        title = prov.get("document_title") or ""
        section = prov.get("section_header") or ""
        head = f"## [{i}] {cite}"
        if section:
            head += f" — {section}"
        lines.append(head if i == 1 else f"\n{head}")
        if title:
            lines.append(f"_(page: {title})_")
        lines.append((h.get("content") or "")[:2000])
    return "\n".join(lines)
