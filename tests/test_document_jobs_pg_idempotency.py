"""Live-Postgres regressions: job enqueue idempotency + storage accounting SQL.

Requires KAZMA_DATABASE_URL (loaded from .env). Skips when no pool is
available, so these are inert on SQLite-only machines.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _load_env() -> None:
    # tests/conftest.py stubs dotenv.load_dotenv to a no-op (tests must not
    # load the real .env), so parse the DSN manually. These PG tests are
    # opt-in: they only run when a local .env carries KAZMA_DATABASE_URL.
    env_path = Path.cwd() / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key in ("KAZMA_DATABASE_URL", "KAZMA_DB_BACKEND") and value:
            os.environ[key] = value


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


def test_enqueue_idempotent_replay(pg_pool) -> None:
    from kazma_core.documents.jobs_pg import PostgresDocumentJobRepository
    from kazma_core.documents.models import new_document_id, new_version_id

    repo = PostgresDocumentJobRepository(pg_pool)
    tenant = f"t-{os.urandom(4).hex()}"
    doc = new_document_id()
    ver = new_version_id()

    first = repo.enqueue(
        tenant_id=tenant,
        workspace_id="ws-1",
        document_id=doc,
        version_id=ver,
        idempotency_key="same-key",
        max_attempts=3,
    )
    second = repo.enqueue(
        tenant_id=tenant,
        workspace_id="ws-1",
        document_id=doc,
        version_id=ver,
        idempotency_key="same-key",
        max_attempts=3,
    )
    # Replay must return the SAME job — not raise UniqueViolation (the pre-fix
    # behavior under a concurrent duplicate enqueue).
    assert str(second.id) == str(first.id)


def test_enqueue_conflicting_request_raises(pg_pool) -> None:
    from kazma_core.documents.jobs import JobConflictError
    from kazma_core.documents.jobs_pg import PostgresDocumentJobRepository
    from kazma_core.documents.models import new_document_id, new_version_id

    repo = PostgresDocumentJobRepository(pg_pool)
    tenant = f"t-{os.urandom(4).hex()}"
    repo.enqueue(
        tenant_id=tenant,
        workspace_id="ws-1",
        document_id=new_document_id(),
        version_id=new_version_id(),
        idempotency_key="same-key",
        max_attempts=3,
    )
    with pytest.raises(JobConflictError):
        repo.enqueue(
            tenant_id=tenant,
            workspace_id="ws-1",
            document_id=new_document_id(),
            version_id=new_version_id(),
            idempotency_key="same-key",
            max_attempts=3,
        )


def test_storage_bytes_sql_on_postgres(pg_pool) -> None:
    """The _storage_bytes SQL must run on PG and return dict rows.

    Previously the metrics path reached into repository._conn (None on the
    Postgres backend) and silently reported 0 bytes. This pins that the query
    and its (dict) row shape work against the live schema.
    """
    from kazma_core.documents.repository_pg import PostgresDocumentRepository

    PostgresDocumentRepository(pg_pool, tenant_quota_bytes=10 * 1024 * 1024)
    _sql = """
        SELECT
          COALESCE(SUM(byte_size), 0) AS logical,
          COALESCE(SUM(CASE WHEN rn = 1 THEN byte_size ELSE 0 END), 0) AS physical
        FROM (
          SELECT byte_size,
                 ROW_NUMBER() OVER (PARTITION BY sha256, storage_kind ORDER BY id) AS rn
          FROM document_blobs
        )
    """
    with pg_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_sql)
            row = cur.fetchone()
        conn.commit()
    # Dict-row access (the shape the fixed _storage_bytes branch expects).
    # The shared DB may already hold blobs from real app use, so assert the
    # values parse as non-negative ints — the point is the query runs and
    # returns dict rows, not that the DB is empty.
    assert int(row["logical"]) >= 0
    assert int(row["physical"]) >= 0
