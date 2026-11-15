"""A chat attachment's address is fetched on the pinned path.

Security audit 2026-11-15. A Discord attachment, a Discord embed image --
which can be the ``og:image`` of any page the sender linked -- and a web
chat's URL attachment reach ``build_user_content`` as a URL. It was fetched
with ``validate_url`` and then a plain client that resolved the name again
for the connection: a name that answered a public address at the check and
127.0.0.1 at the connect made Kazma read an internal-only body into the
chat (reproduced below with the old code against a local server). The
download now goes through ``web_acquire.http.fetch_public``: every redirect
hop checked, the socket pinned to the address that was checked, the peer
checked before the body is read, the body bounded. The same day the voice
path's attachment download (``voice_helpers.download_audio_bytes``), which
used the platform's API client, moved to the pinned download too.
"""

from __future__ import annotations

import asyncio
import http.server
import socket
import threading
from urllib.parse import urljoin

import httpx
import pytest
from kazma_gateway.agent_handler import attachments
from kazma_gateway.gateway import Attachment

from tests._pinned_stand_in import PUBLIC_IP, Answer, install

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
INTERNAL_BODY = b"internal-only: the admin panel"


@pytest.fixture()
def internal_server():
    """A server only this machine can reach (the 'internal' address)."""
    hits: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", str(len(INTERNAL_BODY)))
            self.end_headers()
            self.wfile.write(INTERNAL_BODY)

        def log_message(self, *args):  # silence
            return None

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], hits
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture()
def rebinding(monkeypatch, internal_server):
    """``rebind.example`` answers a public address to the check and the
    internal server's address to a socket that resolves it again."""
    port, hits = internal_server
    real = socket.getaddrinfo

    def _getaddrinfo(host, port_, *args, **kwargs):
        if host == "rebind.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port_ or port))]
        return real(host, port_, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)
    return port, hits


def _fetch_in_a_thread(url: str) -> bytes:
    """``build_user_content`` runs in a worker thread; so does this."""
    out: dict = {}

    def run():
        try:
            out["data"] = attachments._fetch_attachment_url(url)
        except BaseException as exc:  # noqa: BLE001 -- handed to the test
            out["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(60)
    if "error" in out:
        raise out["error"]
    return out["data"]


def _old_fetch_attachment_url(url: str) -> bytes:
    """The fetch as it was until 2026-11-15 (no proxy configured): validate,
    then a plain client that resolves the name again for the connection."""
    from kazma_core.security.ssrf import validate_url

    current_url = url
    with httpx.Client(timeout=30.0, follow_redirects=False, trust_env=False) as client:
        for _ in range(4):
            validate_url(current_url, block_unresolved=True)
            with client.stream("GET", current_url) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    current_url = urljoin(current_url, response.headers["location"])
                    continue
                response.raise_for_status()
                return response.read()
    raise ValueError("redirect limit")


def test_control_the_old_fetch_reads_the_internal_body(monkeypatch, rebinding) -> None:
    port, hits = rebinding
    install(monkeypatch, dns={"rebind.example": [PUBLIC_IP]}, routes={})
    body = _old_fetch_attachment_url(f"http://rebind.example:{port}/secret")
    assert body == INTERNAL_BODY
    assert hits == ["/secret"]


def test_the_fetch_connects_to_the_checked_address(monkeypatch, rebinding) -> None:
    port, hits = rebinding
    stand_in = install(
        monkeypatch,
        dns={"rebind.example": [PUBLIC_IP]},
        routes={("rebind.example", "/secret"): Answer(200, PNG, {"Content-Type": "image/png"})},
    )
    body = _fetch_in_a_thread(f"http://rebind.example:{port}/secret")
    assert body == PNG
    assert stand_in.connects == [(PUBLIC_IP, port)]
    assert hits == []


def test_a_redirect_to_an_internal_address_is_refused_before_it_is_asked(monkeypatch) -> None:
    stand_in = install(
        monkeypatch,
        dns={"cdn.example": [PUBLIC_IP], "inside.example": ["10.0.0.5"]},
        routes={("cdn.example", "/a.png"): Answer(302, b"", {"Location": "https://inside.example/admin"})},
    )
    with pytest.raises(Exception, match="private|reserved|Blocked"):
        _fetch_in_a_thread("https://cdn.example/a.png")
    assert [r["host"] for r in stand_in.requests] == ["cdn.example"]
    assert stand_in.connects == [(PUBLIC_IP, 443)]


def test_a_safe_redirect_is_followed_and_checked(monkeypatch) -> None:
    stand_in = install(
        monkeypatch,
        dns={"cdn.example": [PUBLIC_IP], "media.example": ["93.184.216.35"]},
        routes={
            ("cdn.example", "/a.png"): Answer(302, b"", {"Location": "https://media.example/b.png"}),
            ("media.example", "/b.png"): Answer(200, PNG, {"Content-Type": "image/png"}),
        },
    )
    assert _fetch_in_a_thread("https://cdn.example/a.png") == PNG
    assert stand_in.connects == [(PUBLIC_IP, 443), ("93.184.216.35", 443)]


def test_an_internal_address_is_refused_with_no_request(monkeypatch) -> None:
    stand_in = install(monkeypatch, dns={"inside.example": ["192.168.1.10"]}, routes={})
    with pytest.raises(Exception, match="private|reserved|Blocked"):
        _fetch_in_a_thread("https://inside.example/a.png")
    assert stand_in.connects == [] and stand_in.requests == []


def test_an_oversized_body_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(attachments, "MAX_ATTACHMENT_BYTES", 32)
    install(
        monkeypatch,
        dns={"cdn.example": [PUBLIC_IP]},
        routes={("cdn.example", "/big.pdf"): Answer(200, b"x" * 500)},
    )
    with pytest.raises(ValueError, match="size limit"):
        _fetch_in_a_thread("https://cdn.example/big.pdf")


def test_build_user_content_inlines_the_pinned_download(monkeypatch, rebinding) -> None:
    port, hits = rebinding
    stand_in = install(
        monkeypatch,
        dns={"rebind.example": [PUBLIC_IP]},
        routes={("rebind.example", "/embed.png"): Answer(200, PNG, {"Content-Type": "image/png"})},
    )
    attachment = Attachment(kind="image", mime="image/png", filename="embed.png",
                            url=f"http://rebind.example:{port}/embed.png")
    out: dict = {}
    thread = threading.Thread(target=lambda: out.setdefault(
        "content", attachments.build_user_content("look", [attachment], vision_capable=True)))
    thread.start()
    thread.join(60)
    content = out["content"]
    assert isinstance(content, list)
    image = next(block for block in content if block["type"] == "image_url")
    assert image["image_url"]["url"].startswith("data:image/png;base64,")
    assert stand_in.connects == [(PUBLIC_IP, port)]
    assert hits == []


async def test_the_blocking_fetch_refuses_to_run_on_an_event_loop() -> None:
    with pytest.raises(RuntimeError, match="worker thread"):
        attachments._fetch_attachment_url("https://cdn.example/a.png")
    await asyncio.sleep(0)


async def test_a_voice_attachment_is_downloaded_pinned_without_the_api_client(monkeypatch) -> None:
    """The voice path used the platform's API client (and its credentials)
    for the attachment's address; it now takes the pinned download, and a
    Slack token goes only to Slack's own hosts."""
    from kazma_gateway.adapters.voice_helpers import download_audio_bytes

    stand_in = install(
        monkeypatch,
        dns={"cdn.example": [PUBLIC_IP], "files.slack.com": ["93.184.216.36"]},
        routes={
            ("cdn.example", "/v.ogg"): Answer(200, b"OggS-audio"),
            ("files.slack.com", "/files-pri/v.ogg"): Answer(200, b"OggS-slack"),
        },
    )
    assert await download_audio_bytes(url="https://cdn.example/v.ogg", slack_token="xoxb-demo-token") == b"OggS-audio"
    assert await download_audio_bytes(url="https://files.slack.com/files-pri/v.ogg",
                                      slack_token="xoxb-demo-token") == b"OggS-slack"
    sent = {r["host"]: r["headers"].get("authorization") for r in stand_in.requests}
    assert sent == {"cdn.example": None, "files.slack.com": "Bearer xoxb-demo-token"}
    assert {ip for ip, _port in stand_in.connects} == {PUBLIC_IP, "93.184.216.36"}
