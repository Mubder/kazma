"""Knowledge-library and document-ingest tools.

Extracted from the former ``tool_builtins.py`` god module (2,833
lines) — audit O5.

Knowledge audit 2026-10-13: no Knowledge store, settings or embedding work
runs on the event loop (KB-S06: the two tools that never awaited are plain
``def``s, which the registry runs in a thread; the others await
``asyncio.to_thread``); a library is created only once its first page was
fetched or its crawl admitted, and only an http(s) seed is stored (KB-S09);
every crawled text a hit carries reaches the model inside the untrusted-data
fence, with Kazma's own scaffolding outside it (KB-S01); and on an install
with platform users every tool refuses (decision D1, KB-S04).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from kazma_core.agent.tool_refusal import RefusedBeforeEffect, effect_unknown

logger = logging.getLogger(__name__)


def _refusal() -> str | None:
    """The single-operator refusal, as a tool answer (blocking)."""
    from kazma_core.stores.knowledge_lifecycle import kb_refusal

    refusal = kb_refusal()
    return f"Error: {refusal}" if refusal else None


def register_knowledge_tools(registry: Any) -> None:
    """Register the knowledge tools onto *registry*."""

    @registry.register(
        description=(
            "List Knowledge Libraries (documentation corpora) available for "
            "knowledge_search. Shows id, name, chunk_count, seed_url."
        ),
        category="knowledge",
    )
    def knowledge_list_libraries() -> str:
        try:
            refused = _refusal()
            if refused:
                return refused
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
            "seed_url, when given, must be an http(s) URL. "
            "Then call knowledge_ingest_url to add pages. Search with knowledge_search."
        ),
        category="knowledge",
    )
    def knowledge_create_library(
        library_id: str,
        name: str = "",
        description: str = "",
        seed_url: str = "",
        exist_ok: bool = True,
    ) -> str:
        try:
            refused = _refusal()
            if refused:
                raise RefusedBeforeEffect(refused)
            from kazma_core.stores.knowledge import (
                KnowledgeUrlRefused,
                get_knowledge_store,
                validate_web_url,
            )

            store = get_knowledge_store()
            lib_id = (library_id or "").strip()
            if not lib_id:
                raise RefusedBeforeEffect("Error: library_id is required.")
            try:
                validate_web_url(seed_url, allow_empty=True)
            except KnowledgeUrlRefused as exc:
                raise RefusedBeforeEffect(f"Error: seed_url refused — {exc}") from exc
            display = (name or "").strip() or lib_id.replace("_", " ").replace("-", " ")
            existing = store.get_library(lib_id)
            if existing:
                if exist_ok:
                    return (
                        f"Knowledge library '{existing.get('id')}' already exists and is ready for use "
                        f"(name={existing.get('name')!r}, chunks={existing.get('chunk_count', 0)}). "
                        "Use knowledge_ingest_url to add pages."
                    )
                raise RefusedBeforeEffect(f"Error: Library already exists: id={existing.get('id')} "
                    f"name={existing.get('name')!r}.")
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
        except RefusedBeforeEffect:
            raise
        except Exception as exc:
            return f"Error: create library failed — {exc}"

    @registry.register(
        description=(
            "Ingest a single documentation page URL into a Knowledge Library "
            "(fetch → chunk → index). Creates the library once the page was fetched. "
            "For multi-page trees prefer knowledge_ingest_site with a small max_pages. "
            "Then knowledge_search to retrieve. SSRF-safe (blocks private IPs); http(s) only."
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
            refused = await asyncio.to_thread(_refusal)
            if refused:
                raise RefusedBeforeEffect(refused)
            from kazma_core.stores.knowledge import get_knowledge_store, slugify_library_id
            from kazma_core.stores.knowledge_ingest import ingest_url

            lib_id = slugify_library_id((library_id or "").strip()) if (library_id or "").strip() else ""
            page = (url or "").strip()
            if not lib_id or not page:
                raise RefusedBeforeEffect("Error: library_id and url are required.")
            display = (name or "").strip() or lib_id.replace("_", " ")
            result = await ingest_url(
                lib_id, page, document_title=(document_title or "").strip(), name=display
            )
            if result.pages_failed or not result.pages_fetched:
                err = "; ".join(result.errors[:3]) if result.errors else "fetch failed"
                if getattr(result, "refused", False):
                    # Nothing fetched or stored (AUD-039).
                    raise RefusedBeforeEffect(f"Error: ingest refused for {page!r} — {err}")
                return effect_unknown(f"Error: ingest failed for {page!r} — {err}")
            store = await asyncio.to_thread(get_knowledge_store)
            lib = await asyncio.to_thread(store.get_library, lib_id) or {}
            return (
                f"Ingested page into library '{lib_id}': "
                f"fetched={result.pages_fetched} "
                f"chunks_new={result.chunks_new} chunks_unchanged={result.chunks_skipped}. "
                f"Library total chunks={lib.get('chunk_count', '?')}. "
                f"Search with knowledge_search(query, library={lib_id!r})."
            )
        except RefusedBeforeEffect:
            raise
        except Exception as exc:
            return f"Error: knowledge_ingest_url failed — {exc}"

    @registry.register(
        description=(
            "Ingest a small documentation site tree into a Knowledge Library "
            "(sitemap/BFS discover + fetch + chunk + index). Caps max_pages "
            "(default 5, hard max 15) so agent turns stay bounded. "
            "Creates the library once the crawl was admitted. Prefer knowledge_ingest_url for one page."
        ),
        category="knowledge",
    )
    async def knowledge_ingest_site(
        library_id: str,
        seed_url: str,
        max_pages: int = 5,
        name: str = "",
        resume_job_id: str = "",
    ) -> str:
        try:
            refused = await asyncio.to_thread(_refusal)
            if refused:
                raise RefusedBeforeEffect(refused)
            from kazma_core.stores.knowledge import get_knowledge_store, slugify_library_id
            from kazma_core.stores.knowledge_ingest import _prepare_ingest_site, ingest_site
            from kazma_core.web_acquire.crawl_jobs import call

            lib_id = slugify_library_id((library_id or "").strip()) if (library_id or "").strip() else ""
            seed = (seed_url or "").strip()
            if not lib_id or not seed:
                raise RefusedBeforeEffect("Error: library_id and seed_url are required.")
            try:
                cap = max(1, min(int(max_pages or 5), 15))
            except (TypeError, ValueError):
                raise RefusedBeforeEffect("Error: max_pages must be a whole number.") from None
            # Admit the crawl first; the library exists once it was admitted.
            # A refusal here (a private or unresolvable seed, a store at its
            # quota, a crawl of this library already running) fetched and
            # created nothing: the model corrects the call. Returned as an
            # error from the handler below, it was "effects unknown" and the
            # turn failed (audit 2026-10-26 AUD-039).
            from kazma_core.stores.knowledge import KnowledgeStorageFull
            from kazma_core.web_acquire.result import AcquisitionError

            try:
                prepared = await _prepare_ingest_site(
                    lib_id, seed, max_pages=cap, job_id=resume_job_id or None,
                    resume=bool(resume_job_id),
                )
            except (AcquisitionError, KnowledgeStorageFull) as exc:
                raise RefusedBeforeEffect(
                    f"Error: knowledge_ingest_site refused — {exc}"
                ) from exc
            store = await asyncio.to_thread(get_knowledge_store)

            def _ensure_library() -> None:
                if store.get_library(lib_id) is None:
                    store.create_library(lib_id, (name or "").strip() or lib_id.replace("_", " "),
                                         description="created by knowledge_ingest_site", seed_url=seed)

            try:
                await asyncio.to_thread(_ensure_library)
            except Exception:
                await call(prepared[0].finish, "interrupted")
                raise
            final_msg = ""
            last = None
            async for upd in ingest_site(lib_id, seed, max_pages=cap, job_id=prepared[0].job_id,
                                         resume=bool(resume_job_id), _prepared=prepared):
                last = upd
                final_msg = getattr(upd, "message", "") or final_msg
            if last is None or last.phase == 'error':
                return effect_unknown(f"Error: knowledge_ingest_site failed — {final_msg or 'No terminal crawl outcome received.'}")
            lib = await asyncio.to_thread(store.get_library, lib_id) or {}
            failed = getattr(last, "failed", 0)
            return (
                f"Site ingest {'finished with failures' if failed else 'finished'} for library "
                f"'{lib_id}' (max_pages={cap}). "
                f"discovered={last.discovered} fetched={last.fetched} failed={failed} "
                f"chunks_indexed={last.ingested}. "
                f"Library total chunks={lib.get('chunk_count', '?')}. "
                f"Last: {final_msg or 'done'}. "
                f"Crawl job={getattr(last, 'job_id', '')}; inspect with research_readiness(job_id). "
                f"Search with knowledge_search(query, library={lib_id!r})."
            )
        except RefusedBeforeEffect:
            raise
        except Exception as exc:
            return f"Error: knowledge_ingest_site failed — {exc}"

    @registry.register(
        description=(
            "Search an ingested Knowledge Library (documentation corpus) for "
            "technical reference material — API endpoints, parameters, error codes, "
            "configuration, examples. Use this when the user asks about a documented "
            "system (e.g. the WhatsApp Cloud API) and you need authoritative info with "
            "sources. Each hit includes the source URL and section so you can cite it. "
            "Leave `library` empty to search across all active libraries. top_k is 1-20. "
            "If none exist, create with knowledge_create_library + knowledge_ingest_url."
        ),
        category="knowledge",
    )
    async def knowledge_search(query: str, library: str = "", top_k: int = 5) -> str:
        # Knowledge Libraries are a managed RAG corpus, decoupled from
        # chat memory.  See `kazma_core/stores/knowledge_index.py`.
        try:
            refused = await asyncio.to_thread(_refusal)
            if refused:
                return refused
            from kazma_core.stores.knowledge import get_knowledge_store
            from kazma_core.stores.knowledge_index import MAX_TOP_K, get_knowledge_index

            query = (query or "").strip()[:2000]
            if not query:
                return "Error: query is required."
            try:
                limit = max(1, min(MAX_TOP_K, int(top_k or 5)))
            except (TypeError, ValueError):
                limit = 5
            store = await asyncio.to_thread(get_knowledge_store)
            index = await asyncio.to_thread(get_knowledge_index)

            # Pick target library/libraries.
            lib_id = (library or "").strip()
            if lib_id:
                if not await asyncio.to_thread(store.get_library, lib_id):
                    return (
                        f"Error: knowledge library '{lib_id}' not found. "
                        "Create it with knowledge_create_library or "
                        "knowledge_ingest_url (auto-creates)."
                    )
                hits = await index.search(query, lib_id, top_k=limit)
            else:
                libs = await asyncio.to_thread(store.list_libraries)
                if not libs:
                    return (
                        "No knowledge libraries have been ingested yet. "
                        "Create one: knowledge_create_library(id, name), then "
                        "knowledge_ingest_url(id, url). Or use the /knowledge UI / /kb add."
                    )
                # True cross-library RRF: pool raw per-layer results from
                # every library into one fused ranking.
                hits = await index.search_all(
                    query, [l["id"] for l in libs], top_k=limit,
                )

            if not hits:
                scope = f"library '{lib_id}'" if lib_id else "any library"
                return f"No knowledge hits in {scope} for: {query!r}"

            from kazma_core.safety.prompt_fence import fence_untrusted

            # Kazma's scaffolding -- the hit's number, score and where it was
            # stored -- stays outside the fence; what the page itself wrote
            # (title, section, text) goes inside it (KB-S01). A header that
            # tries to close the fence is redacted by the fence.
            # A hit on thin evidence is said to be possibly related (KQ01).
            parts = [f"# Knowledge search — {len(hits)} hit(s)"]
            for i, h in enumerate(hits, start=1):
                weak = " (possibly related)" if h.strength == "weak" else ""
                parts.append(f"\n## [{i}] score={h.score:.4f} — library {h.library_id}, "
                             f"chunk {h.chunk_index}{weak}")
                page = []
                if h.source_url:
                    page.append(f"Source: {h.source_url}")
                if h.document_title:
                    page.append(f"Page: {h.document_title}")
                if h.section_header:
                    page.append(f"Section: {h.section_header}")
                page.append(h.content)
                parts.append(fence_untrusted("\n".join(page).strip(), source="knowledge_search"))
            # Citation directive: the user wants every KB-derived answer
            # to carry a visible footer naming the source library.
            cited_libs = sorted({h.library_id for h in hits})
            if len(cited_libs) == 1:
                lib_footer = f'📚 This data is from Knowledge "{cited_libs[0]}".'
            else:
                lib_footer = (
                    "📚 This data is from Knowledge libraries: "
                    + ", ".join(f'"{l}"' for l in cited_libs) + "."
                )
            parts.append(
                "\n---\n" + lib_footer + "\n"
                "You MUST include this footer verbatim at the end of any answer "
                "that uses the material above."
            )
            return "\n".join(parts)
        except Exception as exc:
            return f"Error: knowledge search failed — {exc}"
