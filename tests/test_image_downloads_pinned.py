"""A generated image is downloaded on the pinned path, without the key.

Security audit 2026-11-15. Three image backends fetched an address someone
else chose with their own client: DALL-E's ``url`` fallback and FAL's image
``url`` with the API client -- so the provider's key went to whatever host
the answer named -- and Pollinations with redirects followed unchecked.
Every generated image is now downloaded by ``image_backends.base.
fetch_generated_image`` through ``web_acquire.http.fetch_public``: every hop
checked public, the connection pinned to the checked address, the body
bounded, and no credential sent. A provider's key goes only to its own API
host.
"""

from __future__ import annotations

import httpx
import pytest
from kazma_core.security.ssrf import SSRFError
from kazma_core.tools.image_backends.dall_e import DallEBackend
from kazma_core.tools.image_backends.flux import FluxBackend
from kazma_core.tools.image_backends.pollinations import PollinationsBackend

from tests._pinned_stand_in import PUBLIC_IP, Answer, install

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
KEY = "demo-provider-key"


@pytest.fixture()
def provider_api(monkeypatch):
    """The providers' own APIs answer through a MockTransport; a client the
    pinned path builds (it brings its own transport) is left alone."""
    seen: list[httpx.Request] = []
    real = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        host, path = request.url.host, request.url.path
        if host == "api.openai.com" and path == "/v1/images/generations":
            return httpx.Response(200, json={"data": [{"url": "https://images.example/out.png"}]})
        if host == "queue.fal.run" and path.endswith("/status"):
            return httpx.Response(200, json={"status": "COMPLETED"})
        if host == "queue.fal.run" and "/requests/" in path:
            return httpx.Response(200, json={"images": [{"url": "https://images.example/out.png"}]})
        if host == "queue.fal.run":
            return httpx.Response(200, json={"request_id": "r1"})
        if host == "images.example":
            return httpx.Response(200, content=PNG)
        return httpx.Response(404)

    class _Client(real):
        def __init__(self, *args, **kwargs):
            if "transport" not in kwargs:
                kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return seen


def _images_route(**extra):
    return {("images.example", "/out.png"): Answer(200, PNG, {"Content-Type": "image/png"}), **extra}


async def test_dall_e_downloads_the_image_pinned_without_the_key(monkeypatch, provider_api) -> None:
    stand_in = install(monkeypatch, dns={"images.example": [PUBLIC_IP]}, routes=_images_route())
    assert await DallEBackend(api_key=KEY).generate("a cat", 1024, 1024) == PNG
    image = [r for r in stand_in.requests if r["host"] == "images.example"]
    assert len(image) == 1 and "authorization" not in image[0]["headers"]
    assert stand_in.connects == [(PUBLIC_IP, 443)]
    # The key went to the API host, and only there.
    assert {r.url.host for r in provider_api if "authorization" in r.headers} == {"api.openai.com"}


async def test_flux_downloads_the_image_pinned_without_the_key(monkeypatch, provider_api) -> None:
    stand_in = install(monkeypatch, dns={"images.example": [PUBLIC_IP]}, routes=_images_route())
    assert await FluxBackend(api_key=KEY).generate("a cat", 512, 512) == PNG
    assert [r["host"] for r in stand_in.requests] == ["images.example"]
    assert "authorization" not in stand_in.requests[0]["headers"]
    assert {r.url.host for r in provider_api if "authorization" in r.headers} == {"queue.fal.run"}


async def test_an_answer_naming_an_internal_address_is_refused(monkeypatch, provider_api) -> None:
    stand_in = install(monkeypatch, dns={"images.example": ["169.254.169.254"]}, routes=_images_route())
    with pytest.raises(SSRFError):
        await DallEBackend(api_key=KEY).generate("a cat", 1024, 1024)
    assert stand_in.connects == []


async def test_pollinations_checks_every_redirect(monkeypatch) -> None:
    stand_in = install(
        monkeypatch,
        dns={"image.pollinations.ai": [PUBLIC_IP], "inside.example": ["10.0.0.5"]},
        routes={("image.pollinations.ai", "/prompt/cat"): Answer(302, b"", {"Location": "https://inside.example/x"})},
    )
    with pytest.raises(SSRFError):
        await PollinationsBackend().generate("cat", 64, 64)
    assert [r["host"] for r in stand_in.requests] == ["image.pollinations.ai"]


async def test_pollinations_follows_a_public_redirect_pinned(monkeypatch) -> None:
    stand_in = install(
        monkeypatch,
        dns={"image.pollinations.ai": [PUBLIC_IP], "images.example": ["93.184.216.40"]},
        routes={
            ("image.pollinations.ai", "/prompt/cat"): Answer(302, b"", {"Location": "https://images.example/out.png"}),
            **_images_route(),
        },
    )
    assert await PollinationsBackend().generate("cat", 64, 64) == PNG
    assert stand_in.connects == [(PUBLIC_IP, 443), ("93.184.216.40", 443)]


# ---------------------------------------------------------------------------
# Negative controls: the old downloads
# ---------------------------------------------------------------------------


async def test_control_the_old_dall_e_fallback_sent_the_key_to_the_image_host(provider_api) -> None:
    """The fallback as it was: ``client.get(url)`` on the API client."""
    async with httpx.AsyncClient(base_url="https://api.openai.com/v1",
                                 headers={"Authorization": f"Bearer {KEY}"}) as client:
        resp = await client.post("/images/generations", json={"prompt": "a cat"})
        url = resp.json()["data"][0]["url"]
        img = await client.get(url)
    assert img.content == PNG
    sent = [r for r in provider_api if r.url.host == "images.example"]
    assert sent and sent[0].headers["authorization"] == f"Bearer {KEY}"


async def test_control_the_old_pollinations_followed_a_redirect_unchecked() -> None:
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.host)
        if request.url.host == "image.pollinations.ai":
            return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})
        return httpx.Response(200, content=b"metadata")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        response = await client.get("https://image.pollinations.ai/prompt/cat")
    assert response.content == b"metadata" and asked == ["image.pollinations.ai", "169.254.169.254"]
