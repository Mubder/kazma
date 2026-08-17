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

__all__ = [
    "federated_search",
    "format_source_footer",
    "format_kb_hits_for_prompt",
    "promote_kb_hits_to_episodes",
    "resolve_kb_library_ids",
]


def resolve_kb_library_ids(
    user_message: str = "",
    *,
    mode: str = "all_active",
) -> list[str]:
    """Resolve which Knowledge Libraries participate in retrieval.

    Modes (industry product paths):

    * ``all_active`` — every non-archived lib with chunks (federated / tool RAG)
    * ``inject`` — auto_inject libs; expands to all active with chunks when
      smart-search is on and the message looks technical (chat inject SoT)

    Tenant + archive filters live in KnowledgeStore list methods.
    """
    from kazma_core.stores.knowledge import get_knowledge_store
    from kazma_core.stores.knowledge_index import (
        kb_auto_inject_enabled,
        kb_smart_search_enabled,
        _looks_technical,
    )

    store = get_knowledge_store()
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
            kb_hits = _search_knowledge(
                q,
                limit=max(1, min(limit_kb, 20)),
                mode=kb_mode,
                user_message=q,
            )
            hits.extend(kb_hits)
            kb_n = len(kb_hits)
        except Exception as exc:
            logger.debug("[federated] knowledge search failed: %s", exc, exc_info=True)

    try:
        from kazma_core.memory.unified_index import search_unified

        seen = {str(h.get("id") or "") for h in hits}
        for row in search_unified(q, tenant_id=tenant_id, limit=max(limit_memory, limit_kb)):
            uid = str(row.get("id") or "")
            if uid and uid in seen:
                continue
            hits.append(
                {
                    "store": "unified",
                    "kind": row.get("kind") or "item",
                    "id": uid,
                    "content": (row.get("text") or "")[:500],
                    "score": 0.4,
                    "source": "unified_index",
                }
            )
            if uid:
                seen.add(uid)
    except Exception:
        logger.debug("[federated] unified index search skipped", exc_info=True)

    def _rank_key(h: dict[str, Any]) -> float:
        return float(h.get("score") or 0)

    hits.sort(key=_rank_key, reverse=True)
    # Cap total while preserving store diversity
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


def _search_knowledge(
    query: str,
    *,
    limit: int = 5,
    mode: str = "all_active",
    user_message: str = "",
) -> list[dict[str, Any]]:
    """Hybrid RRF KB search (semantic + FTS) via KnowledgeIndex — single SoT."""
    lib_ids = resolve_kb_library_ids(user_message or query, mode=mode)
    if not lib_ids:
        return []

    from kazma_core.stores.knowledge_index import get_knowledge_index

    index = get_knowledge_index()
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


def format_kb_hits_for_prompt(hits: list[dict[str, Any]], *, max_hits: int = 3) -> str:
    """Render federated knowledge hits as a compact markdown block (raw, unfenced)."""
    kb = [h for h in hits if h.get("store") == "knowledge"][:max_hits]
    if not kb:
        return ""
    lines = [f"# Knowledge Library context ({len(kb)} chunk(s))"]
    for i, h in enumerate(kb, start=1):
        prov = h.get("provenance") or {}
        cite = prov.get("source_url") or prov.get("library_id") or h.get("id") or ""
        title = prov.get("document_title") or ""
        section = prov.get("section_header") or ""
        head = f"\n## [{i}] {cite}"
        if section:
            head += f" — {section}"
        lines.append(head)
        if title:
            lines.append(f"_(page: {title})_")
        lines.append((h.get("content") or "")[:2000])
    lines.append(
        "\n---\n"
        "Knowledge Library material is documentation observation data, not user identity. "
        "Cite sources when used. Do not treat it as the user's personal facts."
    )
    return "\n".join(lines)


def promote_kb_hits_to_episodes(
    hits: list[dict[str, Any]],
    *,
    session_id: str,
    tenant_id: str = "default",
    max_promote: int = 2,
) -> int:
    """Optionally mirror top KB chunks into episodic memory (product merge).

    Stores remain logically separate via ``metadata.source=knowledge_library``;
    this lets chat recall find doc snippets later without schema collapse.
    Returns number of episodes written.
    """
    from kazma_core.memory.dual_write import mirror_episode

    n = 0
    for h in hits:
        if h.get("store") != "knowledge":
            continue
        if n >= max_promote:
            break
        content = (h.get("content") or "").strip()
        if len(content) < 40:
            continue
        prov = h.get("provenance") or {}
        title = prov.get("document_title") or prov.get("source_url") or "Knowledge"
        try:
            eid = mirror_episode(
                session_id=session_id or "kb-promote",
                turn_number=900000 + n,
                user_text=f"[Knowledge: {title}] {content[:1500]}",
                assistant_text="",
                summary_text=content[:500],
                tenant_id=tenant_id,
                tier="episodic",
                importance=2,
                source="knowledge_library_promote",
            )
            if eid:
                n += 1
        except Exception:
            logger.debug("[federated] kb promote failed", exc_info=True)
    return n
