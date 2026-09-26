"""A remote vector store is used, and reported, only when it can hold vectors.

Live 2026-09-25: the install's Postgres ran ``postgres:16-alpine``, which has
no pgvector. The DSN auto-selected pgvector anyway; its liveness probe was
``SELECT 1``, which that server passes, so every memory search, upsert and
delete opened a connection and sent a statement Postgres refused — 142
``CREATE TABLE``s and 63 ``DELETE``s in a week, logged only at DEBUG — while
Settings said "search + upsert enabled (URL configured)". Recall kept working
only because it fell back to sqlite-vec. The Settings "Test" button could not
have caught it: it sent an HTTP GET to the ``postgresql://`` DSN.

These pin the probe (catalog state, not connectivity), the one WARNING it
logs, the doors it closes (search, upsert, delete), the DDL that must not
roll back the table with a refused extension or index, and the status that
Settings, the memory health check and the Test button read.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

import pytest
from kazma_core.memory import backends
from kazma_core.memory.backends import PgvectorBackend, QdrantVectorBackend

_DSN = "postgresql://kazma:s3cret-pw@db.internal:5432/kazma"
_LOGGER = "kazma_core.memory.backends"
_REAL_DRIVER_ERRORS = backends._pg_driver_errors


class _PgError(Exception):
    """Stands in for ``psycopg.Error`` (the CI unit job has no driver)."""


@pytest.fixture(autouse=True)
def _fresh_probe_state(monkeypatch):
    backends._REMOTE_VECTOR_STATE.clear()
    backends._PGVECTOR_TABLES_READY.clear()
    backends._PGVECTOR_INDEX_WARNED.clear()
    monkeypatch.setattr(backends, "_pg_driver_errors", lambda: (_PgError,))
    yield
    backends._REMOTE_VECTOR_STATE.clear()
    backends._PGVECTOR_TABLES_READY.clear()
    backends._PGVECTOR_INDEX_WARNED.clear()


class _Server:
    """What the fake Postgres holds, shared by every connection to it."""

    def __init__(self, *, installed: bool = False, installable: bool = False,
                 refuse: tuple[str, ...] = (), may_create: bool = True,
                 table_dim: int | None = None) -> None:
        self.installed = installed
        self.installable = installable
        self.refuse = refuse
        self.may_create = may_create
        self.table_dim = table_dim  # None: no table yet
        self.statements: list[str] = []
        self.connects = 0
        self.commits = 0
        self.rollbacks = 0


class _Cur:
    def __init__(self, server: _Server) -> None:
        self._server = server
        self._row: tuple[Any, ...] | None = None

    def execute(self, sql: str, params: Any = None) -> None:
        import re

        text = " ".join(sql.split())
        server = self._server
        server.statements.append(text)
        for bad in server.refuse:
            if bad in text:
                raise _PgError(f"refused: {bad}\nDETAIL: more")
        if "pg_available_extensions" in text:
            self._row = (server.installed, server.installable, server.may_create,
                         server.table_dim, server.table_dim is not None)
        elif "FROM pg_extension" in text:
            self._row = (server.installed,)
        elif text.startswith("CREATE EXTENSION"):
            server.installed = True
        elif text.startswith("CREATE TABLE") and server.table_dim is None:
            server.table_dim = int(re.search(r"vector\((\d+)\)", text).group(1))

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row

    def fetchall(self) -> list[tuple[Any, ...]]:
        return [("row-1", 0.9)]

    def close(self) -> None:
        return None


class _Conn:
    def __init__(self, server: _Server) -> None:
        self._server = server

    def cursor(self) -> _Cur:
        return _Cur(self._server)

    def commit(self) -> None:
        self._server.commits += 1

    def rollback(self) -> None:
        self._server.rollbacks += 1

    def close(self) -> None:
        return None


def _backend(monkeypatch, server: _Server, dsn: str = _DSN) -> PgvectorBackend:
    be = PgvectorBackend(dsn=dsn, collection="kazma_memory", dimension=3)

    def _connect() -> _Conn:
        server.connects += 1
        return _Conn(server)

    monkeypatch.setattr(be, "_connect", _connect)
    return be


def _warnings(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == _LOGGER and r.levelno >= logging.WARNING]


# ── the probe ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("installed", "installable", "state", "usable"),
    [
        (True, True, "installed", True),
        (False, True, "installable", True),
        (False, False, "missing", False),
    ],
)
def test_the_probe_reads_the_extension_catalog(monkeypatch, installed, installable, state, usable):
    be = _backend(monkeypatch, _Server(installed=installed, installable=installable))
    assert be.probe() == state
    assert be.available is usable


def test_select_one_is_not_the_probe(monkeypatch):
    """The old probe: a server that answers is not a server that holds vectors."""
    server = _Server()
    be = _backend(monkeypatch, server)
    assert be.available is False
    assert all(s != "SELECT 1" for s in server.statements)
    assert any("pg_extension" in s for s in server.statements)


def test_an_unreachable_server_is_unavailable(monkeypatch):
    be = PgvectorBackend(dsn=_DSN, dimension=3)

    def _refused() -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(be, "_connect", _refused)
    assert be.probe() == "unreachable"
    assert be.available is False


def test_a_driver_error_is_unreachable_not_a_crash(monkeypatch):
    be = PgvectorBackend(dsn=_DSN, dimension=3)

    def _refused() -> None:
        raise _PgError("could not connect")

    monkeypatch.setattr(be, "_connect", _refused)
    assert be.probe() == "unreachable"


def test_the_probe_is_cached_across_instances_and_force_refreshes(monkeypatch):
    """Backends are built per call; a per-instance cache probed every search."""
    server = _Server(installed=True)
    first = _backend(monkeypatch, server)
    second = _backend(monkeypatch, server)
    assert first.probe() == "installed"
    assert second.probe() == "installed"
    assert server.connects == 1
    server.installed = False
    assert second.probe() == "installed"  # still inside the TTL
    assert second.probe(force=True) == "missing"
    assert server.connects == 2


def test_the_probe_expires(monkeypatch):
    server = _Server(installed=True)
    be = _backend(monkeypatch, server)
    be.probe()
    checked_at, state = backends._REMOTE_VECTOR_STATE[("pgvector", _DSN)]
    backends._REMOTE_VECTOR_STATE[("pgvector", _DSN)] = (
        checked_at - backends._READY_PROBE_TTL - 1,
        state,
    )
    be.probe()
    assert server.connects == 2


# ── what it says ──────────────────────────────────────────────────────


def test_a_missing_extension_warns_once_and_names_the_fix(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    be = _backend(monkeypatch, _Server())
    for _ in range(3):
        be.probe(force=True)
        assert be.available is False
    warned = _warnings(caplog)
    assert len(warned) == 1
    text = warned[0].getMessage()
    assert "no 'vector' extension" in text
    assert "pgvector/pgvector:pg16" in text
    assert "KAZMA_PGVECTOR=0" in text
    assert "s3cret-pw" not in text and "db.internal" not in text


def test_recovery_is_announced_and_a_relapse_warns_again(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    server = _Server()
    be = _backend(monkeypatch, server)
    be.probe(force=True)
    server.installed = True
    assert be.probe(force=True) == "installed"
    recovered = [
        r for r in caplog.records
        if r.name == _LOGGER and r.levelno == logging.INFO and "usable again" in r.getMessage()
    ]
    assert len(recovered) == 1
    server.installed = False
    be.probe(force=True)
    assert len(_warnings(caplog)) == 2


def test_a_healthy_store_logs_nothing(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    be = _backend(monkeypatch, _Server(installed=True))
    be.probe(force=True)
    be.probe(force=True)
    assert [r for r in caplog.records if r.name == _LOGGER and r.levelno >= logging.INFO] == []


# ── the doors ─────────────────────────────────────────────────────────


def test_no_statement_reaches_a_server_without_pgvector(monkeypatch):
    """Search, upsert and delete stop at the probe: one catalog query, nothing else."""
    server = _Server()
    be = _backend(monkeypatch, server)
    assert be.search([0.1, 0.2, 0.3], tier=None) == []
    assert be.upsert("ep-1", [0.1, 0.2, 0.3], meta={"tier": "episodic"}) is False
    assert be.delete("ep-1") is False
    assert server.connects == 1
    assert len(server.statements) == 1
    assert not any(s.startswith(("CREATE", "INSERT", "DELETE", "SELECT id")) for s in server.statements)


def test_a_usable_store_is_written_searched_and_deleted(monkeypatch):
    server = _Server(installed=True)
    be = _backend(monkeypatch, server)
    assert be.upsert("ep-1", [0.1, 0.2, 0.3], meta={"tier": "episodic"}) is True
    assert be.search([0.1, 0.2, 0.3], tier=None) == [("row-1", 0.9)]
    assert be.delete("ep-1") is True
    assert any(s.startswith("INSERT INTO kazma_memory") for s in server.statements)
    assert any(s.startswith("DELETE FROM kazma_memory") for s in server.statements)


def test_the_table_is_ensured_once_per_process(monkeypatch):
    """Three DDL statements and a commit on every call took a ShareLock each search."""
    server = _Server(installed=True)
    be = _backend(monkeypatch, server)
    be.upsert("ep-1", [0.1, 0.2, 0.3])
    be.upsert("ep-2", [0.1, 0.2, 0.3])
    be.search([0.1, 0.2, 0.3], tier=None)
    assert sum(1 for s in server.statements if s.startswith("CREATE TABLE")) == 1


def test_a_failed_call_re_ensures_the_table(monkeypatch):
    """A table dropped underneath (2026-08-14) is recreated, not failed forever."""
    server = _Server(installed=True)
    be = _backend(monkeypatch, server)
    be.upsert("ep-1", [0.1, 0.2, 0.3])
    server.refuse = ("INSERT INTO",)
    assert be.upsert("ep-2", [0.1, 0.2, 0.3]) is False
    server.refuse = ()
    assert be.upsert("ep-3", [0.1, 0.2, 0.3]) is True
    assert sum(1 for s in server.statements if s.startswith("CREATE TABLE")) == 2


def test_a_refused_extension_marks_the_store_not_permitted(monkeypatch, caplog):
    """The role may not CREATE EXTENSION: say so once, stop trying, keep the answer."""
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    server = _Server(installable=True, refuse=("CREATE EXTENSION",))
    be = _backend(monkeypatch, server)
    assert be.available is True  # installable: worth one try
    assert be.upsert("ep-1", [0.1, 0.2, 0.3]) is False
    assert be.probe(force=True) == "not_permitted"  # the catalog still says installable
    assert be.available is False
    assert not any(s.startswith("CREATE TABLE") for s in server.statements)
    warned = _warnings(caplog)
    assert len(warned) == 1 and "CREATE EXTENSION vector" in warned[0].getMessage()
    # A superuser installs it: the next probe sees it and the store works.
    server.installed = True
    server.refuse = ()
    assert be.probe(force=True) == "installed"
    assert be.upsert("ep-2", [0.1, 0.2, 0.3]) is True


def test_a_role_that_may_not_create_the_table_is_not_permitted(monkeypatch, caplog):
    """The extension is there but the table cannot be made: every call used to
    re-run the refused CREATE TABLE at DEBUG."""
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    server = _Server(installed=True, may_create=False)
    be = _backend(monkeypatch, server)
    assert be.probe() == "not_permitted"
    assert be.upsert("ep-1", [0.1, 0.2, 0.3]) is False
    assert not any(s.startswith("CREATE") for s in server.statements)
    warned = _warnings(caplog)
    assert len(warned) == 1 and "no CREATE on the schema" in warned[0].getMessage()
    # A table someone else made is usable without the privilege.
    server.table_dim = 3
    assert be.probe(force=True) == "installed"


def test_a_table_of_another_vector_size_is_not_used(monkeypatch, caplog):
    """CREATE TABLE IF NOT EXISTS never resizes it; each write was refused."""
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    server = _Server(installed=True, table_dim=1024)
    be = _backend(monkeypatch, server)  # dimension=3
    assert be.probe() == "dimension_mismatch"
    assert be.available is False
    assert be.upsert("ep-1", [0.1, 0.2, 0.3]) is False
    warned = _warnings(caplog)
    assert len(warned) == 1
    text = warned[0].getMessage()
    assert "holds 1024, the embedder makes 3" in text
    assert "memory.backends.vector.collection" in text and "Rebuild embeddings" in text
    cap = backends.vector_capability(_cfg())
    assert cap["vector_status"] == "dimension_mismatch"


def test_an_unsized_vector_column_is_not_a_mismatch(monkeypatch):
    be = _backend(monkeypatch, _Server(installed=True, table_dim=-1))
    assert be.probe() == "installed"


def test_the_remote_table_is_sized_by_the_embedder(monkeypatch):
    """vector.dimension (in no Settings form) sized the table on its own."""
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_dim", lambda: 1536)
    be = backends._build_remote_backend(_cfg())  # vector.dimension=3
    assert be._dim == 1536
    q = backends._build_remote_backend(_cfg(provider="qdrant", url="http://qdrant.test:6333"))
    assert q._dim == 1536


def test_a_concurrent_create_extension_is_not_a_refusal(monkeypatch):
    """Losing the CREATE EXTENSION race leaves the extension there: carry on."""
    server = _Server(installable=True, refuse=("CREATE EXTENSION",))
    be = _backend(monkeypatch, server)
    be.probe()
    server.installed = True  # another process won the race
    assert be.upsert("ep-1", [0.1, 0.2, 0.3]) is True
    assert be.probe() == "installable"  # cached; not marked not_permitted


def test_a_refused_index_keeps_the_table(monkeypatch, caplog):
    """In one transaction the refused HNSW index rolled the CREATE TABLE back."""
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    server = _Server(installed=True, refuse=("CREATE INDEX",))
    be = _backend(monkeypatch, server)
    assert be.upsert("ep-1", [0.1, 0.2, 0.3]) is True
    assert be.upsert("ep-2", [0.1, 0.2, 0.3]) is True
    create_table = next(i for i, s in enumerate(server.statements) if s.startswith("CREATE TABLE"))
    create_index = next(i for i, s in enumerate(server.statements) if s.startswith("CREATE INDEX"))
    assert create_table < create_index
    assert server.commits >= 2 and server.rollbacks == 1
    warned = _warnings(caplog)
    assert len(warned) == 1
    assert "no HNSW index" in warned[0].getMessage()
    assert "DETAIL" not in warned[0].getMessage()  # first line only


# ── what Settings, health and the Test button read ────────────────────


def _cfg(provider: str = "pgvector", url: str = _DSN, mode: str = "hybrid") -> dict[str, Any]:
    return {
        "mode": mode,
        "vector": {"provider": provider, "url": url, "collection": "kazma_memory", "dimension": 3},
        "embedder": {"provider": "local"},
        "graph": {},
        "failover": {"on_remote_error": "local", "timeout_ms": 1000},
    }


def test_capability_never_claims_an_unchecked_store(monkeypatch):
    def _no_io(*_a, **_k):
        raise AssertionError("vector_capability must not connect: it runs on the event loop")

    monkeypatch.setattr(PgvectorBackend, "_connect", _no_io)
    cap = backends.vector_capability(_cfg())
    assert cap["vector_status"] == "unchecked"
    assert cap["vector_search_ready"] is False and cap["vector_write_ready"] is False
    assert "URL configured" not in cap["vector_status_detail"]


def test_capability_reports_a_missing_extension(monkeypatch):
    _backend(monkeypatch, _Server()).probe()
    cap = backends.vector_capability(_cfg())
    assert cap["vector_status"] == "extension_missing"
    assert cap["vector_remote_state"] == "missing"
    assert cap["vector_search_ready"] is False
    detail = cap["vector_status_detail"]
    assert "pgvector/pgvector:pg16" in detail and "KAZMA_PGVECTOR=0" in detail
    assert "s3cret-pw" not in detail


def test_capability_reports_a_working_store(monkeypatch):
    _backend(monkeypatch, _Server(installed=True)).probe()
    cap = backends.vector_capability(_cfg())
    assert cap["vector_status"] == "remote_ready"
    assert cap["vector_search_ready"] is True and cap["vector_write_ready"] is True


def test_an_auto_selected_pgvector_without_the_extension_stays_local_quietly(monkeypatch, caplog):
    """The live shape: vector.provider=sqlite_vec, pgvector picked from the DSN,
    a Postgres that cannot hold vectors. Expected fallback, said once at INFO."""
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    monkeypatch.delenv("KAZMA_PGVECTOR", raising=False)
    cfg = _cfg(provider="sqlite_vec", url="", mode="local")
    cfg["state"] = {"provider": "postgres", "url": _DSN, "role": "mirror"}
    backends._apply_pgvector_scale_defaults(cfg)
    assert cfg["vector_auto"] is True
    assert cfg["vector"]["provider"] == "pgvector" and cfg["mode"] == "hybrid"

    server = _Server()
    monkeypatch.setattr(PgvectorBackend, "_connect", lambda self: (_Conn(server)))
    be = backends._build_remote_backend(cfg)
    assert be.probe() == "missing" and be.available is False
    assert _warnings(caplog) == []
    said = [r for r in caplog.records if r.name == _LOGGER and r.levelno == logging.INFO]
    assert len(said) == 1 and "not used" in said[0].getMessage()

    cap = backends.vector_capability(cfg)
    assert cap["vector_status"] == "full"
    assert cap["vector_search_ready"] is True and cap["vector_remote_state"] == "missing"
    assert "not used" in cap["vector_status_detail"]


def test_an_explicit_pgvector_is_not_auto(monkeypatch):
    monkeypatch.delenv("KAZMA_PGVECTOR", raising=False)
    cfg = _cfg(provider="pgvector", url=_DSN, mode="hybrid")
    backends._apply_pgvector_scale_defaults(cfg)
    assert "vector_auto" not in cfg


def test_the_auto_marker_is_never_saved_as_a_setting(monkeypatch):
    """The Settings form posts back what GET returned; a choice Kazma made
    must not become a choice the operator made."""
    written: list[str] = []

    class _Store:
        def get(self, key, default=None):
            return default

        def batch_set(self, items):
            written.extend(k for k, _v, _c in items)

    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: _Store())
    backends.save_backends_cfg(
        {"mode": "hybrid", "vector_auto": True, "vector": {"provider": "sqlite_vec"}}
    )
    assert written and not any("vector_auto" in k for k in written)


def test_capability_follows_the_provider_not_the_mode(monkeypatch):
    """get_vector_backend uses pgvector even with mode left at "local"."""
    _backend(monkeypatch, _Server()).probe()
    cap = backends.vector_capability(_cfg(mode="local"))
    assert cap["vector_status"] == "extension_missing"


def test_capability_for_local_vectors_is_unchanged():
    cap = backends.vector_capability(_cfg(provider="sqlite_vec", url="", mode="local"))
    assert cap["vector_status"] == "full"
    assert cap["vector_remote_state"] is None


def test_the_test_button_probes_postgres_not_http(monkeypatch):
    import httpx

    def _no_http(*_a, **_k):
        raise AssertionError("a postgresql:// DSN was sent to an HTTP client")

    monkeypatch.setattr(httpx, "Client", _no_http)
    server = _Server()
    monkeypatch.setattr(PgvectorBackend, "_connect", lambda self: (_Conn(server)))
    result = backends.test_vector_backend(_cfg())
    assert result["ok"] is False
    assert result["state"] == "missing"
    assert "no 'vector' extension" in result["error"]
    assert result["capability"]["vector_status"] == "extension_missing"
    server.installed = True
    result = backends.test_vector_backend(_cfg())
    assert result["ok"] is True and result["state"] == "installed"
    assert "error" not in result


def test_the_test_button_tests_the_store_in_use_when_pgvector_was_auto(monkeypatch, tmp_path):
    """Live 2026-09-25, Settings -> Memory: the banner read "Vector: full
    (local)" and the Test button beside it read "Vector failed: this
    Postgres has no 'vector' extension". Kazma picked pgvector from the DSN;
    local sqlite-vec serves memory. The button tests that store and names
    why pgvector is not in use -- and a pgvector the operator CHOSE still
    fails (the test above)."""
    monkeypatch.delenv("KAZMA_PGVECTOR", raising=False)
    monkeypatch.setattr("kazma_core.paths.primary_memory_db", lambda: str(tmp_path / "memory_state.db"))
    cfg = _cfg(provider="sqlite_vec", url="", mode="local")
    cfg["state"] = {"provider": "postgres", "url": _DSN, "role": "mirror"}
    backends._apply_pgvector_scale_defaults(cfg)
    assert cfg["vector_auto"] is True

    server = _Server()
    monkeypatch.setattr(PgvectorBackend, "_connect", lambda self: (_Conn(server)))
    result = backends.test_vector_backend(cfg)
    assert result["ok"] is True, result
    assert result["provider"] == "sqlite_vec" and result["state"] == "missing"
    assert result["note"].startswith("pgvector not used:"), result["note"]
    assert "error" not in result
    assert result["capability"]["vector_status"] == "full"

    # Negative control: the same server with pgvector CHOSEN is a failure.
    chosen = _cfg(provider="pgvector", url=_DSN, mode="hybrid")
    backends._apply_pgvector_scale_defaults(chosen)
    assert "vector_auto" not in chosen
    failed = backends.test_vector_backend(chosen)
    assert failed["ok"] is False and "no 'vector' extension" in failed["error"]
    assert "note" not in failed


def test_the_boot_probe_fills_the_state(monkeypatch):
    server = _Server()
    monkeypatch.setattr(PgvectorBackend, "_connect", lambda self: (_Conn(server)))
    monkeypatch.setattr(backends, "get_backends_cfg", lambda: _cfg())
    assert backends.probe_vector_backend() == "missing"
    assert backends.vector_capability(_cfg())["vector_status"] == "extension_missing"


def test_the_boot_probe_skips_local_vectors(monkeypatch):
    monkeypatch.setattr(
        backends, "get_backends_cfg", lambda: _cfg(provider="sqlite_vec", url="", mode="local")
    )
    assert backends.probe_vector_backend() is None


def test_the_hybrid_backend_falls_back_without_touching_postgres(monkeypatch, tmp_path):
    """The live shape: hybrid over a Postgres with no pgvector serves from sqlite-vec."""
    import sqlite3

    from kazma_core.memory.schema_v2 import ensure_primary_schema

    server = _Server()
    remote = _backend(monkeypatch, server)
    conn = sqlite3.connect(str(tmp_path / "m.db"))
    ensure_primary_schema(conn)
    try:
        hybrid = backends.HybridVectorBackend(remote, backends.LocalSqliteVectorBackend(conn))
        hybrid.upsert("ep-1", [1.0, 0.0, 0.0], meta={"tier": "episodic"})
        hybrid.search([1.0, 0.0, 0.0], tier=None, kind="episode")
        hybrid.delete("ep-1")
    finally:
        conn.close()
    assert server.connects == 1  # the probe, once, then the cache
    assert len(server.statements) == 1


def _settings_app():
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui.routes_direct.settings import register_settings_routes

    app = FastAPI()
    register_settings_routes(SimpleNamespace(app=app))
    return app, TestClient(app)


def test_the_settings_routes_report_the_probe_off_the_loop(monkeypatch):
    """The Test button opens a Postgres connection; it must not run on the loop."""
    import inspect

    server = _Server()
    monkeypatch.setattr(PgvectorBackend, "_connect", lambda self: (_Conn(server)))
    monkeypatch.setattr(backends, "get_backends_cfg", lambda: _cfg())
    app, client = _settings_app()
    endpoints = {
        (tuple(sorted(r.methods)), r.path): r.endpoint for r in app.routes if hasattr(r, "methods")
    }
    for key in ((("POST",), "/api/settings/memory/backends/test-vector"),
                (("GET",), "/api/settings/memory/backends")):
        assert not inspect.iscoroutinefunction(endpoints[key]), key

    body = client.post("/api/settings/memory/backends/test-vector").json()
    assert body["ok"] is False and body["state"] == "missing"
    assert "no 'vector' extension" in body["error"]
    assert "s3cret-pw" not in str(body)

    cap = client.get("/api/settings/memory/backends").json()["capability"]
    assert cap["vector_status"] == "extension_missing"
    assert "s3cret-pw" not in str(cap)


# ── Qdrant: the same class ────────────────────────────────────────────


class _QdrantResp:
    def __init__(self, code: int) -> None:
        self.status_code = code


def _qdrant(monkeypatch, codes: list[int]) -> tuple[QdrantVectorBackend, list[str]]:
    calls: list[str] = []

    class _Client:
        def __init__(self, *a, **k) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a) -> bool:
            return False

        def get(self, url, headers=None):
            calls.append(url)
            return _QdrantResp(codes[min(len(calls), len(codes)) - 1])

    import httpx

    monkeypatch.setattr(httpx, "Client", _Client)
    return QdrantVectorBackend(url="http://qdrant.test:6333", api_key="k", collection="c"), calls


@pytest.mark.parametrize(
    ("code", "state", "usable"),
    [(200, "reachable", True), (404, "reachable", True), (401, "unauthorized", False),
     (403, "unauthorized", False), (503, "unreachable", False)],
)
def test_qdrant_probe_states(monkeypatch, code, state, usable):
    be, _ = _qdrant(monkeypatch, [code])
    assert be.probe() == state
    assert be.available is usable


def test_qdrant_probe_is_cached_across_instances(monkeypatch):
    be, calls = _qdrant(monkeypatch, [200])
    be.probe()
    QdrantVectorBackend(url="http://qdrant.test:6333", collection="c").probe()
    assert len(calls) == 1


def test_qdrant_unreachable_on_a_transport_error(monkeypatch):
    import httpx

    class _Down:
        def __init__(self, *a, **k) -> None:
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "Client", _Down)
    be = QdrantVectorBackend(url="http://qdrant.test:6333")
    assert be.probe() == "unreachable"
    cap = backends.vector_capability(_cfg(provider="qdrant", url="http://qdrant.test:6333"))
    assert cap["vector_status"] == "unreachable"


# ── a real server ─────────────────────────────────────────────────────


def _real_dsn() -> tuple[str, Any]:
    """The Postgres the suite may use, and the driver; skips without either."""
    dsn = (os.environ.get("KAZMA_DATABASE_URL") or "").strip()
    if not dsn:
        pytest.skip("no Postgres DSN (the CI Postgres job, or a throwaway container)")
    return dsn, pytest.importorskip("psycopg")


@pytest.mark.postgres
def test_a_real_postgres_is_used_only_if_it_can_hold_vectors(monkeypatch):
    """On a server with no pgvector (CI's postgres:16) nothing is created; on
    one that ships it, a vector round-trips. Verified against both
    postgres:16 and pgvector/pgvector:pg16."""
    monkeypatch.setattr(backends, "_pg_driver_errors", _REAL_DRIVER_ERRORS)
    dsn, psycopg = _real_dsn()
    table = f"kazma_vec_probe_{uuid.uuid4().hex[:8]}"
    be = PgvectorBackend(dsn=dsn, collection=table, dimension=3)
    state = be.probe(force=True)
    assert state in ("installed", "installable", "missing"), state
    try:
        if state == "missing":
            assert be.upsert("ep-1", [0.1, 0.2, 0.3], meta={"tier": "episodic"}) is False
            assert be.search([0.1, 0.2, 0.3], tier=None) == []
            assert be.delete("ep-1") is False
        else:
            item = f"ep-{uuid.uuid4().hex[:8]}"
            assert be.upsert(item, [0.1, 0.2, 0.3], meta={"tier": "episodic", "kind": "episode"}) is True
            hits = be.search([0.1, 0.2, 0.3], tier="episodic", kind="episode", limit=5)
            assert hits and hits[0][0] == item and hits[0][1] > 0.99
            assert be.delete(item) is True
            assert be.search([0.1, 0.2, 0.3], tier="episodic", limit=5) == []
            assert be.probe(force=True) == "installed"
    finally:
        with psycopg.connect(dsn) as conn:
            exists = conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0]
            if state == "missing":
                assert exists is None, "a table was created on a server with no pgvector"
            if exists is not None:
                conn.execute(f"DROP TABLE {table}")
                conn.commit()


@pytest.mark.postgres
def test_a_real_table_of_another_size_is_refused_before_writing(monkeypatch):
    """Needs pgvector (skips on CI's postgres:16); verified on pgvector/pgvector:pg16."""
    monkeypatch.setattr(backends, "_pg_driver_errors", _REAL_DRIVER_ERRORS)
    dsn, psycopg = _real_dsn()
    table = f"kazma_vec_dim_{uuid.uuid4().hex[:8]}"
    three = PgvectorBackend(dsn=dsn, collection=table, dimension=3)
    if three.probe(force=True) == "missing":
        pytest.skip("this Postgres has no pgvector")
    try:
        assert three.upsert("ep-1", [0.1, 0.2, 0.3]) is True
        four = PgvectorBackend(dsn=dsn, collection=table, dimension=4)
        assert four.probe(force=True) == "dimension_mismatch"
        assert four.upsert("ep-2", [0.1, 0.2, 0.3, 0.4]) is False
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
            conn.commit()
