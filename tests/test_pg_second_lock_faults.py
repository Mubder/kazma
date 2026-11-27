"""Faults with the second lock armed, on a real Postgres (Spaces S4-01 step 5).

The review's point 6, what was not yet proven:

* the documents audit prune runs as the install and deletes what aged out;
  in a space it is refused (review M12: a space may read and add audit
  events, never delete them), the install's run as the control;
* the space role lost while the server runs (renamed here, as a role
  dropped or replaced would look): on the shared pool an idle space
  connection is refused and closed, never used as another role, and a
  checkout that then cannot sign in is said as the space role's own problem,
  never the database's outage (review M1); on the checkpointer's space half
  an idle connection is refused the same way; the role back, both work again;
* ``/health/deep`` and ``/health/ready`` answer the same armed as not:
  nothing a probe reads is blinded by the lock.

Needs ``KAZMA_DATABASE_URL`` (a throwaway server whose login owns its tables
and may make roles) and ``KAZMA_TEST_ALLOW_REAL_DB=1``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from kazma_core.spaces import scope
from kazma_core.spaces.row_work import install_statements, statements_of
from kazma_core.spaces.scope import bind_scope

from tests._as_request import not_armed
from tests.test_pg_armed_flows import _install, armed, documents  # noqa: F401  (fixtures)

try:
    import psycopg
except ImportError:  # CI's light job has no psycopg: collected and skipped, so the counts agree
    psycopg = None

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(psycopg is None, reason="psycopg is not installed")]


def _superuser(statement: str) -> None:
    with psycopg.connect(os.environ["KAZMA_DATABASE_URL"], autocommit=True) as conn:
        conn.execute(statement)


@pytest.fixture()
def role_lost() -> Iterator[Any]:
    """Takes the space role away while the test runs (renamed: its grants and
    memberships stay, so giving it back is one statement), and gives it back."""
    taken: list[bool] = []

    def take() -> None:
        _superuser("ALTER ROLE kazma_space RENAME TO kazma_space_lost")
        taken.append(True)

    def give_back() -> None:
        if taken:
            _superuser("ALTER ROLE kazma_space_lost RENAME TO kazma_space")
            taken.clear()

    try:
        yield type("RoleLost", (), {"take": staticmethod(take), "give_back": staticmethod(give_back)})
    finally:
        give_back()


# ── the audit prune (review M12) ─────────────────────────────────────────


def test_the_audit_prune_is_the_installs_and_no_space_may_prune(documents: Any) -> None:  # noqa: F811 - the armed flows' fixture
    w = documents
    old = f"aged-{uuid.uuid4().hex[:8]}"
    _install("INSERT INTO document_audit_events (tenant_id, event_type, action, outcome, created_at) "
             "VALUES (%s, 'gc', %s, 'success', '2001-01-01T00:00:00+00:00')", (w.a, old))
    with bind_scope(w.a, w.person_a), pytest.raises(psycopg.errors.InsufficientPrivilege):
        w.svc.audit.prune_older_than(cutoff_iso="2002-01-01T00:00:00+00:00", max_rows=50)
    assert _install("SELECT 1 FROM document_audit_events WHERE action = %s", (old,)), "a space's prune removed nothing"
    # The control: the retention's run, the install's, prunes it.
    with install_statements("a test: the retention's audit prune"):
        assert w.svc.audit.prune_older_than(cutoff_iso="2002-01-01T00:00:00+00:00", max_rows=50) >= 1
    assert not _install("SELECT 1 FROM document_audit_events WHERE action = %s", (old,))


# ── the space role lost while running (review M1) ────────────────────────


def test_the_shared_pool_refuses_a_connection_of_a_lost_role_and_says_why(
        armed: Any, role_lost: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:  # noqa: F811 - the armed flows' fixture
    from kazma_core.db import availability, row_security
    from kazma_core.db.postgres_pool import PostgresPool

    monkeypatch.setenv("KAZMA_PG_POOL_TIMEOUT", "1")
    monkeypatch.setattr(row_security, "_half_said", {})
    monkeypatch.setattr(row_security, "_role_refused", (0.0, ""))
    noted: list[str] = []
    monkeypatch.setattr(availability, "note_failure", lambda kind, exc=None, **k: noted.append(kind) or True)
    made = PostgresPool(os.environ["KAZMA_DATABASE_URL"], min_size=1, max_size=1)
    try:
        with bind_scope("sa", "owner"):
            assert made.execute("SELECT current_user::text AS who") == [{"who": "kazma_space"}]
        role_lost.take()
        with bind_scope("sa", "owner"), pytest.raises(row_security._NotTheSpaceRole):
            made.execute("SELECT 1")  # the idle connection, now another role's: never used
        with caplog.at_level(logging.WARNING, logger="kazma_core.db.row_security"), bind_scope("sa", "owner"), \
                pytest.raises(psycopg.OperationalError):
            made.execute("SELECT 1")  # a new one cannot sign in as the role
        assert any("space role cannot sign in" in r.getMessage() for r in caplog.records), caplog.text
        assert noted == [], "the owner signs in: no outage of the database"
        role_lost.give_back()
        assert _back(made), "the role back, the pool's new connections are the space role's again"
    finally:
        made.close()


def _back(made: Any, within_s: float = 10.0) -> bool:
    """The space half answers as the space role again, within *within_s*:
    the pool waits out its reconnect backoff after the failed sign-ins, as a
    running server's would."""
    import time

    deadline = time.monotonic() + within_s
    while True:
        try:
            with bind_scope("sa", "owner"):
                return made.execute("SELECT current_user::text AS who") == [{"who": "kazma_space"}]
        except psycopg.OperationalError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)


def test_the_checkpointers_half_refuses_a_connection_of_a_lost_role(armed: Any, role_lost: Any) -> None:  # noqa: F811 - the armed flows' fixture
    from kazma_core.checkpoints_pg import close_postgres_checkpointer, open_postgres_checkpointer
    from kazma_core.db import row_security
    from langgraph.checkpoint.base import empty_checkpoint

    thread = f"t-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread, "checkpoint_ns": ""}}

    async def _go() -> None:
        saver = await open_postgres_checkpointer(os.environ["KAZMA_DATABASE_URL"], max_size=1)
        try:
            with statements_of("sa"):
                await saver.aput(config, empty_checkpoint(), {"source": "input", "step": 0}, {})
                role_lost.take()
                with pytest.raises(row_security._NotTheSpaceRole):
                    await saver.aget_tuple(config)
                role_lost.give_back()
                assert await saver.aget_tuple(config) is not None, "the role back, the thread is read again"
        finally:
            await close_postgres_checkpointer(saver)

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_go())


# ── the probes answer the same armed (nothing blinded) ───────────────────


def _probes() -> dict[str, Any]:
    from kazma_ui import health

    health._deep_cache.clear()

    async def _go() -> dict[str, Any]:
        import json

        deep = await health.deep_canary()
        ready = await health._readiness(runtime=None, required=[])
        body = json.loads(getattr(ready, "body", b"{}") or b"{}") if hasattr(ready, "body") else ready
        return {"deep": (deep.status_code, {k: v.get("status") for k, v in
                                            json.loads(deep.body)["checks"].items()}),
                "ready": body.get("checks", body) if isinstance(body, dict) else body}

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(_go())


def test_the_health_probes_answer_the_same_armed(armed: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811 - the armed flows' fixture

    answered = _probes()
    deep_status, deep = answered["deep"]
    # They reach Postgres armed: the settings' roundtrip and the database's ping.
    assert deep_status == 200 and deep["database"] == "ok" and deep["config_roundtrip"] == "ok", answered
    assert answered["ready"]["database"]["status"] == "ok", answered
    not_armed(monkeypatch)
    monkeypatch.setattr(scope, "_enforced", True)
    assert _probes() == answered, "armed, a probe saw less than before"
