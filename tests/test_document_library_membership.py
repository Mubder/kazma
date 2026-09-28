"""A document can be taken out of a Knowledge library, and a delete that
cannot take it out does not happen (2026-09-28).

The Documents page could add a document to a library and never take it out
(the unindex route existed; nothing called it), and the page did not say
which libraries a document was in. And a delete that failed to remove the
document from a library carried on silently: the document was archived while
chat could still quote it.

- ``DocumentKnowledgeAdapter.libraries_holding`` is the one answer to "which
  libraries hold this document" (the document store's chunk record and the
  library's own), for the detail view and for delete;
- the detail carries ``libraries`` ([{id, name}], or None when unknown);
- delete stops with ``document_unindex_failed`` when any library refuses.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from kazma_core.documents.models import BlockType, DocumentBlock, DocumentPage
from kazma_core.documents.service import DocumentService

from tests.test_document_knowledge_phase6 import _ir, _setup, _version

REPO = Path(__file__).resolve().parents[1]


def _indexed(tmp_path):
    repository, store, index, adapter, document = _setup(tmp_path)
    version, digest = _version(repository, document, "v1")
    ir = _ir(document.id, version.id, digest,
             [DocumentPage(1, (DocumentBlock("b1", BlockType.TEXT, "rotation schedule"),))])
    assert adapter.index_document_ir(ir, tenant_id="tenant-a", actor_id="owner", library_id="docs").ok
    return repository, store, adapter, document


def test_the_libraries_holding_a_document(tmp_path) -> None:
    repository, store, adapter, document = _indexed(tmp_path)
    assert adapter.libraries_holding(tenant_id="tenant-a", document_id=document.id) == ["docs"]
    assert adapter.libraries_holding(tenant_id="tenant-b", document_id=document.id) == []
    assert adapter.unindex_document(
        tenant_id="tenant-a", actor_id="owner", library_id="docs", document_id=document.id
    ).ok
    assert adapter.libraries_holding(tenant_id="tenant-a", document_id=document.id) == []


def test_the_detail_names_them_and_says_when_it_cannot(tmp_path) -> None:
    from kazma_core.documents.ingestion import DocumentIngestionService

    repository, store, adapter, document = _indexed(tmp_path)
    ingest = SimpleNamespace(service=SimpleNamespace(knowledge_adapter=adapter))
    got = DocumentIngestionService._libraries_holding(ingest, "tenant-a", document.id)
    assert got == [{"id": "docs", "name": "Documents"}]

    # No knowledge wiring, or a lookup that fails: unknown, never "in none".
    none = SimpleNamespace(service=SimpleNamespace(knowledge_adapter=None))
    assert DocumentIngestionService._libraries_holding(none, "tenant-a", document.id) is None

    class Broken:
        def libraries_holding(self, **_kw):
            raise RuntimeError("store down")

    broken = SimpleNamespace(service=SimpleNamespace(knowledge_adapter=Broken()))
    assert DocumentIngestionService._libraries_holding(broken, "tenant-a", document.id) is None


def test_a_delete_that_cannot_unindex_deletes_nothing(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository, store, adapter, document = _indexed(tmp_path)
    service = DocumentService(repository=repository, knowledge_adapter=adapter)

    def refuse(**_kw):
        raise RuntimeError("index locked")

    monkeypatch.setattr(adapter, "unindex_document", refuse)
    result = service.delete_document(
        tenant_id="tenant-a", actor_id="owner", document_id=document.id, reason="test"
    )
    assert result.ok is False and result.code == "document_unindex_failed"
    assert "docs" in result.message
    # Nothing was deleted: the document is still there and still in its library.
    assert repository.get_document(tenant_id="tenant-a", document_id=document.id, actor_id="owner")
    assert adapter.libraries_holding(tenant_id="tenant-a", document_id=document.id) == ["docs"]

    monkeypatch.undo()
    retried = service.delete_document(
        tenant_id="tenant-a", actor_id="owner", document_id=document.id, reason="test"
    )
    assert retried.ok is True and retried.data["libraries"] == ["docs"]
    assert adapter.libraries_holding(tenant_id="tenant-a", document_id=document.id) == []


def test_the_page_offers_the_way_out() -> None:
    html = (REPO / "kazma-ui/kazma_ui/templates/documents.html").read_text(encoding="utf-8")
    js = (REPO / "kazma-ui/kazma_ui/static/js/documents.js").read_text(encoding="utf-8")
    block = html[html.index('class="doc-libraries"'):]
    block = block[: block.index("</template>")]
    assert "removeFromLibrary(lib)" in block and 'x-for="lib in (inLibraries || [])"' in block
    method = js[js.index("async removeFromLibrary(lib)"):]
    method = method[: method.index("\n    },")]
    assert "/unindex" in method and "library_id: lib.id" in method
    assert "this.inLibraries = Array.isArray(doc.libraries)" in js
    # Opening another document clears the last one's list first, so a quick
    # click cannot remove the new document from the old one's library.
    opener = js[js.index("async openDocument(documentId)"):]
    opener = opener[: opener.index("await this.refreshDetail();")]
    assert "this.inLibraries = null;" in opener
