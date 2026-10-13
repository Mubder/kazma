"""The coordinated document backup names exactly the tables the store makes.

2026-10-13 (DOC-A13, found by the Documents documentation review).
``backup_pg.DOCUMENT_PG_TABLES`` is what the point-in-time document backup
dumps and what its restore counts row by row. Only ``KAZMA_PG_TABLES`` (the
whole-database dump) was checked against the document DDL, so a new document
table could be left out of the coordinated backup -- restored nowhere -- or a
dropped one kept, which fails every backup.

Two gates: the tables the Postgres DDL modules create (static, every CI job),
and the tables a fresh database actually holds after every document store has
started on it (real Postgres). Negative control: a list one table short.
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import pytest

DOCUMENTS = Path(__file__).resolve().parents[1] / "kazma-core" / "kazma_core" / "documents"
#: The modules holding Postgres document DDL (``audit.py``'s tables are
#: SQLite's: on Postgres the audit lives in ``repository_pg``).
_PG_DDL = ("repository_pg.py", "jobs_pg.py", "admission.py")


def _declared_by_ddl() -> set[str]:
    names: set[str] = set()
    for name in _PG_DDL:
        names.update(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", (DOCUMENTS / name).read_text(encoding="utf-8")))
    return names


def _difference(listed, actual) -> dict[str, list[str]]:
    return {
        "missing_from_backup": sorted(set(actual) - set(listed)),
        "not_created": sorted(set(listed) - set(actual)),
    }


def test_the_backup_lists_every_table_the_ddl_creates():
    from kazma_core.db.pg_backup import KAZMA_PG_TABLES
    from kazma_core.documents.backup_pg import DOCUMENT_PG_TABLES

    assert _difference(DOCUMENT_PG_TABLES, _declared_by_ddl()) == {"missing_from_backup": [], "not_created": []}
    assert len(set(DOCUMENT_PG_TABLES)) == len(DOCUMENT_PG_TABLES)
    assert not set(DOCUMENT_PG_TABLES) - set(KAZMA_PG_TABLES), "the whole-database dump must hold them too"


def test_a_list_one_table_short_is_caught():
    # Negative control.
    from kazma_core.documents.backup_pg import DOCUMENT_PG_TABLES

    short = tuple(t for t in DOCUMENT_PG_TABLES if t != "document_intake_mutex")
    assert _difference(short, _declared_by_ddl())["missing_from_backup"] == ["document_intake_mutex"]


@pytest.mark.postgres
def test_a_started_store_holds_exactly_the_listed_tables():
    psycopg = pytest.importorskip("psycopg")
    base = os.environ.get("KAZMA_DATABASE_URL", "")
    if not base or os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
        pytest.skip("Postgres not configured")
    from kazma_core.db.postgres_pool import PostgresPool
    from kazma_core.documents.admission import IntakeAdmissionStore
    from kazma_core.documents.audit import DocumentAuditStore
    from kazma_core.documents.backup_pg import DOCUMENT_PG_TABLES
    from kazma_core.documents.jobs_pg import PostgresDocumentJobRepository
    from kazma_core.documents.repository_pg import PostgresDocumentRepository

    prefix, _, _ = base.rpartition("/")
    name = f"kazma_doctables_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    dsn = f"{prefix}/{name}"
    try:
        pool = PostgresPool(dsn, min_size=1, max_size=2)
        try:
            repository = PostgresDocumentRepository(pool, tenant_quota_bytes=1 << 30)
            jobs = PostgresDocumentJobRepository(pool)
            IntakeAdmissionStore(jobs)
            DocumentAuditStore(repository)
        finally:
            pool.close()
        with psycopg.connect(dsn, autocommit=True) as conn:
            actual = {row[0] for row in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'").fetchall()}
        assert _difference(DOCUMENT_PG_TABLES, actual) == {"missing_from_backup": [], "not_created": []}
    finally:
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
