"""Phase 4.3 database backend detection."""

from __future__ import annotations

from kazma_core.db.backend import DatabaseBackend, get_backend, get_database_url, is_postgres


def test_default_sqlite(monkeypatch):
    monkeypatch.delenv("KAZMA_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("KAZMA_DB_BACKEND", raising=False)
    assert get_backend() == DatabaseBackend.SQLITE
    assert is_postgres() is False


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
