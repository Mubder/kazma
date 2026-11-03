"""Unit tests for kazma_core.backup.cloud_sync (native cloud providers).

No real network: httpx.AsyncClient is replaced with a MockTransport-backed
factory, and config/vault reads are stubbed via dict fakes (the dev .env
points at a live Postgres — these tests must never touch the real stores).
Includes a real WebDAV round-trip against a threaded local HTTP server.
"""

from __future__ import annotations

import asyncio
import ftplib  # nosec B402 - tests the FTP backup backend against a fake FTP class; no network
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
def tmp_backup_archive(tmp_path: Path) -> Path:
    """A small backup archive: what the universal backup uploads, one .zip.

    The folder-tree upload these tests once drove (``upload_directory``) had
    no caller and was removed (audit 2026-10-26 AUD-077); each provider's
    token, signing and configuration paths are exercised through
    ``upload_file``, the one the backup uses.
    """
    archive = tmp_path / "backup_20260815_120000.zip"
    archive.write_bytes(b"PK\x03\x04" + b"\x00" * 60)
    return archive


# ── provider routing ─────────────────────────────────────────────────────


def test_get_sync_provider_routes_by_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cs, "_read_config", _FakeConfig({"backups.offsite.provider": "webdav"}))
    assert isinstance(cs.get_sync_provider(), cs.WebDAVSync)

    monkeypatch.setattr(cs, "_read_config", _FakeConfig({"backups.offsite.provider": "ftp"}))
    assert isinstance(cs.get_sync_provider(), cs.FTPSync)

    monkeypatch.setattr(cs, "_read_config", _FakeConfig({"backups.offsite.provider": "s3"}))
    assert isinstance(cs.get_sync_provider(), cs.S3Sync)

    monkeypatch.setattr(cs, "_read_config", _FakeConfig({"backups.offsite.provider": ""}))
    assert cs.get_sync_provider() is None

    monkeypatch.setattr(cs, "_read_config", _FakeConfig({"backups.offsite.provider": "dropbox"}))
    assert cs.get_sync_provider() is None


def test_status_reports_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cs, "_read_vault", _FakeVault({"email.gmail.refresh_token": "rt"}))
    st = cs.GoogleDriveSync().status()
    assert st["connected"] is True
    assert st["provider"] == "google_drive"

    monkeypatch.setattr(cs, "_read_vault", _FakeVault({}))
    assert cs.GoogleDriveSync().status()["connected"] is False


def test_status_reports_drive_health(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cs, "_read_vault", _FakeVault({
        "email.gmail.refresh_token": "rt",
        "email.gmail.drive_ok": "ok",
    }))
    st = cs.GoogleDriveSync().status()
    assert st["drive_ok"] is True
    assert st["drive_error"] == ""

    # Drive blocked at connect time (e.g. consent screen stripped the scope)
    monkeypatch.setattr(cs, "_read_vault", _FakeVault({
        "email.gmail.refresh_token": "rt",
        "email.gmail.drive_ok": "accessNotConfigured",
    }))
    st = cs.GoogleDriveSync().status()
    assert st["drive_ok"] is False
    assert st["drive_error"] == "accessNotConfigured"

    # Legacy token connected before the drive probe existed — unknown, not broken
    monkeypatch.setattr(cs, "_read_vault", _FakeVault({"email.gmail.refresh_token": "rt"}))
    st = cs.GoogleDriveSync().status()
    assert st["drive_ok"] is None
    assert st["drive_error"] == ""


# ── Google Drive ─────────────────────────────────────────────────────────


def test_google_drive_test_connection_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cs, "_read_config", _FakeConfig({"backups.offsite.provider": "google_drive"}))
    monkeypatch.setattr(cs, "_read_vault", _FakeVault({"email.gmail.access_token": "tok"}))
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/drive/v3/about":
            return _json_response(200, {"user": {"emailAddress": "backup@corp.com"}})
        return httpx.Response(404)

    _install_mock_transport(monkeypatch, handler, captured)

    result = asyncio.run(cs.GoogleDriveSync().test_connection())
    assert result["ok"] is True
    assert "backup@corp.com" in result["message"]


@pytest.mark.parametrize(
    "reason,expected_hint",
    [
        ("accessNotConfigured", "Google Drive API is not enabled"),
        ("insufficientPermissions", "drive.file"),
    ],
)
def test_google_drive_test_connection_403_is_actionable(
    monkeypatch: pytest.MonkeyPatch, reason: str, expected_hint: str
) -> None:
    """A 403 after a fresh grant must say WHY — not a bare status code.

    The two causes are indistinguishable without the error body: Drive API
    disabled in the Cloud project vs. the token lacking the drive.file scope.
    """
    monkeypatch.setattr(cs, "_read_config", _FakeConfig({"backups.offsite.provider": "google_drive"}))
    monkeypatch.setattr(cs, "_read_vault", _FakeVault({
        "email.gmail.access_token": "tok",
        "email.gmail.refresh_token": "rt",
        "email.gmail.client_id": "cid",
        "email.gmail.client_secret": "csec",
    }))
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/token":
            return _json_response(200, {"access_token": "fresh"})
        if req.url.path == "/drive/v3/about":
            return _json_response(
                403,
                {"error": {"code": 403, "message": "nope", "errors": [{"reason": reason}]}},
            )
        return httpx.Response(404)

    _install_mock_transport(monkeypatch, handler, captured)

    result = asyncio.run(cs.GoogleDriveSync().test_connection())
    assert result["ok"] is False
    assert "Drive API error: 403" in result["error"]
    assert expected_hint in result["error"]


def test_google_drive_upload_creates_the_root_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_backup_archive: Path
) -> None:
    monkeypatch.setattr(cs, "_read_config", _FakeConfig({"backups.offsite.provider": "google_drive"}))
    monkeypatch.setattr(cs, "_read_vault", _FakeVault({"email.gmail.access_token": "tok"}))
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

    result = asyncio.run(cs.GoogleDriveSync().upload_file(tmp_backup_archive, tmp_backup_archive.name))
    assert result["ok"] is True
    # The kazma-backups root folder did not exist: it is created once.
    create_calls = [r for r in captured if r.method == "POST" and r.url.path == "/drive/v3/files"]
    assert len(create_calls) == 1
    uploads = [r for r in captured if "/upload/drive/v3/files" in r.url.path]
    assert len(uploads) == 1
    assert all(r.headers.get("authorization") == "Bearer tok" for r in captured)


def test_google_drive_large_file_uses_resumable_upload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AUD-006: a file over Google's 5 MB multipart cap uploads through a
    chunked resumable session, not one multipart POST that Drive rejects."""
    monkeypatch.setattr(cs, "_read_config", _FakeConfig({"backups.offsite.provider": "google_drive"}))
    monkeypatch.setattr(cs, "_read_vault", _FakeVault({"email.gmail.access_token": "tok"}))
    # 9 MB > 5 MB cap and > one 8 MB chunk → two resumable PUTs.
    big = tmp_path / "big.dump"
    big.write_bytes(b"x" * (9 * 1024 * 1024))
    total = big.stat().st_size

    captured: list[httpx.Request] = []
    session_uri = "https://upload.example/session/abc"

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/drive/v3/about":
            return _json_response(200, {"user": {"emailAddress": "a@b.c"}})
        if path == "/drive/v3/files" and req.method == "GET":
            return _json_response(200, {"files": []})
        if path == "/drive/v3/files" and req.method == "POST":
            return _json_response(200, {"id": "root"})
        if path == "/upload/drive/v3/files" and req.method == "POST":
            assert req.url.params.get("uploadType") == "resumable"
            return httpx.Response(200, headers={"Location": session_uri})
        if str(req.url) == session_uri and req.method == "PUT":
            cr = req.headers["content-range"]  # "bytes start-end/total"
            end = int(cr.split("-", 1)[1].split("/", 1)[0])
            if end + 1 >= total:
                return _json_response(200, {"id": "file-1"})
            return httpx.Response(308)
        return httpx.Response(404)

    _install_mock_transport(monkeypatch, handler, captured)

    result = asyncio.run(cs.GoogleDriveSync().upload_file(big, "big.dump"))
    assert result["ok"] is True

    inits = [
        r for r in captured
        if r.url.path == "/upload/drive/v3/files" and r.method == "POST"
    ]
    assert len(inits) == 1  # one resumable session, no multipart POST
    assert inits[0].url.params.get("uploadType") == "resumable"

    puts = [r for r in captured if str(r.url) == session_uri and r.method == "PUT"]
    assert len(puts) == 2  # 9 MB / 8 MB chunk
    ranges = [r.headers["content-range"] for r in puts]
    assert ranges[0].startswith("bytes 0-")
    assert ranges[-1].endswith(f"/{total}")


def test_google_drive_refreshes_expired_token(
    monkeypatch: pytest.MonkeyPatch, tmp_backup_archive: Path
) -> None:
    monkeypatch.setattr(cs, "_read_config", _FakeConfig({"backups.offsite.provider": "google_drive"}))
    vault = _FakeVault({
        "email.gmail.access_token": "expired",
        "email.gmail.refresh_token": "rt",
        "email.gmail.client_id": "cid",
        "email.gmail.client_secret": "csec",
    })
    monkeypatch.setattr(cs, "_read_vault", vault)
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

    result = asyncio.run(cs.GoogleDriveSync().upload_file(tmp_backup_archive, tmp_backup_archive.name))
    assert result["ok"] is True
    # The refreshed token was persisted back to the (fake) vault
    assert vault.values["email.gmail.access_token"] == "fresh"


def test_google_drive_without_tokens_fails_clear(
    monkeypatch: pytest.MonkeyPatch, tmp_backup_archive: Path
) -> None:
    monkeypatch.setattr(cs, "_read_config", _FakeConfig({"backups.offsite.provider": "google_drive"}))
    monkeypatch.setattr(cs, "_read_vault", _FakeVault({}))
    captured: list[httpx.Request] = []
    _install_mock_transport(monkeypatch, lambda req: httpx.Response(500), captured)

    with pytest.raises(RuntimeError, match="not connected"):
        asyncio.run(cs.GoogleDriveSync().upload_file(tmp_backup_archive, tmp_backup_archive.name))


def test_google_drive_upload_file_single_archive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cs, "_read_config", _FakeConfig({"backups.offsite.provider": "google_drive"}))
    monkeypatch.setattr(cs, "_read_vault", _FakeVault({"email.gmail.access_token": "tok"}))
    local = tmp_path / "backup_20260816_120000.zip"
    local.write_bytes(b"ZIPDATA")
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/drive/v3/about":
            return _json_response(200, {"user": {}})
        if req.url.path == "/drive/v3/files" and req.method == "GET":
            # The kazma-backups root folder already exists
            return _json_response(200, {"files": [{"id": "root1"}]})
        if req.url.path == "/upload/drive/v3/files":
            return _json_response(200, {"id": "file-1"})
        return httpx.Response(404)

    _install_mock_transport(monkeypatch, handler, captured)

    result = asyncio.run(cs.GoogleDriveSync().upload_file(local, local.name))
    assert result["ok"] is True
    assert result["remote"] == "google_drive:kazma-backups/backup_20260816_120000.zip"
    uploads = [r for r in captured if "/upload/drive/v3/files" in r.url.path]
    assert len(uploads) == 1
    assert uploads[0].headers.get("authorization") == "Bearer tok"
    # Single multipart body: metadata names the file + root parent, payload is the zip
    body = uploads[0].content
    assert b'"parents": ["root1"]' in body
    assert b"ZIPDATA" in body


def test_google_drive_upload_file_reports_actionable_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cs, "_read_config", _FakeConfig({"backups.offsite.provider": "google_drive"}))
    monkeypatch.setattr(cs, "_read_vault", _FakeVault({"email.gmail.access_token": "tok"}))
    local = tmp_path / "b.zip"
    local.write_bytes(b"x")
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/drive/v3/about":
            return _json_response(200, {"user": {}})
        if req.url.path == "/drive/v3/files" and req.method == "GET":
            return _json_response(200, {"files": [{"id": "root1"}]})
        if req.url.path == "/upload/drive/v3/files":
            return _json_response(
                403,
                {"error": {"code": 403, "message": "nope", "errors": [{"reason": "accessNotConfigured"}]}},
            )
        return httpx.Response(404)

    _install_mock_transport(monkeypatch, handler, captured)

    result = asyncio.run(cs.GoogleDriveSync().upload_file(local, "b.zip"))
    assert result["ok"] is False
    assert "Google Drive API is not enabled" in result["error"]


# ── OneDrive ─────────────────────────────────────────────────────────────


def test_onedrive_refreshes_and_rotates_token(
    monkeypatch: pytest.MonkeyPatch, tmp_backup_archive: Path
) -> None:
    monkeypatch.setattr(cs, "_read_config", _FakeConfig({"backups.offsite.provider": "onedrive"}))
    vault = _FakeVault({
        "email.microsoft.access_token": "old",
        "email.microsoft.refresh_token": "rt-old",
        "email.microsoft.client_id": "cid",
    })
    monkeypatch.setattr(cs, "_read_vault", vault)
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

    result = asyncio.run(cs.OneDriveSync().upload_file(tmp_backup_archive, tmp_backup_archive.name))
    assert result["ok"] is True
    assert vault.values["email.microsoft.access_token"] == "ms-fresh"
    assert vault.values["email.microsoft.refresh_token"] == "rt-new"


def test_onedrive_upload_file_single_archive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cs, "_read_config", _FakeConfig({"backups.offsite.provider": "onedrive"}))
    monkeypatch.setattr(cs, "_read_vault", _FakeVault({"email.microsoft.access_token": "mstok"}))
    local = tmp_path / "backup.zip"
    local.write_bytes(b"ZIP")
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1.0/me":
            return _json_response(200, {})
        if req.method == "PUT":
            assert req.url.path == "/v1.0/me/drive/root:/kazma-backups/backup.zip:/content"
            assert req.headers.get("authorization") == "Bearer mstok"
            return httpx.Response(201)
        return httpx.Response(404)

    _install_mock_transport(monkeypatch, handler, captured)

    result = asyncio.run(cs.OneDriveSync().upload_file(local, "backup.zip"))
    assert result["ok"] is True
    assert result["remote"] == "onedrive:kazma-backups/backup.zip"
    puts = [r for r in captured if r.method == "PUT"]
    assert len(puts) == 1
    assert puts[0].content == b"ZIP"


def _onedrive_token_capture(monkeypatch: pytest.MonkeyPatch, vault: "_FakeVault"):
    """Install a mock transport that captures the MS token-grant form body."""
    monkeypatch.setattr(cs, "_read_config", _FakeConfig({"backups.offsite.provider": "onedrive"}))
    monkeypatch.setattr(cs, "_read_vault", vault)
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
    monkeypatch: pytest.MonkeyPatch, tmp_backup_archive: Path
) -> None:
    """Incident 2026-08-16: a confidential Azure app rejects the refresh grant
    with AADSTS70002 unless the client_secret is included."""
    vault = _FakeVault({
        "email.microsoft.refresh_token": "rt",
        "email.microsoft.client_id": "cid",
        "email.microsoft.client_secret": "shh",
    })
    token_body = _onedrive_token_capture(monkeypatch, vault)
    result = asyncio.run(cs.OneDriveSync().upload_file(tmp_backup_archive, tmp_backup_archive.name))
    assert result["ok"] is True
    assert token_body.get("client_secret") == "shh"
    assert token_body.get("grant_type") == "refresh_token"


def test_onedrive_refresh_omits_client_secret_for_public_client(
    monkeypatch: pytest.MonkeyPatch, tmp_backup_archive: Path
) -> None:
    """Public-client Azure apps store no secret → the grant must omit it."""
    vault = _FakeVault({
        "email.microsoft.refresh_token": "rt",
        "email.microsoft.client_id": "cid",
    })
    token_body = _onedrive_token_capture(monkeypatch, vault)
    result = asyncio.run(cs.OneDriveSync().upload_file(tmp_backup_archive, tmp_backup_archive.name))
    assert result["ok"] is True
    assert "client_secret" not in token_body
    assert token_body.get("grant_type") == "refresh_token"


# ── WebDAV (unit + local-server integration) ────────────────────────────


def test_webdav_unconfigured_fails_clear(monkeypatch: pytest.MonkeyPatch, tmp_backup_archive: Path) -> None:
    monkeypatch.setattr(cs, "_read_config", _FakeConfig({
        "backups.offsite.provider": "webdav",
        "backups.offsite.webdav.url": "",
    }))

    result = asyncio.run(cs.WebDAVSync().upload_file(tmp_backup_archive, tmp_backup_archive.name))
    assert result["ok"] is False
    assert "URL not configured" in result["error"]


def test_webdav_upload_file_single_archive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cs, "_read_config", _FakeConfig({
        "backups.offsite.provider": "webdav",
        "backups.offsite.webdav.url": "https://nas.local/backups",
        "backups.offsite.webdav.username": "user",
    }))
    monkeypatch.setattr(cs, "_read_vault", _FakeVault({"backups.offsite.webdav.password": "pw"}))
    local = tmp_path / "backup.zip"
    local.write_bytes(b"ZIP")
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "MKCOL":
            assert req.url.path == "/backups/kazma-backups"
            return httpx.Response(201)
        if req.method == "PUT":
            assert req.url.path == "/backups/kazma-backups/backup.zip"
            return httpx.Response(201)
        return httpx.Response(405)

    _install_mock_transport(monkeypatch, handler, captured)

    result = asyncio.run(cs.WebDAVSync().upload_file(local, "backup.zip"))
    assert result["ok"] is True
    assert result["remote"] == "webdav:nas.local/backups/kazma-backups/backup.zip"
    assert all(r.headers.get("authorization", "").startswith("Basic ") for r in captured)


def test_webdav_integration_real_server(monkeypatch: pytest.MonkeyPatch, tmp_backup_archive: Path) -> None:
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
        monkeypatch.setattr(cs, "_read_config", _FakeConfig({
            "backups.offsite.provider": "webdav",
            "backups.offsite.webdav.url": f"http://127.0.0.1:{server.server_port}/dav",
            "backups.offsite.webdav.username": "",
        }))
        monkeypatch.setattr(cs, "_read_vault", _FakeVault({}))

        provider = cs.WebDAVSync()
        result = asyncio.run(provider.upload_file(tmp_backup_archive, tmp_backup_archive.name))
        assert result["ok"] is True
        assert len(stored) == 1
        # The archive lands under the DAV root's kazma-backups folder, whole.
        assert stored[f"/dav/kazma-backups/{tmp_backup_archive.name}"] == tmp_backup_archive.read_bytes()

        # test_connection against the same server
        test = asyncio.run(provider.test_connection())
        assert test["ok"] is True
    finally:
        server.shutdown()
        thread.join(timeout=5)


# ── FTP (WD MyCloud OS3) ─────────────────────────────────────────────────


class _FakeFTP:
    """Stateful fake for ftplib.FTP — enough to exercise _ensure_dir walks."""

    instances: list["_FakeFTP"] = []

    def __init__(self) -> None:
        self.connect_args: tuple[Any, ...] | None = None
        self.login_args: tuple[Any, ...] | None = None
        self.calls: list[tuple[str, str]] = []
        self.stored: dict[str, bytes] = {}
        self._dirs: set[str] = set()
        _FakeFTP.instances.append(self)

    def connect(self, host: str, port: int, timeout: Any = None) -> None:
        self.connect_args = (host, port, timeout)

    def login(self, user: str = "", passwd: str = "") -> None:
        self.login_args = (user, passwd)

    def set_pasv(self, on: bool) -> None:
        self.calls.append(("pasv", str(on)))

    def pwd(self) -> str:
        return "/shares"

    def cwd(self, path: str) -> None:
        self.calls.append(("cwd", path))
        if path == "/shares":
            return
        if path in self._dirs:
            return
        raise ftplib.error_perm("550 no such dir")

    def mkd(self, path: str) -> None:
        self.calls.append(("mkd", path))
        self._dirs.add(path)

    def storbinary(self, cmd: str, fh: Any) -> None:
        self.calls.append(("stor", cmd))
        self.stored[cmd.split(" ", 1)[1]] = fh.read()

    def quit(self) -> None:
        self.calls.append(("quit",))


@pytest.fixture
def fake_ftplib(monkeypatch: pytest.MonkeyPatch):
    """Route cloud_sync's local `import ftplib` to the fake class."""
    import ftplib as real_ftplib  # nosec B402 - patched to a fake below; no network

    class _NoTLS:
        """A server that refuses AUTH TLS: _connect falls back to the fake FTP.
        The real FTP_TLS connected to the configured host itself -- a LAN
        address -- before the fallback these tests exercise
        (tests/_egress_guard.py, 2026-10-15)."""

        def connect(self, host: str, port: int, timeout: Any = None) -> None:
            raise real_ftplib.error_perm("534 AUTH TLS not supported (test server)")

    _FakeFTP.instances.clear()
    monkeypatch.setattr(real_ftplib, "FTP", _FakeFTP)
    monkeypatch.setattr(real_ftplib, "FTP_TLS", _NoTLS)
    return real_ftplib


def test_ftp_upload_file_single_archive(
    monkeypatch: pytest.MonkeyPatch, fake_ftplib: Any, tmp_path: Path
) -> None:
    monkeypatch.setattr(cs, "_read_config", _FakeConfig({
        "backups.offsite.provider": "ftp",
        "backups.offsite.ftp.host": "192.168.50.45",
        "backups.offsite.ftp.port": "2121",
        "backups.offsite.ftp.username": "user",
        "backups.offsite.ftp.path": "Mubder Alfaris",
    }))
    monkeypatch.setattr(cs, "_read_vault", _FakeVault({"backups.offsite.ftp.password": "pw"}))
    local = tmp_path / "backup.zip"
    local.write_bytes(b"ZIPDATA")

    result = asyncio.run(cs.FTPSync().upload_file(local, "backup.zip"))

    assert result["ok"] is True
    assert result["remote"] == "ftp:192.168.50.45/kazma-backups/backup.zip"
    ftp = _FakeFTP.instances[0]
    assert ftp.connect_args == ("192.168.50.45", 2121, 15)
    assert ftp.login_args == ("user", "pw")
    assert ("stor", "STOR backup.zip") in ftp.calls
    assert ftp.stored["backup.zip"] == b"ZIPDATA"
    # Directory chain created: base path + kazma-backups root
    mkds = [c for c in ftp.calls if c[0] == "mkd"]
    assert ("mkd", "Mubder Alfaris") in mkds
    assert ("mkd", "kazma-backups") in mkds


def test_ftp_test_connection(monkeypatch: pytest.MonkeyPatch, fake_ftplib: Any) -> None:
    monkeypatch.setattr(cs, "_read_config", _FakeConfig({
        "backups.offsite.provider": "ftp",
        "backups.offsite.ftp.host": "192.168.50.45",
        "backups.offsite.ftp.username": "user",
    }))
    monkeypatch.setattr(cs, "_read_vault", _FakeVault({"backups.offsite.ftp.password": "pw"}))

    result = asyncio.run(cs.FTPSync().test_connection())
    assert result["ok"] is True
    assert result["message"] == "FTP: 192.168.50.45"

    monkeypatch.setattr(cs, "_read_config", _FakeConfig({"backups.offsite.provider": "ftp"}))
    result = asyncio.run(cs.FTPSync().test_connection())
    assert result["ok"] is False
    assert result["error"] == "FTP host not configured"


def test_ftp_unconfigured_fails_clear(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cs, "_read_config", _FakeConfig({"backups.offsite.provider": "ftp"}))
    monkeypatch.setattr(cs, "_read_vault", _FakeVault({}))

    result = asyncio.run(cs.FTPSync().upload_file(tmp_path / "x.zip", "x.zip"))
    assert result["ok"] is False
    assert result["error"] == "FTP host not configured"


def test_ftp_status_reports_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cs, "_read_config", _FakeConfig({
        "backups.offsite.ftp.host": "192.168.50.45",
        "backups.offsite.ftp.username": "user",
    }))
    st = cs.FTPSync().status()
    assert st["provider"] == "ftp"
    assert st["connected"] is True
    assert st["remote"] == "ftp:192.168.50.45"

    monkeypatch.setattr(cs, "_read_config", _FakeConfig({}))
    assert cs.FTPSync().status()["connected"] is False


# ── S3 ───────────────────────────────────────────────────────────────────


def test_s3_upload_signs_with_sigv4(
    monkeypatch: pytest.MonkeyPatch, tmp_backup_archive: Path
) -> None:
    monkeypatch.setattr(cs, "_read_config", _FakeConfig({
        "backups.offsite.provider": "s3",
        "backups.offsite.s3.access_key": "AKID",
        "backups.offsite.s3.bucket": "kazma-bucket",
        "backups.offsite.s3.endpoint": "https://s3.example.com",
        "backups.offsite.s3.region": "eu-west-1",
    }))
    monkeypatch.setattr(cs, "_read_vault", _FakeVault({"backups.offsite.s3.secret_key": "s3secret"}))
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    _install_mock_transport(monkeypatch, handler, captured)

    result = asyncio.run(cs.S3Sync().upload_file(tmp_backup_archive, tmp_backup_archive.name))
    assert result["ok"] is True
    puts = [r for r in captured if r.method == "PUT"]
    assert len(puts) == 1
    for req in puts:
        assert req.headers["x-amz-date"]
        assert req.headers["x-amz-content-sha256"]
        auth = req.headers["authorization"]
        assert auth.startswith("AWS4-HMAC-SHA256 Credential=AKID/")
        assert "SignedHeaders=content-type;host" in auth  # canonical (sorted) order
        assert req.url.path.endswith(f"/kazma-backups/{tmp_backup_archive.name}")


def test_s3_unconfigured_fails_clear(monkeypatch: pytest.MonkeyPatch, tmp_backup_archive: Path) -> None:
    monkeypatch.setattr(cs, "_read_config", _FakeConfig({
        "backups.offsite.provider": "s3",
        "backups.offsite.s3.access_key": "",
        "backups.offsite.s3.bucket": "",
    }))

    result = asyncio.run(cs.S3Sync().upload_file(tmp_backup_archive, tmp_backup_archive.name))
    assert result["ok"] is False
    assert "not configured" in result["error"]


def test_s3_upload_file_single_archive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cs, "_read_config", _FakeConfig({
        "backups.offsite.provider": "s3",
        "backups.offsite.s3.access_key": "AKID",
        "backups.offsite.s3.bucket": "kazma-bucket",
        "backups.offsite.s3.endpoint": "https://s3.example.com",
    }))
    monkeypatch.setattr(cs, "_read_vault", _FakeVault({"backups.offsite.s3.secret_key": "s3secret"}))
    local = tmp_path / "backup.zip"
    local.write_bytes(b"ZIP")
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    _install_mock_transport(monkeypatch, handler, captured)

    result = asyncio.run(cs.S3Sync().upload_file(local, "backup.zip"))
    assert result["ok"] is True
    assert result["remote"] == "s3:kazma-bucket/kazma-backups/backup.zip"
    puts = [r for r in captured if r.method == "PUT"]
    assert len(puts) == 1
    # Endpoint-style URL: {endpoint}/{bucket}/{key}
    assert puts[0].url.path == "/kazma-bucket/kazma-backups/backup.zip"
    assert puts[0].headers["authorization"].startswith("AWS4-HMAC-SHA256")
    assert puts[0].content == b"ZIP"


def test_s3_signs_head_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cs, "_read_config", _FakeConfig({
        "backups.offsite.provider": "s3",
        "backups.offsite.s3.access_key": "AKID",
        "backups.offsite.s3.bucket": "kazma-bucket",
    }))
    monkeypatch.setattr(cs, "_read_vault", _FakeVault({"backups.offsite.s3.secret_key": "s3secret"}))

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


# ── listing and deleting old copies (2026-11-03) ─────────────────────────
#
# The universal backup keeps the newest ``backups.offsite.keep`` copies on the
# remote and deletes the rest through these two calls. The prune itself is
# tested in tests/test_offsite_retention.py; here each provider's own answers:
# every page of a listing, an empty or missing folder, a listing that cannot
# be read (an error, never "nothing there"), and a copy already gone (done).


def _s3_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cs, "_read_config", _FakeConfig({
        "backups.offsite.provider": "s3",
        "backups.offsite.s3.access_key": "AKID",
        "backups.offsite.s3.bucket": "kazma-bucket",
        "backups.offsite.s3.endpoint": "https://s3.example.com",
    }))
    monkeypatch.setattr(cs, "_read_vault", _FakeVault({"backups.offsite.s3.secret_key": "s3secret"}))


def _s3_page(keys: list[str], next_token: str = "") -> bytes:
    contents = "".join(f"<Contents><Key>{k}</Key></Contents>" for k in keys)
    more = (f"<IsTruncated>true</IsTruncated><NextContinuationToken>{next_token}</NextContinuationToken>"
            if next_token else "<IsTruncated>false</IsTruncated>")
    return (f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
            f"{contents}{more}</ListBucketResult>").encode()


def test_s3_lists_every_page_and_signs_the_query_it_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    _s3_config(monkeypatch)
    captured: list[httpx.Request] = []
    signed_urls: list[str] = []
    real_sign = cs.S3Sync._sign_request

    def sign(self: Any, method: str, url: str, *args: Any) -> dict[str, str]:
        signed_urls.append(url)
        return real_sign(self, method, url, *args)

    monkeypatch.setattr(cs.S3Sync, "_sign_request", sign)

    def handler(req: httpx.Request) -> httpx.Response:
        if "continuation-token" not in str(req.url):
            return httpx.Response(200, content=_s3_page(
                ["kazma-backups/1700000000.zip", "kazma-backups/1700000100.zip"], "tok/2+"))
        # A file under a sub-folder is not one of the copies.
        return httpx.Response(200, content=_s3_page(
            ["kazma-backups/1700000200.zip", "kazma-backups/nested/x.zip"]))

    _install_mock_transport(monkeypatch, handler, captured)
    result = asyncio.run(cs.S3Sync().list_backups())

    assert result == {"ok": True, "names": ["1700000000.zip", "1700000100.zip", "1700000200.zip"]}
    assert len(captured) == 2
    assert dict(captured[1].url.params)["continuation-token"] == "tok/2+"
    # The signature covers the query string the request actually carries.
    for req, url in zip(captured, signed_urls):
        assert cs._sigv4_query(req.url.query.decode()) == cs._sigv4_query(url.split("?", 1)[1])


def test_s3_listing_that_says_more_without_saying_where_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _s3_config(monkeypatch)
    body = _s3_page(["kazma-backups/1700000000.zip"]).replace(
        b"<IsTruncated>false</IsTruncated>", b"<IsTruncated>true</IsTruncated>")
    _install_mock_transport(monkeypatch, lambda req: httpx.Response(200, content=body), [])

    result = asyncio.run(cs.S3Sync().list_backups())
    assert result["ok"] is False
    assert "could not be read" in result["error"]


def test_s3_listing_refused_is_an_error_not_an_empty_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    _s3_config(monkeypatch)
    _install_mock_transport(monkeypatch, lambda req: httpx.Response(403, content=b"<Error/>"), [])

    result = asyncio.run(cs.S3Sync().list_backups())
    assert result == {"ok": False, "error": "S3 error: HTTP 403 listing the backups"}


def test_s3_delete_signs_and_takes_a_gone_copy_as_done(monkeypatch: pytest.MonkeyPatch) -> None:
    _s3_config(monkeypatch)
    captured: list[httpx.Request] = []
    statuses = iter([204, 404, 403])
    _install_mock_transport(monkeypatch, lambda req: httpx.Response(next(statuses)), captured)

    provider = cs.S3Sync()
    assert asyncio.run(provider.delete_backup("1700000000.zip")) == {"ok": True}
    assert asyncio.run(provider.delete_backup("1700000000.zip")) == {"ok": True}
    assert asyncio.run(provider.delete_backup("1700000000.zip")) == {"ok": False, "error": "S3 error: HTTP 403"}
    assert all(r.method == "DELETE" for r in captured)
    assert captured[0].url.path == "/kazma-bucket/kazma-backups/1700000000.zip"
    assert captured[0].headers["authorization"].startswith("AWS4-HMAC-SHA256")


def _gdrive_signed_in(monkeypatch: pytest.MonkeyPatch) -> None:
    async def token(self: Any) -> str:
        return "gtoken"

    async def folder(self: Any, token: str, name: str, parent: str | None = None) -> str:
        return "root-folder-id"

    monkeypatch.setattr(cs.GoogleDriveSync, "_get_access_token", token)
    monkeypatch.setattr(cs.GoogleDriveSync, "_ensure_folder", folder)


def test_gdrive_lists_every_page_of_the_backup_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    _gdrive_signed_in(monkeypatch)
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.params.get("pageToken") == "p2":
            return _json_response(200, {"files": [{"id": "c", "name": "1700000200.zip"}]})
        return _json_response(200, {
            "files": [{"id": "a", "name": "1700000000.zip"}, {"id": "b", "name": "1700000100.zip"}],
            "nextPageToken": "p2",
        })

    _install_mock_transport(monkeypatch, handler, captured)
    result = asyncio.run(cs.GoogleDriveSync().list_backups())

    assert result == {"ok": True, "names": ["1700000000.zip", "1700000100.zip", "1700000200.zip"]}
    assert "'root-folder-id' in parents" in captured[0].url.params["q"]
    assert "trashed=false" in captured[0].url.params["q"]


def test_gdrive_listing_refused_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _gdrive_signed_in(monkeypatch)
    _install_mock_transport(monkeypatch, lambda req: _json_response(401, {}), [])

    result = asyncio.run(cs.GoogleDriveSync().list_backups())
    assert result["ok"] is False
    assert "Drive API error: 401" in result["error"]


def test_gdrive_delete_takes_every_file_of_that_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drive lets two files share a name: a delete by name takes both."""
    _gdrive_signed_in(monkeypatch)
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return _json_response(200, {"files": [{"id": "x1", "name": "1700000000.zip"},
                                                  {"id": "x2", "name": "1700000000.zip"}]})
        return httpx.Response(204 if req.url.path.endswith("/x1") else 404)

    _install_mock_transport(monkeypatch, handler, captured)
    result = asyncio.run(cs.GoogleDriveSync().delete_backup("1700000000.zip"))

    assert result == {"ok": True}
    assert "name='1700000000.zip'" in captured[0].url.params["q"]
    deleted = [r.url.path for r in captured if r.method == "DELETE"]
    assert deleted == ["/drive/v3/files/x1", "/drive/v3/files/x2"]


def test_gdrive_delete_refused_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _gdrive_signed_in(monkeypatch)

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return _json_response(200, {"files": [{"id": "x1", "name": "1700000000.zip"}]})
        return _json_response(403, {})

    _install_mock_transport(monkeypatch, handler, [])
    result = asyncio.run(cs.GoogleDriveSync().delete_backup("1700000000.zip"))
    assert result["ok"] is False
    assert "Drive API error: 403" in result["error"]


def _onedrive_signed_in(monkeypatch: pytest.MonkeyPatch) -> None:
    async def token(self: Any) -> str:
        return "mstoken"

    monkeypatch.setattr(cs.OneDriveSync, "_get_access_token", token)


def test_onedrive_lists_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    _onedrive_signed_in(monkeypatch)
    captured: list[httpx.Request] = []
    next_link = "https://graph.microsoft.com/v1.0/me/drive/items/abc/children?$skiptoken=2"

    def handler(req: httpx.Request) -> httpx.Response:
        if "skiptoken" in str(req.url):
            return _json_response(200, {"value": [{"name": "1700000200.zip"}]})
        return _json_response(200, {"value": [{"name": "1700000000.zip"}, {"name": "1700000100.zip"}],
                                    "@odata.nextLink": next_link})

    _install_mock_transport(monkeypatch, handler, captured)
    result = asyncio.run(cs.OneDriveSync().list_backups())

    assert result == {"ok": True, "names": ["1700000000.zip", "1700000100.zip", "1700000200.zip"]}
    assert all(r.headers["authorization"] == "Bearer mstoken" for r in captured)


def test_onedrive_never_sends_its_token_off_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    """A next page named off graph.microsoft.com is refused, not fetched."""
    _onedrive_signed_in(monkeypatch)
    captured: list[httpx.Request] = []
    _install_mock_transport(monkeypatch, lambda req: _json_response(200, {
        "value": [{"name": "1700000000.zip"}],
        "@odata.nextLink": "https://elsewhere.example/children?page=2",
    }), captured)

    result = asyncio.run(cs.OneDriveSync().list_backups())
    assert result["ok"] is False
    assert "off graph.microsoft.com" in result["error"]
    assert len(captured) == 1


def test_onedrive_missing_folder_is_empty_and_a_gone_copy_is_done(monkeypatch: pytest.MonkeyPatch) -> None:
    _onedrive_signed_in(monkeypatch)
    captured: list[httpx.Request] = []
    _install_mock_transport(monkeypatch, lambda req: httpx.Response(404), captured)

    provider = cs.OneDriveSync()
    assert asyncio.run(provider.list_backups()) == {"ok": True, "names": []}
    assert asyncio.run(provider.delete_backup("1700000000.zip")) == {"ok": True}
    assert captured[-1].method == "DELETE"
    assert captured[-1].url.path.endswith("/root:/kazma-backups/1700000000.zip")


def test_onedrive_delete_refused_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _onedrive_signed_in(monkeypatch)
    _install_mock_transport(monkeypatch, lambda req: httpx.Response(403), [])
    result = asyncio.run(cs.OneDriveSync().delete_backup("1700000000.zip"))
    assert result == {"ok": False, "error": "Graph API error: 403"}


def _webdav_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cs, "_read_config", _FakeConfig({
        "backups.offsite.provider": "webdav",
        "backups.offsite.webdav.url": "https://nas.example/dav",
        "backups.offsite.webdav.username": "user",
    }))
    monkeypatch.setattr(cs, "_read_vault", _FakeVault({"backups.offsite.webdav.password": "pw"}))


_MULTISTATUS = b"""<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:">
  <d:response><d:href>/dav/kazma-backups/</d:href></d:response>
  <d:response><d:href>/dav/kazma-backups/1700000000.zip</d:href></d:response>
  <d:response><d:href>https://nas.example/dav/kazma-backups/1700000100.zip</d:href></d:response>
  <d:response><d:href>/dav/kazma-backups/a%20b.zip</d:href></d:response>
</d:multistatus>"""


def test_webdav_lists_the_folder_without_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    _webdav_config(monkeypatch)
    captured: list[httpx.Request] = []
    _install_mock_transport(monkeypatch, lambda req: httpx.Response(207, content=_MULTISTATUS), captured)

    result = asyncio.run(cs.WebDAVSync().list_backups())
    assert result == {"ok": True, "names": ["1700000000.zip", "1700000100.zip", "a b.zip"]}
    assert captured[0].method == "PROPFIND"
    assert captured[0].headers["depth"] == "1"
    assert captured[0].url.path == "/dav/kazma-backups/"


def test_webdav_listing_it_cannot_read_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _webdav_config(monkeypatch)
    _install_mock_transport(monkeypatch, lambda req: httpx.Response(207, content=b"<not xml"), [])

    result = asyncio.run(cs.WebDAVSync().list_backups())
    assert result == {"ok": False, "error": "WebDAV answered a listing that could not be read"}


def test_webdav_missing_folder_is_empty_and_a_gone_copy_is_done(monkeypatch: pytest.MonkeyPatch) -> None:
    _webdav_config(monkeypatch)
    captured: list[httpx.Request] = []
    _install_mock_transport(monkeypatch, lambda req: httpx.Response(404), captured)

    provider = cs.WebDAVSync()
    assert asyncio.run(provider.list_backups()) == {"ok": True, "names": []}
    assert asyncio.run(provider.delete_backup("1700000000.zip")) == {"ok": True}
    assert captured[-1].method == "DELETE"
    assert captured[-1].url.path == "/dav/kazma-backups/1700000000.zip"


def test_webdav_delete_refused_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _webdav_config(monkeypatch)
    _install_mock_transport(monkeypatch, lambda req: httpx.Response(423), [])
    result = asyncio.run(cs.WebDAVSync().delete_backup("1700000000.zip"))
    assert result == {"ok": False, "error": "WebDAV error: HTTP 423"}


def _ftp_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cs, "_read_config", _FakeConfig({
        "backups.offsite.provider": "ftp",
        "backups.offsite.ftp.host": "192.168.50.45",
        "backups.offsite.ftp.username": "user",
    }))
    monkeypatch.setattr(cs, "_read_vault", _FakeVault({"backups.offsite.ftp.password": "pw"}))


class _ListingFTP(_FakeFTP):
    """The fake FTP with a backup folder that can be listed and deleted from."""

    files: list[str] = []
    refuse_delete = False  # a 550 on delete while the file stays
    empty_answer = ""  # an nlst error, e.g. "550 No files found"

    def __init__(self) -> None:
        super().__init__()
        self._dirs.add("kazma-backups")

    def nlst(self) -> list[str]:
        if self.empty_answer:
            raise ftplib.error_perm(self.empty_answer)
        return list(_ListingFTP.files)

    def delete(self, name: str) -> None:
        self.calls.append(("delete", name))
        if self.refuse_delete or name not in _ListingFTP.files:
            raise ftplib.error_perm("550 Permission denied")
        _ListingFTP.files.remove(name)


@pytest.fixture
def listing_ftp(monkeypatch: pytest.MonkeyPatch, fake_ftplib: Any) -> type[_ListingFTP]:
    _ftp_config(monkeypatch)
    monkeypatch.setattr(_ListingFTP, "files", ["./1700000000.zip", "1700000100.zip"])
    monkeypatch.setattr(fake_ftplib, "FTP", _ListingFTP)
    return _ListingFTP


def test_ftp_lists_the_backup_folder_by_name(listing_ftp: type[_ListingFTP]) -> None:
    result = asyncio.run(cs.FTPSync().list_backups())
    assert result == {"ok": True, "names": ["1700000000.zip", "1700000100.zip"]}
    assert ("cwd", "kazma-backups") in _FakeFTP.instances[0].calls


def test_ftp_empty_folder_answer_is_empty(listing_ftp: type[_ListingFTP], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(listing_ftp, "empty_answer", "550 No files found")
    assert asyncio.run(cs.FTPSync().list_backups()) == {"ok": True, "names": []}


def test_ftp_listing_error_that_is_not_550_is_an_error(
    listing_ftp: type[_ListingFTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(listing_ftp, "empty_answer", "530 Not logged in")
    result = asyncio.run(cs.FTPSync().list_backups())
    assert result["ok"] is False
    assert "530" in result["error"]


def test_ftp_delete_of_a_gone_copy_is_done(listing_ftp: type[_ListingFTP]) -> None:
    assert asyncio.run(cs.FTPSync().delete_backup("1700000100.zip")) == {"ok": True}
    assert listing_ftp.files == ["./1700000000.zip"]
    # 550, and the name is not listed: gone already.
    assert asyncio.run(cs.FTPSync().delete_backup("1700000100.zip")) == {"ok": True}


def test_ftp_delete_refused_while_still_listed_is_an_error(
    listing_ftp: type[_ListingFTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 550 can mean "no such file" or "not allowed": only the listing tells."""
    monkeypatch.setattr(listing_ftp, "refuse_delete", True)
    result = asyncio.run(cs.FTPSync().delete_backup("1700000000.zip"))
    assert result["ok"] is False
    assert "550" in result["error"]
