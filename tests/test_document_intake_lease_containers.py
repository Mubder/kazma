"""The intake lease and fence, with the stale owner in a REAL other container.

2026-10-13. ``test_document_intake_lease.py`` gives a child process another
host's identity and a renewal that writes nothing. Here the stale owner is a
real Linux container (its own hostname, data dir and install id, today's
kazma_core mounted over the image's) sharing a disposable Postgres with the
recoverer, which runs here, on this host:

* **frozen** -- ``docker pause`` freezes the container after it wrote its
  version; its real renewals stop, the lease really lapses on the database's
  clock, this host takes the intake over, then the container is thawed and
  must find itself fenced (exit 7) with nothing published;
* **partitioned** -- the container is cut off the network instead; its
  renewals fail, it is taken over, reconnected, and fenced;
* **fence alone** -- the container's renewal thread is kept blind (it never
  learns of the takeover, the race window in production): the database fence
  at publication alone refuses it;
* **negative control** -- blind and without the fence, the stale owner
  publishes a job for a reservation this host closed.

Opt-in: ``KAZMA_TEST_CONTAINER_LEASE_DRILL_IMAGE`` names an image holding
kazma_core's dependencies at ``/app/kazma-core`` (the recovery-test image);
Docker must be reachable. A skip is not evidence. Everything it starts
(network, Postgres, containers) is removed when it ends.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import secrets
import subprocess
import time
import traceback
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ZOMBIE = ROOT / "tests" / "fixtures" / "documents" / "lease_zombie.py"
LEASE = 8
_FENCED, _PUBLISHED = 7, 0


# ── this host's side (children, each with the drill database) ─────────────

def _bootstrap(dsn: str) -> None:
    os.environ["KAZMA_DB_BACKEND"] = "postgres"
    os.environ["KAZMA_DATABASE_URL"] = dsn
    os.environ["KAZMA_DOCUMENTS_METADATA_BACKEND"] = "postgres"
    os.environ["KAZMA_DOCUMENTS_JOBS_BACKEND"] = "postgres"
    from kazma_core.config_store import get_config_store

    get_config_store().set("documents.enabled", True, category="documents")


def _host(dsn: str, root: str, tenant: str, action: str):
    _bootstrap(dsn)
    if action == "recover":
        # A service starting on this host: it recovers stranded intakes at
        # start (and its sweep does the same every minute).
        from kazma_core.documents.config import DocumentConfig
        from kazma_core.documents.ingestion import DocumentIngestionService

        svc = DocumentIngestionService(config=DocumentConfig(
            storage_root=Path(root), security_malware_scan="off", security_local_salvage=False, ocr_enabled=False,
        ))
        try:
            return svc._recover_stranded_intakes()
        finally:
            svc.close()
    # Looking only: the stores, never a service (a service start recovers).
    from kazma_core.db.postgres_pool import PostgresPool
    from kazma_core.documents.admission import IntakeAdmissionStore, current_owner
    from kazma_core.documents.jobs_pg import PostgresDocumentJobRepository
    from kazma_core.documents.models import JobId
    from kazma_core.documents.repository_pg import PostgresDocumentRepository

    pool = PostgresPool(dsn, min_size=1, max_size=2)
    try:
        jobs = PostgresDocumentJobRepository(pool)
        repository = PostgresDocumentRepository(pool, tenant_quota_bytes=1 << 30)
        admissions = IntakeAdmissionStore(jobs)
        with admissions._transaction() as cur:
            admissions._execute(
                cur, "SELECT * FROM document_intake_reservations WHERE tenant_id = ? AND request_key = ?",
                (tenant, "zombie"),
            )
            row = cur.fetchone()
        row = dict(row) if row else None
        return {
            "reservation": row,
            "stranded": admissions.snapshot(tenant_id=tenant)["stranded"],
            "documents": len(repository.list_documents(tenant_id=tenant)),
            "job": bool(row and jobs.get(tenant_id=tenant, job_id=JobId(row["job_id"]))),
            "our_host": json.loads(current_owner())["host"],
            "now": admissions.now_epoch(),
        }
    finally:
        pool.close()


def _child(args, queue) -> None:
    try:
        queue.put(("ok", _host(*args)))
    except BaseException:  # noqa: BLE001 - reported to the parent
        queue.put(("error", traceback.format_exc()))


def _on_host(*args):
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    child = ctx.Process(target=_child, args=(args, queue))
    child.start()
    status, value = queue.get(timeout=300)
    child.join(60)
    child.close()
    assert status == "ok", value
    return value


# ── docker ─────────────────────────────────────────────────────────────────

class _Docker:
    def __init__(self, image: str) -> None:
        from kazma_core.docker_cli import find_docker_cli

        self.cli = find_docker_cli()
        if not self.cli:
            pytest.skip("the docker CLI was not found")
        probe = subprocess.run([self.cli, "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True)
        if probe.returncode != 0:
            pytest.skip("Docker is not reachable")
        self.image = image
        self.tag = uuid.uuid4().hex[:8]
        self.network = f"kazma-lease-drill-{self.tag}"
        self.started: list[str] = []

    def run(self, *args: str, check: bool = True) -> str:
        done = subprocess.run([self.cli, *args], capture_output=True, text=True, timeout=600)
        if check and done.returncode != 0:
            raise AssertionError(f"docker {' '.join(args[:3])}: {done.stderr.strip()[:500]}")
        return done.stdout.strip()

    def close(self) -> None:
        for name in self.started:
            self.run("rm", "-f", name, check=False)
        self.run("network", "rm", self.network, check=False)


@pytest.fixture
def drill(tmp_path):
    image = os.environ.get("KAZMA_TEST_CONTAINER_LEASE_DRILL_IMAGE", "")
    if not image:
        pytest.skip("opt-in: set KAZMA_TEST_CONTAINER_LEASE_DRILL_IMAGE")
    psycopg = pytest.importorskip("psycopg")
    docker = _Docker(image)
    password = secrets.token_hex(12)
    try:
        docker.run("network", "create", docker.network)
        pg = f"kazma-lease-pg-{docker.tag}"
        docker.started.append(pg)
        docker.run("run", "-d", "--name", pg, "--network", docker.network, "--network-alias", "pg",
                   "--cpus", "1", "--memory", "1g", "-e", f"POSTGRES_PASSWORD={password}",
                   "-p", "127.0.0.1::5432", "postgres:16-alpine")
        port = docker.run("port", pg, "5432/tcp").splitlines()[0].rsplit(":", 1)[1]
        host_dsn = f"postgresql://postgres:{password}@127.0.0.1:{port}/postgres"
        deadline = time.monotonic() + 90
        while True:
            try:
                with psycopg.connect(host_dsn, connect_timeout=3) as conn:
                    conn.execute("SELECT 1")
                break
            except psycopg.OperationalError:
                assert time.monotonic() < deadline, "the drill database did not start"
                time.sleep(1)
        yield {
            "docker": docker, "host_dsn": host_dsn,
            "container_dsn": f"postgresql://postgres:{password}@pg:5432/postgres",
            "root": tmp_path / "shared",
        }
    finally:
        docker.close()


def _start_zombie(drill, tenant: str, **env: str) -> str:
    docker, shared = drill["docker"], drill["root"]
    shared.mkdir(parents=True, exist_ok=True)
    for marker in ("paused", "go"):
        (shared / marker).unlink(missing_ok=True)
    name = f"kazma-lease-zombie-{docker.tag}-{len(docker.started)}"
    docker.started.append(name)
    settings = {
        "KAZMA_DATABASE_URL": drill["container_dsn"], "KAZMA_DB_BACKEND": "postgres",
        "KAZMA_DOCUMENTS_METADATA_BACKEND": "postgres", "KAZMA_DOCUMENTS_JOBS_BACKEND": "postgres",
        "KAZMA_DATA_DIR": "/tmp/kazma-data", "DRILL_DIR": "/drill", "TENANT": tenant,
        "LEASE_SECONDS": str(LEASE), **env,
    }
    args = ["run", "-d", "--name", name, "--network", drill["docker"].network, "--cpus", "1", "--memory", "1g",
            "-v", f"{ROOT / 'kazma-core' / 'kazma_core'}:/app/kazma-core/kazma_core:ro",
            "-v", f"{ZOMBIE}:/drill-script/lease_zombie.py:ro", "-v", f"{shared}:/drill"]
    for key, value in settings.items():
        args += ["-e", f"{key}={value}"]
    docker.run(*args, "--entrypoint", "python", docker.image, "/drill-script/lease_zombie.py")
    deadline = time.monotonic() + 300
    while not (shared / "paused").exists():
        state = docker.run("inspect", "-f", "{{.State.Status}}", name)
        if state != "running" or time.monotonic() > deadline:
            raise AssertionError(f"the zombie never reached its hold ({state}): {docker.run('logs', name, check=False)[-2000:]}")
        time.sleep(0.5)
    return name


def _finish(drill, name: str) -> tuple[int, str]:
    docker = drill["docker"]
    (drill["root"] / "go").write_text("go", encoding="utf-8")
    code = int(docker.run("wait", name))
    return code, docker.run("logs", name, check=False)


def _inspect(drill, tenant):
    return _on_host(drill["host_dsn"], str(drill["root"] / "content"), tenant, "inspect")


def _recover(drill, tenant):
    return _on_host(drill["host_dsn"], str(drill["root"] / "content"), tenant, "recover")


def _lapse_and_take_over(drill, tenant) -> dict:
    time.sleep(LEASE + 4)
    seen = _inspect(drill, tenant)
    held = seen["reservation"]
    assert seen["stranded"] == 1, {  # lapsed: about to be taken over
        "silent_for": seen["now"] - held["heartbeat_epoch"], "lease": held["lease_seconds"],
        "owner": held["owner"], "our_host": seen["our_host"], "active": held["active"],
    }
    _recover(drill, tenant)
    after = _inspect(drill, tenant)
    taken = after["reservation"]
    assert taken["active"] == 0 and taken["fenced_epoch"] > 0, after  # taken over, kept for the sweep
    assert after["documents"] == 0 and after["stranded"] == 0, after  # its rows rolled back
    return after


# ── the drills ─────────────────────────────────────────────────────────────

def test_a_frozen_container_is_taken_over_and_fenced_when_it_thaws(drill):
    tenant = f"lease-{uuid.uuid4().hex[:8]}"
    zombie = _start_zombie(drill, tenant)
    first = _inspect(drill, tenant)
    assert first["reservation"]["active"] == 1 and first["stranded"] == 0
    owner = json.loads(first["reservation"]["owner"])
    assert owner["host"] != first["our_host"]  # really another host
    _recover(drill, tenant)  # a service starting here leaves a renewing remote intake alone
    time.sleep(LEASE / 2 + 1)
    kept = _inspect(drill, tenant)["reservation"]
    assert kept["active"] == 1 and kept["owner"] == first["reservation"]["owner"]
    assert kept["heartbeat_epoch"] > first["reservation"]["heartbeat_epoch"]  # its real renewals land

    drill["docker"].run("pause", zombie)
    try:
        after = _lapse_and_take_over(drill, tenant)
    finally:
        drill["docker"].run("unpause", zombie)
    code, logs = _finish(drill, zombie)
    assert code == _FENCED, logs
    final = _inspect(drill, tenant)
    assert not final["job"] and final["documents"] == 0, final
    assert after["reservation"]["job_id"] == final["reservation"]["job_id"]


def test_a_partitioned_container_is_taken_over_and_fenced_when_it_returns(drill):
    tenant = f"lease-{uuid.uuid4().hex[:8]}"
    zombie = _start_zombie(drill, tenant)
    assert _inspect(drill, tenant)["stranded"] == 0
    drill["docker"].run("network", "disconnect", drill["docker"].network, zombie)
    try:
        _lapse_and_take_over(drill, tenant)
    finally:
        drill["docker"].run("network", "connect", drill["docker"].network, zombie)
    code, logs = _finish(drill, zombie)
    assert code == _FENCED, logs
    final = _inspect(drill, tenant)
    assert not final["job"] and final["documents"] == 0, final


def test_the_fence_alone_refuses_a_container_that_never_noticed(drill):
    tenant = f"lease-{uuid.uuid4().hex[:8]}"
    zombie = _start_zombie(drill, tenant, BLIND="1")
    drill["docker"].run("pause", zombie)
    try:
        _lapse_and_take_over(drill, tenant)
    finally:
        drill["docker"].run("unpause", zombie)
    code, logs = _finish(drill, zombie)
    assert code == _FENCED, logs
    assert not _inspect(drill, tenant)["job"]


def test_without_the_fence_the_container_publishes_over_the_takeover(drill):
    # Negative control: blind renewals and no fence at publication.
    tenant = f"lease-{uuid.uuid4().hex[:8]}"
    zombie = _start_zombie(drill, tenant, BLIND="1", NO_FENCE="1", PAUSE_AFTER="register_blob")
    drill["docker"].run("pause", zombie)
    try:
        _lapse_and_take_over(drill, tenant)
    finally:
        drill["docker"].run("unpause", zombie)
    code, logs = _finish(drill, zombie)
    assert code == _PUBLISHED, logs
    final = _inspect(drill, tenant)
    assert final["job"] and final["reservation"]["active"] == 0  # a job for a closed reservation
