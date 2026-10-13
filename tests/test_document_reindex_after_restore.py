"""A restored document store re-publishes what it records as searchable.

2026-10-13. The coordinated Postgres backup (``backup_pg``) carries the
document store, not the knowledge libraries (library chunks in the settings
store, vectors in Chroma). A restore came back with documents that
``document_chunks`` records as indexed and that no library could find: the
restore report listed them under ``reindex_required`` and nothing re-indexed
them.

``DocumentIngestionService.reconcile_index`` -- run once at every worker
start, and by ``scripts/document_pg_backup.py index`` -- compares both sides
and re-publishes each missing version from its own manifest, as the
document's owner, through the normal publication path. Each one is re-checked
under the document's publication lock, so an unindex that ran meanwhile is
never undone. A missing library, a superseded version and a missing manifest
are reported, never invented; chunks the store does not record (``extra``,
``orphaned``) are reported and removed only on request.

Negative controls: the restored install without the reconcile finds nothing,
and the reconcile without its re-check under the lock undoes an unindex.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from types import SimpleNamespace

import pytest
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.ingestion import SYSTEM_ACTOR, DocumentIngestionService
from kazma_core.documents.knowledge import DocumentKnowledgeAdapter
from kazma_core.documents.models import DocumentId
from kazma_core.documents.worker import DocumentWorker
from kazma_core.stores.knowledge import KnowledgeStore
from kazma_core.stores.knowledge_index import KnowledgeIndex
from kazma_core.tenant_context import reset_current_tenant_id, set_current_tenant_id

_BODY = b"Quarterly turbine inspection checklist for the northern plant.\n"
_WORD = "turbine"


class _NoVector:
    available = False


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def backend(request, monkeypatch):
    if request.param == "postgres":
        try:
            from kazma_core.db.postgres_pool import get_postgres_pool

            pool = get_postgres_pool()  # never reset: the settings store holds it
        except Exception:  # noqa: BLE001 - no configured pool means skip
            pool = None
        if pool is None:
            pytest.skip("Postgres pool not configured")
        monkeypatch.setenv("KAZMA_DOCUMENTS_METADATA_BACKEND", "postgres")
        monkeypatch.setenv("KAZMA_DOCUMENTS_JOBS_BACKEND", "postgres")
        from kazma_core.config_store import get_config_store

        store = get_config_store()
        previous = store.get("documents.enabled")
        store.set("documents.enabled", True, category="documents")
        yield SimpleNamespace(name="postgres", tenant=f"reidx-{uuid.uuid4().hex[:12]}")
        if previous is None:
            store.delete("documents.enabled")
        else:
            store.set("documents.enabled", previous, category="documents")
        return
    yield SimpleNamespace(name="sqlite", tenant=f"reidx-{uuid.uuid4().hex[:12]}")


def _library_store(path, tenant: str, *, with_library: bool = True) -> KnowledgeStore:
    store = KnowledgeStore(str(path))
    if with_library:
        token = set_current_tenant_id(tenant)
        try:
            store.create_library("docs", "Documents")
        finally:
            reset_current_tenant_id(token)
    return store


def _service(root, backend_name: str, store: KnowledgeStore) -> DocumentIngestionService:
    svc = DocumentIngestionService(config=DocumentConfig(
        storage_root=root, security_malware_scan="off", security_local_salvage=False,
        ocr_enabled=False,
    ))
    if backend_name == "postgres":
        assert getattr(svc.repository, "backend_name", None) == "postgres"
    index = KnowledgeIndex(store=store)
    index._vector_stores["docs"] = _NoVector()
    adapter = DocumentKnowledgeAdapter(
        repository=svc.repository, knowledge_store=store, knowledge_index=index,
        config=svc._policy.initial, policy=svc._policy,
    )
    svc.knowledge_adapter = adapter
    svc.service.knowledge_adapter = adapter
    return svc


async def _drive(svc, tenant: str) -> None:
    worker = DocumentWorker(svc.jobs, svc.stage_handlers())
    for _ in range(20):
        claimed = svc.jobs.claim_next(owner="reindex-test", lease_seconds=60, tenant_id=tenant)
        if claimed is None:
            return
        await worker._process_claimed(claimed, "reindex-test")


def _ready(svc, tenant: str, body: bytes = _BODY, document_id=None):
    result = svc.ingest_stream(io.BytesIO(body), filename="checklist.txt", tenant_id=tenant,
                               actor_id="alice", workspace_id="w", document_id=document_id)
    asyncio.run(_drive(svc, tenant))
    status = svc.job_status(tenant_id=tenant, job_id=result.job_id, actor_id=SYSTEM_ACTOR)
    assert status["state"] == "ready", status
    return result


def _hits(svc, tenant: str, document_id) -> list:
    index = svc.knowledge_adapter.index
    return asyncio.run(index.search_document(
        _WORD, tenant_id=tenant, library_id="docs", document_id=str(document_id),
    ))


def _indexed_then_restored(tmp_path, backend, *, with_library: bool = True):
    """Index a document on the source; return a service on the same document
    store with a knowledge store that never saw it (what a restore leaves)."""
    tenant = backend.tenant
    source = _service(tmp_path / "content", backend.name,
                      _library_store(tmp_path / "source-settings.db", tenant))
    try:
        doc = _ready(source, tenant)
        source.index_document(tenant_id=tenant, actor_id="alice",
                              document_id=doc.document_id, library_id="docs")
        assert _hits(source, tenant, doc.document_id)
        version = source.repository.get_document(
            tenant_id=tenant, document_id=DocumentId(doc.document_id)
        ).current_version_id
    finally:
        source.close()
    restored = _service(tmp_path / "content", backend.name,
                        _library_store(tmp_path / "restored-settings.db", tenant,
                                       with_library=with_library))
    entry = {"tenant_id": tenant, "library_id": "docs",
             "document_id": str(doc.document_id), "version_id": str(version)}
    return restored, doc, entry


def test_a_restored_store_is_searchable_again_after_the_reconcile(tmp_path, backend):
    svc, doc, entry = _indexed_then_restored(tmp_path, backend)
    try:
        # Negative control: what the restore left before this change.
        assert _hits(svc, backend.tenant, doc.document_id) == []

        report = svc.reconcile_index(tenant_id=backend.tenant)

        assert report["reindexed"] == [entry]
        assert report["failed"] == report["library_missing"] == report["unavailable"] == []
        assert _hits(svc, backend.tenant, doc.document_id)
        events = svc.audit.list_events(tenant_id=backend.tenant, document_id=doc.document_id,
                                       event_type="index")["events"]
        assert any(e["action"] == "reconcile" and e["actor_id"] == "alice" for e in events)
        # Idempotent: a second pass has nothing to do.
        assert svc.reconcile_index(tenant_id=backend.tenant)["reindexed"] == []
    finally:
        svc.close()


@pytest.mark.asyncio
async def test_the_service_reconciles_once_when_its_workers_start(tmp_path, backend):
    svc, doc, _entry = await asyncio.to_thread(_indexed_then_restored, tmp_path, backend)
    try:
        await svc.start_workers()
        task = svc._index_reconcile
        assert task is not None
        await asyncio.wait_for(asyncio.shield(task), timeout=60)
        chunks = svc.knowledge_adapter.store.get_document_chunks(
            tenant_id=backend.tenant, library_id="docs", document_id=str(doc.document_id)
        )
        assert chunks
    finally:
        await svc.stop_workers()
        svc.close()


def test_a_dry_run_reports_and_changes_nothing(tmp_path, backend):
    svc, doc, entry = _indexed_then_restored(tmp_path, backend)
    try:
        report = svc.reconcile_index(apply=False, tenant_id=backend.tenant)
        assert report["needs_reindex"] == [entry] and report["reindexed"] == []
        assert _hits(svc, backend.tenant, doc.document_id) == []
    finally:
        svc.close()


def test_a_library_this_install_lacks_is_reported_never_created(tmp_path, backend):
    svc, _doc, entry = _indexed_then_restored(tmp_path, backend, with_library=False)
    try:
        report = svc.reconcile_index(tenant_id=backend.tenant)
        assert report["library_missing"] == [entry] and report["reindexed"] == []
        assert svc.knowledge_adapter.store.get_library_for_tenant("docs", backend.tenant) is None
    finally:
        svc.close()


def test_a_missing_manifest_is_reported(tmp_path, backend, monkeypatch):
    svc, doc, entry = _indexed_then_restored(tmp_path, backend)
    try:
        monkeypatch.setattr(svc.storage, "read_manifest", lambda **_kw: None)
        report = svc.reconcile_index(tenant_id=backend.tenant)
        assert report["unavailable"] == [{**entry, "reason": "manifest_missing"}]
        assert _hits(svc, backend.tenant, doc.document_id) == []
    finally:
        svc.close()


def _unindex_after_reading(svc, tenant: str, doc) -> None:
    """Run the user's unindex between the reconcile's read of the knowledge
    side and its publication -- the window the re-check closes."""
    store = svc.knowledge_adapter.store
    real = store.document_index_entries

    def read_then_unindex():
        held = real()
        svc.unindex_document(tenant_id=tenant, actor_id="alice",
                             document_id=doc.document_id, library_id="docs")
        return held

    store.document_index_entries = read_then_unindex


def test_an_unindex_during_the_reconcile_is_not_undone(tmp_path, backend):
    svc, doc, _entry = _indexed_then_restored(tmp_path, backend)
    try:
        _unindex_after_reading(svc, backend.tenant, doc)
        report = svc.reconcile_index(tenant_id=backend.tenant)
        assert report["reindexed"] == []
        assert _hits(svc, backend.tenant, doc.document_id) == []
        assert svc.repository.list_indexed_libraries(
            tenant_id=backend.tenant, document_id=DocumentId(doc.document_id)) == ()
    finally:
        svc.close()


def test_without_the_recheck_the_unindex_is_undone(tmp_path, backend, monkeypatch):
    # Negative control: trust the first read, as a reconcile without the
    # re-check under the publication lock would, and the unindex is undone.
    svc, doc, _entry = _indexed_then_restored(tmp_path, backend)
    try:
        stale = svc.repository.list_document_chunks(
            tenant_id=backend.tenant, library_id="docs", document_id=DocumentId(doc.document_id))
        assert stale
        monkeypatch.setattr(svc.repository, "list_document_chunks", lambda **_kw: stale)
        _unindex_after_reading(svc, backend.tenant, doc)
        report = svc.reconcile_index(tenant_id=backend.tenant)
        assert len(report["reindexed"]) == 1
        assert _hits(svc, backend.tenant, doc.document_id)
    finally:
        svc.close()


def test_a_superseded_version_is_reported_not_republished(tmp_path, backend):
    tenant = backend.tenant
    svc, doc, entry = _indexed_then_restored(tmp_path, backend)
    try:
        newer = _ready(svc, tenant, b"Second revision of the plant checklist.\n",
                       document_id=doc.document_id)
        current = svc.repository.get_document(
            tenant_id=tenant, document_id=DocumentId(newer.document_id)).current_version_id
        assert str(current) != entry["version_id"]
        report = svc.reconcile_index(tenant_id=tenant)
        assert report["stale_version"] == [{**entry, "current_version_id": str(current)}]
        assert report["reindexed"] == []
        assert _hits(svc, tenant, doc.document_id) == []
    finally:
        svc.close()


def test_extra_and_orphaned_chunks_are_pruned_only_on_request(tmp_path, backend):
    tenant = backend.tenant
    store = _library_store(tmp_path / "settings.db", tenant)
    svc = _service(tmp_path / "content", backend.name, store)
    try:
        doc = _ready(svc, tenant)
        svc.index_document(tenant_id=tenant, actor_id="alice", document_id=doc.document_id,
                           library_id="docs")
        held = store.get_document_chunks(tenant_id=tenant, library_id="docs",
                                         document_id=str(doc.document_id))
        # Orphaned: chunks of a document this store has never heard of (a
        # restore to a point before it existed).
        ghost, ghost_version = str(uuid.uuid4()), str(uuid.uuid4())
        store.publish_document_version(
            tenant_id=tenant, library_id="docs", document_id=ghost, version_id=ghost_version,
            source_sha256=held[0]["source_sha256"],
            chunks=[{**c, "id": f"{c['id']}-ghost", "document_id": ghost, "version_id": ghost_version,
                     "source_url": f"document://{ghost}/{ghost_version}"} for c in held],
        )
        # Extra: a live document the store records as unindexed (a restore to
        # a point after it was taken out of the library).
        svc.repository.tombstone_document_chunks(
            tenant_id=tenant, document_id=DocumentId(doc.document_id), library_id="docs")
        version = str(held[0]["version_id"])

        report = svc.reconcile_index(tenant_id=tenant)
        assert report["extra"] == [{"tenant_id": tenant, "library_id": "docs",
                                    "document_id": str(doc.document_id), "version_id": version}]
        assert report["orphaned"] == [{"tenant_id": tenant, "library_id": "docs",
                                       "document_id": ghost, "version_id": ghost_version}]
        assert report["pruned"] == [] and _hits(svc, tenant, doc.document_id)

        pruned = svc.reconcile_index(tenant_id=tenant, prune=True)
        assert len(pruned["pruned"]) == 2
        assert _hits(svc, tenant, doc.document_id) == []
        assert store.document_index_entries() == set()
    finally:
        svc.close()


def test_the_operator_command_reports_and_fails_on_a_failed_reindex(monkeypatch, capsys):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "document_pg_backup", Path(__file__).resolve().parents[1] / "scripts" / "document_pg_backup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls: list[dict] = []

    class _Svc:
        def __init__(self, failed):
            self.failed = failed

        def reconcile_index(self, **kwargs):
            calls.append(kwargs)
            return {"ok": True, "failed": self.failed}

        def close(self):
            calls.append({"closed": True})

    import kazma_core.documents.ingestion as ingestion

    monkeypatch.setattr("kazma_core.env_files.load_env_files", lambda *a, **k: None)
    monkeypatch.setattr(ingestion, "create_default_ingestion_service", lambda: _Svc([]))
    assert module.main(["index", "--tenant", "t1"]) == 0
    assert calls[0] == {"apply": False, "prune": False, "tenant_id": "t1"}
    assert calls[1] == {"closed": True}
    monkeypatch.setattr(ingestion, "create_default_ingestion_service", lambda: _Svc([{"code": "x"}]))
    assert module.main(["index", "--apply", "--prune"]) == 1
    assert calls[2] == {"apply": True, "prune": True, "tenant_id": None}
