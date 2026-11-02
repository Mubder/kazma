"""Every port a shipped compose file publishes is on loopback unless asked.

The 2026-11 functional audit (FA-218) found the live Postgres, Neo4j and
SearXNG containers published on every interface: a port written as
``"5433:5432"`` is reachable from the whole network, and the database
answered there with a superuser login. A compose file now names the host
address of each port, ``127.0.0.1`` by default; publishing elsewhere is a
variable the operator sets on purpose (``PUBLISH_HOST``,
``SEARXNG_PUBLISH_HOST``).

The compose files are enumerated from git, so a new one is checked the day
it is added. The old port strings are the negative control.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]

_LOOPBACK = {"127.0.0.1", "::1", "[::1]", "localhost"}
# ``${NAME:-default}``: the address an unset variable leaves.
_VAR_DEFAULT = re.compile(r"^\$\{[A-Z0-9_]+:-(?P<default>[^}]*)\}$")


def _compose_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    pattern = re.compile(r"(^|/)(docker-)?compose[^/]*\.ya?ml$", re.IGNORECASE)
    return [REPO / p for p in out if pattern.search(p)]


def _host_address(port: object) -> str | None:
    """The host address a compose port entry publishes on, None for every one.

    Short syntax is ``[HOST:]HOST_PORT:CONTAINER_PORT[/proto]``; variables
    may stand for any part. Long syntax has ``host_ip``.
    """
    if isinstance(port, dict):
        return port.get("host_ip")
    text = str(port).split("/", 1)[0]
    parts = re.findall(r"\$\{[^}]*\}|\[[^\]]*\]|[^:]+", text)
    if len(parts) < 3:
        return None
    return parts[0]


def _is_loopback(address: str | None) -> bool:
    if address is None:
        return False
    match = _VAR_DEFAULT.match(address)
    if match:
        address = match.group("default")
    return address in _LOOPBACK


def _unsafe_ports(compose_text: str) -> list[str]:
    doc = yaml.safe_load(compose_text) or {}
    bad = []
    for name, service in (doc.get("services") or {}).items():
        for port in (service or {}).get("ports") or []:
            if not _is_loopback(_host_address(port)):
                bad.append(f"{name}: {port}")
    return bad


def test_the_shipped_compose_files_are_found() -> None:
    names = {p.name for p in _compose_files()}
    assert {"docker-compose.yml", "docker-compose.postgres.yml"} <= names


@pytest.mark.parametrize("path", _compose_files(), ids=lambda p: p.name)
def test_every_published_port_is_on_loopback_by_default(path: Path) -> None:
    assert _unsafe_ports(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "old",
    [
        '"5433:5432"',
        '"${HOST_PORT:-9090}:8000"',
        '"${SEARXNG_HOST_PORT:-8088}:8080"',
        '"0.0.0.0:7687:7687"',
        '"${PUBLISH_HOST:-0.0.0.0}:9090:8000"',
        "{target: 5432, published: 5433}",
    ],
)
def test_the_gate_refuses_a_port_open_to_the_network(old: str) -> None:
    """Negative control: the live shapes FA-218 found, and their kin."""
    compose = f"services:\n  db:\n    image: x\n    ports:\n      - {old}\n"
    assert _unsafe_ports(compose) != []


@pytest.mark.parametrize(
    "good",
    [
        '"127.0.0.1:5433:5432"',
        '"${PUBLISH_HOST:-127.0.0.1}:${HOST_PORT:-9090}:8000"',
        '"[::1]:7687:7687"',
        "{target: 5432, published: 5433, host_ip: 127.0.0.1}",
    ],
)
def test_the_gate_passes_a_loopback_port(good: str) -> None:
    compose = f"services:\n  db:\n    image: x\n    ports:\n      - {good}\n"
    assert _unsafe_ports(compose) == []
