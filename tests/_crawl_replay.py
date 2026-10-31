"""Real sites, recorded once, replayed through the real crawl.

A recording (``tests/fixtures/crawl_corpus/<name>.json.gz``) holds every HTTP
response one real Knowledge crawl of a site received -- robots.txt, sitemaps,
llms.txt, pages, redirects -- through the one HTTP seam acquisition has
(``proxy.client.get_scraping_client``). Replayed, the same crawl runs every
step of the product for real: admission, robots enforcement and its durable
claims, the sitemap and HTML parser children, the fetch ladder's bounds and
judgments, retries, chunking, publication and the journal. Only the network
and the browser tier are not real (a page Chromium would rescue is recorded
as what plain HTTP got, both ways).

``scripts/crawl_corpus.py`` records (real network, a private data folder) and
locks the expected outcomes (``expected.json``); ``tests/test_crawl_corpus.py``
replays every site and compares.
"""
from __future__ import annotations

import base64
import contextlib
import gzip
import json
import zlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "crawl_corpus"
SITES = FIXTURES / "sites.json"
EXPECTED = FIXTURES / "expected.json"
#: A public address the replayed responses say they came from (the peer
#: check refuses a response with no peer evidence).
PUBLIC_PEER = ("93.184.216.34", 443)
#: The response headers a recording keeps: what acquisition reads. Cookies
#: and the rest are left out.
_KEPT_HEADERS = ("content-type", "location", "retry-after", "x-robots-tag", "content-language",
                 "link", "refresh", "last-modified", "etag")


def sites() -> list[dict[str, Any]]:
    return json.loads(SITES.read_text(encoding="utf-8"))


def recording_path(name: str) -> Path:
    return FIXTURES / f"{name}.json.gz"


def load(name: str) -> dict[str, Any]:
    with gzip.open(recording_path(name), "rt", encoding="utf-8") as handle:
        return json.load(handle)


def save(name: str, recording: dict[str, Any]) -> None:
    data = json.dumps(recording, ensure_ascii=False, sort_keys=True, indent=0).encode("utf-8")
    # mtime=0: the same recording is the same bytes.
    recording_path(name).write_bytes(gzip.compress(data, compresslevel=9, mtime=0))


def expected() -> dict[str, Any]:
    return json.loads(EXPECTED.read_text(encoding="utf-8")) if EXPECTED.exists() else {}


def _decoded(body: bytes, encoding: str) -> bytes | None:
    if encoding in ("", "identity"):
        return body
    if encoding in ("gzip", "x-gzip"):
        return zlib.decompress(body, 16 + zlib.MAX_WBITS)
    if encoding == "deflate":
        try:
            return zlib.decompress(body)
        except zlib.error:
            return zlib.decompress(body, -zlib.MAX_WBITS)
    return None


def _entry(status: int, headers: httpx.Headers, raw: bytes) -> dict[str, Any]:
    encoding = headers.get("content-encoding", "").strip().lower()
    body = _decoded(raw, encoding)
    kept = {name: headers[name] for name in _KEPT_HEADERS if name in headers}
    if body is None:  # an encoding Kazma does not ask for: kept as it came
        body = raw
        kept["content-encoding"] = encoding
    try:
        return {"status": status, "headers": kept, "text": body.decode("utf-8")}
    except UnicodeDecodeError:
        return {"status": status, "headers": kept, "base64": base64.b64encode(body).decode("ascii")}


class RecordingTransport(httpx.AsyncBaseTransport):
    """The real transport; every response is kept, decoded, by its URL."""

    def __init__(self, inner: httpx.AsyncBaseTransport, sink: dict[str, Any]) -> None:
        self.inner = inner
        self.sink = sink

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.inner.handle_async_request(request)
        try:
            raw = b"".join([chunk async for chunk in response.stream])
        finally:
            await response.stream.aclose()
        if request.method == "GET":
            self.sink[str(request.url)] = _entry(response.status_code, response.headers, raw)
        return httpx.Response(response.status_code, headers=response.headers, stream=httpx.ByteStream(raw),
                              extensions=response.extensions, request=request)

    async def aclose(self) -> None:
        await self.inner.aclose()


class ReplayTransport(httpx.AsyncBaseTransport):
    """Answers each request from a recording; a URL it does not hold fails
    to connect and is named in ``missing`` (the recording is stale)."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.missing: list[str] = []
        self.served: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        entry = self.responses.get(url)
        if entry is None or request.method != "GET":
            self.missing.append(url)
            raise httpx.ConnectError("not in the recording", request=request)
        self.served.append(url)
        body = base64.b64decode(entry["base64"]) if "base64" in entry else entry["text"].encode("utf-8")
        response = httpx.Response(entry["status"], headers=entry["headers"], stream=httpx.ByteStream(body),
                                  request=request)
        response.extensions["network_stream"] = SimpleNamespace(get_extra_info=lambda name: PUBLIC_PEER)
        return response


class ToyEmbedder:
    """Deterministic word-hash vectors for a real vector store in a test (no
    model is loaded under pytest)."""

    dim = 32

    def encode(self, text: str) -> list[float]:
        import hashlib
        import math
        import re

        vector = [0.0] * self.dim
        for word in re.findall(r"\w+", text.lower()):
            vector[int(hashlib.sha256(word.encode()).hexdigest(), 16) % self.dim] += 1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.encode(text) for text in texts]


class _NoSpacing:
    """``origins.origin_delay`` for a replay: no wait between requests to one
    origin (the recording already waited; the scheduler's other rules hold)."""

    def get(self):
        return 0.0

    def set(self, value):
        return None

    def reset(self, token):
        return None


@contextlib.contextmanager
def replaying(monkeypatch, recording: dict[str, Any]):
    """Serve *recording* to every HTTP client acquisition builds."""
    from kazma_core.proxy import client as pc
    from kazma_core.security import ssrf
    from kazma_core.stores import knowledge_ingest as ki
    from kazma_core.tools import read_url as ru
    from kazma_core.web_acquire import origins

    transport = ReplayTransport(recording["responses"])
    original = httpx.AsyncClient

    def client(**kwargs):
        return original(transport=transport, follow_redirects=kwargs.get("follow_redirects", True))

    ru.clear_url_cache()
    monkeypatch.setenv("KAZMA_ACQUISITION_MODE", "local_only")
    monkeypatch.delenv("KAZMA_FETCH_BACKEND", raising=False)
    monkeypatch.setattr(pc, "get_scraping_client", client)
    monkeypatch.setattr(pc, "get_active_proxy_url", lambda: None)
    monkeypatch.setattr(ssrf, "validate_url", lambda *a, **k: (PUBLIC_PEER[0],))
    # The metadata fetch (robots.txt, sitemaps) holds its own reference.
    monkeypatch.setattr("kazma_core.web_acquire.http.validate_url", lambda *a, **k: (PUBLIC_PEER[0],))
    # Production's robots manager: the recorded robots.txt, with its claims
    # durable in the settings store (pytest's stand-in answers "missing").
    from kazma_core.web_acquire import robots

    monkeypatch.setattr(robots, "_MANAGER", robots._RobotsManager(durable=True))
    monkeypatch.setattr(ru, "_recover_hard_page", _no_browser)
    monkeypatch.setattr(origins, "origin_delay", _NoSpacing())
    monkeypatch.setattr(ki, "_kb_delay_ms", lambda: 0)
    monkeypatch.setattr(ki, "_PAGE_RETRY_WAITS", (0, 0))
    try:
        yield transport
    finally:
        ru.clear_url_cache()


async def _no_browser(url, **kwargs):
    return None


@contextlib.contextmanager
def recording_to(monkeypatch, sink: dict[str, Any]):
    """Keep every response the real network gives (scripts/crawl_corpus.py)."""
    from kazma_core.proxy import client as pc
    from kazma_core.tools import read_url as ru

    real = pc.get_scraping_client

    def client(**kwargs):
        made = real(**kwargs)
        made._transport = RecordingTransport(made._transport, sink)
        return made

    monkeypatch.setattr(pc, "get_scraping_client", client)
    monkeypatch.setattr(ru, "_recover_hard_page", _no_browser)
    yield


async def crawl(site: dict[str, Any], folder: Path, monkeypatch, *, vectors=None) -> dict[str, Any]:
    """Crawl *site* into a private Knowledge store under *folder* -- the
    product's own ``ingest_site``, from admission to the journal's verdict --
    and return its :func:`summary`. The network is whatever the caller set up
    (:func:`replaying` or :func:`recording_to`). *vectors*, when given, is the
    embedder the library's meaning search is built with (a real Chroma store
    under *folder*); without it the library is keyword-only."""
    from kazma_core.stores import knowledge, knowledge_index
    from kazma_core.stores import knowledge_ingest as ki
    from kazma_core.stores.knowledge import KnowledgeStore
    from kazma_core.stores.knowledge_index import KnowledgeIndex
    from kazma_core.web_acquire import crawl_jobs

    folder.mkdir(parents=True, exist_ok=True)
    store = KnowledgeStore(db_path=str(folder / "knowledge.db"))
    if vectors is not None:
        from kazma_core.memory import vector_store_global as vsg

        monkeypatch.setenv("KAZMA_VECTOR_PATH", str(folder / "vector_memory"))
        monkeypatch.setattr(vsg, "_CLIENTS", {})
        monkeypatch.setattr(vsg, "get_encoder", lambda model_name=None: vectors)
    index = KnowledgeIndex(store)
    if vectors is None:
        index._vector_store_for = lambda _lib: SimpleNamespace(available=False)
    for module in (ki, knowledge):
        monkeypatch.setattr(module, "get_knowledge_store", lambda: store)
    for module in (ki, knowledge_index):
        monkeypatch.setattr(module, "get_knowledge_index", lambda: index)
    monkeypatch.setattr(ki, "_save_provenance", lambda *a: None)
    name = site["name"]
    store.create_library(name, name, seed_url=site["seed"])  # as the page creates it
    try:
        async for _update in ki.ingest_site(name, site["seed"], max_pages=site["max_pages"], job_id=f"{name}:corpus",
                                            scope=ki.PREVIEW_SCOPES[site["scope"]]):
            pass
        result = summary(crawl_jobs.projection(f"{name}:corpus"), store)
        if vectors is not None:
            result["vectors"] = index._vector_store_for(name).count() if index._vector_store_for(name).available else 0
        return result
    finally:
        store.close()


def summary(projection: dict[str, Any], store) -> dict[str, Any]:
    """What a crawl's outcome is compared by: its verdict, its notes, each
    page's outcome and reason, and how many chunks its library holds."""
    return {
        "status": projection["acquisition"]["status"],
        "notes": sorted(projection.get("notes", [])),
        "pages": {page["url"]: [page["outcome"], page["reason"]] for page in projection["pages"]},
        "chunks": store.count_chunks(projection["library_id"]),
    }
