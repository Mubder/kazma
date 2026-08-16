"""Unit tests for kazma_core.backup.cloud_sync (native cloud providers).

No real network: httpx.AsyncClient is replaced with a MockTransport-backed
factory, and config/vault reads are stubbed via dict fakes (the dev .env
points at a live Postgres — these tests must never touch the real stores).
Includes a real WebDAV round-trip against a threaded local HTTP server.
"""

from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

from kazma_core.backup import cloud_sync as cs


class _FakeConfig:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values: dict[str, str] = values or {}

    def __call__(self, key: str, default: str = "") -> str:
        return self.values.get(key, default)


class _FakeVault:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values: dict[str, str] = values or {}

    def __call__(self, key: str) -> str:
        return self.values.get(key, "")


@pytest.fixture(autouse=True)
def _isolated_stores(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub config/vault reads and capture vault writes into the fake."""
    cfg = _FakeConfig()
    vault = _FakeVault()
    monkeypatch.setattr(cs, "_read_config", cfg)
    monkeypatch.setattr(cs, "_read_vault", vault)

    def _write(key: str, value: str, category: str = "backups") -> None:  # noqa: ARG001
        # Write into whichever fake is currently installed as the reader
        reader = cs._read_vault
        if isinstance(reader, _FakeVault):
            reader.values[key] = value

    monkeypatch.setattr(cs, "_write_vault", _write)

    # The providers fall back to EMAIL_* env vars when the vault is empty; a
    # token written into os.environ by one test would leak into the next and
    # silently skip the refresh path under test. Clear them per test.
    for _var in ("EMAIL_GMAIL_ACCESS_TOKEN", "EMAIL_GMAIL_REFRESH_TOKEN",
                 "EMAIL_MS_ACCESS_TOKEN", "EMAIL_MS_REFRESH_TOKEN"):
        monkeypatch.delenv(_var, raising=False)


def _install_mock_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
    captured: list[httpx.Request],
) -> None:
    """Replace cloud_sync's AsyncClient with a MockTransport-bound factory."""
    real_client = cs.httpx.AsyncClient  # capture BEFORE patching the module attr

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(
            lambda req: _capturing(handler, captured, req)
        )
        return real_client(*args, **kwargs)

    monkeypatch.setattr(cs.httpx, "AsyncClient", factory)


def _capturing(handler: Any, captured: list[httpx.Request], req: httpx.Request) -> httpx.Response:
    captured.append(req)
    return handler(req)


def _json_response(status: int, payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(status, json=payload)


@pytest.fixture
def tmp_backup_dir(tmp_path: Path) -> Path:
    """A small backup dir: one root file + one nested file."""
    dest = tmp_path / "backup_20260815_120000"
    dest.mkdir()
    (dest / "manifest.json").write_text('{"ok": true}', encoding="utf-8")
    nested = dest / "data"
    nested.mkdir()
    (nested / "memory.db").write_bytes(b"\x00" * 64)
    return dest


# ── provider routing ─────────────────────────────────────────────────────


def test_get_sync_provider_routes_by_config() -> None:
    cs._read_config = _FakeConfig({"backups.offsite.provider": "webdav"})  # type: ignore[assignment]
    assert isinstance(cs.get_sync_provider(), cs.WebDAVSync)

    cs._read_config = _FakeConfig({"backups.offsite.provider": "s3"})  # type: ignore[assignment]
    assert isinstance(cs.get_sync_provider(), cs.S3Sync)

    cs._read_config = _FakeConfig({"backups.offsite.provider": ""})  # type: ignore[assignment]
    assert cs.get_sync_provider() is None

    cs._read_config = _FakeConfig({"backups.offsite.provider": "dropbox"})  # type: ignore[assignment]
    assert cs.get_sync_provider() is None


def test_status_reports_connection() -> None:
    cs._read_vault = _FakeVault({"email.gmail.refresh_token": "rt"})  # type: ignore[assignment]
    st = cs.GoogleDriveSync().status()
    assert st["connected"] is True
    assert st["provider"] == "google_drive"

    cs._read_vault = _FakeVault({})  # type: ignore[assignment]
    assert cs.GoogleDriveSync().status()["connected"] is False


# ── Google Drive ─────────────────────────────────────────────────────────


def test_google_drive_upload_and_folder_creation(
    monkeypatch: pytest.MonkeyPatch, tmp_backup_dir: Path
) -> None:
    cs._read_config = _FakeConfig({"backups.offsite.provider": "google_drive"})  # type: ignore[assignment]
    cs._read_vault = _FakeVault({"email.gmail.access_token": "tok"})  # type: ignore[assignment]
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/drive/v3/about":
            return _json_response(200, {"user": {"emailAddress": "a@b.c"}})
        if path == "/drive/v3/files" and req.method == "GET":
            return _json_response(200, {"files": []})
        if path == "/drive/v3/files" and req.method == "POST":
            return _json_response(200, {"id": f"folder-{len(captured)}"})
        if path == "/upload/drive/v3/files":
            return _json_response(200, {"id": "file-1"})
        return httpx.Response(404)

    _install_mock_transport(monkeypatch, handler, captured)

    result = asyncio.run(cs.GoogleDriveSync().upload_directory(tmp_backup_dir, tmp_backup_dir.name))
    assert result["ok"] is True
    assert result["files"] == 2
    # Folder chain created: kazma-backups + <ts> + nested "data" dir
    create_calls = [r for r in captured if r.method == "POST" and r.url.path == "/drive/v3/files"]
    assert len(create_calls) == 3
    uploads = [r for r in captured if "/upload/drive/v3/files" in r.url.path]
    assert len(uploads) == 2
    assert all(r.headers.get("authorization") == "Bearer tok" for r in captured)


def test_google_drive_refreshes_expired_token(
    monkeypatch: pytest.MonkeyPatch, tmp_backup_dir: Path
) -> None:
    cs._read_config = _FakeConfig({"backups.offsite.provider": "google_drive"})  # type: ignore[assignment]
    vault = _FakeVault({
        "email.gmail.access_token": "expired",
        "email.gmail.refresh_token": "rt",
        "email.gmail.client_id": "cid",
        "email.gmail.client_secret": "csec",
    })
    cs._read_vault = vault  # type: ignore[assignment]
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/drive/v3/about":
            if req.headers.get("authorization") == "Bearer expired":
                return httpx.Response(401)
            return _json_response(200, {"user": {}})
        if req.url.path == "/token":
            return _json_response(200, {"access_token": "fresh"})
        if req.url.path == "/drive/v3/files" and req.method == "GET":
            return _json_response(200, {"files": []})
        if req.url.path == "/drive/v3/files":
            return _json_response(200, {"id": "f"})
        if req.url.path == "/upload/drive/v3/files":
            return _json_response(200, {"id": "x"})
        return httpx.Response(404)

    _install_mock_transport(monkeypatch, handler, captured)

    result = asyncio.run(cs.GoogleDriveSync().upload_directory(tmp_backup_dir, tmp_backup_dir.name))
    assert result["ok"] is True
    # The refreshed token was persisted back to the (fake) vault
    assert vault.values["email.gmail.access_token"] == "fresh"


def test_google_drive_without_tokens_fails_clear(
    monkeypatch: pytest.MonkeyPatch, tmp_backup_dir: Path
) -> None:
    cs._read_config = _FakeConfig({"backups.offsite.provider": "google_drive"})  # type: ignore[assignment]
    cs._read_vault = _FakeVault({})  # type: ignore[assignment]
    captured: list[httpx.Request] = []
    _install_mock_transport(monkeypatch, lambda req: httpx.Response(500), captured)

    with pytest.raises(RuntimeError, match="not connected"):
        asyncio.run(cs.GoogleDriveSync().upload_directory(tmp_backup_dir, tmp_backup_dir.name))


# ── OneDrive ─────────────────────────────────────────────────────────────


def test_onedrive_upload_via_graph_put(
    monkeypatch: pytest.MonkeyPatch, tmp_backup_dir: Path
) -> None:
    cs._read_config = _FakeConfig({"backups.offsite.provider": "onedrive"})  # type: ignore[assignment]
    cs._read_vault = _FakeVault({"email.microsoft.access_token": "mstok"})  # type: ignore[assignment]
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1.0/me":
            return _json_response(200, {})
        if req.method == "PUT":
            assert req.url.path.startswith("/v1.0/me/drive/root:/kazma-backups/")
            assert req.headers.get("authorization") == "Bearer mstok"
            return httpx.Response(201)
        return httpx.Response(404)

    _install_mock_transport(monkeypatch, handler, captured)

    result = asyncio.run(cs.OneDriveSync().upload_directory(tmp_backup_dir, tmp_backup_dir.name))
    assert result["ok"] is True
    puts = [r for r in captured if r.method == "PUT"]
    assert len(puts) == 2
    assert any(r.url.path.endswith("/manifest.json:/content") for r in puts)


def test_onedrive_refreshes_and_rotates_token(
    monkeypatch: pytest.MonkeyPatch, tmp_backup_dir: Path
) -> None:
    cs._read_config = _FakeConfig({"backups.offsite.provider": "onedrive"})  # type: ignore[assignment]
    vault = _FakeVault({
        "email.microsoft.access_token": "old",
        "email.microsoft.refresh_token": "rt-old",
        "email.microsoft.client_id": "cid",
    })
    cs._read_vault = vault  # type: ignore[assignment]
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1.0/me":
            if req.headers.get("authorization") == "Bearer old":
                return httpx.Response(401)
            return _json_response(200, {})
        if "login.microsoftonline.com" in req.url.host:
            return _json_response(200, {"access_token": "ms-fresh", "refresh_token": "rt-new"})
        if req.method == "PUT":
            return httpx.Response(201)
        return httpx.Response(404)

    _install_mock_transport(monkeypatch, handler, captured)

    result = asyncio.run(cs.OneDriveSync().upload_directory(tmp_backup_dir, tmp_backup_dir.name))
    assert result["ok"] is True
    assert vault.values["email.microsoft.access_token"] == "ms-fresh"
    assert vault.values["email.microsoft.refresh_token"] == "rt-new"


def _onedrive_token_capture(monkeypatch: pytest.MonkeyPatch, vault: "_FakeVault"):
    """Install a mock transport that captures the MS token-grant form body."""
    cs._read_config = _FakeConfig({"backups.offsite.provider": "onedrive"})  # type: ignore[assignment]
    cs._read_vault = vault  # type: ignore[assignment]
    captured: list[httpx.Request] = []
    token_body: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if "login.microsoftonline.com" in req.url.host:
            from urllib.parse import parse_qsl

            token_body.update(dict(parse_qsl(req.content.decode())))
            return _json_response(200, {"access_token": "t"})
        if req.url.path == "/v1.0/me":
            return _json_response(200, {})
        if req.method == "PUT":
            return httpx.Response(201)
        return httpx.Response(404)

    _install_mock_transport(monkeypatch, handler, captured)
    return token_body


def test_onedrive_refresh_sends_client_secret_when_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_backup_dir: Path
) -> None:
    """Incident 2026-08-16: a confidential Azure app rejects the refresh grant
    with AADSTS70002 unless the client_secret is included."""
    vault = _FakeVault({
        "email.microsoft.refresh_token": "rt",
        "email.microsoft.client_id": "cid",
        "email.microsoft.client_secret": "shh",
    })
    token_body = _onedrive_token_capture(monkeypatch, vault)
    result = asyncio.run(cs.OneDriveSync().upload_directory(tmp_backup_dir, tmp_backup_dir.name))
    assert result["ok"] is True
    assert token_body.get("client_secret") == "shh"
    assert token_body.get("grant_type") == "refresh_token"


def test_onedrive_refresh_omits_client_secret_for_public_client(
    monkeypatch: pytest.MonkeyPatch, tmp_backup_dir: Path
) -> None:
    """Public-client Azure apps store no secret → the grant must omit it."""
    vault = _FakeVault({
        "email.microsoft.refresh_token": "rt",
        "email.microsoft.client_id": "cid",
    })
    token_body = _onedrive_token_capture(monkeypatch, vault)
    result = asyncio.run(cs.OneDriveSync().upload_directory(tmp_backup_dir, tmp_backup_dir.name))
    assert result["ok"] is True
    assert "client_secret" not in token_body
    assert token_body.get("grant_type") == "refresh_token"


# ── WebDAV (unit + local-server integration) ────────────────────────────


def test_webdav_upload_mkcol_and_put(
    monkeypatch: pytest.MonkeyPatch, tmp_backup_dir: Path
) -> None:
    cs._read_config = _FakeConfig({
        "backups.offsite.provider": "webdav",
        "backups.offsite.webdav.url": "https://nas.local/backups",
        "backups.offsite.webdav.username": "user",
    })  # type: ignore[assignment]
    cs._read_vault = _FakeVault({"backups.offsite.webdav.password": "pw"})  # type: ignore[assignment]
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "MKCOL":
            return httpx.Response(201)
        if req.method == "PUT":
            return httpx.Response(201)
        return httpx.Response(405)

    _install_mock_transport(monkeypatch, handler, captured)

    result = asyncio.run(cs.WebDAVSync().upload_directory(tmp_backup_dir, tmp_backup_dir.name))
    assert result["ok"] is True
    assert result["files"] == 2
    mkcols = [r for r in captured if r.method == "MKCOL"]
    assert len(mkcols) == 1  # nested data/ dir only — root files need no parent
    puts = [r for r in captured if r.method == "PUT"]
    assert len(puts) == 2
    # Basic auth attached to every request
    assert all(r.headers.get("authorization", "").startswith("Basic ") for r in captured)


def test_webdav_unconfigured_fails_clear(tmp_backup_dir: Path) -> None:
    cs._read_config = _FakeConfig({
        "backups.offsite.provider": "webdav",
        "backups.offsite.webdav.url": "",
    })  # type: ignore[assignment]

    result = asyncio.run(cs.WebDAVSync().upload_directory(tmp_backup_dir, tmp_backup_dir.name))
    assert result["ok"] is False
    assert "URL not configured" in result["error"]


def test_webdav_partial_failure_reports_count(
    monkeypatch: pytest.MonkeyPatch, tmp_backup_dir: Path
) -> None:
    cs._read_config = _FakeConfig({
        "backups.offsite.provider": "webdav",
        "backups.offsite.webdav.url": "https://nas.local/backups",
    })  # type: ignore[assignment]
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "MKCOL":
            return httpx.Response(201)
        if req.method == "PUT" and req.url.path.endswith("/manifest.json"):
            return httpx.Response(500)
        return httpx.Response(201)

    _install_mock_transport(monkeypatch, handler, captured)

    result = asyncio.run(cs.WebDAVSync().upload_directory(tmp_backup_dir, tmp_backup_dir.name))
    assert result["ok"] is True  # one file still made it
    assert "1/2 files failed" in result["error"]


def test_webdav_integration_real_server(tmp_backup_dir: Path) -> None:
    """End-to-end WebDAV upload against a real threaded local HTTP server."""
    stored: dict[str, bytes] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # silence
            pass

        def do_PROPFIND(self) -> None:  # noqa: N802
            self.send_response(207)
            self.end_headers()

        def do_MKCOL(self) -> None:  # noqa: N802
            self.send_response(201)
            self.end_headers()

        def do_PUT(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            stored[self.path] = self.rfile.read(length)
            self.send_response(201)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cs._read_config = _FakeConfig({  # type: ignore[assignment]
            "backups.offsite.provider": "webdav",
            "backups.offsite.webdav.url": f"http://127.0.0.1:{server.server_port}/dav",
            "backups.offsite.webdav.username": "",
        })
        cs._read_vault = _FakeVault({})  # type: ignore[assignment]

        provider = cs.WebDAVSync()
        result = asyncio.run(provider.upload_directory(tmp_backup_dir, tmp_backup_dir.name))
        assert result["ok"] is True
        assert len(stored) == 2
        # Paths are relative to the DAV root + remote_path
        assert any(p.endswith(f"/{tmp_backup_dir.name}/manifest.json") for p in stored)
        nested_key = next(p for p in stored if p.endswith("/data/memory.db"))
        assert stored[nested_key] == b"\x00" * 64

        # test_connection against the same server
        test = asyncio.run(provider.test_connection())
        assert test["ok"] is True
    finally:
        server.shutdown()
        thread.join(timeout=5)


# ── S3 ───────────────────────────────────────────────────────────────────


def test_s3_upload_signs_with_sigv4(
    monkeypatch: pytest.MonkeyPatch, tmp_backup_dir: Path
) -> None:
    cs._read_config = _FakeConfig({
        "backups.offsite.provider": "s3",
        "backups.offsite.s3.access_key": "AKID",
        "backups.offsite.s3.bucket": "kazma-bucket",
        "backups.offsite.s3.endpoint": "https://s3.example.com",
        "backups.offsite.s3.region": "eu-west-1",
    })  # type: ignore[assignment]
    cs._read_vault = _FakeVault({"backups.offsite.s3.secret_key": "s3secret"})  # type: ignore[assignment]
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    _install_mock_transport(monkeypatch, handler, captured)

    result = asyncio.run(cs.S3Sync().upload_directory(tmp_backup_dir, tmp_backup_dir.name))
    assert result["ok"] is True
    puts = [r for r in captured if r.method == "PUT"]
    assert len(puts) == 2
    for req in puts:
        assert req.headers["x-amz-date"]
        assert req.headers["x-amz-content-sha256"]
        auth = req.headers["authorization"]
        assert auth.startswith("AWS4-HMAC-SHA256 Credential=AKID/")
        assert "SignedHeaders=content-type;host" in auth  # canonical (sorted) order
        assert f"/{tmp_backup_dir.name}/" in req.url.path


def test_s3_unconfigured_fails_clear(tmp_backup_dir: Path) -> None:
    cs._read_config = _FakeConfig({
        "backups.offsite.provider": "s3",
        "backups.offsite.s3.access_key": "",
        "backups.offsite.s3.bucket": "",
    })  # type: ignore[assignment]

    result = asyncio.run(cs.S3Sync().upload_directory(tmp_backup_dir, tmp_backup_dir.name))
    assert result["ok"] is False
    assert "not configured" in result["error"]


def test_s3_signs_head_request() -> None:
    cs._read_config = _FakeConfig({
        "backups.offsite.provider": "s3",
        "backups.offsite.s3.access_key": "AKID",
        "backups.offsite.s3.bucket": "kazma-bucket",
    })  # type: ignore[assignment]
    cs._read_vault = _FakeVault({"backups.offsite.s3.secret_key": "s3secret"})  # type: ignore[assignment]

    provider = cs.S3Sync()
    signed = provider._sign_request(
        "HEAD",
        "https://kazma-bucket.s3.us-east-1.amazonaws.com/",
        {},
        None,
        provider._get_config(),
    )
    assert signed["Authorization"].startswith("AWS4-HMAC-SHA256")
    assert signed["x-amz-content-sha256"] == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
