"""Knowledge Base API — Web transport for Knowledge Libraries.

Thin FastAPI layer that exposes the Knowledge Library subsystem
(``kazma_core.stores.knowledge*``) to the ``/knowledge`` page. Library CRUD
delegates to ``KnowledgeStore``, deletion to
``knowledge_lifecycle.delete_library``, ingestion to ``ingest_url`` /
``ingest_site`` (a crawl runs as a background ``asyncio`` task), search to
``KnowledgeIndex``.

Endpoints (all under ``/api/kb``; 16 routes):
  GET    /libraries                         — list libraries (no storage side effects)
  POST   /libraries        {id,name,...}    — create an empty library
  GET    /libraries/archived/list           — the archived libraries
  GET    /libraries/{id}                    — one library
  PATCH  /libraries/{id}   {auto_inject?..} — update a library
  DELETE /libraries/{id}                    — delete a library and every derived copy
  POST   /libraries/{id}/archive            — archive
  POST   /libraries/{id}/unarchive          — restore from the archive
  GET    /libraries/{id}/chunks             — paginated chunk browser
  POST   /libraries/{id}/refresh            — re-crawl from the seed URL
  POST   /ingest           {library_id,url,mode,max_pages?,name?}
                                             — one page now, or a crawl in the background
  GET    /jobs                              — this install's Knowledge crawls (the journal)
  GET    /jobs/{job_id}                     — one crawl's progress, from the journal
  POST   /jobs/{job_id}/resume              — resume an interrupted crawl (the one resume path)
  POST   /search           {library_id,query,top_k?}
                                             — the page's test search
  POST   /services/firecrawl/credits/refresh — refresh the hosted crawler's balance

Contract (Knowledge audit 2026-10-13):
  - Every refusal answers a non-2xx status with ``{"ok": false, "code",
    "transient", "error"}``: ``code`` is stable and the page words it from
    the ``knowledge.err.<code>`` catalog entry in its own language (KB-S05);
    ``error`` is the English text. An expected refusal (a full journal, a
    policy change, a URL that is not http(s)) is logged once at INFO or
    WARNING, never as an ERROR with a traceback.
  - Every request body is read through a bounded reader and validated by a
    model with bounds (KB-S13): ``top_k`` 1-20, ``query`` up to 2,000
    characters, ids up to 200 (48 after slugging), names and descriptions up
    to 500, URLs up to 2,048 and http(s) only.
  - On an install with platform users every route refuses with 409
    ``kb_single_operator`` (decision D1, KB-S04): libraries, chunks and
    crawl jobs are not separated per user.
  - A crawl's state has one source, the crawl journal (KB-E14).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable, Coroutine
from contextlib import aclosing
from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from kazma_core.background import spawn_background
from kazma_core.errors import safe_error
from kazma_core.playwright_loop import finish_owned
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

logger = logging.getLogger(__name__)

__all__ = ["KB_ERROR_CODES", "create_kb_router"]

#: The most a Knowledge request body may hold: the largest one is a URL and a
#: few short fields.
_BODY_MAX_BYTES = 64 * 1024

#: Every ``code`` a Knowledge route answers with, beyond the acquisition
#: codes (``FetchErrorCode``): each has a ``knowledge.err.<code>`` catalog
#: entry in English and Arabic (tests/test_kb_api_contract.py).
KB_ERROR_CODES = (
    "kb_single_operator", "invalid_request", "invalid_json", "request_too_large",
    "not_found", "library_exists", "url_refused", "kb_storage_full", "kb_library_limit", "library_busy",
    "crawl_owns_library", "documents_unavailable", "page_not_ingested", "no_seed",
    "resume_refused", "internal_error",
)

#: How each acquisition refusal is answered: (HTTP status, log level).
_ACQUISITION_STATUS: dict[str, tuple[int, int]] = {
    FetchErrorCode.INVALID_URL.value: (422, logging.INFO),
    FetchErrorCode.POLICY.value: (409, logging.INFO),
    FetchErrorCode.EXISTS.value: (409, logging.INFO),
    FetchErrorCode.LIMIT.value: (429, logging.INFO),
    FetchErrorCode.STORAGE.value: (503, logging.WARNING),
    FetchErrorCode.ROUTING.value: (503, logging.WARNING),
    FetchErrorCode.ROBOTS.value: (409, logging.INFO),
    FetchErrorCode.ROBOTS_UNAVAILABLE.value: (503, logging.INFO),
    FetchErrorCode.SECURITY.value: (422, logging.INFO),
    FetchErrorCode.SCOPE.value: (422, logging.INFO),
}


class _KbRoute(APIRoute):
    """Refuses every Knowledge route on an install with platform users
    (decision D1, KB-S04), with the shared single-operator rule."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def checked(request: Request) -> Response:
            from kazma_core.stores.knowledge_lifecycle import kb_refusal

            refusal = await asyncio.to_thread(kb_refusal)
            if refusal:
                return _fail("kb_single_operator", refusal, 409)
            return await handler(request)

        return checked


def _fail(code: str, error: str, status: int, *, transient: bool = False, **extra: Any) -> JSONResponse:
    return JSONResponse({"ok": False, "code": code, "transient": transient, "error": error, **extra},
                        status_code=status)


def _acquisition_failure(exc: AcquisitionError) -> JSONResponse:
    """A typed acquisition refusal as a stable code, logged once (KB-S05).
    ``safe_error`` turned each into ``internal_error`` with an ERROR log and
    a traceback -- every 2 s while the page polled a journal outage."""
    from kazma_core.web_acquire.crawl_jobs import CrawlLibraryBusy

    if isinstance(exc, CrawlLibraryBusy):
        code, status, level = "library_busy", 409, logging.INFO
    else:
        code = exc.code.value
        status, level = _ACQUISITION_STATUS.get(code, (409, logging.INFO))
    logger.log(level, "[kb_api] refused (%s): %s", code, exc)
    return _fail(code, str(exc), status, transient=bool(exc.transient))


def _capacity_refusal(lib_id: str, exc: Exception) -> JSONResponse:
    """A store at its quota (507 ``kb_storage_full``), or an install at its
    library ceiling (409 ``kb_library_limit``, KB-Q01), as a stable code."""
    from kazma_core.stores.knowledge import KnowledgeLibraryLimit

    if isinstance(exc, KnowledgeLibraryLimit):
        logger.info("[kb_api] library %s refused: %s", lib_id, exc)
        return _fail("kb_library_limit", str(exc), 409, limit=exc.ceiling)
    logger.warning("[kb_api] work on %s refused: %s", lib_id, exc)
    return _fail("kb_storage_full", str(exc), 507)


class _Body(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class _CreateBody(_Body):
    id: str = Field(min_length=1, max_length=200)
    name: str = Field("", max_length=500)
    description: str = Field("", max_length=500)
    seed_url: str = Field("", max_length=2048)


class _UpdateBody(_Body):
    name: str | None = Field(None, max_length=500)
    description: str | None = Field(None, max_length=500)
    seed_url: str | None = Field(None, max_length=2048)
    auto_inject: bool | None = None


class _IngestBody(_Body):
    library_id: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2048)
    mode: Literal["page", "site"] = "page"
    max_pages: int | None = Field(None, ge=1, le=1000)
    name: str = Field("", max_length=500)


class _SearchBody(_Body):
    library_id: str = Field(min_length=1, max_length=200)
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(5, ge=1, le=20)


async def _body(request: Request, model: type[_Body]) -> Any:
    """The request's JSON object read under :data:`_BODY_MAX_BYTES` and
    validated by *model* (KB-S13), or the refusal to return. A declared
    length past the cap is refused before a byte is read; an undeclared one
    is cut off as soon as it passes the cap."""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > _BODY_MAX_BYTES:
                return _fail("request_too_large", "Request body is too large", 413)
        except ValueError:
            return _fail("invalid_request", "Invalid Content-Length", 400)
    raw = bytearray()
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > _BODY_MAX_BYTES:
            return _fail("request_too_large", "Request body is too large", 413)
    try:
        value = json.loads(bytes(raw) or b"null")
    except (ValueError, RecursionError):
        return _fail("invalid_json", "Request body is not valid JSON", 422)
    if not isinstance(value, dict):
        return _fail("invalid_json", "Request body must be a JSON object", 422)
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(part) for part in first.get("loc", ())) or "body"
        return _fail("invalid_request", f"{field}: {first.get('msg', 'invalid value')}", 422)


def _not_found() -> JSONResponse:
    return _fail("not_found", "Not found", 404)


def create_kb_router() -> APIRouter:
    """Create and return the Knowledge Base API router."""

    router = APIRouter(prefix="/api/kb", tags=["knowledge"], route_class=_KbRoute)

    def _store():
        from kazma_core.stores.knowledge import get_knowledge_store

        return get_knowledge_store()

    def _index():
        from kazma_core.stores.knowledge_index import get_knowledge_index

        return get_knowledge_index()

    # ── Libraries ───────────────────────────────────────────────────────

    def _meaning_search(index: Any, library_id: str) -> dict[str, Any]:
        """How much of a library meaning search reaches, as the repair sweep
        last recorded it (keyword search reaches all of it). Read from the
        store: no collection is opened and no model loaded (KB-S15)."""
        health = index.health(library_id)
        return {
            "available": bool(health.get("vector_available")),
            "state": health.get("state", "unknown"),
            "vectors": int(health.get("vector_chunks") or 0),
            "chunks": int(health.get("sqlite_chunks") or 0),
        }

    @router.get("/libraries")
    def list_libraries() -> Any:
        try:
            libs = _store().list_libraries()
            index = _index()
            for lib in libs:
                lib["meaning_search"] = _meaning_search(index, lib["id"])
            return {"ok": True, "libraries": libs}
        except Exception as exc:
            logger.exception("[kb_api] list_libraries failed")
            return _fail("internal_error", safe_error(exc), 500, libraries=[])

    @router.post("/libraries")
    async def create_library(request: Request) -> Any:
        data = await _body(request, _CreateBody)
        if isinstance(data, JSONResponse):
            return data
        return await asyncio.to_thread(_create, data)

    def _create(data: _CreateBody) -> Any:
        from kazma_core.stores.knowledge import KnowledgeStorageFull, KnowledgeUrlRefused, slugify_library_id

        lib_id = slugify_library_id(data.id)
        try:
            if _store().get_library(lib_id):
                return _fail("library_exists", f"Library '{lib_id}' already exists", 409, library_id=lib_id)
            lib = _store().create_library(lib_id, name=data.name or data.id,
                                          description=data.description, seed_url=data.seed_url)
            return {"ok": True, "library": lib}
        except KnowledgeStorageFull as exc:  # the library ceiling (KB-Q01)
            return _capacity_refusal(lib_id, exc)
        except KnowledgeUrlRefused as exc:
            return _fail("url_refused", str(exc), 422)
        except ValueError as exc:
            return _fail("invalid_request", str(exc), 422)
        except Exception as exc:
            logger.exception("[kb_api] create_library failed")
            return _fail("internal_error", safe_error(exc), 500)

    @router.get("/libraries/{library_id}")
    def get_library(library_id: str) -> Any:
        lib = _store().get_library(library_id)
        if not lib:
            return _not_found()
        return {"ok": True, "library": lib}

    @router.get("/libraries/archived/list")
    def list_archived() -> Any:
        """List archived libraries for the Archived tab."""
        try:
            libs = _store().list_archived_libraries()
            return {"ok": True, "libraries": libs}
        except Exception as exc:
            logger.exception("[kb_api] list_archived failed")
            return _fail("internal_error", safe_error(exc), 500, libraries=[])

    def _set_archived(library_id: str, archived: bool) -> Any:
        try:
            if not _store().archive_library(library_id, archived=archived):
                return _not_found()
            return {"ok": True}
        except Exception as exc:
            logger.exception("[kb_api] archive_library failed")
            return _fail("internal_error", safe_error(exc), 500)

    @router.post("/libraries/{library_id}/archive")
    def archive_library(library_id: str) -> Any:
        return _set_archived(library_id, True)

    @router.post("/libraries/{library_id}/unarchive")
    def unarchive_library(library_id: str) -> Any:
        return _set_archived(library_id, False)

    @router.patch("/libraries/{library_id}")
    async def update_library(library_id: str, request: Request) -> Any:
        data = await _body(request, _UpdateBody)
        if isinstance(data, JSONResponse):
            return data
        return await asyncio.to_thread(_update, library_id, data)

    def _update(library_id: str, data: _UpdateBody) -> Any:
        from kazma_core.stores.knowledge import KnowledgeUrlRefused

        try:
            lib = _store().update_library(library_id, name=data.name, description=data.description,
                                          seed_url=data.seed_url, auto_inject=data.auto_inject)
            if not lib:
                return _not_found()
            return {"ok": True, "library": lib}
        except KnowledgeUrlRefused as exc:
            return _fail("url_refused", str(exc), 422)
        except ValueError as exc:
            return _fail("invalid_request", str(exc), 422)
        except Exception as exc:
            logger.exception("[kb_api] update_library failed")
            return _fail("internal_error", safe_error(exc), 500)

    @router.delete("/libraries/{library_id}")
    def delete_library(library_id: str) -> Any:
        """Delete the library and every copy derived from it; 409 while a
        crawl owns it (KB-E02, KB-E08)."""
        from kazma_core.stores.knowledge_lifecycle import (
            KnowledgeDocumentsUnavailable,
            KnowledgeLibraryBusy,
            delete_library as delete_everything,
        )

        try:
            if _store().get_library(library_id) is None:
                return _not_found()
            outcome = delete_everything(library_id)
            if not outcome["deleted"]:
                return _not_found()
            return {"ok": True, **outcome}
        except KnowledgeLibraryBusy as exc:
            logger.info("[kb_api] delete of %s refused: a crawl owns it", library_id)
            return _fail("crawl_owns_library", str(exc), 409, transient=True, jobs=exc.jobs)
        except KnowledgeDocumentsUnavailable as exc:
            logger.warning("[kb_api] delete of %s refused: %s", library_id, exc)
            return _fail("documents_unavailable", str(exc), 503, transient=True)
        except AcquisitionError as exc:
            return _acquisition_failure(exc)
        except Exception as exc:
            logger.exception("[kb_api] delete_library failed")
            return _fail("internal_error", safe_error(exc), 500)

    # ── Chunk browser ───────────────────────────────────────────────────

    @router.get("/libraries/{library_id}/chunks")
    def list_chunks(
        library_id: str,
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0, le=10_000_000),
    ) -> Any:
        try:
            if _store().get_library(library_id) is None:
                return _not_found()
            chunks = _store().list_chunks(library_id, limit=limit, offset=offset)
            total = _store().count_chunks(library_id)
            # Strip the full content for the list view.
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
            return _fail("internal_error", safe_error(exc), 500, chunks=[], total=0)

    # ── Ingestion (page now / site in the background) ───────────────────

    @router.post("/ingest")
    async def ingest(request: Request) -> Any:
        data = await _body(request, _IngestBody)
        if isinstance(data, JSONResponse):
            return data
        from kazma_core.stores.knowledge import slugify_library_id

        lib_id = slugify_library_id(data.library_id)
        if data.mode == "page":
            return await _ingest_page(lib_id, data.url, name=data.name)
        return await _start_site(lib_id, data.url, max_pages=data.max_pages, kind="site", name=data.name)

    async def _ingest_page(lib_id: str, url: str, *, name: str) -> Any:
        """One page now. ``ok`` is the page's outcome (KB-S13): a page that
        was not stored answers 422 ``page_not_ingested`` with its reasons,
        and the library is created only once the page was fetched (KB-S09)."""
        from kazma_core.stores.knowledge import KnowledgeStorageFull
        from kazma_core.stores.knowledge_ingest import ingest_url

        try:
            # Building the store opens SQLite: inside the thread too (AUD-111).
            await asyncio.to_thread(lambda: _store().ensure_capacity(lib_id))
        except KnowledgeStorageFull as exc:
            return _capacity_refusal(lib_id, exc)
        try:
            result = await ingest_url(lib_id, url, name=name)
        except Exception as exc:
            logger.exception("[kb_api] page ingest failed")
            return _fail("internal_error", safe_error(exc), 500)
        if result.pages_failed or not result.pages_fetched:
            reason = result.errors[0] if result.errors else "The page was not stored."
            logger.info("[kb_api] page not ingested into %s: %s", lib_id, reason)
            return _fail("page_not_ingested", reason, 422, errors=result.errors[:5],
                         library_created=result.library_created)
        return {
            "ok": True,
            "mode": "page",
            "library_id": lib_id,
            "pages_fetched": result.pages_fetched,
            "chunks_new": result.chunks_new,
            "chunks_skipped": result.chunks_skipped,
            "pages_unchanged": result.pages_unchanged,
            "library_created": result.library_created,
            "errors": result.errors[:5],
        }

    async def _start_site(lib_id: str, url: str, *, max_pages: int | None, kind: str,
                          name: str = "", resume_job_id: str | None = None) -> Any:
        """Admit a crawl in the journal, make sure its library exists, and
        run it in the background. The library is created only once the
        crawl was admitted (KB-S09)."""
        from kazma_core.stores.knowledge import KnowledgeStorageFull
        from kazma_core.stores.knowledge_ingest import _prepare_ingest_site
        from kazma_core.web_acquire.crawl_jobs import call

        job_id = resume_job_id or f"{lib_id}:{uuid.uuid4().hex}"
        try:
            prepared = await _prepare_ingest_site(lib_id, url, max_pages=max_pages, job_id=job_id,
                                                  resume=bool(resume_job_id))
        except AcquisitionError as exc:
            return _acquisition_failure(exc)
        except KnowledgeStorageFull as exc:
            return _capacity_refusal(lib_id, exc)
        dispatched = False
        try:
            await call(prepared[0].update, lambda row: row.update(kind=kind))

            def _ensure_library() -> None:
                store = _store()
                if store.get_library(lib_id) is None:
                    store.create_library(lib_id, name=name or lib_id, seed_url=url)
                elif kind == "site" and not resume_job_id:
                    store.update_library(lib_id, seed_url=url)

            await asyncio.to_thread(_ensure_library)

            async def _run() -> None:
                from kazma_core.stores.knowledge_ingest import ingest_site

                try:
                    async with aclosing(ingest_site(lib_id, url, max_pages=max_pages, job_id=job_id,
                                                    resume=bool(resume_job_id), _prepared=prepared)) as updates:
                        async for _update in updates:
                            pass  # the journal records every update (job.progress)
                except Exception as exc:
                    logger.warning("[kb_api] crawl %s of %s ended with an error: %s", job_id, lib_id, exc)
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

            spawn_background(_run(), name=f"kb-crawl:{job_id}")
            dispatched = True
            return {"ok": True, "mode": "site", "job_id": job_id}
        except KnowledgeStorageFull as exc:  # another library took the last place meanwhile
            return _capacity_refusal(lib_id, exc)
        except Exception as exc:
            logger.exception("[kb_api] crawl %s could not start", job_id)
            return _fail("internal_error", safe_error(exc), 500)
        finally:
            if not dispatched:
                await finish_owned(call(prepared[0].finish, 'interrupted'))

    @router.post('/services/firecrawl/credits/refresh')
    async def refresh_firecrawl_credits() -> Any:
        from kazma_core.web_acquire.credits import refresh_firecrawl_balance

        try:
            return {'ok': True, 'balances': await refresh_firecrawl_balance()}
        except AcquisitionError as exc:
            return _acquisition_failure(exc)

    @router.get('/jobs')
    def durable_jobs() -> Any:
        from kazma_core.web_acquire.crawl_jobs import inspect_jobs

        try:
            return {'ok': True, 'jobs': sorted((row for row in inspect_jobs() if row['kind'] in {'site', 'refresh'}),
                key=lambda row: row['updated_at'], reverse=True)}
        except AcquisitionError as exc:
            return _acquisition_failure(exc)

    @router.get("/jobs/{job_id}")
    def job_status(job_id: str) -> Any:
        from kazma_core.web_acquire.crawl_jobs import projection

        try:
            durable = projection(job_id)
        except AcquisitionError as exc:
            return _acquisition_failure(exc)
        if durable is None:
            return _fail("not_found", "Unknown crawl job; it may have been evicted from the journal.", 404)
        if durable['kind'] not in {'site', 'refresh'}:
            return _fail("not_found", 'This job belongs to research; inspect acquisition readiness.', 404)
        return {'ok': True, 'job': durable}

    @router.post('/jobs/{job_id}/resume')
    async def resume_job(job_id: str) -> Any:
        """The one resume path (KB-S13: ``/ingest`` no longer takes a job id)."""
        from kazma_core.web_acquire.crawl_jobs import call, inspect_jobs, recover

        try:
            await call(recover)
            row = await call(inspect_jobs, job_id)
        except AcquisitionError as exc:
            return _acquisition_failure(exc)
        if row is None:
            return _fail("not_found", "Unknown crawl job.", 404)
        if row['status'] != 'interrupted':
            return _fail("resume_refused", 'Only an interrupted durable crawl can resume.', 409)
        if row.get('kind', 'site') not in {'site', 'refresh'}:
            return _fail("resume_refused", 'Research crawls resume through crawl_site with identical options.', 409)
        if not await asyncio.to_thread(lambda: _store().get_library(row['library_id'])):
            return _fail("not_found", 'The crawl library no longer exists.', 404)
        return await _start_site(row['library_id'], row['seed'], max_pages=row['policy']['max_pages'],
                                 kind=row.get('kind', 'site'), resume_job_id=job_id)

    # ── Search (UI "Test search") ───────────────────────────────────────

    @router.post("/search")
    async def search(request: Request) -> Any:
        data = await _body(request, _SearchBody)
        if isinstance(data, JSONResponse):
            return data
        if not await asyncio.to_thread(lambda: _store().get_library(data.library_id)):
            return _not_found()
        try:
            index = await asyncio.to_thread(_index)
            hits = await index.search(data.query, data.library_id, top_k=data.top_k)
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
            return _fail("internal_error", safe_error(exc), 500, hits=[])

    # ── Refresh (re-crawl from seed_url) ───────────────────────────────

    @router.post("/libraries/{library_id}/refresh")
    async def refresh(library_id: str) -> Any:
        lib = await asyncio.to_thread(lambda: _store().get_library(library_id))
        if not lib:
            return _not_found()
        seed = lib.get("seed_url") or ""
        if not seed:
            return _fail("no_seed", "Library has no seed_url to refresh from", 409)
        return await _start_site(library_id, seed, max_pages=None, kind='refresh')

    return router
