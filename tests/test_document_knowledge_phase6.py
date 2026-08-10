from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from dataclasses import replace

import pytest
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.indexer import chunk_document_ir
from kazma_core.documents.knowledge import DocumentKnowledgeAdapter, format_document_hits
from kazma_core.documents.models import (
    BlockType,
    BoundingBox,
    DocumentBlock,
    DocumentIR,
    DocumentPage,
    Provenance,
)
from kazma_core.documents.repository import DocumentRepository
from kazma_core.stores.knowledge import KnowledgeStore
from kazma_core.stores.knowledge_index import KnowledgeIndex
from kazma_core.tenant_context import reset_current_tenant_id, set_current_tenant_id


class _NoVector:
    available = False


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _config(tmp_path, **changes) -> DocumentConfig:
    return replace(DocumentConfig(storage_root=tmp_path), **changes)


def _ir(document_id, version_id, source_sha: str, pages) -> DocumentIR:
    return DocumentIR(
        document_id=document_id,
        version_id=version_id,
        pages=tuple(pages),
        provenance=Provenance(
            source="canonical.bin",
            parser="phase6-test-parser",
            parser_version="6.0",
            metadata={"sha256": source_sha},
        ),
        metadata={"source_sha256": source_sha, "language": "en"},
    )


def _setup(tmp_path):
    repository = DocumentRepository(tmp_path / "documents.db", tenant_quota_bytes=100_000)
    store = KnowledgeStore(str(tmp_path / "settings.db"))
    token = set_current_tenant_id("tenant-a")
    try:
        store.create_library("docs", "Documents")
    finally:
        reset_current_tenant_id(token)
    index = KnowledgeIndex(store=store)
    index._vector_stores["docs"] = _NoVector()
    adapter = DocumentKnowledgeAdapter(
        repository=repository,
        knowledge_store=store,
        knowledge_index=index,
        config=_config(tmp_path, indexing_chunk_tokens=24, indexing_overlap_tokens=4),
    )
    document = repository.create_document(
        tenant_id="tenant-a", owner_id="owner", title="Runbook"
    )
    return repository, store, index, adapter, document


def _version(repository, document, value: str):
    digest = _sha(value)
    blob = repository.register_blob(
        tenant_id="tenant-a",
        sha256=digest,
        byte_size=len(value),
        storage_kind="originals",
    )
    version = repository.create_version(
        tenant_id="tenant-a",
        document_id=document.id,
        actor_id="owner",
        source_blob_id=blob.id,
        source_sha256=digest,
        original_filename="runbook.pdf",
        mime_type="application/pdf",
    )
    return version, digest


def test_structural_chunks_preserve_pages_tables_and_ocr_metadata(tmp_path):
    source = _sha("source")
    document = _ir(
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
        source,
        [
            DocumentPage(
                page_number=1,
                blocks=(
                    DocumentBlock(
                        "p1-text",
                        BlockType.PARAGRAPH,
                        "alpha paragraph " * 8,
                    ),
                    DocumentBlock(
                        "p1-table",
                        BlockType.TABLE,
                        "cell | value\n" * 30,
                        bounding_box=BoundingBox(1, 2, 30, 40),
                        confidence=0.81,
                        metadata={"ocr": True, "language": "ara", "direction": "rtl"},
                    ),
                ),
            ),
            DocumentPage(
                page_number=2,
                blocks=(DocumentBlock("p2-text", BlockType.TEXT, "second page"),),
            ),
        ],
    )
    chunks = chunk_document_ir(
        document,
        _config(
            tmp_path,
            indexing_chunk_tokens=20,
            indexing_overlap_tokens=3,
            indexing_preserve_tables=True,
            indexing_preserve_page_boundaries=True,
        ),
    )
    table = next(chunk for chunk in chunks if "p1-table" in chunk.block_ids)
    assert table.content.count("cell | value") == 30
    assert table.page_start == table.page_end == 1
    assert table.coordinates[0]["bounding_box"]["x0"] == 1.0
    assert table.ocr is True
    assert table.language == ("ara",)
    assert table.direction == ("rtl",)
    assert table.confidence == 0.81
    assert all(chunk.page_start == chunk.page_end for chunk in chunks)


def test_chunk_ids_are_deterministic_and_citation_sensitive(tmp_path):
    source = _sha("same")
    pages = [
        DocumentPage(
            page_number=1,
            blocks=(DocumentBlock("p1", BlockType.TEXT, "identical"),),
        ),
        DocumentPage(
            page_number=2,
            blocks=(DocumentBlock("p2", BlockType.TEXT, "identical"),),
        ),
    ]
    document = _ir(
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
        source,
        pages,
    )
    config = _config(tmp_path)
    first = chunk_document_ir(document, config)
    second = chunk_document_ir(document, config)
    assert [item.chunk_id for item in first] == [item.chunk_id for item in second]
    assert first[0].chunk_id != first[1].chunk_id
    assert first[0].block_hashes == first[1].block_hashes
    assert "canonical.bin" not in "\n".join(item.content for item in first)


def test_idempotent_publish_exact_citations_and_single_fence(tmp_path):
    repository, store, index, adapter, document = _setup(tmp_path)
    version, digest = _version(repository, document, "v1")
    ir = _ir(
        document.id,
        version.id,
        digest,
        [
            DocumentPage(
                page_number=1,
                blocks=(
                    DocumentBlock(
                        "p1-ocr",
                        BlockType.TEXT,
                        "rotating credentials safely",
                        confidence=0.9,
                        metadata={"ocr": True, "language": "eng", "direction": "ltr"},
                    ),
                ),
            )
        ],
    )
    first = adapter.index_document_ir(
        ir, tenant_id="tenant-a", actor_id="owner", library_id="docs"
    )
    second = adapter.index_document_ir(
        ir, tenant_id="tenant-a", actor_id="owner", library_id="docs"
    )
    assert first.ok and first.data and first.data.published is True
    assert second.ok and second.data and second.data.published is False
    assert store.count_chunks("docs") == 1
    hits = asyncio.run(
        index.search_document(
            "credentials",
            tenant_id="tenant-a",
            library_id="docs",
            document_id=str(document.id),
        )
    )
    assert hits[0].metadata["block_ids"] == ["p1-ocr"]
    assert hits[0].metadata["ocr"] is True
    assert f"document:{document.id}@{version.id}" in hits[0].citation_label
    prompt = format_document_hits(hits)
    assert prompt.count('<kazma:data source="document_knowledge"') == 1
    assert prompt.count("</kazma:data>") == 1


def test_version_switch_is_active_only_and_history_is_explicit(tmp_path):
    repository, store, index, adapter, document = _setup(tmp_path)
    version1, digest1 = _version(repository, document, "v1")
    ir1 = _ir(
        document.id,
        version1.id,
        digest1,
        [DocumentPage(1, (DocumentBlock("old", BlockType.TEXT, "legacyonly"),))],
    )
    assert adapter.index_document_ir(
        ir1, tenant_id="tenant-a", actor_id="owner", library_id="docs"
    ).ok
    version2, digest2 = _version(repository, document, "v2")
    ir2 = _ir(
        document.id,
        version2.id,
        digest2,
        [DocumentPage(1, (DocumentBlock("new", BlockType.TEXT, "currentonly"),))],
    )
    assert adapter.index_document_ir(
        ir2, tenant_id="tenant-a", actor_id="owner", library_id="docs"
    ).ok
    assert asyncio.run(index.search("legacyonly", "docs", tenant_id="tenant-a")) == []
    current = asyncio.run(index.search("currentonly", "docs", tenant_id="tenant-a"))
    assert current and current[0].version_id == str(version2.id)
    history = store.get_document_chunks(
        tenant_id="tenant-a",
        library_id="docs",
        document_id=str(document.id),
        version_id=str(version1.id),
        include_history=True,
    )
    assert history and history[0]["active"] is False


def test_atomic_version_switch_rolls_back_on_insert_failure(tmp_path):
    repository, store, _index, adapter, document = _setup(tmp_path)
    version1, digest1 = _version(repository, document, "v1")
    ir1 = _ir(
        document.id,
        version1.id,
        digest1,
        [DocumentPage(1, (DocumentBlock("old", BlockType.TEXT, "stableold"),))],
    )
    assert adapter.index_document_ir(
        ir1, tenant_id="tenant-a", actor_id="owner", library_id="docs"
    ).ok
    version2, digest2 = _version(repository, document, "v2")
    base = {
        "id": "duplicate",
        "library_id": "docs",
        "source_url": f"document://{document.id}/{version2.id}",
        "document_title": "Runbook",
        "section_header": "citation",
        "content_hash": _sha("new"),
        "has_code": False,
        "content": "new",
        "metadata": {},
        "document_id": str(document.id),
        "version_id": str(version2.id),
    }
    with pytest.raises(Exception):
        store.publish_document_version(
            tenant_id="tenant-a",
            library_id="docs",
            document_id=str(document.id),
            version_id=str(version2.id),
            source_sha256=digest2,
            chunks=[{**base, "chunk_index": 0}, {**base, "chunk_index": 1}],
        )
    active = store.get_document_chunks(
        tenant_id="tenant-a",
        library_id="docs",
        document_id=str(document.id),
    )
    assert len(active) == 1
    assert active[0]["version_id"] == str(version1.id)


def test_repository_failure_compensates_knowledge_publication(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository, store, index, adapter, document = _setup(tmp_path)
    version1, digest1 = _version(repository, document, "v1")
    ir1 = _ir(
        document.id,
        version1.id,
        digest1,
        [DocumentPage(1, (DocumentBlock("old", BlockType.TEXT, "stableold"),))],
    )
    assert adapter.index_document_ir(
        ir1, tenant_id="tenant-a", actor_id="owner", library_id="docs"
    ).ok
    version2, digest2 = _version(repository, document, "v2")
    ir2 = _ir(
        document.id,
        version2.id,
        digest2,
        [DocumentPage(1, (DocumentBlock("new", BlockType.TEXT, "unstablenew"),))],
    )
    monkeypatch.setattr(
        repository,
        "record_indexed_version",
        lambda **_kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("disk full")),
    )

    result = adapter.index_document_ir(
        ir2, tenant_id="tenant-a", actor_id="owner", library_id="docs"
    )

    assert result.ok is False and result.retryable is True
    assert asyncio.run(index.search("unstablenew", "docs", tenant_id="tenant-a")) == []
    restored = asyncio.run(index.search("stableold", "docs", tenant_id="tenant-a"))
    assert restored and restored[0].version_id == str(version1.id)


def test_tenant_isolation_and_unindex_tombstone(tmp_path):
    repository, store, index, adapter, document = _setup(tmp_path)
    version, digest = _version(repository, document, "v1")
    ir = _ir(
        document.id,
        version.id,
        digest,
        [DocumentPage(1, (DocumentBlock("secret", BlockType.TEXT, "tenantsecret"),))],
    )
    assert adapter.index_document_ir(
        ir, tenant_id="tenant-a", actor_id="owner", library_id="docs"
    ).ok
    assert asyncio.run(index.search("tenantsecret", "docs", tenant_id="tenant-b")) == []
    denied = adapter.unindex_document(
        tenant_id="tenant-b",
        actor_id="owner",
        library_id="docs",
        document_id=document.id,
    )
    assert denied.ok is False
    removed = adapter.unindex_document(
        tenant_id="tenant-a",
        actor_id="owner",
        library_id="docs",
        document_id=document.id,
    )
    assert removed.ok is True
    assert asyncio.run(index.search("tenantsecret", "docs", tenant_id="tenant-a")) == []
    history = store.get_document_chunks(
        tenant_id="tenant-a",
        library_id="docs",
        document_id=str(document.id),
        include_history=True,
    )
    assert history[0]["tombstoned"] is True


def test_knowledge_chunk_schema_migrates_existing_database(tmp_path):
    path = tmp_path / "legacy-settings.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE knowledge_libraries (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
            seed_url TEXT NOT NULL DEFAULT '', auto_inject INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0, chunk_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE knowledge_chunks (
            id TEXT PRIMARY KEY, library_id TEXT NOT NULL, source_url TEXT NOT NULL,
            document_title TEXT NOT NULL DEFAULT '', section_header TEXT NOT NULL DEFAULT '',
            chunk_index INTEGER NOT NULL, content_hash TEXT NOT NULL,
            has_code INTEGER NOT NULL DEFAULT 0, char_count INTEGER NOT NULL DEFAULT 0,
            content TEXT NOT NULL, created_at TEXT NOT NULL
        );
        """
    )
    connection.close()
    store = KnowledgeStore(str(path))
    columns = {
        row[1] for row in store._get_conn().execute("PRAGMA table_info(knowledge_chunks)")
    }
    assert {
        "metadata_json",
        "document_id",
        "version_id",
        "source_sha256",
        "active",
        "tombstoned",
    }.issubset(columns)
