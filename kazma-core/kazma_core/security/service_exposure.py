"""Kazma's own services reachable from the network, and a superuser database role.

The 2026-11 functional audit (FA-218): the install's Postgres (logged in as a
superuser), Neo4j and SearXNG were published on every interface. Docker's
port publishing goes around the Windows firewall's per-program rules, so the
database answered anyone on the LAN. The compose files publish on loopback
now; this is how Kazma notices a container started some other way.

Two answers, each read from what the machine says, never assumed:

* :func:`_published_problems` -- Kazma's service containers (by name: a
  ``kazma`` container of the database, Neo4j or SearXNG, or the one
  ``KAZMA_DB_CONTAINER`` names; a container recreated from an image shows
  the image's id, so the image cannot say) with a port published on an
  address other than loopback, from ``docker ps``'s rows. Another project's
  containers on the same machine are not Kazma's to report;
* :func:`_superuser_problem` -- the database role Kazma signs in as is a
  superuser (``pg_roles.rolsuper``): a role that can read every database on
  the server and run programs through ``COPY ... PROGRAM``.

:func:`exposure_problems` asks both (blocking: async callers use a thread);
:func:`report_exposure` logs each at WARNING and raises one ops alert, at
boot. The security report (``security/hardening.py``) shows the same.

A third, not an exposure (2026-11-11): :func:`searxng_secret_problems` -- a
SearXNG container of Kazma's running on the placeholder secret key that
settings.yml and docker-compose.yml ship (``security.searxng_secret``; setup
generates one). Said once at boot (a WARNING each, no alert) and in the
security report's ``check_searxng_secret``.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["exposure_problems", "report_exposure", "searxng_secret_problems"]

#: A container of a service Kazma runs beside itself, by name.
_SERVICE_NAME = re.compile(r"(?i)^kazma[-_].*(db|postgres|pg|neo4j|searxng)")
#: Kazma's SearXNG container (docker-compose.yml names it kazma-searxng).
_SEARXNG_NAME = re.compile(r"(?i)^kazma[-_].*searxng")
#: One published mapping in ``docker ps``'s Ports column:
#: ``0.0.0.0:5433->5432/tcp`` or ``[::]:5433->5432/tcp``.
_MAPPING = re.compile(r"(?P<host>\[[0-9a-fA-F:.]*\]|[0-9.]+):(?P<port>\d+)->(?P<inner>\d+)/(?P<proto>tcp|udp)")
_LOOPBACK = {"127.0.0.1", "[::1]", "::1"}
_DOCKER_TIMEOUT_S = 10


def _published_problems(rows: Iterable[dict[str, Any]]) -> list[str]:
    """Each Kazma service port published on a non-loopback address, from
    ``docker ps --format '{{json .}}'`` rows (``Names``, ``Image``, ``Ports``)."""
    import os

    configured = (os.environ.get("KAZMA_DB_CONTAINER") or "").strip()
    problems = []
    for row in rows:
        image = str(row.get("Image") or "")
        name = str(row.get("Names") or "")
        if not (_SERVICE_NAME.search(name) or (configured and name == configured)):
            continue
        for m in _MAPPING.finditer(str(row.get("Ports") or "")):
            if m.group("host") not in _LOOPBACK:
                problems.append(
                    f"container {name} ({image}) publishes port {m.group('port')} on "
                    f"{m.group('host')}: reachable from the network, not only this machine"
                )
    return sorted(set(problems))


def _docker_rows() -> list[dict[str, Any]] | None:
    """``docker ps``'s rows, or None when Docker cannot be asked."""
    from kazma_core.docker_cli import find_docker_cli
    from kazma_core.security.child_env import tool_child_env

    docker = find_docker_cli()
    if not docker:
        return None
    try:
        out = subprocess.run(
            [docker, "ps", "--format", "{{json .}}"], capture_output=True, text=True,
            timeout=_DOCKER_TIMEOUT_S, env=tool_child_env(), check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.info("[security] docker ps did not answer: the published-port check is skipped")
        return None
    if out.returncode != 0:
        return None
    rows = []
    for line in out.stdout.splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _inspect_container(name: str) -> dict[str, Any] | None:
    """``docker inspect``'s record of *name*, or None when Docker cannot say."""
    from kazma_core.docker_cli import find_docker_cli
    from kazma_core.security.child_env import tool_child_env

    docker = find_docker_cli()
    if not docker:
        return None
    try:
        out = subprocess.run(
            [docker, "inspect", "--type", "container", name], capture_output=True, text=True,
            timeout=_DOCKER_TIMEOUT_S, env=tool_child_env(), check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        records = json.loads(out.stdout)
    except ValueError:
        return None
    return records[0] if isinstance(records, list) and records and isinstance(records[0], dict) else None


def searxng_secret_problems(rows: Iterable[dict[str, Any]] | None = None) -> tuple[list[str], list[str]]:
    """``(problems, what could not be checked)`` for Kazma's SearXNG
    containers: each one running on the placeholder secret key. Blocking."""
    from kazma_core.security.searxng_secret import uses_placeholder

    rows = _docker_rows() if rows is None else rows
    if rows is None:
        return [], ["SearXNG's secret key (Docker did not answer)"]
    problems: list[str] = []
    unchecked: list[str] = []
    for row in rows:
        name = str(row.get("Names") or "")
        if not _SEARXNG_NAME.search(name):
            continue
        info = _inspect_container(name)
        verdict = uses_placeholder(info) if info is not None else None
        if verdict is None:
            unchecked.append(f"SearXNG's secret key in {name}")
        elif verdict:
            problems.append(
                f"container {name} runs SearXNG on the placeholder secret key shipped in "
                "deploy/searxng/settings.yml: run `kazma setup install --with private_search` "
                "(it keeps a generated SEARXNG_SECRET in .env and starts the container again)"
            )
    return problems, unchecked


def _superuser_problem(answer: dict[str, Any] | None) -> str | None:
    """The problem a ``pg_roles`` answer for Kazma's role shows, or None."""
    if answer and answer.get("rolsuper"):
        name = answer.get("rolname") or "Kazma's role"
        return (f"Kazma signs in to Postgres as {name}, a superuser: it can read every "
                "database on the server and run programs on it")
    return None


def _role_answer() -> dict[str, Any] | None:
    from kazma_core.db.postgres_pool import get_postgres_pool

    pool = get_postgres_pool()
    if pool is None:
        return None
    return pool.execute_one("SELECT rolname, rolsuper FROM pg_roles WHERE rolname = current_user")


def exposure_problems() -> tuple[list[str], list[str]]:
    """``(problems, what could not be checked)``. Blocking."""
    problems: list[str] = []
    unchecked: list[str] = []
    rows = _docker_rows()
    if rows is None:
        unchecked.append("published ports (Docker did not answer)")
    else:
        problems += _published_problems(rows)
    from kazma_core.db.pg_helpers import store_errors

    try:
        problem = _superuser_problem(_role_answer())
    except store_errors() as exc:  # a database that cannot answer is reported, never fatal
        unchecked.append(f"the database role ({type(exc).__name__})")
    else:
        if problem:
            problems.append(problem)
    return problems, unchecked


def report_exposure() -> list[str]:
    """Say at boot what is exposed: a WARNING each and one ops alert. A
    SearXNG container on the placeholder key is a WARNING too, no alert."""
    problems, _unchecked = exposure_problems()
    for problem in problems:
        logger.warning("[security] %s", problem)
    secret_problems, _ = searxng_secret_problems()
    for problem in secret_problems:
        logger.warning("[security] %s", problem)
    if problems:
        from kazma_core.observability.ops_alerts import alert

        alert(
            "security.service_exposed",
            "A service of Kazma's is reachable beyond this machine",
            "; ".join(problems)
            + ". Publish container ports on 127.0.0.1 (the compose files do since 2026-11) "
            "and give Kazma a database role that is not a superuser "
            "(deploy/postgres/kazma_app_role.sql; docs/docs/ops/postgres-and-saas.md).",
            severity="warn",
        )
    return problems
