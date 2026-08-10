"""Regression: Postgres metadata quota checks must not raise IndeterminateDatatype."""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        cwd_env = Path.cwd() / ".env"
        user_env = Path(os.environ.get("KAZMA_WORKSPACE", "C:/Users/balfa/kazma")) / ".env"
        if cwd_env.is_file():
            load_dotenv(dotenv_path=cwd_env, override=True)
        if user_env.is_file() and user_env.resolve() != cwd_env.resolve():
            load_dotenv(dotenv_path=user_env, override=True)
    except Exception:
        pass


@pytest.fixture(scope="module")
def pg_pool():
    _load_env()
    try:
        from kazma_core.db.postgres_pool import get_postgres_pool, reset_postgres_pool

        reset_postgres_pool()
        pool = get_postgres_pool()
    except Exception:
        pool = None
    if pool is None:
        pytest.skip("Postgres pool not configured")
    return pool


def test_tenant_references_sha256_with_kind_does_not_raise(pg_pool) -> None:
    from kazma_core.documents.repository_pg import PostgresDocumentRepository

    repo = PostgresDocumentRepository(pg_pool, tenant_quota_bytes=10 * 1024 * 1024)
    digest = "a" * 64
    assert (
        repo.tenant_references_sha256(
            tenant_id="default",
            sha256=digest,
            storage_kind="quarantine",
        )
        is False
    )
    assert (
        repo.tenant_references_sha256(
            tenant_id="default",
            sha256=digest,
            storage_kind=None,
        )
        is False
    )


def test_put_stream_quota_path_accepts_bytes(pg_pool, tmp_path: Path) -> None:
    from kazma_core.documents.repository_pg import PostgresDocumentRepository
    from kazma_core.documents.storage import ContentAddressedStorage

    repo = PostgresDocumentRepository(pg_pool, tenant_quota_bytes=50 * 1024 * 1024)
    storage = ContentAddressedStorage(tmp_path / "cas")
    payload = b"quota-path regression\n"
    stored = storage.put_stream(
        io.BytesIO(payload),
        kind="quarantine",
        max_bytes=1024 * 1024,
        tenant_id="default",
        repository=repo,
        tenant_quota_bytes=50 * 1024 * 1024,
    )
    assert stored.byte_size == len(payload)
    assert len(stored.sha256) == 64
