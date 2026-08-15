"""Phase 4.3 database backend detection."""

from __future__ import annotations

import dotenv
import pytest

from kazma_core.db.backend import DatabaseBackend, get_backend, get_database_url, is_postgres

# The root conftest's production-database shield (2026-08-14 incident) forces
# KAZMA_DB_BACKEND=sqlite and strips/blocks every DSN for the whole test
# process, so the Postgres-selection branches below can never be exercised
# in-process. Detect the shield via its neutered dotenv loader (a no-op
# lambda) and skip the Postgres cases with the reason. sqlite cases still run.
_SHIELD_ACTIVE = getattr(dotenv.load_dotenv, "__name__", "") == "<lambda>"


def test_default_sqlite(monkeypatch):
    monkeypatch.delenv("KAZMA_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("KAZMA_DB_BACKEND", raising=False)
    assert get_backend() == DatabaseBackend.SQLITE
    assert is_postgres() is False


@pytest.mark.skipif(
    _SHIELD_ACTIVE,
    reason="root conftest production-DB shield forces sqlite + strips DSNs; "
           "Postgres backend selection can't be tested in-process",
)
def test_url_selects_postgres(monkeypatch):
    monkeypatch.setenv("KAZMA_DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.delenv("KAZMA_DB_BACKEND", raising=False)
    assert get_backend() == DatabaseBackend.POSTGRES
    assert is_postgres() is True
    assert "postgresql" in (get_database_url() or "")


def test_force_sqlite_even_with_url(monkeypatch):
    monkeypatch.setenv("KAZMA_DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setenv("KAZMA_DB_BACKEND", "sqlite")
    assert get_backend() == DatabaseBackend.SQLITE
