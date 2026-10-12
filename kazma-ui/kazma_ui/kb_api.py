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
from contextlib import aclosing
import uuid
from collections import OrderedDict
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Query
from kazma_core.background import spawn_background
from kazma_core.errors import safe_error
from kazma_core.web_acquire.result import AcquisitionError
from kazma_core.playwright_loop import finish_owned

logger = logging.getLogger(__name__)

__all__ = ["create_kb_router"]


# In-process job registry for low-latency polling.  Durable mirror lives
# in ConfigStore via ``kazma_core.stores.kb_jobs`` so restarts don't
# orphan the UI on "Unknown job_id".
#
# Bounded LRU (audit F-14): this was an unbounded dict that kept every job
# ever run resident for the process lifetime, and `_bootstrap_jobs` reloaded
# the entire durable history at router construction. The durable store stays
# the source of truth — `job_status` already falls back to it — so evicting
# the oldest in-memory entries costs one lookup, not correctness.
_KB_JOBS_MAX = 500
_kb_api_jobs: OrderedDict[str, dict[str, Any]] = OrderedDict()
_kb_jobs_bootstrapped = False


def _remember_job_in_memory(job_id: str, data: dict[str, Any]) -> None:
    _kb_api_jobs[job_id] = data
    _kb_api_jobs.move_to_end(job_id)
    while len(_kb_api_jobs) > _KB_JOBS_MAX:
        _kb_api_jobs.popitem(last=False)


def _persist_job(job_id: str, data: dict[str, Any]) -> None:
    try:
        from kazma_core.stores.kb_jobs import upsert_job

        upsert_job(job_id, **data)
    except Exception as exc:
        logger.debug("[kb_api] durable job write failed: %s", exc)


async def _record_job(job_id: str, data: dict[str, Any]) -> None:
    """Job snapshot from the event loop.

    The in-memory registry is updated on the loop (it is what polling reads,
    and only the loop mutates it); the durable ConfigStore write — one per
    crawl progress update — runs in a thread (AGENTS.md §26E).
    """
    _remember_job_in_memory(job_id, data)
    await asyncio.to_thread(_persist_job, job_id, dict(data))


#: Only jobs this recent (or still running) are pre-loaded into memory at boot.
_KB_BOOTSTRAP_MAX_AGE_S = 7 * 24 * 3600

#: Phases that mean "not finished" — always reloaded regardless of age.
_KB_LIVE_PHASES = frozenset({"queued", "running", "crawling", "indexing", "starting"})


def _job_is_recent(row: dict[str, Any]) -> bool:
    """True when a durable job row is still live or finished within the window."""
    if str(row.get("phase") or "").lower() in _KB_LIVE_PHASES:
        return True
    stamp = row.get("finished_at") or row.get("started_at") or ""
    if not stamp:
        return True  # no timestamp — keep rather than silently drop
    try:
        from datetime import UTC, datetime

        ts = datetime.fromisoformat(str(stamp))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return (datetime.now(UTC) - ts).total_seconds() <= _KB_BOOTSTRAP_MAX_AGE_S
    except (ValueError, TypeError):
        return True


def _bootstrap_jobs() -> None:
    """Load recent/live durable jobs + mark mid-crawl jobs interrupted."""
    global _kb_jobs_bootstrapped
    if _kb_jobs_bootstrapped:
        return
    _kb_jobs_bootstrapped = True
    try:
        from kazma_core.stores.kb_jobs import list_jobs, mark_stale_jobs_interrupted
        from kazma_core.web_acquire.crawl_jobs import recover

        recover()
        mark_stale_jobs_interrupted()
        loaded = 0
        for row in list_jobs():
            jid = row.pop("job_id", None)
            if not jid or not _job_is_recent(row):
                continue
            _kb_api_jobs[jid] = row
            loaded += 1
            if loaded >= _KB_JOBS_MAX:
                break
        logger.debug("[kb_api] bootstrapped %d recent job(s)", loaded)
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

    def _meaning_search(index: Any, library_id: str) -> dict[str, Any]:
        """How much of a library meaning search reaches (keyword search reaches
        all of it). Shown per library: from 2026-07-31 to 2026-09-26 meaning
        search was off everywhere and nothing on the page said so."""
        health = index.health(library_id)
        return {
            "available": bool(health.get("vector_available")),
            "vectors": int(health.get("vector_chunks") or 0),
            "chunks": int(health.get("sqlite_chunks") or 0),
        }

    @router.get("/libraries")
    def list_libraries() -> dict[str, Any]:
        try:
            libs = _store().list_libraries()
            index = _index()
            for lib in libs:
                lib["meaning_search"] = _meaning_search(index, lib["id"])
            return {"ok": True, "libraries": libs}
        except Exception as exc:
            logger.exception("[kb_api] list_libraries failed")
            return {"ok": False, "error": safe_error(exc), "libraries": []}

    @router.post("/libraries")
    def create_library(
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
            return {"ok": False, "error": safe_error(exc)}

    @router.get("/libraries/{library_id}")
    def get_library(library_id: str) -> dict[str, Any]:
        lib = _store().get_library(library_id)
        if not lib:
            return {"ok": False, "error": "Not found"}
        return {"ok": True, "library": lib}

    @router.get("/libraries/archived/list")
    def list_archived() -> dict[str, Any]:
        """List archived libraries for the Archived tab."""
        try:
            libs = _store().list_archived_libraries()
            return {"ok": True, "libraries": libs}
        except Exception as exc:
            logger.exception("[kb_api] list_archived failed")
            return {"ok": False, "error": safe_error(exc), "libraries": []}

    @router.post("/libraries/{library_id}/archive")
    def archive_library(library_id: str) -> dict[str, Any]:
        try:
            ok = _store().archive_library(library_id, archived=True)
            if not ok:
                return {"ok": False, "error": "Not found"}
            return {"ok": True}
        except Exception as exc:
            logger.exception("[kb_api] archive_library failed")
            return {"ok": False, "error": safe_error(exc)}

    @router.post("/libraries/{library_id}/unarchive")
    def unarchive_library(library_id: str) -> dict[str, Any]:
        try:
            ok = _store().archive_library(library_id, archived=False)
            if not ok:
                return {"ok": False, "error": "Not found"}
            return {"ok": True}
        except Exception as exc:
            logger.exception("[kb_api] unarchive_library failed")
            return {"ok": False, "error": safe_error(exc)}

    @router.patch("/libraries/{library_id}")
    def update_library(
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
            return {"ok": False, "error": safe_error(exc)}

    @router.delete("/libraries/{library_id}")
    def delete_library(library_id: str) -> dict[str, Any]:
        try:
            # delete_library on the index drops ChromaDB + SQLite rows.
            ok = _index().delete_library(library_id)
            if not ok:
                return {"ok": False, "error": "Not found"}
            return {"ok": True}
        except Exception as exc:
            logger.exception("[kb_api] delete_library failed")
            return {"ok": False, "error": safe_error(exc)}

    # ── Chunk browser ───────────────────────────────────────────────────

    @router.get("/libraries/{library_id}/chunks")
    def list_chunks(
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
            return {"ok": False, "error": safe_error(exc), "chunks": [], "total": 0}

    # ── Ingestion (page sync / site background) ─────────────────────────

    @router.post("/ingest")
    async def ingest(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        return await _ingest(payload)

    async def _ingest(payload, *, kind='site'):
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

        def _ensure_library() -> None:
            store = _store()
            if not store.get_library(lib_id):
                store.create_library(lib_id, name=lib_id, seed_url=url)
            else:
                store.update_library(lib_id, seed_url=url)

        try:
            await asyncio.to_thread(_ensure_library)
        except Exception as exc:
            logger.exception("[kb_api] library setup failed")
            return {"ok": False, "error": f"library setup failed: {safe_error(exc)}"}

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
                return {"ok": False, "error": safe_error(exc)}

        # mode == "site" → background job.
        resume_id = payload.get('resume_job_id')
        job_id = resume_id or f"{lib_id}:{uuid.uuid4().hex}"
        from kazma_core.stores.knowledge_ingest import _prepare_ingest_site
        from kazma_core.web_acquire.crawl_jobs import call

        try:
            prepared = await _prepare_ingest_site(lib_id, url, max_pages=max_pages, job_id=job_id, resume=bool(resume_id))
        except AcquisitionError as exc:
            return {'ok': False, 'error': safe_error(exc)}
        dispatched = False
        try:
            await call(prepared[0].update, lambda row: row.update(kind=kind))
            await _record_job(
                job_id,
                {
                    "phase": "starting",
                    "library_id": lib_id,
                    "url": url,
                    "kind": kind,
                    "started_at": datetime.now(UTC).isoformat(),
                },
            )

            async def _run() -> None:
                from kazma_core.stores.knowledge_ingest import ingest_site

                try:
                    async with aclosing(ingest_site(lib_id, url, max_pages=max_pages, job_id=job_id, resume=bool(resume_id), _prepared=prepared)) as updates:
                        async for update in updates:
                            snap = dict(_kb_api_jobs.get(job_id) or {})
                            snap.update(
                                {
                                    "phase": update.phase,
                                    "discovered": update.discovered,
                                    "fetched": update.fetched,
                                    "ingested": update.ingested,
                                    "skipped": update.skipped,
                                    "failed": update.failed,
                                    "pages_unchanged": getattr(update, "pages_unchanged", 0) or 0,
                                    "pruned_urls": getattr(update, "pruned_urls", 0) or 0,
                                    "current_url": update.current_url,
                                    "message": update.message,
                                    "errors": list(update.errors or []),
                                    "library_id": lib_id,
                                    "url": url,
                                }
                            )
                            await _record_job(job_id, snap)
                    snap = dict(_kb_api_jobs.get(job_id) or {})
                    snap["finished_at"] = datetime.now(UTC).isoformat()
                    if snap.get("phase") not in ("error", "interrupted"):
                        snap.setdefault("phase", "done")
                    await _record_job(job_id, snap)
                except Exception as exc:
                    logger.exception("[kb_api] site ingest failed")
                    snap = dict(_kb_api_jobs.get(job_id) or {})
                    snap.update(
                        {
                            "phase": "error",
                            "message": safe_error(exc),
                            "finished_at": datetime.now(UTC).isoformat(),
                        }
                    )
                    await _record_job(job_id, snap)

                finally:
                    def release_unclaimed():
                        job = prepared[0]
                        try:
                            if job.read()['status'] == 'running':
                                job.finish('interrupted')
                        except AcquisitionError as exc:
                            if exc.code != 'policy_error':
                                raise
                    await finish_owned(call(release_unclaimed))

            spawn_background(_run(), name=f"kb-ingest-url:{job_id}")
            dispatched = True
            return {"ok": True, "mode": "site", "job_id": job_id}
        finally:
            if not dispatched:
                await finish_owned(call(prepared[0].finish, 'interrupted'))

    @router.post('/services/firecrawl/credits/refresh')
    async def refresh_firecrawl_credits() -> dict[str, Any]:
        from kazma_core.web_acquire.credits import refresh_firecrawl_balance

        try:
            return {'ok': True, 'balances': await refresh_firecrawl_balance()}
        except AcquisitionError as exc:
            return {'ok': False, 'error': safe_error(exc)}

    @router.get('/jobs')
    def durable_jobs() -> dict[str, Any]:
        from kazma_core.web_acquire.crawl_jobs import inspect_jobs

        try:
            return {'ok': True, 'jobs': sorted((row for row in inspect_jobs() if row['kind'] in {'site', 'refresh'}),
                key=lambda row: row['updated_at'], reverse=True)}
        except AcquisitionError as exc:
            return {'ok': False, 'error': safe_error(exc)}

    @router.get("/jobs/{job_id}")
    def job_status(job_id: str) -> dict[str, Any]:
        from kazma_core.web_acquire.crawl_jobs import projection

        try:
            durable = projection(job_id)
        except AcquisitionError as exc:
            return {'ok': False, 'error': safe_error(exc)}
        if durable is not None:
            if durable['kind'] not in {'site', 'refresh'}:
                return {'ok': False, 'error': 'This job belongs to research; inspect acquisition readiness.'}
            return {'ok': True, 'job': durable}
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

    @router.post('/jobs/{job_id}/resume')
    async def resume_job(job_id: str) -> dict[str, Any]:
        from kazma_core.web_acquire.crawl_jobs import call, inspect_jobs, recover

        try:
            await call(recover)
            row = await call(inspect_jobs, job_id)
            if row is None or row['status'] != 'interrupted':
                return {'ok': False, 'error': 'Only an interrupted durable crawl can resume.'}
            if row.get('kind', 'site') not in {'site', 'refresh'}:
                return {'ok': False, 'error': 'Research crawls resume through crawl_site with identical options.'}
            if not await asyncio.to_thread(lambda: _store().get_library(row['library_id'])):
                return {'ok': False, 'error': 'The crawl library no longer exists.'}
            return await _ingest({'mode': 'site', 'library_id': row['library_id'], 'url': row['seed'],
                'max_pages': row['policy']['max_pages'], 'resume_job_id': job_id}, kind=row.get('kind', 'site'))
        except AcquisitionError as exc:
            return {'ok': False, 'error': safe_error(exc)}

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
            return {"ok": False, "error": safe_error(exc), "hits": []}

    # ── Refresh (re-ingest from seed_url) ───────────────────────────────

    @router.post("/libraries/{library_id}/refresh")
    async def refresh(library_id: str) -> dict[str, Any]:
        lib = await asyncio.to_thread(lambda: _store().get_library(library_id))
        if not lib:
            return {"ok": False, "error": "Not found"}
        seed = lib.get("seed_url") or ""
        if not seed:
            return {"ok": False, "error": "Library has no seed_url to refresh from"}
        return await _ingest({'mode': 'site', 'library_id': library_id, 'url': seed}, kind='refresh')

    return router
