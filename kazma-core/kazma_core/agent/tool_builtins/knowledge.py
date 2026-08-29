"""Knowledge-library and document-ingest tools.

Extracted from the former ``tool_builtins.py`` god module (2,833
lines) — audit O5. Tool bodies are unchanged; registration order
within this group is preserved.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass

def _qnorm(q: str) -> str:
    """Normalize a memory q-filter: underscores/hyphens -> single spaces,
    lowercased. Paired with REPLACE(...) in SQL so 'memory system' matches
    user_memory_system (2026-08-27 report — the literal LIKE filter missed
    it while FTS memory_search matched fine)."""
    import re as _re

    return _re.sub(r"[_\-\s]+", " ", str(q or "").strip().lower()).strip()




def register_knowledge_tools(registry: Any) -> None:
    """Register the knowledge tools onto *registry*."""
    from kazma_core.agent.tool_registry import _pending_dispatch_tasks  # noqa: F401

    @registry.register(
        description=(
            "List Knowledge Libraries (documentation corpora) available for "
            "knowledge_search. Shows id, name, chunk_count, seed_url."
        ),
        category="knowledge",
    )
    async def knowledge_list_libraries() -> str:
        try:
            from kazma_core.stores.knowledge import get_knowledge_store

            libs = get_knowledge_store().list_libraries()
            if not libs:
                return (
                    "No knowledge libraries yet. Create one with "
                    "knowledge_create_library, then knowledge_ingest_url."
                )
            lines = [f"# Knowledge libraries ({len(libs)})"]
            for lib in libs:
                lines.append(
                    f"- **{lib.get('id')}** — {lib.get('name') or '(unnamed)'} "
                    f"({lib.get('chunk_count', 0)} chunks)"
                    + (
                        f" seed={lib.get('seed_url')}"
                        if lib.get("seed_url")
                        else ""
                    )
                )
            return "\n".join(lines)
        except Exception as exc:
            return f"Error: list libraries failed — {exc}"
    @registry.register(
        description=(
            "Create a Knowledge Library (empty corpus) for documentation RAG. "
            "library_id should be a short slug (e.g. smoke_realwork_kb). "
            "Then call knowledge_ingest_url to add pages. Search with knowledge_search."
        ),
        category="knowledge",
    )
    async def knowledge_create_library(
        library_id: str,
        name: str = "",
        description: str = "",
        seed_url: str = "",
        exist_ok: bool = True,
    ) -> str:
        try:
            from kazma_core.stores.knowledge import get_knowledge_store

            store = get_knowledge_store()
            lib_id = (library_id or "").strip()
            if not lib_id:
                return "Error: library_id is required."
            display = (name or "").strip() or lib_id.replace("_", " ").replace("-", " ")
            existing = store.get_library(lib_id)
            if existing:
                if exist_ok:
                    return (
                        f"Knowledge library '{existing.get('id')}' already exists and is ready for use "
                        f"(name={existing.get('name')!r}, chunks={existing.get('chunk_count', 0)}). "
                        "Use knowledge_ingest_url to add pages."
                    )
                return (
                    f"Error: Library already exists: id={existing.get('id')} "
                    f"name={existing.get('name')!r}."
                )
            created = store.create_library(
                lib_id,
                display,
                description=description or "",
                seed_url=seed_url or "",
            )
            return (
                f"Created knowledge library id={created.get('id')} "
                f"name={created.get('name')!r}. "
                "Next: knowledge_ingest_url(library_id, url)."
            )
        except Exception as exc:
            return f"Error: create library failed — {exc}"
    @registry.register(
        description=(
            "Ingest a single documentation page URL into a Knowledge Library "
            "(fetch → chunk → index). Creates the library if missing. "
            "For multi-page trees prefer knowledge_ingest_site with a small max_pages. "
            "Then knowledge_search to retrieve. SSRF-safe (blocks private IPs)."
        ),
        category="knowledge",
    )
    async def knowledge_ingest_url(
        library_id: str,
        url: str,
        document_title: str = "",
        name: str = "",
    ) -> str:
        try:
            from kazma_core.stores.knowledge import get_knowledge_store
            from kazma_core.stores.knowledge_ingest import ingest_url

            store = get_knowledge_store()
            lib_id = (library_id or "").strip()
            page = (url or "").strip()
            if not lib_id or not page:
                return "Error: library_id and url are required."
            if not store.get_library(lib_id):
                display = (name or "").strip() or lib_id.replace("_", " ")
                store.create_library(
                    lib_id, display, description="auto-created by knowledge_ingest_url", seed_url=page
                )
            result = await ingest_url(
                lib_id, page, document_title=(document_title or "").strip()
            )
            lib = store.get_library(lib_id) or {}
            if result.pages_failed and not result.chunks_new:
                err = "; ".join(result.errors[:3]) if result.errors else "fetch failed"
                return f"Error: ingest failed for {page!r} — {err}"
            return (
                f"Ingested page into library '{lib_id}': "
                f"fetched={result.pages_fetched} failed={result.pages_failed} "
                f"chunks_new={result.chunks_new} chunks_skipped={result.chunks_skipped}. "
                f"Library total chunks={lib.get('chunk_count', '?')}. "
                f"Search with knowledge_search(query, library={lib_id!r})."
            )
        except Exception as exc:
            return f"Error: knowledge_ingest_url failed — {exc}"
    @registry.register(
        description=(
            "Ingest a small documentation site tree into a Knowledge Library "
            "(sitemap/BFS discover + fetch + chunk + index). Caps max_pages "
            "(default 5, hard max 15) so agent turns stay bounded. "
            "Creates the library if missing. Prefer knowledge_ingest_url for one page."
        ),
        category="knowledge",
    )
    async def knowledge_ingest_site(
        library_id: str,
        seed_url: str,
        max_pages: int = 5,
        name: str = "",
    ) -> str:
        try:
            from kazma_core.stores.knowledge import get_knowledge_store
            from kazma_core.stores.knowledge_ingest import ingest_site

            store = get_knowledge_store()
            lib_id = (library_id or "").strip()
            seed = (seed_url or "").strip()
            if not lib_id or not seed:
                return "Error: library_id and seed_url are required."
            cap = max(1, min(int(max_pages or 5), 15))
            if not store.get_library(lib_id):
                display = (name or "").strip() or lib_id.replace("_", " ")
                store.create_library(
                    lib_id,
                    display,
                    description="auto-created by knowledge_ingest_site",
                    seed_url=seed,
                )
            final_msg = ""
            last = None
            async for upd in ingest_site(lib_id, seed, max_pages=cap):
                last = upd
                final_msg = getattr(upd, "message", "") or final_msg
            lib = store.get_library(lib_id) or {}
            discovered = getattr(last, "discovered", 0) if last else 0
            fetched = getattr(last, "fetched", 0) if last else 0
            failed = getattr(last, "failed", 0) if last else 0
            chunks_new = getattr(last, "ingested", 0) if last else 0
            return (
                f"Site ingest finished for library '{lib_id}' (max_pages={cap}). "
                f"discovered={discovered} fetched={fetched} failed={failed} "
                f"chunks_indexed≈{chunks_new}. "
                f"Library total chunks={lib.get('chunk_count', '?')}. "
                f"Last: {final_msg or 'done'}. "
                f"Search with knowledge_search(query, library={lib_id!r})."
            )
        except Exception as exc:
            return f"Error: knowledge_ingest_site failed — {exc}"
    @registry.register(
        description=(
            "Search an ingested Knowledge Library (documentation corpus) for "
            "technical reference material — API endpoints, parameters, error codes, "
            "configuration, examples. Use this when the user asks about a documented "
            "system (e.g. the WhatsApp Cloud API) and you need authoritative info with "
            "sources. Each hit includes the source URL and section so you can cite it. "
            "Leave `library` empty to search across all libraries. "
            "If none exist, create with knowledge_create_library + knowledge_ingest_url."
        ),
        category="knowledge",
    )
    async def knowledge_search(query: str, library: str = "", top_k: int = 5) -> str:
        # Knowledge Libraries are a managed RAG corpus, decoupled from
        # chat memory.  See `kazma_core/stores/knowledge_index.py`.
        try:
            from kazma_core.stores.knowledge import get_knowledge_store
            from kazma_core.stores.knowledge_index import get_knowledge_index

            store = get_knowledge_store()
            index = get_knowledge_index()

            # Pick target library/libraries.
            lib_id = (library or "").strip()
            if lib_id:
                if not store.get_library(lib_id):
                    return (
                        f"Error: knowledge library '{lib_id}' not found. "
                        "Create it with knowledge_create_library or "
                        "knowledge_ingest_url (auto-creates)."
                    )
                hits = await index.search(query, lib_id, top_k=top_k)
            else:
                libs = store.list_libraries()
                if not libs:
                    return (
                        "No knowledge libraries have been ingested yet. "
                        "Create one: knowledge_create_library(id, name), then "
                        "knowledge_ingest_url(id, url). Or use the /knowledge UI / /kb add."
                    )
                # True cross-library RRF: pool raw per-layer results from
                # every library into one fused ranking (not flatten+sort,
                # which would double-count RRF contributions).
                hits = await index.search_all(
                    query, [l["id"] for l in libs], top_k=top_k,
                )

            if not hits:
                scope = f"library '{lib_id}'" if lib_id else "any library"
                return f"No knowledge hits in {scope} for: {query!r}"

            lines = [f"# Knowledge search — {len(hits)} hit(s)"]
            for i, h in enumerate(hits, start=1):
                cite = f"{h.source_url}"
                if h.section_header:
                    cite += f" — {h.section_header}"
                lines.append(f"\n## [{i}] score={h.score:.4f} — {cite}")
                if h.document_title:
                    lines.append(f"*(page: {h.document_title})*")
                lines.append(h.content)
            # Citation directive: the user wants every KB-derived answer
            # to carry a visible footer naming the source library, so they
            # can tell where the information came from. Per-item libraries
            # vary when searching across libraries; collect the unique set.
            cited_libs = sorted({h.library_id for h in hits})
            if len(cited_libs) == 1:
                lib_footer = f'📚 This data is from Knowledge "{cited_libs[0]}".'
            else:
                lib_footer = (
                    "📚 This data is from Knowledge libraries: "
                    + ", ".join(f'"{l}"' for l in cited_libs) + "."
                )
            lines.append(
                "\n---\n"
                + lib_footer + "\n"
                "You MUST include this footer verbatim at the end of any answer "
                "that uses the material above."
            )
            return "\n".join(lines)
        except Exception as exc:
            return f"Error: knowledge search failed — {exc}"
