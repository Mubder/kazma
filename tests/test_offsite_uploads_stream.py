"""An offsite backup of any size reaches OneDrive, WebDAV and S3 a piece at a time.

Audit 2026-10-26 AUD-113. A universal backup zip is about 1.3 GB. OneDrive
sent it in one ``PUT …:/content``, which Graph refuses past 250 MB, so every
OneDrive offsite copy failed; OneDrive, WebDAV and S3 all read the whole
archive into the server's memory first.

Each provider is driven against a fake of its service (``httpx`` transports,
no network) that enforces the service's own rules, scaled down so the test
file stays small: the fake refuses a single upload past its limit, and
records the size of every piece it receives. The old code (the negative
control) sends one piece the size of the file and is refused.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

import httpx
import pytest
from kazma_core.backup import cloud_sync as cs

pytestmark = pytest.mark.usefixtures("scaled")

#: The scaled-down service limit a single upload may not pass.
_FAKE_SINGLE_LIMIT = 1024 * 1024
#: The scaled-down test archive: three times over that limit.
_ARCHIVE_BYTES = 3 * 1024 * 1024 + 12345


class _Config:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def __call__(self, key: str, default: str = "") -> str:
        return self.values.get(key, default)


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    path = tmp_path / "kazma_backup_20261026.zip"
    # Not one repeated byte: a piece sent twice or out of order shows.
    block = bytes(range(256)) * 4096
    data = (block * (_ARCHIVE_BYTES // len(block) + 1))[:_ARCHIVE_BYTES]
    path.write_bytes(data)
    return path


@pytest.fixture
def scaled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The product's size thresholds, scaled to the fake's limit.

    ``raising=False``: on the old code (the negative control) these names do
    not exist, and the old upload runs as it always did.
    """
    monkeypatch.setattr(cs, "_MS_SIMPLE_UPLOAD_MAX", 256 * 1024, raising=False)
    monkeypatch.setattr(cs, "_MS_SESSION_CHUNK", 320 * 1024, raising=False)
    monkeypatch.setattr(cs, "_STREAM_CHUNK", 64 * 1024, raising=False)
    monkeypatch.setattr(cs, "_S3_SINGLE_PUT_MAX", 512 * 1024, raising=False)
    monkeypatch.setattr(cs, "_S3_PART_SIZE", 512 * 1024, raising=False)


def _install(monkeypatch: pytest.MonkeyPatch, transport: httpx.AsyncBaseTransport) -> None:
    real = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real(*args, **kwargs)

    monkeypatch.setattr(cs.httpx, "AsyncClient", factory)


class _PieceRecordingTransport(httpx.AsyncBaseTransport):
    """Reads each request body the way a socket does, recording every piece."""

    def __init__(self, handler: Any) -> None:
        self.handler = handler
        self.pieces: list[tuple[str, str, list[int]]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        sizes: list[int] = []
        body = bytearray()
        async for piece in request.stream:
            sizes.append(len(piece))
            body.extend(piece)
        self.pieces.append((request.method, request.url.path, sizes))
        return self.handler(request, bytes(body))


def test_the_real_thresholds_are_inside_the_services_limits() -> None:
    """The unscaled values (as the module defines them) obey Graph's and
    S3's own rules."""
    import ast
    import types

    def arithmetic(node: ast.AST) -> int:
        """An integer product of literals, the only shape these constants take."""
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            return arithmetic(node.left) * arithmetic(node.right)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            return arithmetic(node.left) ** arithmetic(node.right)
        raise AssertionError(f"not a literal product: {ast.unparse(node)}")

    tree = ast.parse(Path(cs.__file__).read_text(encoding="utf-8"))
    values: dict[str, int] = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in ("_MS_SIMPLE_UPLOAD_MAX", "_MS_SESSION_CHUNK",
                                           "_S3_PART_SIZE", "_S3_SINGLE_PUT_MAX")):
            values[node.targets[0].id] = arithmetic(node.value)
    assert len(values) == 4, values
    real = types.SimpleNamespace(**values)
    assert real._MS_SIMPLE_UPLOAD_MAX < 250 * 1000 * 1000
    assert real._MS_SESSION_CHUNK % (320 * 1024) == 0
    assert real._MS_SESSION_CHUNK <= 60 * 1024 * 1024
    assert 5 * 1024 * 1024 <= real._S3_PART_SIZE <= 5 * 1024 ** 3
    assert real._S3_SINGLE_PUT_MAX <= 5 * 1024 ** 3


# ── OneDrive: an upload session ───────────────────────────────────────────


def _fake_graph(store: dict[str, Any]) -> Any:
    session = "https://up.fake-graph.test/session/abc"

    def handler(request: httpx.Request, body: bytes) -> httpx.Response:
        url = str(request.url)
        if request.url.path == "/v1.0/me":
            return httpx.Response(200, json={})
        if request.method == "PUT" and url.endswith(":/content"):
            # Graph's simple upload, scaled: refused past the limit.
            if len(body) > _FAKE_SINGLE_LIMIT:
                return httpx.Response(413, json={"error": {"code": "requestTooLarge"}})
            store["object"] = body
            return httpx.Response(201, json={"id": "x"})
        if request.method == "POST" and url.endswith(":/createUploadSession"):
            assert request.headers["authorization"] == "Bearer mstok"
            store["received"] = bytearray()
            return httpx.Response(200, json={"uploadUrl": session})
        if url == session and request.method == "PUT":
            # The pre-authenticated URL refuses a bearer token.
            if "authorization" in request.headers:
                return httpx.Response(401)
            first, last, total = map(int, re.match(
                r"bytes (\d+)-(\d+)/(\d+)", request.headers["content-range"]).groups())
            if len(body) > _FAKE_SINGLE_LIMIT or first != len(store["received"]):
                return httpx.Response(416)
            assert last - first + 1 == len(body) == int(request.headers["content-length"])
            store["received"].extend(body)
            if len(store["received"]) == total:
                store["object"] = bytes(store["received"])
                return httpx.Response(201, json={"id": "x"})
            return httpx.Response(202, json={"nextExpectedRanges": [f"{last + 1}-"]})
        if url == session and request.method == "DELETE":
            store["cancelled"] = True
            return httpx.Response(204)
        return httpx.Response(404)

    return handler


def test_onedrive_sends_a_large_archive_through_an_upload_session(
    monkeypatch: pytest.MonkeyPatch, archive: Path
) -> None:
    monkeypatch.setattr(cs, "_read_vault", lambda key: {"email.microsoft.access_token": "mstok"}.get(key, ""))
    store: dict[str, Any] = {}
    transport = _PieceRecordingTransport(_fake_graph(store))
    _install(monkeypatch, transport)

    result = asyncio.run(cs.OneDriveSync().upload_file(archive, archive.name))

    assert result["ok"] is True, result
    assert store["object"] == archive.read_bytes()
    puts = [sizes for method, _path, sizes in transport.pieces if method == "PUT"]
    assert len(puts) >= 3
    assert max(sum(sizes) for sizes in puts) <= _FAKE_SINGLE_LIMIT
    assert "cancelled" not in store


def test_onedrive_cancels_the_session_when_a_fragment_is_refused(
    monkeypatch: pytest.MonkeyPatch, archive: Path
) -> None:
    monkeypatch.setattr(cs, "_read_vault", lambda key: {"email.microsoft.access_token": "mstok"}.get(key, ""))
    store: dict[str, Any] = {}
    graph = _fake_graph(store)
    calls = {"n": 0}

    def flaky(request: httpx.Request, body: bytes) -> httpx.Response:
        if request.method == "PUT" and "session" in request.url.path:
            calls["n"] += 1
            if calls["n"] == 2:
                return httpx.Response(500)
        return graph(request, body)

    _install(monkeypatch, _PieceRecordingTransport(flaky))
    result = asyncio.run(cs.OneDriveSync().upload_file(archive, archive.name))
    assert result["ok"] is False
    assert "500" in result["error"]
    assert store.get("cancelled") is True
    assert "object" not in store


def test_onedrive_small_file_stays_one_put(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cs, "_read_vault", lambda key: {"email.microsoft.access_token": "mstok"}.get(key, ""))
    small = tmp_path / "small.zip"
    small.write_bytes(b"PK" + b"\x01" * 100)
    store: dict[str, Any] = {}
    transport = _PieceRecordingTransport(_fake_graph(store))
    _install(monkeypatch, transport)
    result = asyncio.run(cs.OneDriveSync().upload_file(small, "small.zip"))
    assert result["ok"] is True
    assert store["object"] == small.read_bytes()
    assert [m for m, _p, _s in transport.pieces if m != "GET"] == ["PUT"]


# ── WebDAV: a streamed body ───────────────────────────────────────────────


def test_webdav_streams_the_archive_with_a_declared_length(
    monkeypatch: pytest.MonkeyPatch, archive: Path
) -> None:
    monkeypatch.setattr(cs, "_read_config", _Config({
        "backups.offsite.webdav.url": "https://nas.test/dav",
        "backups.offsite.webdav.username": "u",
    }))
    monkeypatch.setattr(cs, "_read_vault", lambda key: "pw")
    stored: dict[str, Any] = {}

    def handler(request: httpx.Request, body: bytes) -> httpx.Response:
        if request.method == "MKCOL":
            return httpx.Response(201)
        if request.method == "PUT":
            assert request.headers["content-length"] == str(len(body))
            assert "transfer-encoding" not in request.headers
            stored["object"] = body
            return httpx.Response(201)
        return httpx.Response(405)

    transport = _PieceRecordingTransport(handler)
    _install(monkeypatch, transport)
    result = asyncio.run(cs.WebDAVSync().upload_file(archive, archive.name))

    assert result["ok"] is True, result
    assert stored["object"] == archive.read_bytes()
    (put,) = [sizes for method, _p, sizes in transport.pieces if method == "PUT"]
    # The body arrived in pieces; none of them the archive held in memory.
    assert len(put) > 1
    assert max(put) <= _FAKE_SINGLE_LIMIT


# ── S3: multipart, every request signed (SigV4, query included) ───────────


def _sigv4_valid(request: httpx.Request, body: bytes, secret: str, region: str) -> bool:
    """Recompute the request's SigV4 signature, AWS's way, and compare."""
    auth = request.headers["authorization"]
    m = re.match(r"AWS4-HMAC-SHA256 Credential=[^/]+/([^,]+), SignedHeaders=([^,]+), Signature=(\w+)", auth)
    assert m, auth
    scope, signed_names, signature = m.groups()
    if request.headers["x-amz-content-sha256"] != hashlib.sha256(body).hexdigest():
        return False
    pairs = []
    for item in filter(None, request.url.query.decode().split("&")):
        name, _, value = item.partition("=")
        pairs.append((quote(unquote(name), safe="-_.~"), quote(unquote(value), safe="-_.~")))
    canonical_query = "&".join(f"{n}={v}" for n, v in sorted(pairs))
    names = signed_names.split(";")
    canonical_headers = "".join(
        f"{n}:{request.url.host if n == 'host' else request.headers[n]}\n" for n in names
    )
    canonical = "\n".join([
        request.method, quote(request.url.path, safe="/"), canonical_query,
        canonical_headers, signed_names, request.headers["x-amz-content-sha256"],
    ])
    date = scope.split("/")[0]
    key = f"AWS4{secret}".encode()
    for part in (date, region, "s3", "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    to_sign = "\n".join([
        "AWS4-HMAC-SHA256", request.headers["x-amz-date"], scope,
        hashlib.sha256(canonical.encode()).hexdigest(),
    ])
    return hmac.compare_digest(hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest(), signature)


def _fake_s3(state: dict[str, Any], *, fail_part: int | None = None) -> Any:
    ns = "http://s3.amazonaws.com/doc/2006-03-01/"

    def handler(request: httpx.Request, body: bytes) -> httpx.Response:
        if not _sigv4_valid(request, body, "s3secret", "eu-west-1"):
            return httpx.Response(403, content=b"<Error><Code>SignatureDoesNotMatch</Code></Error>")
        params = dict(item.partition("=")[::2] for item in request.url.query.decode().split("&") if item)
        if request.method == "PUT" and not params:
            if len(body) > _FAKE_SINGLE_LIMIT:
                return httpx.Response(400, content=b"<Error><Code>EntityTooLarge</Code></Error>")
            state["object"] = body
            return httpx.Response(200, headers={"etag": '"single"'})
        if request.method == "POST" and "uploads" in params:
            state["parts"] = {}
            return httpx.Response(200, content=(
                f'<?xml version="1.0"?><InitiateMultipartUploadResult xmlns="{ns}">'
                "<Bucket>kazma-bucket</Bucket><Key>k</Key><UploadId>up/id+1</UploadId>"
                "</InitiateMultipartUploadResult>").encode())
        if request.method == "PUT" and "partNumber" in params:
            assert unquote(params["uploadId"]) == "up/id+1"
            number = int(params["partNumber"])
            if number == fail_part:
                return httpx.Response(503)
            state["parts"][number] = body
            return httpx.Response(200, headers={"etag": f'"etag-{number}"'})
        if request.method == "POST" and "uploadId" in params:
            listed = re.findall(rb'<PartNumber>(\d+)</PartNumber><ETag>"etag-(\d+)"</ETag>', body)
            assert all(n == tag for n, tag in listed), body
            numbers = [int(n) for n, _ in listed]
            assert numbers == sorted(state["parts"]), body
            state["object"] = b"".join(state["parts"][n] for n in numbers)
            return httpx.Response(200, content=(
                f'<CompleteMultipartUploadResult xmlns="{ns}"><ETag>"final-3"</ETag>'
                "</CompleteMultipartUploadResult>").encode())
        if request.method == "DELETE" and "uploadId" in params:
            state["aborted"] = True
            return httpx.Response(204)
        return httpx.Response(404)

    return handler


def _s3_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cs, "_read_config", _Config({
        "backups.offsite.s3.access_key": "AKID",
        "backups.offsite.s3.bucket": "kazma-bucket",
        "backups.offsite.s3.endpoint": "https://s3.fake.test",
        "backups.offsite.s3.region": "eu-west-1",
    }))
    monkeypatch.setattr(cs, "_read_vault", lambda key: "s3secret")


def test_s3_sends_a_large_archive_as_a_signed_multipart_upload(
    monkeypatch: pytest.MonkeyPatch, archive: Path
) -> None:
    _s3_config(monkeypatch)
    state: dict[str, Any] = {}
    transport = _PieceRecordingTransport(_fake_s3(state))
    _install(monkeypatch, transport)

    result = asyncio.run(cs.S3Sync().upload_file(archive, archive.name))

    assert result["ok"] is True, result
    assert result["size"] == _ARCHIVE_BYTES
    assert state["object"] == archive.read_bytes()
    assert "aborted" not in state
    parts = [sum(sizes) for method, _p, sizes in transport.pieces if method == "PUT"]
    assert len(parts) >= 3
    assert max(parts) <= _FAKE_SINGLE_LIMIT


def test_s3_aborts_a_multipart_upload_whose_part_fails(
    monkeypatch: pytest.MonkeyPatch, archive: Path
) -> None:
    _s3_config(monkeypatch)
    state: dict[str, Any] = {}
    _install(monkeypatch, _PieceRecordingTransport(_fake_s3(state, fail_part=2)))
    result = asyncio.run(cs.S3Sync().upload_file(archive, archive.name))
    assert result["ok"] is False
    assert "part 2" in result["error"]
    assert state.get("aborted") is True
    assert "object" not in state


def test_s3_reads_an_error_inside_a_200_completion_as_a_failure(
    monkeypatch: pytest.MonkeyPatch, archive: Path
) -> None:
    _s3_config(monkeypatch)
    state: dict[str, Any] = {}
    s3 = _fake_s3(state)

    def error_in_200(request: httpx.Request, body: bytes) -> httpx.Response:
        if request.method == "POST" and "uploadId=" in request.url.query.decode():
            return httpx.Response(200, content=b"<Error><Code>InternalError</Code></Error>")
        return s3(request, body)

    _install(monkeypatch, _PieceRecordingTransport(error_in_200))
    result = asyncio.run(cs.S3Sync().upload_file(archive, archive.name))
    assert result["ok"] is False
    assert state.get("aborted") is True


def test_s3_small_file_stays_one_signed_put(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _s3_config(monkeypatch)
    small = tmp_path / "small.zip"
    small.write_bytes(b"PK" + b"\x02" * 100)
    state: dict[str, Any] = {}
    transport = _PieceRecordingTransport(_fake_s3(state))
    _install(monkeypatch, transport)
    result = asyncio.run(cs.S3Sync().upload_file(small, "small.zip"))
    assert result["ok"] is True
    assert state["object"] == small.read_bytes()
    assert [m for m, _p, _s in transport.pieces] == ["PUT"]
