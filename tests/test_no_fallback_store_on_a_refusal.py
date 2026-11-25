"""A refusal is never answered by another store (S4-01 step 0a, H2).

``NoSpaceBound`` is a ``RuntimeError`` and a policy's refusal is a psycopg
error, so both look like "the database is away" to code that falls back to
SQLite. A fallback made then lasted for the process: documents listed empty,
uploads and chat state were kept in two places. Each of Kazma's
Postgres-to-SQLite fallbacks raises a refusal instead, once Spaces is
enforced, and falls back on an outage as before. The last test finds the
fallbacks from the source.
"""

from __future__ import annotations

import ast
import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest
from kazma_core.db import pg_helpers
from kazma_core.spaces import scope
from kazma_core.spaces.scope import NoSpaceBound

REPO = Path(__file__).resolve().parent.parent


class _DeniedError(Exception):
    """As psycopg's ``InsufficientPrivilege``, without the driver."""

    sqlstate = "42501"


def _refusals() -> list[BaseException]:
    return [NoSpaceBound("no space is bound"), _DeniedError("a policy refused")]


@pytest.fixture
def enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scope, "_enforced", True)


# ── what a refusal is ────────────────────────────────────────────────────


@pytest.mark.usefixtures("enforced")
@pytest.mark.parametrize("refusal", _refusals(), ids=lambda e: type(e).__name__)
def test_a_refusal_is_found_through_what_wraps_it(refusal: BaseException) -> None:
    assert pg_helpers._is_space_refusal(refusal)
    try:
        try:
            raise refusal
        except Exception as inner:
            raise RuntimeError("the store could not open") from inner
    except RuntimeError as outer:
        assert pg_helpers._is_space_refusal(outer)
    try:
        try:
            raise refusal
        except Exception:
            raise ValueError("while handling it")
    except ValueError as outer:
        assert pg_helpers._is_space_refusal(outer)


@pytest.mark.usefixtures("enforced")
def test_an_outage_is_no_refusal() -> None:
    assert not pg_helpers._is_space_refusal(None)
    assert not pg_helpers._is_space_refusal(RuntimeError("Postgres pool unavailable"))
    away = ConnectionError("refused by the network")
    away.__cause__ = away  # a cycle ends
    assert not pg_helpers._is_space_refusal(away)


@pytest.mark.parametrize("refusal", _refusals(), ids=lambda e: type(e).__name__)
def test_with_spaces_off_nothing_is_a_refusal(refusal: BaseException) -> None:
    assert not pg_helpers._is_space_refusal(refusal)
    assert pg_helpers.no_other_store_on_refusal(refusal, "a store") is None


# ── the fallbacks ────────────────────────────────────────────────────────


def _documents(monkeypatch: pytest.MonkeyPatch, exc: BaseException) -> Any:
    from kazma_core.documents import repository_pg

    monkeypatch.setattr(repository_pg, "document_metadata_backend_name", lambda: "postgres")
    _pool_raises(monkeypatch, exc)
    return lambda: repository_pg.resolve_document_repository("sqlite", tenant_quota_bytes=1)


def _jobs(monkeypatch: pytest.MonkeyPatch, exc: BaseException) -> Any:
    from kazma_core.documents import jobs_pg

    monkeypatch.setattr(jobs_pg, "document_jobs_backend", lambda: "postgres")
    _pool_raises(monkeypatch, exc)
    return lambda: jobs_pg.resolve_job_repository("sqlite")


def _pool_raises(monkeypatch: pytest.MonkeyPatch, exc: BaseException) -> None:
    def refused() -> Any:
        raise exc

    monkeypatch.setattr(pg_helpers, "get_pool", refused)


def _saver_raises(monkeypatch: pytest.MonkeyPatch, exc: BaseException) -> None:
    from kazma_core import checkpoints_pg
    from kazma_core.db import backend

    async def refused(*_a: Any, **_k: Any) -> Any:
        raise exc

    monkeypatch.setattr(checkpoints_pg, "open_postgres_checkpointer", refused)
    monkeypatch.setattr(backend, "is_postgres", lambda: True)
    monkeypatch.setattr(backend, "get_database_url", lambda: "postgresql://demo@localhost/kazma")


def _chat_state(monkeypatch: pytest.MonkeyPatch, exc: BaseException, tmp_path: Path) -> Any:
    from kazma_core import checkpoints_shared
    from kazma_gateway.stores import checkpoint

    _saver_raises(monkeypatch, exc)

    async def sqlite(_path: str) -> str:
        return "sqlite"

    monkeypatch.setattr(checkpoints_shared, "get_shared_sqlite_saver", sqlite)
    monkeypatch.setattr(checkpoints_shared, "retain_shared_checkpoints", lambda _path: None)

    async def opened() -> Any:
        return (await checkpoint.create_checkpoint_manager(str(tmp_path / "c.db")))._saver

    return lambda: asyncio.run(opened())


def _agent_state(monkeypatch: pytest.MonkeyPatch, exc: BaseException, tmp_path: Path) -> Any:
    from types import SimpleNamespace

    from kazma_core import agent_runner

    _saver_raises(monkeypatch, exc)

    class _ReachedError(Exception):
        pass

    async def no_close() -> None:
        return None

    async def sqlite(*_a: Any, **_k: Any) -> Any:
        raise _ReachedError

    async def opened() -> Any:
        agent = SimpleNamespace(
            _graph=None, _graph_build_lock=asyncio.Lock(), _checkpointer=None, _close_checkpointer=no_close,
            config=SimpleNamespace(raw={"storage": {"checkpoint_path": str(tmp_path / "own.db")}}))
        monkeypatch.setattr(agent_runner.aiosqlite, "connect", sqlite)
        try:
            await agent_runner.KazmaAgent._ensure_graph(agent)
        except _ReachedError:
            return "sqlite"

    return lambda: asyncio.run(opened())


_FALLBACKS = ["documents", "jobs", "chat state", "agent state"]


def _open(which: str, monkeypatch: pytest.MonkeyPatch, exc: BaseException, tmp_path: Path) -> Any:
    if which in ("documents", "jobs"):
        return (_documents if which == "documents" else _jobs)(monkeypatch, exc)
    return (_chat_state if which == "chat state" else _agent_state)(monkeypatch, exc, tmp_path)


@pytest.mark.usefixtures("enforced")
@pytest.mark.parametrize("refusal", _refusals(), ids=lambda e: type(e).__name__)
@pytest.mark.parametrize("which", _FALLBACKS)
def test_a_refused_store_opens_no_other(which: str, refusal: BaseException, monkeypatch: pytest.MonkeyPatch,
                                        tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("ERROR", logger=pg_helpers.__name__)
    with pytest.raises(type(refusal)):
        _open(which, monkeypatch, refusal, tmp_path)()
    assert "No other store is used" in caplog.text, "the reason is said"


@pytest.mark.usefixtures("enforced")
@pytest.mark.parametrize("which", _FALLBACKS)
def test_control_an_outage_still_falls_back(which: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert _open(which, monkeypatch, RuntimeError("Postgres pool unavailable"), tmp_path)() == "sqlite"


@pytest.mark.parametrize("refusal", _refusals(), ids=lambda e: type(e).__name__)
@pytest.mark.parametrize("which", _FALLBACKS)
def test_off_every_failure_falls_back_as_before(which: str, refusal: BaseException,
                                                monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert _open(which, monkeypatch, refusal, tmp_path)() == "sqlite"


# ── every fallback, from the source ──────────────────────────────────────

#: What opens Postgres.
OPENERS = {"get_pool", "get_postgres_pool", "open_postgres_checkpointer", "PostgresPool",
           "PostgresDocumentRepository", "PostgresDocumentJobRepository", "create_checkpointer",
           "create_checkpoint_manager"}

#: A handler that answers for the failure and uses no other store:
#: ``file::function [what it opens]`` -> what it answers.
NO_OTHER_STORE = {
    "kazma-core/kazma_core/memory/chat_history.py::conversations_for [get_pool]":
        "answers [] (the reading is memory's own step 0b)",
    "kazma-core/kazma_core/memory/chat_history.py::sessions_changed_since [get_pool]":
        "answers [] (the reading is memory's own step 0b)",
    "kazma-core/kazma_core/memory/health.py::build_memory_health [get_postgres_pool]": "a probe: it says the failure",
    "kazma-ui/kazma_ui/health.py::check_database [get_postgres_pool]": "a probe: it says the failure",
    "kazma-ui/kazma_ui/app.py::KazmaAppBuilder._on_startup [get_postgres_pool]":
        "a boot check: it says the failure",
    "kazma-ui/kazma_ui/session_manager.py::SessionManager._hydrate_from_checkpoints_db [get_pool]":
        "no caller: piece 5 of S4-01 step 0a deletes it",
}


def _called(node: ast.AST) -> set[str]:
    return {(c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", ""))
            for c in ast.walk(node) if isinstance(c, ast.Call)}


def _raises_first(handler: ast.ExceptHandler) -> bool:
    """Whether the handler's first statement (after its imports) raises a
    refusal again, or it ends by raising whatever it caught."""
    body = [s for s in handler.body if not isinstance(s, ast.Import | ast.ImportFrom)]
    return bool(body) and ("no_other_store_on_refusal" in _called(body[0]) or isinstance(body[-1], ast.Raise))


def _broad(handler: ast.ExceptHandler) -> bool:
    return not (handler.type is not None and ast.unparse(handler.type) in {"ImportError", "ModuleNotFoundError"})


def swallowing(source: str, rel: str) -> set[str]:
    """Functions of *source*, and what they open, that name SQLite, open Postgres inside a ``try``
    and have a handler for it that goes on without raising a refusal."""
    found: set[str] = set()

    def visit(node: ast.AST, stack: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            inner = stack
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                inner = [*stack, child.name]
                if not isinstance(child, ast.ClassDef) and "sqlite" in (
                        ast.get_source_segment(source, child) or "").lower():
                    for tried in ast.walk(child):
                        if isinstance(tried, ast.Try) and any(
                                _broad(h) and not _raises_first(h) for h in tried.handlers):
                            found.update(f"{rel}::{'.'.join(inner)} [{opener}]"
                                         for opener in OPENERS & set().union(*map(_called, tried.body)))
            visit(child, inner)

    visit(ast.parse(source), [])
    return found


def test_every_fallback_to_another_store_raises_a_refusal() -> None:
    files = subprocess.run(["git", "ls-files", "--", "kazma-*/*.py"], cwd=REPO, capture_output=True, text=True,
                           check=True).stdout.splitlines()
    found: set[str] = set()
    for rel in files:
        text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        if any(opener in text for opener in OPENERS):
            found |= swallowing(text, rel)
    assert found - set(NO_OTHER_STORE) == set(), "raise a refusal first (pg_helpers.no_other_store_on_refusal)"
    assert set(NO_OTHER_STORE) - found == set(), "declared, and no longer so"


@pytest.mark.parametrize(("source", "found"), [
    ("def f():\n    try:\n        return get_pool()\n    except Exception as e:\n        return sqlite\n", "get_pool"),
    ("def f():\n    try:\n        return get_pool()\n    except store_errors():\n        pass\n    return sqlite\n", "get_pool"),
    ("async def f():\n    try:\n        await open_postgres_checkpointer(x)\n    except ImportError:\n        pass\n"
     "    except Exception as e:\n        log(e)\n        no_other_store_on_refusal(e, 'x')\n    sqlite()\n", "open_postgres_checkpointer"),
    ("def f():\n    try:\n        return get_pool()\n    except Exception as e:\n"
     "        no_other_store_on_refusal(e, 'x')\n        return sqlite\n", ""),
    ("def f():\n    try:\n        return get_pool()\n    except Exception:\n        sqlite()\n        raise\n", ""),
    ("def f():\n    try:\n        return get_pool()\n    except Exception:\n        return None\n", ""),
])
def test_a_planted_fallback_is_found(source: str, found: str) -> None:
    assert swallowing(source, "x.py") == ({f"x.py::f [{found}]"} if found else set())
