"""A stand-in network for tests of the pinned fetch path (AGENTS.md §32).

The real code runs -- ``web_acquire.http.fetch_public``,
``security.pinned_request.pinned_request``, the real
``security.ssrf.validate_url`` checks, the real ``PinHostAsyncTransport`` and
its httpcore pool -- and only two things are stand-ins:

* the name resolver: ``ssrf._resolve_host_ips`` answers from a table, and the
  owned resolver (``dns_worker.validate_public_url``) runs the real
  ``validate_url`` on it instead of a child process;
* the socket: the pinned transport's network backend is replaced by one that
  records the address it was asked to connect to and answers each request
  from a table keyed by ``(Host, path)``.

Nothing leaves the machine. ``StandIn.connects`` is every address a socket
was opened to, ``StandIn.requests`` every request that reached one.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import httpcore

#: A public address (documentation range of 93.184.216.0/24's example).
PUBLIC_IP = "93.184.216.34"
OTHER_PUBLIC_IP = "93.184.216.35"


@dataclass
class Answer:
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def json(cls, data: Any, status: int = 200, **headers: str) -> "Answer":
        return cls(status, json.dumps(data).encode(), {"Content-Type": "application/json", **headers})


@dataclass
class StandIn:
    dns: dict[str, list[str]]
    routes: dict[tuple[str, str], Answer]
    connects: list[tuple[str, int]] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)

    def answer(self, ip: str, raw: bytes) -> bytes:
        head, _, body = raw.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        method, target, _version = lines[0].split(" ", 2)
        headers = {}
        for line in lines[1:]:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        host = headers.get("host", "").split(":")[0]
        path = target.split("?", 1)[0]
        self.requests.append({"ip": ip, "method": method, "host": host, "path": path,
                              "headers": headers, "body": body})
        answer = self.routes.get((host, path), Answer(404, b"{}", {"Content-Type": "application/json"}))
        lines = [f"HTTP/1.1 {answer.status} X"]
        for name, value in {**answer.headers, "Content-Length": str(len(answer.body)),
                            "Connection": "close"}.items():
            lines.append(f"{name}: {value}")
        return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + answer.body


class _Stream(httpcore.AsyncNetworkStream):
    def __init__(self, stand_in: StandIn, ip: str) -> None:
        self._stand_in = stand_in
        self._ip = ip
        self._written = bytearray()
        self._reply: bytes | None = None

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        if self._reply is None:
            self._reply = self._stand_in.answer(self._ip, bytes(self._written))
        chunk, self._reply = self._reply[:max_bytes], self._reply[max_bytes:]
        return chunk

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._written.extend(buffer)

    async def aclose(self) -> None:
        return None

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        return self

    def get_extra_info(self, info: str) -> Any:
        return None


class _Backend(httpcore.AsyncNetworkBackend):
    def __init__(self, stand_in: StandIn) -> None:
        self._stand_in = stand_in

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self._stand_in.connects.append((host, port))
        return _Stream(self._stand_in, host)

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise AssertionError("no unix sockets here")

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


def install(monkeypatch, *, dns: dict[str, list[str]], routes: dict[tuple[str, str], Answer]) -> StandIn:
    """Route the pinned path through the stand-in resolver and socket."""
    from kazma_core.proxy import client as proxy_client
    from kazma_core.security import ssrf, ssrf_pin
    from kazma_core.web_acquire import dns_worker

    stand_in = StandIn(dns=dns, routes=routes)
    monkeypatch.setattr(ssrf, "_resolve_host_ips", lambda host: list(stand_in.dns.get(host, [])))

    async def _validate(url, *, validator=None):
        return list(await asyncio.to_thread(ssrf.validate_url, url, block_unresolved=True))

    monkeypatch.setattr(dns_worker, "validate_public_url", _validate)
    monkeypatch.setattr(proxy_client, "get_active_proxy_url", lambda: None)

    real = ssrf_pin.PinHostAsyncTransport

    class _StandInTransport(real):
        def __init__(self, pins, **kwargs):
            super().__init__(pins, **kwargs)
            # The pinned backend stays: only the socket under it is a stand-in.
            self._pool._network_backend.backend = _Backend(stand_in)

    monkeypatch.setattr(ssrf_pin, "PinHostAsyncTransport", _StandInTransport)
    return stand_in
