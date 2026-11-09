"""The unified-turn harness lets go of the data dir it deletes (2026-11-09).

Every harness server runs in a temporary data dir that is deleted when its
test ends. A process-wide store that bound that dir on first use outlives
it, and the next test is served from a folder that is gone. The Playwright
job's first run in four weeks (2026-11-09) failed on it: an earlier test's
bell bound the run-incident store, and the Dashboard's problems card in the
next test could not read it ("unable to open database file", Linux). On
Windows the delete fails quietly (``ignore_cleanup_errors``) and the stale
store works on in a dead folder, so the suite ran green there.

This boots the harness, runs a four-gate turn and calls every GET route the
app serves (the network refused), stops it, and reads every module global of
Kazma's packages -- and each attribute of the object one holds -- for a path
inside the deleted dir (``paths_left_in``). The harness's teardown must leave
none. Negative control: the teardown as it was before 2026-11-09 leaves the
run-incident store bound.
"""

from __future__ import annotations

import asyncio
import os
import re
import socket
import sys

import pytest

from tests._no_models import finish_turn_memory, keep_models_out
from tests.test_api_route_callers import _all_routes

pytest.importorskip("uvicorn")

#: The only hosts the walk may reach: this machine.
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@pytest.fixture(autouse=True)
def _no_models_or_network(monkeypatch):
    """No real embedder, model or voice service (tests/_no_models.py), no
    host but this machine (a route that would reach GitHub or OSV fails at
    once, ``tests/test_get_routes_off_the_loop.py``'s rule), and no Kazma
    server but the harness's own: a developer's machine may run Kazma on its
    port, and the walk calls routes that answer with a redirect there (the
    OAuth callbacks go to ``KAZMA_PUBLIC_URL``, else ``127.0.0.1:9090``).
    The walk follows no redirect; this refuses any connection to that port."""
    from tests.e2e import _unified_turn_harness as harness

    keep_models_out(monkeypatch, llm=False)
    monkeypatch.setitem(sys.modules, "edge_tts", None)
    port = harness.free_port()
    monkeypatch.setattr(harness, "free_port", lambda: port)
    kazma_ports = {9090, int(os.environ.get("KAZMA_PORT") or 9090)} - {port}
    real_getaddrinfo = socket.getaddrinfo

    def local_names_only(host, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else host
        if name is not None and str(name) not in _LOCAL_HOSTS:
            raise socket.gaierror(socket.EAI_NONAME, f"no network in this test: {name}")
        return real_getaddrinfo(host, *args, **kwargs)

    def refuse(address) -> None:
        if not isinstance(address, tuple) or len(address) < 2:
            return
        host = str(address[0])
        if host not in _LOCAL_HOSTS:
            raise ConnectionRefusedError(f"no network in this test: {host}")
        if address[1] in kazma_ports:
            raise ConnectionRefusedError(f"not this machine's Kazma ({host}:{address[1]})")

    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def connect(self, address):
        refuse(address)
        return real_connect(self, address)

    def connect_ex(self, address):
        refuse(address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", local_names_only)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    for loop_class in (asyncio.selector_events.BaseSelectorEventLoop,
                       asyncio.proactor_events.BaseProactorEventLoop):
        real_sock_connect = loop_class.sock_connect

        async def sock_connect(self, sock, address, _real=real_sock_connect):
            refuse(address)
            return await _real(self, sock, address)

        monkeypatch.setattr(loop_class, "sock_connect", sock_connect)
    yield
    finish_turn_memory()  # a turn's memory write ends inside its test


def _get_routes(app) -> list[str]:
    paths: list[str] = []
    for route in _all_routes(app.routes):
        path = getattr(route, "path", "")
        if "GET" not in (getattr(route, "methods", None) or ()):
            continue
        if "stream" in path or path.startswith("/ws"):
            continue
        paths.append(re.sub(r"\{[^}]+\}", "x", path))
    return paths


def test_the_harness_leaves_no_store_in_its_deleted_data_dir() -> None:
    from tests.e2e._unified_turn_harness import api_client, drive_turn, paths_left_in, unified_turn_server

    with unified_turn_server(approved_fixture=True) as h:
        root = h.data_dir
        run = drive_turn(h, "Scaffold the project", [])
        assert len(run.cycles) == 4, "the turn did not reach its four gates"
        with api_client(h.base) as client:
            routes = _get_routes(h.app)
            assert len(routes) > 100, "the walk found too few routes to mean anything"
            for path in routes:
                client.get(h.base + path, follow_redirects=False)

    left = paths_left_in(root)
    assert not left, (
        "These process singletons are still bound to the harness's deleted data "
        "dir; the next test is served from a folder that is gone. Reset each in "
        "tests/e2e/_unified_turn_harness.py (_DATA_DIR_STORE_RESETS or "
        "_DATA_DIR_STORE_GLOBALS):\n  " + "\n  ".join(left)
    )


@pytest.mark.parametrize("release_at_start", [True, False], ids=["harness", "without-the-release-at-start"])
def test_a_store_another_fixture_left_on_a_gone_folder_is_let_go_at_start(tmp_path, release_at_start) -> None:
    """The Playwright job's failure itself: the run-incident store bound to a
    folder that is gone when the next server starts. The Dashboard's running
    work (``/api/runs``) answered 500 and its problems card named the run
    incidents unreadable. A server fixture of another file can leave it so;
    the harness lets go of it when it starts. Without that release (the
    negative control) the failure comes back, on every OS: the folder here
    never existed."""
    from kazma_core.runs.incidents import IncidentStore, reset_incident_store

    from tests.e2e import _unified_turn_harness as harness

    gone = tmp_path / "an-earlier-servers-data-dir"
    try:
        with pytest.MonkeyPatch.context() as mp:
            if not release_at_start:
                mp.setattr(harness, "_release_data_dir_stores", lambda: None)
            reset_incident_store(IncidentStore(gone / "run_incidents.db"))
            with harness.unified_turn_server() as h, harness.api_client(h.base) as client:
                problems = client.get(h.base + "/api/problems").json()
                # The unhandled 500 /api/runs gave on CI is not asked for in the
                # control: uvicorn may drop the connection under a 500 it re-raises.
                runs = client.get(h.base + "/api/runs") if release_at_start else None
        if release_at_start:
            assert problems.get("unreadable") == [], problems
            assert runs is not None and runs.status_code == 200, runs.text if runs else None
        else:
            assert "incidents" in problems.get("unreadable", []), problems
    finally:
        reset_incident_store(None)
        harness._reset_process_singletons()


def test_the_teardown_before_the_fix_leaves_the_incident_store_bound() -> None:
    """Negative control (§28): the harness's teardown as it was, then the
    route the bell reads (``/api/alerts/recent`` reads the run incidents)."""
    from tests.e2e import _unified_turn_harness as harness

    try:
        with pytest.MonkeyPatch.context() as old_teardown:
            old_teardown.setattr(harness, "_DATA_DIR_STORE_RESETS", ())
            old_teardown.setattr(harness, "_DATA_DIR_STORE_GLOBALS", ())
            old_teardown.setattr(harness, "_release_process_recorder", lambda: None)
            with harness.unified_turn_server() as h:
                root = h.data_dir
                with harness.api_client(h.base) as client:
                    assert client.get(h.base + "/api/alerts/recent").status_code == 200
        left = harness.paths_left_in(root)
        assert any(line.startswith("kazma_core.runs.incidents._store.") for line in left), left
    finally:
        harness._reset_process_singletons()  # the full teardown, for the tests after this one
