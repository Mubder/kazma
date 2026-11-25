"""Whose each statement is, at the real Postgres pool's checkout (S4-01 step 0a).

``tests/_space_door.py`` asks at ``PostgresPool.connection`` what the second
lock will ask there. Each test fails on the code before step 0a, where the
checkout answers nothing, or the space of whoever started the work.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest
from kazma_core.spaces import scope
from kazma_core.spaces.scope import bind_scope

from tests._space_door import Calls, watched_pool

pytestmark = pytest.mark.postgres


@pytest.fixture
def pool(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    dsn = os.environ.get("KAZMA_DATABASE_URL") or ""
    if not dsn or os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
        pytest.skip("needs a throwaway Postgres (scripts/postgres_suite.py)")
    pytest.importorskip("psycopg")
    from kazma_core.db.postgres_pool import PostgresPool

    made = PostgresPool(dsn, min_size=1, max_size=3)
    monkeypatch.setattr("kazma_core.db.postgres_pool.get_postgres_pool", lambda: made)
    try:
        yield made
    finally:
        made.close()


@pytest.fixture
def enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scope, "_enforced", True)


def _said(calls: Calls) -> set[str]:
    return {c.space for c in calls}


# ── probes and schema ────────────────────────────────────────────────────


@pytest.mark.usefixtures("enforced")
def test_probes_and_schemas_are_the_installs(pool: Any) -> None:
    from kazma_core.db import pg_backup
    from kazma_core.documents.audit import DocumentAuditStore
    from kazma_core.documents.jobs_pg import PostgresDocumentJobRepository
    from kazma_core.documents.repository_pg import PostgresDocumentRepository
    from kazma_core.security import service_exposure

    for bound in (False, True):
        with watched_pool() as calls:
            def probe() -> None:
                documents = PostgresDocumentRepository(pool, tenant_quota_bytes=1_000_000)
                PostgresDocumentJobRepository(pool)
                DocumentAuditStore(documents)
                assert "documents" not in (pg_backup.verify_required_pg_tables(pool) or [])
                assert service_exposure._role_answer()["rolname"]

            if bound:
                with bind_scope("family", "layla"):
                    probe()
            else:
                probe()
        assert len(calls) >= 5 and _said(calls) == {"<install>"}


def test_a_real_refusal_is_one_and_an_away_server_is_not(pool: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.db import pg_helpers
    from kazma_core.db.postgres_pool import PostgresPool

    def refused() -> BaseException:
        try:
            with pool.connection() as conn:
                conn.execute("SET LOCAL ROLE pg_read_all_settings")
                conn.execute("SELECT rolpassword FROM pg_authid")
        except Exception as exc:
            return exc
        raise AssertionError("Postgres allowed it")

    exc = refused()
    assert not pg_helpers.is_space_refusal(exc), "Spaces off: as before"
    monkeypatch.setattr(scope, "_enforced", True)
    assert pg_helpers.is_space_refusal(exc)
    with pytest.raises(Exception) as away:
        with PostgresPool("postgresql://demo@127.0.0.1:1/none", min_size=0, max_size=1).connection(timeout=2):
            pass
    assert not pg_helpers.is_space_refusal(away.value)
