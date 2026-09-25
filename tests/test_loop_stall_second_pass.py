"""The second loop-stall pass: what the live dumps caught, kept off the loop.

The live install wrote 50 loop-stall dumps between 2026-09-10 and 09-25
(``tests/test_static_gates.py::_LOOP_STALL_HELPERS`` holds the helper names).
The biggest single source was the auth middleware: 8 dumps of a per-request
user-store or session read on the event loop, one after the first pass had
shipped. Then the MCP server list read by the reconnect sweeper (49.7 s and
28.5 s), and the readiness probe the guard hits every 30 s. And the dumps of
2026-09-25 held no loop stack at all: faulthandler stops at 100 threads.

Each test here spies on the blocking call and records whether an event loop
was running in its thread: the old code ran them ON the loop and fails.
"""

from __future__ import annotations

import asyncio
import threading
import time


def _on_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _spy(record: dict[str, list[bool]], name: str, result):
    def _fn(*_a, **_k):
        record.setdefault(name, []).append(_on_loop())
        return result

    return _fn


# ── the auth middleware (8 dumps) ─────────────────────────────────────


def test_the_auth_middleware_reads_credentials_and_users_off_the_loop(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.security import platform_rbac
    from kazma_ui import auth

    seen: dict[str, list[bool]] = {}
    monkeypatch.setattr(auth, "extract_provided_credential", _spy(seen, "credential", "session:x"))
    monkeypatch.setattr(auth, "is_authenticated", _spy(seen, "is_authenticated", True))
    monkeypatch.setattr(platform_rbac, "multi_user_enabled", _spy(seen, "multi_user", True))
    monkeypatch.setattr(
        auth, "get_request_principal",
        _spy(seen, "principal", {"role": "admin", "source": "session"}),
    )

    app = FastAPI()
    app.middleware("http")(auth.create_auth_middleware(secret="s3cret-for-test"))

    @app.get("/api/probe")
    def _probe() -> dict:
        return {"ok": True}

    resp = TestClient(app).get("/api/probe")
    assert resp.status_code == 200
    for name in ("credential", "is_authenticated", "multi_user", "principal"):
        assert seen.get(name), f"{name} was not consulted"
        assert not any(seen[name]), f"{name} ran on the event loop"


def test_the_auth_middleware_still_denies(monkeypatch):
    """Moving the checks off the loop must not change a single answer."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import auth

    monkeypatch.setattr(auth, "is_authenticated", lambda request, expected: False)
    app = FastAPI()
    app.middleware("http")(auth.create_auth_middleware(secret="s3cret-for-test"))

    @app.get("/api/probe")
    def _probe() -> dict:
        return {"ok": True}

    assert TestClient(app).get("/api/probe").status_code == 401


def test_the_tenant_middleware_resolves_the_principal_off_the_loop(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import auth

    seen: dict[str, list[bool]] = {}
    monkeypatch.setattr(auth, "get_request_principal", _spy(seen, "principal", None))
    app = FastAPI()
    app.middleware("http")(auth.create_tenant_middleware())

    @app.get("/api/probe")
    def _probe() -> dict:
        return {"ok": True}

    TestClient(app).get("/api/probe")
    assert seen.get("principal") and not any(seen["principal"])


# ── the readiness probe the guard hits every 30 s ─────────────────────


def test_readiness_runs_its_database_checks_off_the_loop(monkeypatch):
    from kazma_ui import health

    seen: dict[str, list[bool]] = {}
    monkeypatch.setattr(health, "check_config_store", _spy(seen, "config", {"status": "ok"}))
    monkeypatch.setattr(health, "check_llm_provider", _spy(seen, "llm", {"status": "ok"}))
    monkeypatch.setattr(health, "check_database", lambda: {"status": "ok"})
    asyncio.run(health._readiness())
    assert seen["config"] == [False] and seen["llm"] == [False]


def test_a_hung_check_is_an_answer_not_a_frozen_loop():
    from kazma_ui import health

    def _hangs() -> dict:
        time.sleep(2.0)
        return {"status": "ok"}

    async def _probe() -> tuple[dict, float]:
        started = time.monotonic()
        beats = 0

        async def _heartbeat() -> None:
            nonlocal beats
            while True:
                beats += 1
                await asyncio.sleep(0.05)

        hb = asyncio.create_task(_heartbeat())
        result = await health._offloaded_check(_hangs, "config_store", 0.3)
        hb.cancel()
        assert beats >= 3, "the loop froze while the check ran"
        return result, time.monotonic() - started

    result, took = asyncio.run(_probe())
    assert result["status"] == "failed" and "timed out" in result["error"]
    assert took < 1.5


# ── the MCP reconnect sweeper (49.7 s, 28.5 s) ────────────────────────


def test_the_mcp_sweeper_reads_the_server_list_off_the_loop():
    from kazma_core.mcp.reconnect import MCPReconnector

    seen: dict[str, list[bool]] = {}

    class _Manager:
        connection_errors: dict = {}
        clients: dict = {}

    rec = MCPReconnector(_Manager(), _spy(seen, "config", []))
    asyncio.run(rec.sweep_once())
    assert seen["config"] == [False]


# ── the dump must always carry the loop's stack ───────────────────────


def test_the_dump_leads_with_the_loop_thread_past_100_threads(tmp_path, monkeypatch):
    """faulthandler stops at 100 threads; on 2026-09-25 that cut the loop out."""
    from kazma_core.observability import loop_stall

    monkeypatch.setattr(loop_stall, "stall_dump_dir", lambda: tmp_path)
    release = threading.Event()
    started = threading.Event()
    ident: dict[str, int] = {}

    def the_frozen_loop() -> None:
        ident["id"] = threading.get_ident()
        started.set()
        release.wait(10)

    loop_thread = threading.Thread(target=the_frozen_loop, daemon=True)
    loop_thread.start()
    started.wait(5)
    idlers = [threading.Thread(target=release.wait, args=(10,), daemon=True) for _ in range(110)]
    for t in idlers:
        t.start()
    try:
        path = loop_stall._write_dump(20.0, "test", ident["id"])
    finally:
        release.set()
    text = path.read_text(encoding="utf-8")
    head = text.split("=" * 70, 1)[1]
    first_section = head.strip().split("\n\n", 1)[0]
    assert first_section.startswith("Event-loop thread")
    assert "the_frozen_loop" in first_section
    assert "threads alive:" in text


def test_an_unknown_loop_thread_still_dumps_every_thread(tmp_path, monkeypatch):
    from kazma_core.observability import loop_stall

    monkeypatch.setattr(loop_stall, "stall_dump_dir", lambda: tmp_path)
    path = loop_stall._write_dump(20.0, "test-none", None)
    text = path.read_text(encoding="utf-8")
    assert "Event-loop thread" not in text and "Thread 0x" in text
