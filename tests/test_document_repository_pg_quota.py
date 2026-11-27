"""Regression: Postgres metadata quota checks must not raise IndeterminateDatatype.

Runs only when the ENVIRONMENT supplies a Postgres DSN (the CI Postgres job,
or a deliberate run against a throwaway container). It used to load the
working directory's `.env` and then the operator's LIVE install `.env`
(a hard-coded C:/Users/... path) with override=True -- the root conftest's
load_dotenv stub is all that kept it off the live database.
tests/test_postgres_suite.py now refuses any test that loads a real `.env`.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests._as_request import tests_own

# Verified against a real Postgres; the CI Postgres job runs every test
# carrying this marker (scripts/postgres_suite.py).
pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def _the_tests_own_rows() -> Iterator[None]:
    """A store test: what it seeds, reads and deletes is its own, of no space
    -- the install's (S4-01 step 5). The doors of the store's callers are the
    flow tests' and the door gates' (tests/test_row_work_gates.py)."""
    with tests_own():
        yield


@pytest.fixture(scope="module")
def pg_pool():
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
    storage = ContentAddressedStorage(tmp_path / "cas").for_space("default")
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
