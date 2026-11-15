"""The real app with sign-in on, and raw ASGI calls that count what it reads.

Shared by the 2026-11-15 sign-in and roles tests. ``create_app()`` makes and
keeps a sign-in secret and a vault key when none is set; a test sets its own
first, so nothing is written to an env file. A call goes through the app's
whole middleware stack with a body sent in pieces, and reports the status,
the bytes the app pulled from the connection and the answer's body.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

SECRET = "sign-in-and-roles-test-secret-0123456789abcdef"
VAULT_KEY = "0f" * 32


def secured_app(monkeypatch: Any) -> Any:
    """``create_app()`` with a secret and a vault key of the test's own."""
    monkeypatch.setenv("KAZMA_SECRET", SECRET)
    monkeypatch.setenv("KAZMA_VAULT_KEY", VAULT_KEY)
    for name in ("KAZMA_AUTH_DISABLED", "KAZMA_DEMO_MODE", "KAZMA_MULTI_USER", "KAZMA_TRUSTED_PROXIES"):
        monkeypatch.delenv(name, raising=False)
    from kazma_ui.app import create_app

    return create_app()


@dataclass
class Answer:
    status: int
    read: int
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body or b"null")


def call(
    app: Any,
    method: str,
    path: str,
    *,
    body: bytes = b"",
    headers: dict[str, str] | None = None,
    declare: bool = True,
    piece: int = 64 * 1024,
    client: tuple[str, int] = ("127.0.0.1", 50000),
    raw_path: bytes | None = None,
) -> Answer:
    """One request through *app*. *path* is the decoded path (``scope["path"]``);
    *raw_path* what was on the wire (defaults to *path*)."""
    pieces = [body[i:i + piece] for i in range(0, len(body), piece)] or [b""]
    sent = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()]
    sent.append((b"host", b"127.0.0.1:59999"))
    if declare:
        sent.append((b"content-length", str(len(body)).encode("ascii")))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method.upper(), "scheme": "http", "path": path,
        "raw_path": raw_path if raw_path is not None else path.encode("utf-8"),
        "query_string": b"", "headers": sent, "client": client,
        "server": ("127.0.0.1", 59999), "root_path": "",
    }
    read = 0
    status: list[int] = []
    out: list[bytes] = []

    async def receive() -> dict[str, Any]:
        nonlocal read
        if not pieces:
            await asyncio.sleep(0)
            return {"type": "http.disconnect"}
        chunk = pieces.pop(0)
        read += len(chunk)
        return {"type": "http.request", "body": chunk, "more_body": bool(pieces)}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            status.append(message["status"])
        elif message["type"] == "http.response.body":
            out.append(message.get("body") or b"")

    asyncio.run(app(scope, receive, send))
    return Answer(status[0] if status else 0, read, b"".join(out))
