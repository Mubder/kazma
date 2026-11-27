"""A document service on Postgres closes the SQLite stores it opened beside it (S4-01 step 5).

With Postgres metadata the service still opens the SQLite metadata store the
resolver did not take, and a sidecar SQLite store for the job queue's
fallback. ``close()`` closed only the Postgres repository: both connections
stayed open for the life of the process, and on Windows their folder could
not be removed (the space matrix run on Postgres found it).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from kazma_core.documents import repository_pg
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.ingestion import DocumentIngestionService
from kazma_core.documents.repository import DocumentRepository


class _AsPostgres(DocumentRepository):
    """What the resolver answers on Postgres, on a file of its own."""

    backend_name = "postgres"


def _closed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    real = DocumentRepository.close

    def close(self: Any) -> None:
        seen.append(self._db_path.name)
        real(self)

    monkeypatch.setattr(DocumentRepository, "close", close)
    return seen


def _service(root: Path) -> DocumentIngestionService:
    return DocumentIngestionService(config=DocumentConfig(
        storage_root=root, security_malware_scan="off", security_local_salvage=False, ocr_enabled=False,
    ))


def test_a_service_on_postgres_closes_what_it_opened_beside_it(tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KAZMA_DOCUMENTS_JOBS_BACKEND", raising=False)
    made = _AsPostgres(tmp_path / "as-postgres.db", tenant_quota_bytes=1 << 30)
    monkeypatch.setattr(repository_pg, "resolve_document_repository", lambda sqlite_repo, **_k: made)
    closed = _closed(monkeypatch)
    svc = _service(tmp_path)
    assert svc.repository is made
    svc.close()
    assert sorted(closed) == ["as-postgres.db", "documents-jobs-fallback.db", "documents.db"]


def test_on_sqlite_the_one_store_is_closed_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The control: on SQLite the metadata store is the job queue's too, and
    nothing was opened beside it."""
    monkeypatch.setattr(repository_pg, "resolve_document_repository", lambda sqlite_repo, **_k: sqlite_repo)
    closed = _closed(monkeypatch)
    _service(tmp_path).close()
    assert closed == ["documents.db"]
    assert not (tmp_path / "documents-jobs-fallback.db").exists()


def test_a_repository_the_caller_gave_is_not_closed_as_one_opened_beside(tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    mine = DocumentRepository(tmp_path / "callers.db", tenant_quota_bytes=1 << 30)
    made = _AsPostgres(tmp_path / "as-postgres.db", tenant_quota_bytes=1 << 30)
    monkeypatch.setattr(repository_pg, "resolve_document_repository", lambda sqlite_repo, **_k: made)
    closed = _closed(monkeypatch)
    DocumentIngestionService(config=DocumentConfig(storage_root=tmp_path, security_malware_scan="off",
                                                   security_local_salvage=False, ocr_enabled=False),
                             repository=mine).close()
    assert "callers.db" not in closed and sorted(closed) == ["as-postgres.db", "documents-jobs-fallback.db"]
    mine.close()
