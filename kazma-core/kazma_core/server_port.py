"""Kazma's own port: one answer to "which port", and whether a port can be used.

Kazma's page listens on ``KAZMA_PORT``, 9090 when it is not set. Until
2026-11-13 that answer was written out in some twenty places (the CLI's
clients, the OAuth redirect addresses, the IDE note, the importer, the
guard's fallbacks), most of them reading the environment alone: a port kept
in the install's ``.env`` reached the server but not a ``kazma`` command run
from another shell, and a client then knocked on 9090. The owner asked for
the port to be chosen at install (2026-11-13), so every reader asks here.

* :func:`server_port` -- the environment's ``KAZMA_PORT``, else the
  install's ``.env`` files (``env_files.env_file_ladder`` without the launch
  folder, the most specific last; one key read, nothing loaded), else
  :data:`DEFAULT_PORT`. ``serve.py`` binds the same answer (it loads the same
  files first), and the guard keeps a standard-library copy
  (``kazma_guard.install_port``), held equal by
  ``tests/test_server_port.py``.
* :func:`port_problem` -- why a port cannot be Kazma's, or None: not a port,
  below 1024 (the system's), one Kazma's own services use, the guard's
  document-broker range, a range Windows keeps for itself (Hyper-V and WSL
  reserve blocks: ``netsh ... show excludedportrange``, read only), or
  another program listening. The port the running Kazma already holds is
  its own, not "in use".
* :func:`first_free_port` -- the first port from 9090 up with no problem.
"""

from __future__ import annotations

import errno
import os
import re
import socket
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

__all__ = [
    "BROKER_RANGE",
    "DEFAULT_PORT",
    "KAZMA_SERVICE_PORTS",
    "configured_port",
    "first_free_port",
    "local_url",
    "port_problem",
    "server_port",
]

#: Kazma's page when nothing chose another port.
DEFAULT_PORT = 9090
#: Below this a port is the system's: Linux lets only root listen there.
LOWEST_PORT = 1024
HIGHEST_PORT = 65535
#: Ports Kazma's own services listen on (a page there would fight them).
KAZMA_SERVICE_PORTS: dict[int, str] = {
    5432: "Kazma's database (Postgres)",
    2376: "Kazma's container engine",
    8088: "SearXNG, Kazma's private search",
    7474: "Neo4j's page",
    7687: "Neo4j, Kazma's memory graph",
}
#: The guard gives the document worker broker a loopback port from this
#: range (``kazma_guard.BROKER_PORT_RANGE``), chosen while Kazma is stopped,
#: so a page port there could be taken before Kazma starts.
BROKER_RANGE = (20000, 29999)
_KEY = "KAZMA_PORT"


def _key_in(path: Path, name: str = _KEY) -> str | None:
    """*name*'s value in one env file (the last line setting it), or None."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    found = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if sep and key.strip() == name:
            found = value.split(" #", 1)[0].strip().strip("'\"")
    return found


def _env_files() -> list[Path]:
    from kazma_core.env_files import env_file_ladder

    try:
        return env_file_ladder(include_cwd=False)
    except OSError:  # no home folder to read: the environment alone decides
        return []


def _as_port(raw: str | None) -> int | None:
    text = (raw or "").strip()
    if not text.isdigit():
        return None
    port = int(text)
    return port if 1 <= port <= HIGHEST_PORT else None


def configured_port(env_files: list[Path] | None = None) -> int | None:
    """The port set for this install, or None when none is (or it is no port)."""
    raw = (os.environ.get(_KEY) or "").strip()
    if not raw:
        for path in env_files if env_files is not None else _env_files():
            value = _key_in(path)
            if value:
                raw = value
    return _as_port(raw)


def server_port(env_files: list[Path] | None = None) -> int:
    """The port Kazma's page listens on."""
    return configured_port(env_files) or DEFAULT_PORT


def local_url(path: str = "", *, port: int | None = None) -> str:
    """Kazma's page on this machine: ``http://127.0.0.1:<port><path>``."""
    return f"http://127.0.0.1:{port or server_port()}{path}"


# -- whether a port can be Kazma's ------------------------------------------------------------

_RANGE_LINE = re.compile(r"^\s*(\d{1,5})\s+(\d{1,5})\b")


def _excluded_port_ranges(run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> list[tuple[int, int]]:
    """The TCP port ranges Windows keeps for itself (Hyper-V, WSL, Docker).

    Read with ``netsh interface ipv4 show excludedportrange protocol=tcp``:
    the command only reads. Nothing elsewhere, or when netsh cannot answer
    (then binding the port says it).
    """
    if sys.platform != "win32":
        return []
    try:
        done = run(["netsh", "interface", "ipv4", "show", "excludedportrange", "protocol=tcp"],
                   capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    ranges = []
    for line in (done.stdout or "").splitlines():
        match = _RANGE_LINE.match(line)
        if match:
            low, high = int(match.group(1)), int(match.group(2))
            if 0 < low <= high <= HIGHEST_PORT:
                ranges.append((low, high))
    return ranges


def _listening(port: int, host: str) -> bool:
    """Something answers a connection to *host*:*port* (a listener on any
    address of this machine, which a bind test on Windows can miss)."""
    target = "127.0.0.1" if host in ("", "0.0.0.0") else host  # noqa: S104 -- the wildcard is read, never bound
    try:
        with socket.create_connection((target, port), timeout=0.5):
            return True
    except OSError:
        return False


def _bind_refusal(port: int, host: str) -> str | None:
    """Why this process could not listen on *host*:*port*, or None."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind((host or "127.0.0.1", port))
        return None
    except OSError as exc:
        if exc.errno in (errno.EACCES, 10013) or getattr(exc, "winerror", None) == 10013:
            return "the system does not let programs listen on it"
        return "another program already listens on it"
    finally:
        sock.close()


def port_problem(port: object, *, current: int | None = None, host: str | None = None,
                 ranges: list[tuple[int, int]] | None = None,
                 in_use: Callable[[int, str], str | None] | None = None) -> str | None:
    """Why *port* cannot be Kazma's page, in words, or None when it can.

    *current* is the port this install's Kazma already listens on: it is held
    by Kazma, not taken. *ranges* (Windows' excluded ranges) and *in_use*
    (a bind and connect test) are for tests.
    """
    try:
        number = int(str(port).strip())
    except (TypeError, ValueError):
        return f"{port!r} is not a port number"
    if not 1 <= number <= HIGHEST_PORT:
        return f"{number} is not a port (1-{HIGHEST_PORT})"
    if number < LOWEST_PORT:
        return f"{number} is below {LOWEST_PORT}: those ports are the system's"
    if number in KAZMA_SERVICE_PORTS:
        return f"{number} is used by {KAZMA_SERVICE_PORTS[number]}"
    if BROKER_RANGE[0] <= number <= BROKER_RANGE[1]:
        return (f"{number} is in {BROKER_RANGE[0]}-{BROKER_RANGE[1]}, the range Kazma's guard "
                "gives its document worker")
    if current is not None and number == current:
        return None
    for low, high in ranges if ranges is not None else _excluded_port_ranges():
        if low <= number <= high:
            return f"{number} is in {low}-{high}, a range Windows keeps for itself"
    bind_host = host if host is not None else (os.environ.get("KAZMA_HOST") or "127.0.0.1").strip()
    if in_use is not None:
        return in_use(number, bind_host)
    refusal = _bind_refusal(number, bind_host)
    if refusal is None and _listening(number, bind_host):
        refusal = "another program already listens on it"
    return f"{number}: {refusal}" if refusal else None


def first_free_port(start: int = DEFAULT_PORT, *, current: int | None = None,
                    problem: Callable[[int], str | None] | None = None, tries: int = 1000) -> int:
    """The first port from *start* up that :func:`port_problem` accepts."""
    ranges = _excluded_port_ranges() if problem is None else []

    def check(port: int) -> str | None:
        if problem is not None:
            return problem(port)
        return port_problem(port, current=current, ranges=ranges)

    for port in range(start, min(start + tries, HIGHEST_PORT + 1)):
        if check(port) is None:
            return port
    raise RuntimeError(f"no port from {start} to {start + tries - 1} can be Kazma's")
