"""No GET route runs a database statement on the event loop (2026-10-01).

That loop serves every chat stream (AGENTS §35). The name-based gates
(``_LOOP_STALL_HELPERS``, the settings-store gate) see a known helper called
from async code, and the async-route declaration sees a handler that never
awaits; neither sees an async handler that awaits something AND reads a store
inline -- the Scheduled page's list read the X schedule store on the loop on
every poll. This starts the real app and calls every GET route it serves,
recording each SQLite statement run while an event loop runs on its thread
(the memory routes' tracer, ``tests/test_memory_routes_tenant_scope.py``, for
the whole app). On Postgres the same code is a network round trip.
"""

from __future__ import annotations

import re
import socket
import sqlite3
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_api_route_callers import _all_routes

#: GET routes this walk does not call, each with why.
NOT_WALKED: dict[str, str] = {}

#: GET routes known to run SQL on the loop, each with the reason. Shrinks only.
ON_THE_LOOP: dict[str, str] = {}

#: The only hosts the walk may reach: the app under test itself.
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _running_loop() -> object | None:
    import asyncio

    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def walk_app(app: FastAPI, skip: dict[str, str] | None = None) -> dict[str, list[str]]:
    """GET path -> the first SQL statements it ran on the event loop.

    Every route the started app serves is called twice (path parameters as
    ``x``) and the SECOND call is recorded: what a route runs on the loop on
    every request, not a cache it fills once per process (a first read of a
    cached setting is not caught -- a known limit). Streams and sockets are
    skipped. Connections are traced from the app's construction on, so a
    connection opened in a thread and then used on the loop is caught too.
    Network calls to other hosts fail at once (connections and name lookups,
    so async clients too). A statement counts only on
    the SERVER's loop (a plain-def route may run a private loop in its
    worker thread), and the settings and session caches do not expire during
    the walk: an entry outliving its TTL between the two calls is a refresh
    by design, not per-request work.
    """
    from kazma_core.config_store import ConfigStore
    from kazma_core.security import web_sessions

    mp = pytest.MonkeyPatch()
    on_loop: list[str] = []
    recording: dict[str, Any] = {"on": False, "loop": None}
    real_connect = sqlite3.connect

    def note(sql: str) -> None:
        loop = _running_loop()
        if recording["on"] and loop is not None and loop is recording["loop"]:
            on_loop.append(" ".join(sql.split())[:90])

    def traced(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(note)
        return conn

    mp.setattr(ConfigStore, "_CACHE_TTL_SECONDS", 1e9)
    mp.setattr(web_sessions, "_CACHE_TTL_S", 1e9)
    # The hardening report's source scans read files, never a database, and
    # cost about 15 s a walk (two calls over the whole checkout): its checks
    # still run, over no files.
    from kazma_core.security import hardening

    mp.setattr(hardening.SecurityHardeningRunner, "_product_files", lambda _self: [])

    real_create = socket.create_connection
    real_getaddrinfo = socket.getaddrinfo

    def local_only(address: Any, *args: Any, **kwargs: Any) -> socket.socket:
        if str(address[0]) not in _LOCAL_HOSTS:
            raise OSError(f"no network in this test: {address[0]}")
        return real_create(address, *args, **kwargs)

    def local_names_only(host: Any, *args: Any, **kwargs: Any) -> Any:
        # Every connection by name resolves first, sync or async: an async
        # client never calls socket.create_connection (asyncio connects the
        # socket itself), and the walk reached OSV and GitHub until the name
        # lookup was refused too.
        name = host.decode() if isinstance(host, bytes) else host
        if name is not None and str(name) not in _LOCAL_HOSTS:
            raise socket.gaierror(socket.EAI_NONAME, f"no network in this test: {name}")
        return real_getaddrinfo(host, *args, **kwargs)

    mp.setattr(sqlite3, "connect", traced)
    mp.setattr(socket, "create_connection", local_only)
    mp.setattr(socket, "getaddrinfo", local_names_only)
    found: dict[str, list[str]] = {}
    try:
        built = app() if callable(app) and not isinstance(app, FastAPI) else app
        with TestClient(built, raise_server_exceptions=False) as client:

            async def _server_loop() -> object | None:
                return _running_loop()

            recording["loop"] = client.portal.call(_server_loop)
            for route in _all_routes(built.routes):
                path = getattr(route, "path", "")
                if "GET" not in (getattr(route, "methods", None) or ()):
                    continue
                if path in (skip or {}) or "stream" in path or path.startswith("/ws"):
                    continue
                url = re.sub(r"\{[^}]+\}", "x", path)
                client.get(url)  # fills whatever the route caches
                on_loop.clear()
                recording["on"] = True
                try:
                    client.get(url)
                finally:
                    recording["on"] = False
                if on_loop:
                    found[path] = list(dict.fromkeys(on_loop))[:3]
    finally:
        mp.undo()
    return found


@pytest.fixture(scope="module")
def walked() -> dict[str, list[str]]:
    from kazma_ui.app import create_app

    return walk_app(create_app, NOT_WALKED)


def test_no_get_route_runs_sql_on_the_loop(walked: dict[str, list[str]]) -> None:
    new = {p: s for p, s in walked.items() if p not in ON_THE_LOOP}
    assert not new, (
        "These GET routes ran a database statement on the event loop. Make the "
        "handler a plain def, or await asyncio.to_thread around the store call:\n"
        + "\n".join(f"  {p}: {s}" for p, s in sorted(new.items()))
    )


def test_no_declaration_is_stale(walked: dict[str, list[str]]) -> None:
    stale = sorted(p for p in ON_THE_LOOP if p not in walked)
    assert not stale, f"no longer on the loop; remove from ON_THE_LOOP: {stale}"


def test_a_store_read_on_the_loop_is_found(tmp_path) -> None:
    """Negative control (§28): the Scheduled list's shape -- an async handler
    that awaits, then reads a store inline -- next to its fixed twins."""
    import asyncio

    db = tmp_path / "x.db"
    real_connect = sqlite3.connect
    real_connect(db).close()

    def read() -> int:
        conn = sqlite3.connect(db)  # looked up at call time: traced
        try:
            return conn.execute("SELECT 1").fetchone()[0]
        finally:
            conn.close()

    app = FastAPI()

    @app.get("/api/inline")
    async def inline() -> dict:
        await asyncio.sleep(0)
        return {"v": read()}

    @app.get("/api/threaded")
    async def threaded() -> dict:
        return {"v": await asyncio.to_thread(read)}

    @app.get("/api/plain")
    def plain() -> dict:
        return {"v": read()}

    assert list(walk_app(app)) == ["/api/inline"]
