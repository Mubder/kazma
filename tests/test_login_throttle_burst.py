"""AUD-101: a concurrent burst of logins cannot pass the throttle.

The route checked an address's failures, then awaited the body and the
password hash, and only then appended the failure. Twenty attempts sent at
once all passed the check (none had failed yet), so the limit of ten guesses
per window was no limit for a client that does not wait for its answers.
The fix reserves the attempt's slot before the first await.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

SECRET = "k" * 32


def _app(monkeypatch, calls: list[str]) -> FastAPI:
    from kazma_core.security import platform_rbac
    from kazma_ui import auth
    from kazma_ui.routes_direct.auth import register_auth_routes

    monkeypatch.setenv("KAZMA_SECRET", SECRET)
    monkeypatch.setattr(auth, "client_address", lambda request: "198.51.100.7")
    lock = threading.Lock()

    def authenticate_local_user(username: str, password: str):
        with lock:
            calls.append(username)
        time.sleep(0.3)  # the password hash: every attempt is in flight at once
        return None

    monkeypatch.setattr(platform_rbac, "authenticate_local_user", authenticate_local_user)
    stub = SimpleNamespace(app=FastAPI())
    register_auth_routes(stub)
    return stub.app


async def _burst(app: FastAPI, n: int) -> list[int]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Each guess under its own username: only the address limit applies.
        responses = await asyncio.gather(
            *(
                client.post(
                    "/api/auth/login",
                    json={"username": f"user{i}", "password": "guess"},
                )
                for i in range(n)
            )
        )
    return [r.status_code for r in responses]


def test_a_burst_from_one_address_gets_ten_guesses_at_most(monkeypatch) -> None:
    calls: list[str] = []
    codes = asyncio.run(_burst(_app(monkeypatch, calls), 20))
    assert len(calls) <= 10, f"{len(calls)} passwords were checked in one window"
    assert codes.count(401) == len(calls)
    assert codes.count(429) == 20 - len(calls)


def test_the_window_still_counts_the_burst_afterwards(monkeypatch) -> None:
    calls: list[str] = []
    app = _app(monkeypatch, calls)
    asyncio.run(_burst(app, 20))
    checked = len(calls)
    codes = asyncio.run(_burst(app, 3))
    assert codes == [429, 429, 429]
    assert len(calls) == checked


def test_a_successful_login_does_not_stay_counted(monkeypatch) -> None:
    calls: list[str] = []
    app = _app(monkeypatch, calls)

    async def run() -> list[int]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            out = []
            for _ in range(12):  # more than the limit, every one the right secret
                out.append((await c.post("/api/auth/login", json={"secret": SECRET})).status_code)
            return out

    assert asyncio.run(run()) == [200] * 12
