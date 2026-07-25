"""Knowledge Base API — Web transport for Knowledge Libraries.

Thin FastAPI layer that exposes the Knowledge Library subsystem
(``kazma_core.stores.knowledge*``) to the ``/knowledge`` page.  It adds
**zero** business logic: library CRUD delegates to
``KnowledgeStore``; ingestion runs ``ingest_url`` / ``ingest_site`` as
background ``asyncio`` tasks; search delegates to ``KnowledgeIndex``.

Endpoints (all under ``/api/kb``):
  GET    /libraries                       — list libraries
  POST   /libraries      {id,name,...}    — create a library
  GET    /libraries/{id}                  — one library
  PATCH  /libraries/{id} {auto_inject?}   — update a library
  DELETE /libraries/{id}                  — delete library + chunks
  GET    /libraries/{id}/chunks           — paginated chunk browser
  POST   /ingest         {library_id,url,mode,max_pages?}
                                           — page (sync) or site (background)
  GET    /jobs/{job_id}                   — live ingestion progress
  POST   /search         {library_id,query,top_k?}
                                           — in-page test search
  POST   /libraries/{id}/refresh          — re-ingest from seed_url

Security:
  - Ingestion targets are validated by the shared SSRF guard inside
    ``_fetch_full_text`` (no parallel un-checked path).
  - Failures return HTTP 200 with ``{"ok": false, "error": ...}`` so the
    UI renders messages uniformly (matches ``ide_api.py``).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Query

logger = logging.getLogger(__name__)

__all__ = ["create_kb_router"]


# In-process job registry for low-latency polling.  Durable mirror lives
# in ConfigStore via ``kazma_core.stores.kb_jobs`` so restarts don't
# orphan the UI on "Unknown job_id".
_kb_api_jobs: dict[str, dict[str, Any]] = {}
_kb_jobs_bootstrapped = False


def _remember_job(job_id: str, data: dict[str, Any]) -> None:
    """Update in-memory + durable job snapshot."""
    _kb_api_jobs[job_id] = data
    try:
        from kazma_core.stores.kb_jobs import upsert_job

        upsert_job(job_id, **data)
    except Exception as exc:
        logger.debug("[kb_api] durable job write failed: %s", exc)


def _bootstrap_jobs() -> None:
    """Load durable jobs + mark mid-crawl jobs interrupted after restart."""
    global _kb_jobs_bootstrapped
    if _kb_jobs_bootstrapped:
        return
    _kb_jobs_bootstrapped = True
    try:
        from kazma_core.stores.kb_jobs import list_jobs, mark_stale_jobs_interrupted

        mark_stale_jobs_interrupted()
        for row in list_jobs():
            jid = row.pop("job_id", None)
            if jid:
                _kb_api_jobs[jid] = row
    except Exception as exc:
        logger.debug("[kb_api] job bootstrap failed: %s", exc)


def create_kb_router() -> APIRouter:
    """Create and return the Knowledge Base API router."""

    _bootstrap_jobs()
    router = APIRouter(prefix="/api/kb", tags=["knowledge"])

    def _store():
        from kazma_core.stores.knowledge import get_knowledge_store

        return get_knowledge_store()

    def _index():
        from kazma_core.stores.knowledge_index import get_knowledge_index

        return get_knowledge_index()

    # ── Libraries ───────────────────────────────────────────────────────

    @router.get("/libraries")
    async def list_libraries() -> dict[str, Any]:
        try:
            libs = _store().list_libraries()
            return {"ok": True, "libraries": libs}
        except Exception as exc:
            logger.exception("[kb_api] list_libraries failed")
            return {"ok": False, "error": str(exc), "libraries": []}

    @router.post("/libraries")
    async def create_library(
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        from kazma_core.stores.knowledge import slugify_library_id

        raw_id = (payload.get("id") or "").strip()
        lib_id = slugify_library_id(raw_id)
        name = (payload.get("name") or raw_id).strip() or lib_id
        if not raw_id:
            return {"ok": False, "error": "Missing 'id'"}
        try:
            existing = _store().get_library(lib_id)
            if existing:
                return {"ok": False, "error": f"Library '{lib_id}' already exists"}
            lib = _store().create_library(
                lib_id,
                name=name,
                description=(payload.get("description") or "").strip(),
                seed_url=(payload.get("seed_url") or "").strip(),
            )
            return {"ok": True, "library": lib}
        except Exception as exc:
            logger.exception("[kb_api] create_library failed")
            return {"ok": False, "error": str(exc)}

    @router.get("/libraries/{library_id}")
    async def get_library(library_id: str) -> dict[str, Any]:
        lib = _store().get_library(library_id)
        if not lib:
            return {"ok": False, "error": "Not found"}
        return {"ok": True, "library": lib}

    @router.get("/libraries/archived/list")
    async def list_archived() -> dict[str, Any]:
        """List archived libraries for the Archived tab."""
        try:
            libs = _store().list_archived_libraries()
            return {"ok": True, "libraries": libs}
        except Exception as exc:
            logger.exception("[kb_api] list_archived failed")
            return {"ok": False, "error": str(exc), "libraries": []}

    @router.post("/libraries/{library_id}/archive")
    async def archive_library(library_id: str) -> dict[str, Any]:
        try:
            ok = _store().archive_library(library_id, archived=True)
            if not ok:
                return {"ok": False, "error": "Not found"}
            return {"ok": True}
        except Exception as exc:
            logger.exception("[kb_api] archive_library failed")
            return {"ok": False, "error": str(exc)}

    @router.post("/libraries/{library_id}/unarchive")
    async def unarchive_library(library_id: str) -> dict[str, Any]:
        try:
            ok = _store().archive_library(library_id, archived=False)
            if not ok:
                return {"ok": False, "error": "Not found"}
            return {"ok": True}
        except Exception as exc:
            logger.exception("[kb_api] unarchive_library failed")
            return {"ok": False, "error": str(exc)}

    @router.patch("/libraries/{library_id}")
    async def update_library(
        library_id: str,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        try:
            auto = payload.get("auto_inject")
            auto_bool = None
            if isinstance(auto, bool):
                auto_bool = auto
            elif isinstance(auto, str) and auto.lower() in ("true", "false"):
                auto_bool = auto.lower() == "true"
            lib = _store().update_library(
                library_id,
                name=payload.get("name"),
                description=payload.get("description"),
                seed_url=payload.get("seed_url"),
                auto_inject=auto_bool,
            )
            if not lib:
                return {"ok": False, "error": "Not found"}
            return {"ok": True, "library": lib}
        except Exception as exc:
            logger.exception("[kb_api] update_library failed")
            return {"ok": False, "error": str(exc)}

    @router.delete("/libraries/{library_id}")
    async def delete_library(library_id: str) -> dict[str, Any]:
        try:
            # delete_library on the index drops ChromaDB + SQLite rows.
            ok = _index().delete_library(library_id)
            if not ok:
                return {"ok": False, "error": "Not found"}
            return {"ok": True}
        except Exception as exc:
            logger.exception("[kb_api] delete_library failed")
            return {"ok": False, "error": str(exc)}

    # ── Chunk browser ───────────────────────────────────────────────────

    @router.get("/libraries/{library_id}/chunks")
    async def list_chunks(
        library_id: str,
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        try:
            chunks = _store().list_chunks(library_id, limit=limit, offset=offset)
            total = _store().count_chunks(library_id)
            # Strip the full content for the list view; UI fetches one chunk
            # in full via the existing knowledge_search or a dedicated call.
            slim = [
                {
                    "id": c["id"],
                    "source_url": c["source_url"],
                    "document_title": c["document_title"],
                    "section_header": c["section_header"],
                    "chunk_index": c["chunk_index"],
                    "char_count": c["char_count"],
                    "has_code": c["has_code"],
                    "preview": c["content"][:200],
                }
                for c in chunks
            ]
            return {"ok": True, "chunks": slim, "total": total}
        except Exception as exc:
            logger.exception("[kb_api] list_chunks failed")
            return {"ok": False, "error": str(exc), "chunks": [], "total": 0}

    # ── Ingestion (page sync / site background) ─────────────────────────

    @router.post("/ingest")
    async def ingest(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        lib_id = (payload.get("library_id") or "").strip()
        url = (payload.get("url") or "").strip()
        mode = (payload.get("mode") or "page").strip().lower()
        max_pages = payload.get("max_pages")
        if not lib_id or not url:
            return {"ok": False, "error": "Missing 'library_id' or 'url'"}
        if mode not in ("page", "site"):
            return {"ok": False, "error": "mode must be 'page' or 'site'"}

        # Create-or-use library.  Slugify ONCE at the boundary so the
        # existence check and the insert agree on the ID (otherwise a raw
        # user input like "ShipX WhatsApp API" passes get_library() as
        # None but create_library() slugifies it to an existing slug and
        # raises UNIQUE constraint).
        from kazma_core.stores.knowledge import slugify_library_id

        lib_id = slugify_library_id(lib_id)
        try:
            if not _store().get_library(lib_id):
                _store().create_library(lib_id, name=lib_id, seed_url=url)
            else:
                _store().update_library(lib_id, seed_url=url)
        except Exception as exc:
            return {"ok": False, "error": f"library setup failed: {exc}"}

        if mode == "page":
            # Synchronous single-page ingest.
            try:
                from kazma_core.stores.knowledge_ingest import ingest_url

                result = await ingest_url(lib_id, url)
                return {
                    "ok": True,
                    "mode": "page",
                    "pages_fetched": result.pages_fetched,
                    "chunks_new": result.chunks_new,
                    "chunks_skipped": result.chunks_skipped,
                    "errors": result.errors[:5],
                }
            except Exception as exc:
                logger.exception("[kb_api] page ingest failed")
                return {"ok": False, "error": str(exc)}

        # mode == "site" → background job.
        job_id = f"{lib_id}:{datetime.now(UTC).strftime('%H%M%S%f')}"
        _remember_job(
            job_id,
            {
                "phase": "starting",
                "library_id": lib_id,
                "url": url,
                "started_at": datetime.now(UTC).isoformat(),
            },
        )

        async def _run() -> None:
            from kazma_core.stores.knowledge_ingest import ingest_site

            try:
                async for update in ingest_site(lib_id, url, max_pages=max_pages):
                    snap = dict(_kb_api_jobs.get(job_id) or {})
                    snap.update(
                        {
                            "phase": update.phase,
                            "discovered": update.discovered,
                            "fetched": update.fetched,
                            "ingested": update.ingested,
                            "skipped": update.skipped,
                            "failed": update.failed,
                            "current_url": update.current_url,
                            "message": update.message,
                            "errors": list(update.errors or []),
                            "library_id": lib_id,
                            "url": url,
                        }
                    )
                    _remember_job(job_id, snap)
                snap = dict(_kb_api_jobs.get(job_id) or {})
                snap["finished_at"] = datetime.now(UTC).isoformat()
                if snap.get("phase") not in ("error", "interrupted"):
                    snap.setdefault("phase", "done")
                _remember_job(job_id, snap)
            except Exception as exc:
                logger.exception("[kb_api] site ingest failed")
                snap = dict(_kb_api_jobs.get(job_id) or {})
                snap.update(
                    {
                        "phase": "error",
                        "message": str(exc),
                        "finished_at": datetime.now(UTC).isoformat(),
                    }
                )
                _remember_job(job_id, snap)

        asyncio.create_task(_run())
        return {"ok": True, "mode": "site", "job_id": job_id}

    @router.get("/jobs/{job_id}")
    async def job_status(job_id: str) -> dict[str, Any]:
        job = _kb_api_jobs.get(job_id)
        if not job:
            try:
                from kazma_core.stores.kb_jobs import get_job

                job = get_job(job_id)
            except Exception:
                job = None
        if not job:
            return {
                "ok": False,
                "error": (
                    "Unknown job_id — it may have expired, or the server "
                    "restarted before durable status was written. Re-run crawl."
                ),
            }
        return {"ok": True, "job": job}

    # ── Search (UI "Test search") ───────────────────────────────────────

    @router.post("/search")
    async def search(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        lib_id = (payload.get("library_id") or "").strip()
        query = (payload.get("query") or "").strip()
        top_k = int(payload.get("top_k") or 5)
        if not lib_id or not query:
            return {"ok": False, "error": "Missing 'library_id' or 'query'"}
        try:
            hits = await _index().search(query, lib_id, top_k=top_k)
            return {
                "ok": True,
                "hits": [
                    {
                        "chunk_id": h.chunk_id,
                        "score": h.score,
                        "source_url": h.source_url,
                        "document_title": h.document_title,
                        "section_header": h.section_header,
                        "content": h.content,
                        "has_code": h.has_code,
                    }
                    for h in hits
                ],
            }
        except Exception as exc:
            logger.exception("[kb_api] search failed")
            return {"ok": False, "error": str(exc), "hits": []}

    # ── Refresh (re-ingest from seed_url) ───────────────────────────────

    @router.post("/libraries/{library_id}/refresh")
    async def refresh(library_id: str) -> dict[str, Any]:
        lib = _store().get_library(library_id)
        if not lib:
            return {"ok": False, "error": "Not found"}
        seed = lib.get("seed_url") or ""
        if not seed:
            return {"ok": False, "error": "Library has no seed_url to refresh from"}
        # Re-ingest as a background site crawl.  Per-chunk content_hash dedup
        # means only changed pages are actually re-indexed.
        job_id = f"{library_id}:refresh:{datetime.now(UTC).strftime('%H%M%S%f')}"
        _kb_api_jobs[job_id] = {
            "phase": "starting",
            "library_id": library_id,
            "url": seed,
            "started_at": datetime.now(UTC).isoformat(),
        }

        async def _run() -> None:
            from kazma_core.stores.knowledge_ingest import ingest_site

            try:
                async for update in ingest_site(library_id, seed):
                    _kb_api_jobs[job_id].update(
                        {
                            "phase": update.phase,
                            "discovered": update.discovered,
                            "fetched": update.fetched,
                            "ingested": update.ingested,
                            "skipped": update.skipped,
                            "failed": update.failed,
                            "current_url": update.current_url,
                            "message": update.message,
                            "errors": list(update.errors or []),
                        }
                    )
                _kb_api_jobs[job_id]["finished_at"] = datetime.now(UTC).isoformat()
            except Exception as exc:
                logger.exception("[kb_api] refresh failed")
                _kb_api_jobs[job_id].update(
                    {"phase": "error", "message": str(exc),
                     "finished_at": datetime.now(UTC).isoformat()}
                )

        asyncio.create_task(_run())
        return {"ok": True, "job_id": job_id}

    return router
