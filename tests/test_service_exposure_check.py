"""Kazma notices its database, Neo4j or SearXNG published beyond loopback,
and a superuser database role (FA-218, Kazma's side).

The 2026-11 functional audit: Postgres (signed in to as a superuser), Neo4j
and SearXNG were published on every interface of the live machine. The
compose files publish on loopback since 2b6ab511; this is how Kazma notices
a container started some other way.

Recorded ``docker ps`` rows (the shapes the live machine printed on
2026-11-02, Kazma's containers showing an image id, another project's
database on the same machine) and ``pg_roles`` answers, through the
security report's real check.
"""

from __future__ import annotations

import asyncio

import pytest

from kazma_core.security import service_exposure as se

LOOPBACK_ROWS = [
    {"Names": "kazma-db-win", "Image": "57c72fd2a128", "Ports": "127.0.0.1:5433->5432/tcp"},
    {"Names": "kazma-neo4j-win", "Image": "d9dd3dc7d1c7",
     "Ports": "127.0.0.1:7474->7474/tcp, 127.0.0.1:7687->7687/tcp"},
    {"Names": "kazma-searxng", "Image": "searxng/searxng:latest", "Ports": "127.0.0.1:8088->8080/tcp"},
    # Another project's database, published on every interface: not Kazma's.
    {"Names": "shipx-db", "Image": "ankane/pgvector:v0.5.1",
     "Ports": "0.0.0.0:5533->5432/tcp, [::]:5533->5432/tcp"},
    {"Names": "shipx-postgres", "Image": "pgvector/pgvector:pg16", "Ports": "5432/tcp"},
]
#: The live machine before 2b6ab511 (the audit's evidence).
EXPOSED_ROWS = [
    {"Names": "kazma-db-win", "Image": "57c72fd2a128",
     "Ports": "0.0.0.0:5433->5432/tcp, [::]:5433->5432/tcp"},
    {"Names": "kazma-neo4j-win", "Image": "d9dd3dc7d1c7",
     "Ports": "0.0.0.0:7474->7474/tcp, 0.0.0.0:7687->7687/tcp"},
    {"Names": "kazma-searxng", "Image": "searxng/searxng:latest", "Ports": "0.0.0.0:8088->8080/tcp"},
]


def test_loopback_bindings_and_other_projects_pass():
    assert se._published_problems(LOOPBACK_ROWS) == []


def test_every_interface_fails():
    problems = se._published_problems(EXPOSED_ROWS)
    assert len(problems) == 5  # 5433 twice (v4, v6) is one problem per host
    assert any("kazma-db-win" in p and "0.0.0.0" in p for p in problems)
    assert any("kazma-db-win" in p and "[::]" in p for p in problems)


def test_the_configured_database_container_counts_whatever_its_name(monkeypatch):
    monkeypatch.setenv("KAZMA_DB_CONTAINER", "pgmain")
    rows = [{"Names": "pgmain", "Image": "57c7", "Ports": "192.168.1.20:5432->5432/tcp"}]
    assert se._published_problems(rows)


def test_a_superuser_role_is_a_problem():
    assert se._superuser_problem({"rolname": "postgres", "rolsuper": True})
    assert se._superuser_problem({"rolname": "kazma_app", "rolsuper": False}) is None
    assert se._superuser_problem(None) is None


@pytest.mark.parametrize("rows, role, passed", [
    (LOOPBACK_ROWS, {"rolname": "kazma_app", "rolsuper": False}, True),
    (EXPOSED_ROWS, {"rolname": "kazma_app", "rolsuper": False}, False),
    (LOOPBACK_ROWS, {"rolname": "postgres", "rolsuper": True}, False),
])
def test_the_security_report_says_so(monkeypatch, rows, role, passed):
    from kazma_core.security.hardening import SecurityHardeningRunner

    monkeypatch.setattr(se, "_docker_rows", lambda: rows)
    monkeypatch.setattr(se, "_role_answer", lambda: role)
    check = asyncio.run(SecurityHardeningRunner().run_check("check_service_exposure"))
    assert check.passed is passed
    assert "check_service_exposure" in SecurityHardeningRunner.CHECKS


def test_docker_not_answering_is_said_not_passed_silently(monkeypatch):
    monkeypatch.setattr(se, "_docker_rows", lambda: None)
    monkeypatch.setattr(se, "_role_answer", lambda: None)
    problems, unchecked = se.exposure_problems()
    assert problems == [] and any("published ports" in u for u in unchecked)


def test_boot_says_it_once(monkeypatch, caplog):
    import logging

    sent = []
    monkeypatch.setattr(se, "_docker_rows", lambda: EXPOSED_ROWS)
    monkeypatch.setattr(se, "_inspect_container", lambda name: None)  # SearXNG's key: not this test's
    monkeypatch.setattr(se, "_role_answer", lambda: {"rolname": "postgres", "rolsuper": True})
    monkeypatch.setattr("kazma_core.observability.ops_alerts.alert", lambda *a, **k: sent.append(a[0]))
    with caplog.at_level(logging.WARNING, logger="kazma_core.security.service_exposure"):
        problems = se.report_exposure()
    assert len(problems) == 6 and sent == ["security.service_exposed"]
    assert sum(r.levelno == logging.WARNING for r in caplog.records) == 6


@pytest.mark.postgres
def test_the_role_question_on_a_real_postgres(monkeypatch):
    """``pg_roles`` asked through Kazma's own pool on a throwaway Postgres: the
    suite's superuser login is reported, by name."""
    import os

    dsn = os.environ.get("KAZMA_DATABASE_URL") or ""
    if not dsn or os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
        pytest.skip("needs a throwaway Postgres (scripts/postgres_suite.py)")
    from kazma_core.db.postgres_pool import PostgresPool

    pool = PostgresPool(dsn, min_size=1, max_size=1)
    try:
        monkeypatch.setattr("kazma_core.db.postgres_pool.get_postgres_pool", lambda: pool)
        answer = se._role_answer()
        assert answer and answer["rolname"]
        problem = se._superuser_problem(answer)
        assert (problem is not None) is bool(answer["rolsuper"])
    finally:
        pool.close()
