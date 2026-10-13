"""Real scans, measured through the real intake, OCR and search (opt-in).

2026-10-13. The fidelity corpus is generated; a real scan is not. The first
one supplied (a 300 dpi copier scan of an Arabic study booklet, no text layer)
found two defects no generated case showed -- the OCR parser swallowed rows
after a word that started with '"', and ruled tables lost their numbers and
rows -- and both were fixed against it.

Real documents are not committed (they are someone's, and large). A manifest
outside the repository names each file by path and SHA-256, the pages to
measure, what each page holds -- numbers, symbols, single key terms, a
contents table's rows; never page text -- the searches that must find their
page, and the floors this build reached. Set ``KAZMA_TEST_REAL_SCANS_MANIFEST``
to it; without it the test SKIPS, and a skip is not evidence.
``KAZMA_REAL_SCANS_REPORT`` writes the measurement; raising a manifest's
floors from it is the operator's edit, never automatic.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
from pathlib import Path

import pytest

TENANT = "real-scans"


def _manifest() -> dict:
    path = os.environ.get("KAZMA_TEST_REAL_SCANS_MANIFEST", "")
    if not path:
        pytest.skip("opt-in: set KAZMA_TEST_REAL_SCANS_MANIFEST to a real-scan manifest")
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _has_number(text: str, number: str) -> bool:
    return re.search(r"(?<![\d٠-٩])" + re.escape(number) + r"(?![\d٠-٩])", text) is not None


def measure_page(truth: dict, lines: list[str]) -> dict:
    """Recall of what the page is known to hold; nothing about wording."""
    from kazma_core.documents.arabic import fold_for_search

    text = "\n".join(lines)
    folded = fold_for_search(text)
    out: dict = {}
    if truth.get("terms"):
        found = [t for t in truth["terms"] if fold_for_search(t) in folded]
        out["term_recall"] = round(len(found) / len(truth["terms"]), 3)
    if truth.get("numbers"):
        out["number_recall"] = round(sum(_has_number(text, n) for n in truth["numbers"]) / len(truth["numbers"]), 3)
    if truth.get("symbols"):
        found = [s for s in truth["symbols"] if re.search(r"(?<![A-Za-z])" + re.escape(s) + r"(?![a-z])", text)]
        out["symbol_recall"] = round(len(found) / len(truth["symbols"]), 3)
    rows = truth.get("rows") or []
    if rows:
        def same_line(book, booklet):
            return next((line for line in lines if _has_number(line, str(book)) and _has_number(line, str(booklet))), None)

        paired = [(row, same_line(row[0], row[1])) for row in rows]
        out["toc_book_number_recall"] = round(sum(_has_number(text, str(b)) for b, _, _ in rows) / len(rows), 3)
        out["toc_row_numbers_same_line"] = round(sum(line is not None for _, line in paired) / len(rows), 3)
        out["toc_row_complete_same_line"] = round(
            sum(line is not None and fold_for_search(row[2]) in fold_for_search(line) for row, line in paired)
            / len(rows), 3)
    return out


def _subset(source: Path, pages: list[int], dest: Path) -> Path:
    import pymupdf

    with pymupdf.open(source) as src, pymupdf.open() as out:
        for number in pages:
            out.insert_pdf(src, from_page=number - 1, to_page=number - 1)
        out.save(dest)
    return dest


class _NoVector:
    available = False


def _service(root: Path):
    from kazma_core.documents.config import DocumentConfig
    from kazma_core.documents.ingestion import DocumentIngestionService
    from kazma_core.documents.knowledge import DocumentKnowledgeAdapter
    from kazma_core.stores.knowledge import KnowledgeStore
    from kazma_core.stores.knowledge_index import KnowledgeIndex
    from kazma_core.tenant_context import reset_current_tenant_id, set_current_tenant_id

    store = KnowledgeStore(str(root / "kb.db"))
    token = set_current_tenant_id(TENANT)
    try:
        store.create_library("scans", "Scans")
    finally:
        reset_current_tenant_id(token)
    svc = DocumentIngestionService(config=DocumentConfig(storage_root=root / "content", security_malware_scan="off"))
    index = KnowledgeIndex(store=store)
    index._vector_stores["scans"] = _NoVector()
    adapter = DocumentKnowledgeAdapter(repository=svc.repository, knowledge_store=store, knowledge_index=index,
                                       config=svc._policy.initial, policy=svc._policy)
    svc.knowledge_adapter = adapter
    svc.service.knowledge_adapter = adapter
    return svc


async def _drive(svc) -> None:
    from kazma_core.documents.worker import DocumentWorker

    worker = DocumentWorker(svc.jobs, svc.stage_handlers())
    for _ in range(40):
        claimed = svc.jobs.claim_next(owner="real-scans", lease_seconds=1800, tenant_id=TENANT)
        if claimed is None:
            return
        await worker._process_claimed(claimed, "real-scans")


def measure_document(entry: dict, work: Path) -> dict:
    from kazma_core.documents.ingestion import SYSTEM_ACTOR
    from kazma_core.documents.models import DocumentIR

    source = Path(entry["path"])
    assert hashlib.sha256(source.read_bytes()).hexdigest() == entry["sha256"], "the scan is not the one measured"
    page_map = sorted(int(p) for p in entry["pages"])
    pdf = _subset(source, page_map, work / "subset.pdf")
    svc = _service(work)
    try:
        result = svc.ingest_stream(io.BytesIO(pdf.read_bytes()), filename="scan.pdf", tenant_id=TENANT,
                                   actor_id="operator", workspace_id="w")
        asyncio.run(_drive(svc))
        state = svc.job_status(tenant_id=TENANT, job_id=result.job_id, actor_id=SYSTEM_ACTOR)["state"]
        record = svc.repository.get_document(tenant_id=TENANT, document_id=result.document_id, actor_id="operator")
        manifest = svc.storage.read_manifest(document_id=result.document_id, version_id=record.current_version_id)
        ir = DocumentIR.from_dict(manifest["ir"])
        pages = {}
        for page in ir.pages:
            original = str(page_map[page.page_number - 1])
            lines = [line for block in page.blocks if block.text for line in block.text.splitlines()]
            pages[original] = measure_page(entry["pages"][original], lines)
        svc.index_document(tenant_id=TENANT, actor_id="operator", document_id=result.document_id, library_id="scans")
        searches = []
        for query, expected in entry.get("searches", []):
            hits = svc.knowledge_adapter.index.search_document_sync(
                query, tenant_id=TENANT, library_id="scans", document_id=str(result.document_id), top_k=3)
            pages_hit = [page_map[int((h.metadata or {}).get("page_start") or 1) - 1] for h in hits]
            searches.append({"expected": int(expected), "top": pages_hit[0] if pages_hit else None})
        return {"state": state, "pages": pages, "searches": searches}
    finally:
        svc.close()


def test_real_scans_hold_their_floors(tmp_path):
    manifest = _manifest()
    report, failures = {}, []
    for index, entry in enumerate(manifest["documents"]):
        work = tmp_path / f"doc-{index}"
        work.mkdir()
        measured = measure_document(entry, work)
        report[entry["id"]] = measured
        if measured["state"] != "ready":
            failures.append(f"{entry['id']}: ended {measured['state']}")
        for page, floors in (entry.get("floors") or {}).items():
            for metric, floor in floors.items():
                got = measured["pages"].get(page, {}).get(metric)
                if got is None or got < floor:
                    failures.append(f"{entry['id']} p{page} {metric}: {got} < {floor}")
        for search in measured["searches"]:
            if search["top"] != search["expected"]:
                failures.append(f"{entry['id']}: a search found page {search['top']}, not {search['expected']}")
    out = os.environ.get("KAZMA_REAL_SCANS_REPORT")
    if out:
        Path(out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    assert not failures, failures
