"""Every container service Kazma installs is held to its compose definition.

``kazma_core.container_services`` is the one table the install builds its
container steps from (SearXNG and Neo4j, the owner's request of 2026-11-13).
Each row must match its compose file: the service exists under the container
name the guard and the move tool look for, publishes only on this machine, at
the ports Kazma is told to reach, starts again with the engine, takes each
secret setup generates, and runs an image pinned by digest. And every image
any shipped compose file runs is pinned (a moving tag runs whatever the
registry serves that day); a locally built image is the one exception.
Each rule is checked on a planted violation first (the negative controls).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

from kazma_core import container_services as cs
from kazma_core import install_components as ic

ROOT = Path(__file__).resolve().parents[1]
_DEFAULT = re.compile(r"\$\{[A-Z0-9_]+:-([^}]*)\}")
_PINNED = re.compile(r"^[^@\s]+:[^@\s]+@sha256:[0-9a-f]{64}$")


def _resolve(text: str) -> str:
    """A compose string with each ``${VAR:-default}`` replaced by its default."""
    return _DEFAULT.sub(lambda m: m.group(1), str(text))


def _compose(rel: str) -> dict:
    return yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))


def _published(service: dict) -> list[tuple[str, int]]:
    """(host address, host port) of each published port."""
    out = []
    for entry in service.get("ports") or []:
        host, _, rest = _resolve(entry).rpartition(":")  # host:port:container
        address, _, port = host.rpartition(":")
        out.append((address, int(port)))
    return out


def service_problems(svc: cs.ContainerService, compose: dict) -> list[str]:
    """What of *svc*'s row its compose definition contradicts."""
    problems = []
    service = (compose.get("services") or {}).get(svc.service)
    if service is None:
        return [f"{svc.compose_file} has no service {svc.service!r}"]
    if service.get("container_name") != svc.container:
        problems.append(f"container_name is {service.get('container_name')!r}, not {svc.container!r}")
    if svc.profile and svc.profile not in (service.get("profiles") or []):
        problems.append(f"not in the {svc.profile!r} profile")
    published = _published(service)
    if not published or any(address != "127.0.0.1" for address, _ in published):
        problems.append(f"publishes beyond this machine: {published}")
    ports = {port for _, port in published}
    for _, url in svc.kazma_env:
        parsed = urlsplit(url)
        if parsed.scheme and parsed.port and parsed.port not in ports:
            problems.append(f"Kazma is told {url}, which is not published")
    ready = urlsplit(svc.ready_url).port
    if ready not in ports:
        problems.append(f"ready_url {svc.ready_url} is not published")
    if service.get("restart") != "unless-stopped":
        problems.append(f"restart is {service.get('restart')!r}, not 'unless-stopped'")
    environment = yaml.safe_dump(service.get("environment") or {})
    for name in svc.secrets:
        if "${" + name not in environment:
            problems.append(f"the service never reads {name}")
    if not _PINNED.match(str(service.get("image") or "")):
        problems.append(f"image {service.get('image')!r} is not pinned by digest")
    if not service.get("healthcheck"):
        problems.append("no healthcheck")
    return problems


@pytest.mark.parametrize("svc", cs.SERVICES, ids=lambda s: s.part)
def test_every_container_service_matches_its_compose_definition(svc):
    assert service_problems(svc, _compose(svc.compose_file)) == []
    part = ic.by_id(svc.part)
    assert part.best_effort and "docker" in part.needs, "a container part is best effort and needs docker"
    assert svc.url_var in dict(svc.kazma_env)


def test_control_a_drifted_row_is_caught():
    import dataclasses

    searxng = cs.by_part("private_search")
    compose = _compose(searxng.compose_file)
    assert service_problems(dataclasses.replace(searxng, container="kazma-search"), compose)
    assert service_problems(dataclasses.replace(searxng, secrets=("NOT_READ_BY_IT",)), compose)
    bad = yaml.safe_load(yaml.safe_dump(compose))
    bad["services"]["searxng"]["ports"] = ["0.0.0.0:8088:8080"]
    bad["services"]["searxng"]["image"] = "docker.io/searxng/searxng:latest"
    problems = service_problems(searxng, bad)
    assert any("beyond this machine" in p for p in problems) and any("pinned" in p for p in problems)


def test_every_container_part_has_a_row():
    """A part that needs containers and runs a service is in the table: the
    install starts nothing it has no row for."""
    assert {s.part for s in cs.SERVICES} == {"private_search", "graph_neo4j"}
    assert all(ic.by_id(s.part) for s in cs.SERVICES)


def _tracked_compose_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [f for f in out.splitlines() if re.search(r"(^|/)(docker-)?compose[^/]*\.ya?ml$", f)]


def unpinned_images(files: dict[str, str]) -> list[str]:
    """``file: image`` for every image a compose file runs that is neither
    pinned by digest nor built here (a service with ``build``, or an image
    named ``:local``)."""
    found = []
    for name, text in files.items():
        data = yaml.safe_load(text) or {}
        for service, body in (data.get("services") or {}).items():
            image = str((body or {}).get("image") or "")
            if not image or "build" in (body or {}) or image.endswith(":local"):
                continue
            if not _PINNED.match(image):
                found.append(f"{name}: {service}: {image}")
    return found


def test_every_shipped_compose_image_is_pinned_by_digest():
    files = {f: (ROOT / f).read_text(encoding="utf-8") for f in _tracked_compose_files()}
    assert "docker-compose.yml" in files and "deploy/docker-compose.neo4j.yml" in files
    assert unpinned_images(files) == []
    # Negative control: the lines these files held before 2026-11-13.
    planted = {"old.yml": "services:\n  db:\n    image: postgres:16-alpine\n"
                          "  web:\n    image: nginx:1.27-alpine\n  graph:\n    image: neo4j:5\n"}
    assert len(unpinned_images(planted)) == 3
