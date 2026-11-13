"""A Postgres outage is ridden out: reads keep answering, probes keep answering.

The 2026-11 functional audit (FA-259, drills P1 and P2) stopped the live
Postgres for two and for twelve minutes. Kazma was killed by its guard both
times, at ~75 s, and the 12-minute outage kept it down for 15:

* ``ConfigStore`` held its process-wide lock across every Postgres statement,
  so with the database away each cache-missed read held it for the pool's
  5 s checkout timeout, and every settings read in the process queued
  behind it;
* each waiting read held a shared worker thread, and the auth and tenant
  middlewares sent every request -- the guard's ``/health/ready`` probe
  included -- to those threads for a settings read: the probe could not
  answer, whatever its checks found;
* every readiness probe started its checks again beside the ones still
  running from the probe before;
* a boot during the outage gave up on the store after five pool attempts
  and exited, and the guard's growing wait between boots did the rest.

Each piece has its test here, with the old shape as its negative control.
The checks of a real outage (a real Postgres stopped and started) are in
``test_a_real_postgres_outage_is_ridden_out`` (opt-in: it needs a
throwaway container).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from starlette.requests import Request
from starlette.responses import Response

from kazma_core import config_store as cs_mod
from kazma_core.config_availability import ConfigStoreUnavailableError, wait_for_settings_store


# ── A Postgres-shaped store on a fake pool ──────────────────────────────


class _Pool:
    """Answers like a ``PostgresPool``; a statement can be made to wait or fail."""

    def __init__(self, rows: dict[str, Any] | None = None) -> None:
        self.rows = dict(rows or {})
        self.calls = 0
        self.gate: threading.Event | None = None  # set: statements wait on it
        self.entered = threading.Event()
        self.fail: BaseException | None = None
        self.fail_after = 0.0

    def _statement(self) -> None:
        self.calls += 1
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(10)
        if self.fail is not None:
            time.sleep(self.fail_after)
            raise self.fail

    def execute(self, sql: str, params: Any = None) -> list[dict]:
        # The row is read first, then the answer is slow to come back: what
        # a read that overlapped a write sees.
        rows: list[dict] = []
        if "WHERE key = %s" in sql and params[0] in self.rows:
            rows = [{"value": self.rows[params[0]]}]
        self._statement()
        return rows

    def execute_one(self, sql: str, params: Any = None) -> dict | None:
        rows = self.execute(sql, params)
        return rows[0] if rows else None

    def connection(self, **_kw: Any):
        pool = self

        class _Cur:
            rowcount = 1

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=None):
                pool._statement()
                if sql.lstrip().startswith("INSERT") and params:
                    pool.rows[params[0]] = params[1]

            def fetchone(self):
                return None

        class _Conn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def cursor(self):
                return _Cur()

            def commit(self):
                pass

        return _Conn()


@pytest.fixture
def pg_store(tmp_path, monkeypatch):
    """A real ``ConfigStore`` whose Postgres branch runs on ``_Pool``."""
    store = cs_mod.ConfigStore(db_path=str(tmp_path / "s.db"), yaml_path=str(tmp_path / "none.yaml"))
    pool = _Pool()
    monkeypatch.setattr(store, "_use_postgres", lambda: True)
    monkeypatch.setattr(store, "_pg_pool", lambda: pool)
    monkeypatch.setattr(store, "_load_yaml", lambda: {})
    yield store, pool
    if pool.gate is not None:
        pool.gate.set()


class _OldScope(cs_mod.ConfigStore):
    """The lock scope before FA-259: the Postgres read ran under ``self._lock``."""

    def _pg_get_raw(self, key: str) -> Any:
        with self._lock:
            return super()._pg_get_raw(key)


@pytest.fixture
def old_pg_store(tmp_path, monkeypatch):
    store = _OldScope(db_path=str(tmp_path / "o.db"), yaml_path=str(tmp_path / "none.yaml"))
    pool = _Pool()
    monkeypatch.setattr(store, "_use_postgres", lambda: True)
    monkeypatch.setattr(store, "_pg_pool", lambda: pool)
    monkeypatch.setattr(store, "_load_yaml", lambda: {})
    yield store, pool
    if pool.gate is not None:
        pool.gate.set()


def _read_beside_a_stalled_read(store, pool) -> float:
    """How long a cached read takes while another read waits on the pool."""
    pool.rows["fast"] = '"cached"'
    assert store.get("fast") == "cached"  # now cached
    pool.gate = threading.Event()
    pool.entered.clear()
    stalled = threading.Thread(target=store.get, args=("slow",), daemon=True)
    stalled.start()
    assert pool.entered.wait(5)
    result: list[float] = []

    def cached_read() -> None:
        start = time.monotonic()
        store.get("fast")
        result.append(time.monotonic() - start)

    reader = threading.Thread(target=cached_read, daemon=True)
    reader.start()
    reader.join(1.0)
    pool.gate.set()
    stalled.join(5)
    reader.join(5)
    return result[0] if result else float("inf")


def test_a_cached_read_does_not_wait_behind_a_read_on_the_pool(pg_store):
    store, pool = pg_store
    assert _read_beside_a_stalled_read(store, pool) < 0.2


def test_the_old_lock_scope_made_it_wait(old_pg_store):
    """Negative control: under the old scope the cached read waits it out."""
    store, pool = old_pg_store
    assert _read_beside_a_stalled_read(store, pool) >= 0.9


def test_a_read_does_not_wait_behind_a_write_on_the_pool(pg_store):
    store, pool = pg_store
    pool.rows["fast"] = '"cached"'
    assert store.get("fast") == "cached"
    pool.gate = threading.Event()
    pool.entered.clear()
    writer = threading.Thread(target=store.set, args=("other.key", 5), daemon=True)
    writer.start()
    assert pool.entered.wait(5)
    start = time.monotonic()
    assert store.get("fast") == "cached"
    assert time.monotonic() - start < 0.2
    pool.gate.set()
    writer.join(5)


def test_a_read_that_overlapped_a_write_is_not_cached(pg_store):
    """The epoch: a value read while a write landed may be the value before."""
    store, pool = pg_store
    pool.rows["k"] = "1"
    pool.gate = threading.Event()
    pool.entered.clear()
    got: list[Any] = []
    reader = threading.Thread(target=lambda: got.append(store.get("k")), daemon=True)
    reader.start()
    assert pool.entered.wait(5)
    # A write lands while the read waits on its statement.
    with store._lock:
        store._invalidate_key("k")
    pool.rows["k"] = "2"
    pool.gate.set()
    reader.join(5)
    assert got == [1]
    pool.gate = None
    assert store.get("k") == 2  # the old value was not cached


# ── The outage itself ───────────────────────────────────────────────────


@pytest.fixture
def outage(pg_store, monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    store, pool = pg_store
    monkeypatch.setattr(store, "_PG_OUTAGE_HOLD_S", 0.5)
    pool.fail = psycopg.OperationalError("connection refused")
    pool.fail_after = 0.3
    return store, pool


def test_an_outage_fails_fast_after_the_first_wait(outage):
    store, pool = outage
    start = time.monotonic()
    with pytest.raises(ConfigStoreUnavailableError):
        store.get("missing.key")
    assert time.monotonic() - start >= 0.25  # the first one waited on the pool
    calls = pool.calls
    start = time.monotonic()
    for _ in range(20):
        with pytest.raises(ConfigStoreUnavailableError):
            store.get("other.missing")
    assert time.monotonic() - start < 0.2  # the rest are refused at once
    assert pool.calls == calls  # and never reached the pool


def test_a_cached_value_is_answered_through_an_outage_whatever_its_age(outage, monkeypatch):
    store, pool = outage
    fail = pool.fail
    pool.fail = None
    pool.rows["agent.language"] = '"ar"'
    assert store.get("agent.language") == "ar"
    monkeypatch.setattr(store, "_CACHE_TTL_SECONDS", 0.0)  # every entry stale
    pool.fail = fail
    assert store.get("agent.language") == "ar"
    assert store.get("agent.language") == "ar"


def test_one_caller_at_a_time_tries_the_database_again(outage):
    store, pool = outage
    with pytest.raises(ConfigStoreUnavailableError):
        store.get("a")
    time.sleep(0.6)  # the hold has passed
    pool.gate = threading.Event()
    pool.entered.clear()
    calls_before = pool.calls
    errors: list[str] = []

    def read() -> None:
        try:
            store.get("b")
        except ConfigStoreUnavailableError:
            errors.append("refused")

    probe = threading.Thread(target=read, daemon=True)
    probe.start()
    assert pool.entered.wait(5)
    others = [threading.Thread(target=read, daemon=True) for _ in range(8)]
    for t in others:
        t.start()
    for t in others:
        t.join(2)
    assert errors.count("refused") == 8  # refused at once, beside the probe
    assert pool.calls == calls_before + 1
    pool.gate.set()
    probe.join(5)


def test_the_database_back_is_said_once_and_reads_reach_it(outage, caplog):
    store, pool = outage
    with pytest.raises(ConfigStoreUnavailableError):
        store.get("a")
    time.sleep(0.6)
    pool.fail = None
    pool.rows["a"] = "7"
    with caplog.at_level("WARNING", logger="kazma_core.config_store"):
        assert store.get("a") == 7
        assert store.get("b") is None
    assert sum("answers again" in r.getMessage() for r in caplog.records) == 1


def test_a_write_in_an_outage_fails_at_once_and_blanks_no_secret(outage):
    """``_stored_raw`` raises in an outage; it used to answer None ("no
    secret stored"), which would let an empty write through over a secret
    the guard could not read."""
    store, pool = outage
    with pytest.raises(ConfigStoreUnavailableError):
        store.get("x")
    start = time.monotonic()
    with pytest.raises(ConfigStoreUnavailableError):
        store.set("providers.deepseek.api_key", "")
    with pytest.raises(ConfigStoreUnavailableError):
        store.set("agent.language", "en")
    assert time.monotonic() - start < 0.2


# ── The probe path ──────────────────────────────────────────────────────


def _request(path: str) -> Request:
    scope = {
        "type": "http", "method": "GET", "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": [(b"host", b"127.0.0.1:9090")],
        "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 9090),
        "scheme": "http", "root_path": "", "http_version": "1.1",
    }
    return Request(scope)


async def _ok(_request: Request) -> Response:
    return Response("ok")


@pytest.fixture
def no_threads(monkeypatch):
    """The middlewares' thread hand-offs, counted."""
    import kazma_ui.auth as auth

    used: list[str] = []

    async def to_thread(fn, *a, **kw):
        used.append(getattr(fn, "__name__", repr(fn)))
        return fn(*a, **kw)

    monkeypatch.setattr(auth.asyncio, "to_thread", to_thread)
    monkeypatch.setenv("KAZMA_SECRET", "s" * 40)
    return used


@pytest.mark.parametrize("path", ["/health", "/health/live", "/health/ready", "/health/deep"])
def test_a_probe_passes_the_middlewares_with_no_settings_read(no_threads, path):
    from kazma_ui.auth import create_auth_middleware, create_tenant_middleware

    auth_mw, tenant_mw = create_auth_middleware(), create_tenant_middleware()

    async def through_both() -> Response:
        return await auth_mw(_request(path), lambda r: tenant_mw(r, _ok))

    assert asyncio.run(through_both()).status_code == 200
    assert no_threads == []


def test_any_other_request_still_reads_what_it_needs(no_threads):
    """Control: the same middlewares send an ordinary request to a thread."""
    from kazma_ui.auth import create_auth_middleware, create_tenant_middleware

    auth_mw, tenant_mw = create_auth_middleware(), create_tenant_middleware()

    async def through_both() -> Response:
        return await auth_mw(_request("/api/status"), lambda r: tenant_mw(r, _ok))

    asyncio.run(through_both())
    assert no_threads  # the cookie and tenant questions went to threads


def test_the_probe_list_is_only_probes():
    from kazma_ui.auth import ALWAYS_OPEN_PATHS, HEALTH_PROBE_PATHS

    assert HEALTH_PROBE_PATHS <= ALWAYS_OPEN_PATHS
    assert "/health/details" not in HEALTH_PROBE_PATHS
    assert "/health/activity" not in HEALTH_PROBE_PATHS


# ── One check per component at a time ───────────────────────────────────


def _probe_many(offload, probes: int) -> int:
    started: list[int] = []
    release = threading.Event()

    def check() -> dict:
        started.append(1)
        release.wait(5)
        return {"status": "ok", "component": "database"}

    async def run() -> None:
        for _ in range(probes):
            await offload(check, "database", 0.05)

    try:
        asyncio.run(run())
    finally:
        release.set()
    return len(started)


def test_probes_share_a_check_still_running():
    from kazma_ui import health

    assert _probe_many(health._offloaded_check, 5) == 1


def test_the_old_offload_started_one_per_probe():
    """Negative control: the old offload started a thread per probe."""

    async def old(check, component, timeout_s):
        try:
            return await asyncio.wait_for(asyncio.to_thread(check), timeout=timeout_s)
        except TimeoutError:
            return {"status": "failed", "component": component}

    assert _probe_many(old, 5) == 5


# ── A boot during an outage waits for the database ──────────────────────


def test_a_boot_waits_for_the_settings_database():
    tries: list[int] = []

    def open_store() -> str:
        tries.append(1)
        if len(tries) < 4:
            raise ConfigStoreUnavailableError(opening=True)
        return "store"

    slept: list[float] = []
    assert wait_for_settings_store(open_store, budget_s=60, retry_s=5, sleep=slept.append) == "store"
    assert len(tries) == 4 and slept == [5, 5, 5]


def test_a_boot_gives_up_at_its_budget_with_the_reason():
    clock = [0.0]

    def sleep(s: float) -> None:
        clock[0] += s

    def open_store() -> str:
        clock[0] += 30  # the pool's own attempts
        raise ConfigStoreUnavailableError(opening=True)

    with pytest.raises(ConfigStoreUnavailableError):
        wait_for_settings_store(open_store, budget_s=120, retry_s=10, sleep=sleep, clock=lambda: clock[0])
    assert clock[0] <= 120 + 30


def test_a_boot_does_not_wait_out_a_bug():
    def open_store() -> str:
        raise ValueError("schema bug")

    with pytest.raises(ValueError):
        wait_for_settings_store(open_store, budget_s=60, retry_s=5, sleep=lambda s: None)


def test_the_boot_waits_where_it_opens_the_store():
    """The app's boot calls the wait, not the store directly (it also hands
    the wait the read-only start's check, ``on_wait``: resilience plan A4)."""
    import re

    source = Path("kazma-ui/kazma_ui/app.py").read_text(encoding="utf-8")
    assert re.search(r"wait_for_settings_store\(get_config_store\b", source)


# ── A real outage (opt-in) ──────────────────────────────────────────────


@pytest.mark.postgres
@pytest.mark.real_docker
def test_a_real_postgres_outage_is_ridden_out():
    """A real Postgres stopped and started under a real ``ConfigStore``.

    Opt-in: ``KAZMA_TEST_PG_OUTAGE_CONTAINER`` names a THROWAWAY container
    that ``KAZMA_DATABASE_URL`` points at (never the live database).
    """
    container = os.environ.get("KAZMA_TEST_PG_OUTAGE_CONTAINER", "").strip()
    if not container:
        pytest.skip("set KAZMA_TEST_PG_OUTAGE_CONTAINER to a throwaway Postgres container")
    from kazma_core.db import postgres_pool

    store = cs_mod.ConfigStore()
    store.set("audit.outage.drill", "before")
    assert store.get("audit.outage.drill") == "before"
    subprocess.run(["docker", "stop", container], check=True, capture_output=True, timeout=60)
    try:
        cs_mod.ConfigStore._CACHE_TTL_SECONDS = 0.0
        start = time.monotonic()
        assert store.get("audit.outage.drill") == "before"  # the cache answers
        with pytest.raises(ConfigStoreUnavailableError):
            store.get("audit.outage.never.read")
        start = time.monotonic()
        for _ in range(10):
            with pytest.raises(ConfigStoreUnavailableError):
                store.get("audit.outage.never.read2")
        assert time.monotonic() - start < 1.0
    finally:
        cs_mod.ConfigStore._CACHE_TTL_SECONDS = 15.0
        subprocess.run(["docker", "start", container], check=True, capture_output=True, timeout=60)
    deadline = time.monotonic() + 60
    while True:
        try:
            store.set("audit.outage.drill", "after")
            break
        except ConfigStoreUnavailableError:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)
    assert store.get("audit.outage.drill") == "after"
    store.delete("audit.outage.drill")
    assert postgres_pool.get_postgres_pool() is not None


# ── Every outage classifier knows the settings store's outage ───────────


def test_the_settings_outage_is_an_outage_to_every_classifier():
    """The settings store answers a Postgres outage with
    ``ConfigStoreUnavailableError`` (a ``RuntimeError``), where a pool timeout
    (an ``OperationalError``) surfaced before. A classifier that knew only
    the second would turn an outage into a permanent failure: a document
    REJECTED, a swarm write given up."""
    from kazma_core.documents.errors import infrastructure_errors
    from kazma_core.swarm.engine import _store_error_passes

    exc = ConfigStoreUnavailableError()
    assert isinstance(exc, infrastructure_errors())
    assert _store_error_passes(exc)


def _classifier_functions(source: str) -> list[str]:
    """Functions that name psycopg's outage errors without the settings one."""
    import ast

    found = []
    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        names = {n.attr if isinstance(n, ast.Attribute) else n.id
                 for n in ast.walk(fn) if isinstance(n, (ast.Name, ast.Attribute))}
        names |= {n.value for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        if "InterfaceError" in names and "OperationalError" in names:
            if not names & {"ConfigStoreUnavailableError", "RuntimeError"}:
                found.append(fn.name)
    return found


def test_no_classifier_forgets_the_settings_outage():
    import subprocess as sp

    files = sp.run(["git", "ls-files", "kazma-*/kazma_*/*.py", "kazma-*/kazma_*/**/*.py"],
                   capture_output=True, text=True, check=True).stdout.split()
    missing = {f: names for f in files
               if (names := _classifier_functions(Path(f).read_text(encoding="utf-8")))}
    assert missing == {}, missing


def test_the_classifier_gate_sees_one_that_forgets():
    planted = (
        "def passes(exc):\n"
        "    import psycopg\n"
        "    return isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError))\n"
    )
    assert _classifier_functions(planted) == ["passes"]
