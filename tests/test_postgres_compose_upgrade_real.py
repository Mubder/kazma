"""A real compose deployment moved from Postgres 16 to 18 (2026-11-13,
``scripts/pg_compose_upgrade.py``, ``kazma_core.postgres_compose_upgrade``).

A throwaway compose project on the developer's engine: the db service as
``docker-compose.postgres.yml`` ran it on 16, seeded; then the db service as
that file runs it now (read from the file: the 18 image, its own volume, the
entrypoint's check), with a stand-in ``kazma`` service.

* the negative control: ``docker compose up`` of the new file before the move
  starts no empty 18 -- the entrypoint refuses and names the command;
* the move from that state (compose already replaced the 16 container: the
  data is read from the 16 volume by a temporary 16 server): every row and
  sequence is in 18, the 16 volume unchanged;
* the move from a running 16 container, with the stand-in Kazma stopped
  during the copy and started after;
* a run after the move has nothing to do.

Opt-in: ``KAZMA_TEST_PG_COMPOSE_UPGRADE=1`` and a Docker engine.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.real_docker, pytest.mark.timeout(1200)]

ROOT = Path(__file__).resolve().parents[1]
OPT_IN = os.environ.get("KAZMA_TEST_PG_COMPOSE_UPGRADE") == "1"
SEED = """
CREATE TABLE kazma_settings (key text PRIMARY KEY, value text);
INSERT INTO kazma_settings VALUES ('system.lifecycle.last_boot_epoch', '1790000000.5');
CREATE TABLE kazma_chat_sessions (id serial PRIMARY KEY, title text);
INSERT INTO kazma_chat_sessions (title) SELECT 'chat ' || g FROM generate_series(1, 300) g;
INSERT INTO kazma_chat_sessions (title) VALUES ('محادثة');
"""


def _docker() -> str:
    from kazma_core.docker_cli import find_docker_cli

    return find_docker_cli() or "docker"


def _compose_files(folder: Path, name: str) -> tuple[Path, Path]:
    """The db service as the compose file ran it on 16, and as it runs it now."""
    current = yaml.safe_load((ROOT / "docker-compose.postgres.yml").read_text(encoding="utf-8"))
    db18 = dict(current["services"]["db"])
    db18["container_name"] = name
    db18.pop("ports", None)
    kazma = {"image": "alpine:3.20", "command": ["sleep", "infinity"], "depends_on": {"db": {"condition": "service_healthy"}}}
    new = {"services": {"db": db18, "kazma": kazma},
           "volumes": {"kazma_pgdata": None, "kazma_pgdata18": None}}
    from kazma_core.postgres_compose_upgrade import OLD_IMAGES

    db16 = {k: v for k, v in db18.items() if k not in ("entrypoint",)}
    db16["image"] = OLD_IMAGES[16]
    db16["environment"] = {k: v for k, v in db18["environment"].items() if k != "KAZMA_PG_UPGRADING"}
    db16["volumes"] = ["kazma_pgdata:/var/lib/postgresql/data"]
    old = {"services": {"db": db16, "kazma": kazma}, "volumes": {"kazma_pgdata": None}}
    (folder / ".env").write_text("POSTGRES_PASSWORD=drill-password-1\n", encoding="utf-8")
    old_file, new_file = folder / "old.yml", folder / "new.yml"
    old_file.write_text(yaml.safe_dump(old), encoding="utf-8")
    new_file.write_text(yaml.safe_dump(new), encoding="utf-8")
    return old_file, new_file


def _compose(file: Path, project: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run([_docker(), "compose", "-f", str(file), "-p", project, *args], capture_output=True,
                          text=True, timeout=600, check=check)


def _psql(name: str, sql: str) -> str:
    return subprocess.run([_docker(), "exec", name, "psql", "-U", "kazma", "-d", "kazma", "-At", "-c", sql],
                          capture_output=True, text=True, encoding="utf-8", timeout=120, check=True).stdout.strip()


def _ready(name: str, wait_s: float = 120) -> None:
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        done = subprocess.run([_docker(), "exec", name, "psql", "-U", "kazma", "-d", "kazma", "-c", "SELECT 1"],
                              capture_output=True, timeout=30)
        if done.returncode == 0:
            return
        time.sleep(2)
    raise AssertionError(f"{name} did not answer")


@pytest.fixture
def deployment(tmp_path):
    if not OPT_IN:
        pytest.skip("set KAZMA_TEST_PG_COMPOSE_UPGRADE=1 to run a real compose drill")
    project = f"kazmapgdrill{uuid.uuid4().hex[:6]}"
    name = f"{project}-kazma-db"
    old_file, new_file = _compose_files(tmp_path, name)
    _compose(old_file, project, "up", "-d")
    _ready(name)
    _psql(name, SEED)
    try:
        yield SimpleDeployment(project, name, old_file, new_file)
    finally:
        _compose(new_file, project, "down", "-v", "--remove-orphans", check=False)
        _compose(old_file, project, "down", "-v", "--remove-orphans", check=False)
        subprocess.run([_docker(), "rm", "-f", name, f"{name}-pg16-source"], capture_output=True, timeout=120)


class SimpleDeployment:
    def __init__(self, project, name, old_file, new_file):
        self.project, self.name, self.old_file, self.new_file = project, name, old_file, new_file


def _upgrade(dep: SimpleDeployment, said: list[str]) -> int:
    from kazma_core.postgres_compose_upgrade import upgrade_compose

    return upgrade_compose(dep.new_file, project=dep.project, yes=True, say=said.append)


def test_compose_up_before_the_move_refuses_and_the_move_from_the_volume_finishes(deployment):
    dep = deployment
    # The negative control: the new file started before the move.
    _compose(dep.new_file, dep.project, "up", "-d", "db", check=False)
    time.sleep(8)
    state = json.loads(subprocess.run([_docker(), "inspect", dep.name], capture_output=True, text=True,
                                      check=True).stdout)[0]["State"]
    logs = subprocess.run([_docker(), "logs", dep.name], capture_output=True, text=True).stdout
    assert not state["Running"] or state["Restarting"], state
    assert "pg_compose_upgrade.py" in logs
    # The move, reading the 16 volume.
    said: list[str] = []
    assert _upgrade(dep, said) == 0, "\n".join(said)
    assert any("temporary Postgres 16" in line for line in said)
    assert _psql(dep.name, "SHOW server_version_num")[:2] == "18"
    assert _psql(dep.name, "SELECT count(*) FROM kazma_chat_sessions") == "301"
    assert _psql(dep.name, "SELECT title FROM kazma_chat_sessions WHERE id = 301") == "محادثة"
    said.clear()
    assert _upgrade(dep, said) == 0 and any("nothing to move" in line for line in said)


def test_the_move_from_a_running_16_container_stops_and_starts_kazma(deployment):
    dep = deployment
    said: list[str] = []
    assert _upgrade(dep, said) == 0, "\n".join(said)
    assert any("Stopping Kazma" in line for line in said) and any("Starting Kazma" in line for line in said)
    assert _psql(dep.name, "SHOW server_version_num")[:2] == "18"
    assert _psql(dep.name, "SELECT nextval('kazma_chat_sessions_id_seq')") == "302"
    kazma = _compose(dep.new_file, dep.project, "ps", "-q", "kazma").stdout.split()
    assert kazma


def test_a_failed_restore_puts_16_back_and_removes_the_partial_18(deployment):
    """The restore refused (a failing pg_restore): the 16 container is made
    again from its kept definition with its data, the 18 volume is gone (so
    the entrypoint keeps refusing an empty 18), and the stand-in Kazma runs."""
    from kazma_core import postgres_compose_upgrade as pcu

    dep = deployment

    def run(argv, **kw):
        if "pg_restore" in argv:
            return subprocess.CompletedProcess(argv, 1, b"", b"pg_restore: error: planted failure")
        return subprocess.run(argv, **kw)

    said: list[str] = []
    assert pcu.upgrade_compose(dep.new_file, project=dep.project, yes=True, say=said.append, run=run) == 1
    assert any("planted failure" in line for line in said), said
    assert any("is back in place" in line for line in said), said
    _ready(dep.name)
    assert _psql(dep.name, "SHOW server_version_num")[:2] == "16"
    assert _psql(dep.name, "SELECT count(*) FROM kazma_chat_sessions") == "301"
    volumes = subprocess.run([_docker(), "volume", "ls", "-q", "--filter", f"name={dep.project}_kazma_pgdata18"],
                             capture_output=True, text=True, timeout=60).stdout.split()
    assert volumes == []
    assert not (dep.new_file.parent / pcu.JOURNAL_NAME).exists()
    # The next run moves it.
    said.clear()
    assert pcu.upgrade_compose(dep.new_file, project=dep.project, yes=True, say=said.append) == 0, "\n".join(said)
    assert _psql(dep.name, "SHOW server_version_num")[:2] == "18"
