"""Tell the guard which containers Kazma's configured addresses point to.

Live 2026-11-03: topgrade updated Docker Desktop and the engine stayed
stopped. Kazma's database (the ``kazma-db-win`` container) was gone, and the
guard restarted Kazma 34 times over eleven hours: "never became healthy".
Nothing on the machine was going to start Docker again. The fix is in the
guard: it now starts the Docker engine, and any stopped container Kazma
depends on (``scripts/service/kazma_guard.py``, ``Dependencies``). The guard
never imports the app and never reads ``.env``, so it cannot tell which
containers those are. This module tells it.

What it writes, to the file the guard hands over in
``KAZMA_GUARD_DEPENDENCIES_FILE``::

    {"version": 1, "containers": [{"name": ..., "ports": [...], "for": [...]}],
     "engine": "kazma" | "desktop" | "", "updated": <epoch>}

``engine`` is the container engine chosen (``containers.engine``, or the one
setup installed; ``""``: none chosen, the docker CLI's current context;
2026-11-12, :mod:`kazma_core.container_engine`): the guard pins the same
Docker context for its own docker calls and the document broker's, and
brings Kazma's engine back -- never Docker Desktop -- when it is Kazma's
that does not answer.

How a container gets on the list:

* Kazma's addresses are read from where Kazma reads them: the database
  (``KAZMA_DATABASE_URL`` / ``DATABASE_URL``), the memory mirror
  (``memory.backends.state.url``), Neo4j (its variables, else
  ``memory.backends.graph.url`` when the graph provider is neo4j) and SearXNG
  (``KAZMA_SEARXNG_URL``, ``search.searxng_url``).
* Only addresses on this machine count (127.0.0.1, localhost, ::1). A
  container counts when it publishes that port on a loopback or wildcard
  address (``HostConfig.PortBindings``, which a stopped container keeps).
* SearXNG's built-in default (``127.0.0.1:8088``) is a guess, not a
  configured address. It counts only for a container named like Kazma's own,
  so another project's container on that port is never started for Kazma.
* ``KAZMA_DB_CONTAINER`` names the database's container outright -- when it
  publishes the port the database's address uses. After the move to the
  Postgres service (``kazma setup postgres --move-from-container``) the
  variable may still name the old container, which publishes another port.
* When two containers could be the one, the running one wins. With none
  running, the one the last record named wins. If neither settles it, the
  address is skipped and the reason is logged.
* With none running, a port that answers is held by a server outside Docker
  (the Postgres service): no stopped container is named for it. Live
  2026-11-06 another project's stopped ``kca-postgres``, publishing 5432, was
  named for the memory mirror the native service serves on 5432.
* When Docker cannot be asked, nothing is written: the last record stands,
  and the engine being down is what the guard must fix. When a setting
  cannot be read, its containers from the last record are kept.

Written only when the list changes, so the file names what Kazma needs now,
not every check. Blocking (``docker inspect``, a settings read): run in a
thread. Nothing happens when no guard handed a file.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

__all__ = [
    "RECORD_FILE_ENV",
    "Endpoint",
    "guard_dependencies_sweep",
    "start_dependency_records",
]

#: The record file's path, handed over by the guard that started Kazma.
RECORD_FILE_ENV = "KAZMA_GUARD_DEPENDENCIES_FILE"
RECORD_VERSION = 1
_DOCKER_TIMEOUT_S = 15
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
#: A published port Kazma's loopback address reaches.
_REACHING_HOST_IPS = {"", "0.0.0.0", "127.0.0.1", "::", "::1"}
_DEFAULT_PORTS = {
    "postgres": 5432, "postgresql": 5432,
    "bolt": 7687, "bolt+s": 7687, "bolt+ssc": 7687,
    "neo4j": 7687, "neo4j+s": 7687, "neo4j+ssc": 7687,
    "http": 80, "https": 443,
}
#: A container name Docker accepts: also what makes it safe as an argument.
CONTAINER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
#: A container of a service Kazma runs beside itself, by name (the rule the
#: security report uses, ``security/service_exposure.py``).
_KAZMA_SERVICE = re.compile(r"(?i)^kazma[-_].*(db|postgres|pg|neo4j|searxng)")
#: SearXNG's addresses Kazma tries when none is configured
#: (``tools/web_search._searxng_candidate_bases``), on this machine.
_SEARXNG_GUESSES = ("http://127.0.0.1:8088", "http://127.0.0.1:8080")

_publish_lock = threading.Lock()


@dataclass(frozen=True)
class Endpoint:
    """An address of a service Kazma uses, on this machine."""

    purpose: str
    port: int
    configured: bool = True


# ── Kazma's addresses ─────────────────────────────────────────────────


def _loopback_port(address: str) -> int | None:
    """The port of a URL or a key=value DSN on this machine, else None."""
    text = (address or "").strip()
    if not text:
        return None
    if "://" not in text:
        host = re.search(r"(?:^|\s)host\s*=\s*'?([^\s']+)", text)
        port = re.search(r"(?:^|\s)port\s*=\s*'?(\d+)", text)
        if not host or host.group(1).lower() not in _LOOPBACK_HOSTS:
            return None
        return int(port.group(1)) if port else 5432
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return None
    if host not in _LOOPBACK_HOSTS:
        return None
    if port is None:
        scheme = parts.scheme.lower()
        # "postgresql+psycopg" is SQLAlchemy's spelling of the same server.
        port = _DEFAULT_PORTS.get(scheme) or _DEFAULT_PORTS.get(scheme.split("+")[0])
    return port if port and 0 < port < 65536 else None


def _setting(key: str) -> Any:
    from kazma_core.config_store import get_config_store

    return get_config_store().get(key)


def _env(names: tuple[str, ...]) -> str:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def _endpoints() -> tuple[list[Endpoint], set[str]]:
    """Kazma's addresses on this machine, and the purposes whose settings
    could not be read (their containers are carried over)."""
    from kazma_core.db.backend import get_database_url
    from kazma_core.safety.deployment_policy import policy_read_failures

    found: list[Endpoint] = []
    unread: set[str] = set()

    def add(purpose: str, address: str, *, configured: bool = True) -> None:
        port = _loopback_port(address)
        if port is not None:
            found.append(Endpoint(purpose, port, configured))

    add("database", get_database_url() or "")

    try:
        add("memory mirror", str(_setting("memory.backends.state.url") or ""))
    except policy_read_failures():
        unread.add("memory mirror")

    graph = _env(("KAZMA_NEO4J_URL", "NEO4J_URI", "NEO4J_URL"))
    if graph:
        add("graph", graph)
    else:
        try:
            if str(_setting("memory.backends.graph.provider") or "").strip().lower() == "neo4j":
                add("graph", str(_setting("memory.backends.graph.url") or "bolt://localhost:7687"))
            elif (_env(("KAZMA_GRAPH_PROVIDER",)).lower() == "neo4j"
                  or _env(("KAZMA_NEO4J_DEFAULT",)).lower() in {"1", "true", "yes", "on"}):
                add("graph", "bolt://localhost:7687")
        except policy_read_failures():
            unread.add("graph")

    search = _env(("KAZMA_SEARXNG_URL",))
    if search:
        add("search", search)
    else:
        try:
            search = str(_setting("search.searxng_url") or "").strip()
        except policy_read_failures():
            unread.add("search")
            search = ""
        if search:
            add("search", search)
        elif "search" not in unread:
            for guess in _SEARXNG_GUESSES:
                add("search", guess, configured=False)
    return found, unread


# ── what Docker holds ─────────────────────────────────────────────────


def _docker(args: list[str]) -> subprocess.CompletedProcess[str] | None:
    from kazma_core.docker_cli import find_docker_cli
    from kazma_core.security.child_env import tool_child_env

    docker = find_docker_cli()
    if not docker:
        return None
    try:
        out = subprocess.run(
            [docker, *args], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=_DOCKER_TIMEOUT_S, env=tool_child_env(), check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out if out.returncode == 0 else None


def _containers() -> list[dict[str, Any]] | None:
    """Every container's ``docker inspect`` answer, or None when Docker
    cannot be asked."""
    ids = _docker(["ps", "-aq", "--no-trunc"])
    if ids is None:
        return None
    names = ids.stdout.split()
    if not names:
        return []
    answer = _docker(["inspect", *names])
    if answer is None:
        return None
    try:
        rows = json.loads(answer.stdout or "[]")
    except ValueError:
        return None
    return rows if isinstance(rows, list) else None


def _published_ports(row: dict[str, Any]) -> set[int]:
    """The host ports a container publishes where a loopback address reaches."""
    bindings = ((row.get("HostConfig") or {}).get("PortBindings") or {})
    ports: set[int] = set()
    if not isinstance(bindings, dict):
        return ports
    for entries in bindings.values():
        for entry in entries or ():
            if not isinstance(entry, dict):
                continue
            if str(entry.get("HostIp") or "") not in _REACHING_HOST_IPS:
                continue
            try:
                ports.add(int(entry.get("HostPort") or 0))
            except (TypeError, ValueError):
                continue
    ports.discard(0)
    return ports


def _port_answers(port: int) -> bool:
    """Something on this machine takes a connection on *port* now."""
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            return True
    except OSError:
        return False


def _name(row: dict[str, Any]) -> str:
    return str(row.get("Name") or "").lstrip("/")


def _running(row: dict[str, Any]) -> bool:
    return bool((row.get("State") or {}).get("Running"))


# ── the decision ──────────────────────────────────────────────────────


def _match_containers(
    endpoints: list[Endpoint],
    inspected: list[dict[str, Any]],
    previous: list[dict[str, Any]],
    *,
    unread: frozenset[str] | set[str] = frozenset(),
    named_db: str = "",
    answering: Callable[[int], bool] = lambda port: False,
) -> tuple[list[dict[str, Any]], list[str]]:
    """``(the containers Kazma depends on, what could not be decided)``.

    Pure but for *answering* (is a port on this machine answered now): the
    rules of the module docstring, on ``docker inspect`` rows and the last
    record's entries."""
    rows = {_name(r): r for r in inspected if CONTAINER_NAME.match(_name(r))}
    before = {str(e.get("name")): e for e in previous if isinstance(e, dict)}
    chosen: dict[str, dict[str, Any]] = {}
    undecided: list[str] = []

    def take(name: str, purpose: str, port: int | None) -> None:
        entry = chosen.setdefault(name, {"name": name, "ports": [], "for": []})
        if port is not None and port not in entry["ports"]:
            entry["ports"].append(port)
        if purpose not in entry["for"]:
            entry["for"].append(purpose)

    named = named_db.strip()
    for endpoint in endpoints:
        if (endpoint.purpose == "database" and named and named in rows
                and endpoint.port in _published_ports(rows[named])):
            take(named, "database", endpoint.port)
            continue
        candidates = [
            name for name, row in rows.items()
            if endpoint.port in _published_ports(row)
            and (endpoint.configured or _KAZMA_SERVICE.search(name))
        ]
        if not candidates:
            continue  # a native service, or nothing at all: not Docker's to start
        running = [n for n in candidates if _running(rows[n])]
        if len(running) == 1:
            take(running[0], endpoint.purpose, endpoint.port)
            continue
        if not running and answering(endpoint.port):
            # No candidate runs, yet the port answers: a server outside Docker
            # (the Postgres service kazma setup installed) holds it.
            continue
        pool = running or candidates
        remembered = [n for n in pool if n in before]
        if len(remembered) == 1:
            take(remembered[0], endpoint.purpose, endpoint.port)
        elif len(pool) == 1:
            take(pool[0], endpoint.purpose, endpoint.port)
        else:
            undecided.append(f"{endpoint.purpose} on port {endpoint.port}: {', '.join(sorted(pool))}")

    for name, entry in before.items():
        if name in chosen or not CONTAINER_NAME.match(name):
            continue
        purposes = [p for p in entry.get("for") or () if p in unread]
        if purposes and name in rows:
            for purpose in purposes:
                take(name, purpose, None)
            chosen[name]["ports"] = sorted(set(chosen[name]["ports"]) | {
                int(p) for p in entry.get("ports") or () if str(p).isdigit()})
    out = []
    for name in sorted(chosen):
        entry = chosen[name]
        out.append({"name": name, "ports": sorted(entry["ports"]), "for": sorted(entry["for"])})
    return out, undecided


# ── the record ────────────────────────────────────────────────────────


def _record_path() -> Path | None:
    raw = (os.environ.get(RECORD_FILE_ENV) or "").strip()
    return Path(raw) if raw else None


def _read_whole(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_record(path: Path) -> list[dict[str, Any]]:
    containers = _read_whole(path).get("containers")
    return [c for c in containers or () if isinstance(c, dict)]


def _write_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            # The guard has it open this instant on Windows.
            if attempt == 19:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05)


def _publish_dependencies() -> bool:
    """Write the record when what Kazma depends on changed. True when written.

    Blocking (Docker, settings, a file): async callers use a thread."""
    path = _record_path()
    if path is None:
        return False
    from kazma_core.container_engine import engine_setting

    with _publish_lock:
        engine = engine_setting()
        whole = _read_whole(path)
        if str(whole.get("engine") or "") != engine:
            # The guard must follow a new choice whether or not Docker
            # answers now: written first, with the last list kept.
            whole = {"version": RECORD_VERSION, "containers": _read_record(path),
                     "engine": engine, "updated": time.time()}
            _write_atomic(path, whole)
            logger.info("[guard] the guard uses %s", {
                "kazma": "Kazma's container engine", "desktop": "Docker Desktop",
            }.get(engine, "the docker CLI's current context"))
        inspected = _containers()
        if inspected is None:
            logger.info("[guard] Docker did not answer: the guard's list of Kazma's containers is kept")
            return False
        endpoints, unread = _endpoints()
        previous = _read_record(path)
        containers, undecided = _match_containers(
            endpoints, inspected, previous, unread=unread,
            named_db=_env(("KAZMA_DB_CONTAINER",)), answering=_port_answers,
        )
        for line in undecided:
            logger.info("[guard] more than one container could serve %s; none is named for the guard", line)
        if containers == previous:
            return False
        _write_atomic(path, {"version": RECORD_VERSION, "containers": containers,
                             "engine": engine, "updated": time.time()})
        logger.info("[guard] Kazma depends on these containers: %s",
                    ", ".join(f"{c['name']} ({'/'.join(c['for'])})" for c in containers) or "none")
        return True


def start_dependency_records() -> None:
    """At boot (in a thread): tell the guard now. Nothing when no guard
    handed a file."""
    if _record_path() is None:
        return
    try:
        _publish_dependencies()
    except OSError:
        logger.warning("[guard] could not write the guard's list of Kazma's containers", exc_info=True)


def guard_dependencies_sweep() -> None:
    """The maintenance cadence's pass (15 min): a container recreated, or a
    setting changed, reaches the guard."""
    _publish_dependencies()
