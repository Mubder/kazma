"""Kazma's container services: one table for every install part that runs in a container.

Private web search (SearXNG) and the memory graph's copy (Neo4j) run on the
container engine: Kazma's own on Windows (Docker Engine in its WSL distro,
started at boot), the system's Docker on Linux. Each row says where its compose
definition is, the secrets ``kazma setup install`` generates into the
install's ``.env`` before it starts (compose reads the same file with
``--env-file``), what Kazma is told to reach it, and the address that answers
once it is ready. The install builds its steps from this table and judges each
service by it (``kazma_cli.install``); a new service is a row here and a compose
definition, nothing else. ``tests/test_container_services.py`` holds every row
to its compose file: the service exists, binds this machine only, starts again
with the engine, requires its secrets and runs a pinned image.

The owner's request (2026-11-13): SearXNG and Neo4j install by default, as
Kazma and its engine run as services.
"""

from __future__ import annotations

import os
import secrets as _secrets
from dataclasses import dataclass
from pathlib import Path

__all__ = ["SERVICES", "ContainerService", "by_part", "compose_up_argv", "ensure_secret",
           "secret_is_set"]


@dataclass(frozen=True)
class ContainerService:
    """One install part that runs in a container.

    ``compose_file``: relative to Kazma's source folder. ``project``: the
    compose project (``-p``); empty is compose's own (the file's folder).
    ``secrets``: ``.env`` variables setup generates, kept when set; a value in
    ``placeholders`` does not count. ``kazma_env``: what setup writes into
    ``.env`` so Kazma reaches it (``url_var`` names the address).
    ``ready_url``: answers 2xx once the service is ready.
    """

    part: str
    title: str
    compose_file: str
    service: str
    container: str
    url_var: str
    ready_url: str
    kazma_env: tuple[tuple[str, str], ...]
    secrets: tuple[str, ...] = ()
    placeholders: tuple[str, ...] = ()
    profile: str = ""
    project: str = ""
    ready_wait_s: float = 120.0


SERVICES: tuple[ContainerService, ...] = (
    ContainerService(
        part="private_search", title="SearXNG",
        compose_file="docker-compose.yml", service="searxng", container="kazma-searxng", profile="search",
        secrets=("SEARXNG_SECRET",), placeholders=("change-me-in-production-use-long-random",),
        url_var="KAZMA_SEARXNG_URL", kazma_env=(("KAZMA_SEARXNG_URL", "http://127.0.0.1:8088"),),
        # The boot drill (2026-11-12): asked 2 s after `compose up`, it
        # answered 49 s later.
        ready_url="http://127.0.0.1:8088/healthz", ready_wait_s=120.0),
    ContainerService(
        part="graph_neo4j", title="Neo4j",
        compose_file="deploy/docker-compose.neo4j.yml", service="kazma-neo4j", container="kazma-neo4j",
        project="kazma-graph", secrets=("KAZMA_NEO4J_PASSWORD",),
        url_var="KAZMA_NEO4J_URL",
        kazma_env=(("KAZMA_NEO4J_URL", "bolt://127.0.0.1:7687"), ("KAZMA_NEO4J_DEFAULT", "1")),
        # Neo4j's browser endpoint answers once the database has started;
        # a first start creates the store and takes longer than SearXNG.
        ready_url="http://127.0.0.1:7474/", ready_wait_s=180.0),
)

_BY_PART = {s.part: s for s in SERVICES}


def by_part(part: str) -> ContainerService | None:
    """The container service an install part runs, or None."""
    return _BY_PART.get(part)


def _file_value(path: Path, name: str) -> str | None:
    """*name*'s value in the dotenv file *path* (the last line naming it), or None."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    found = None
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and not key.lstrip().startswith("#") and key.strip() == name:
            found = value.strip()
    return found


def secret_is_set(value: str | None, placeholders: tuple[str, ...] = ()) -> bool:
    """A real value: something other than empty or a shipped placeholder."""
    text = (value or "").strip().strip('"').strip("'")
    return bool(text) and text not in placeholders


def ensure_secret(env_path: Path, name: str, placeholders: tuple[str, ...] = ()) -> bool:
    """Keep a generated *name* in *env_path* when none is set; True when one was written.

    An owner's value -- in the file or in the environment compose runs with --
    or one written before is kept; only an empty line or a placeholder is
    replaced. The file is restricted to this account before the value is
    written (``env_files.persist_generated_secret``). Raises ``OSError`` when
    the file cannot be written.
    """
    if secret_is_set(os.environ.get(name), placeholders) or secret_is_set(_file_value(env_path, name), placeholders):
        return False
    from kazma_core.env_files import persist_generated_secret

    persist_generated_secret(name, _secrets.token_hex(32), path=env_path)
    return True


def compose_up_argv(svc: ContainerService, docker: str, root: Path, env_path: Path) -> tuple[str, ...]:
    """``docker compose ... up -d <service>`` for *svc*, reading *env_path*."""
    argv = [docker, "compose", "-f", str(Path(root) / svc.compose_file), "--env-file", str(env_path)]
    if svc.project:
        argv += ["-p", svc.project]
    if svc.profile:
        argv += ["--profile", svc.profile]
    return (*argv, "up", "-d", svc.service)
