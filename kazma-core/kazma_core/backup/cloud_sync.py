"""Native cloud backup providers — OAuth-integrated, no rclone.

Replaces the rclone subprocess with Python-native uploads using the same
httpx REST pattern Kazma uses for Gmail and Microsoft Graph. Reuses existing
OAuth tokens from the vault where possible.

Providers:
    google_drive — reuses Gmail OAuth tokens (adds drive.file scope)
    onedrive     — reuses Microsoft OAuth tokens (adds Files.ReadWrite scope)
    webdav       — WD MyCloud OS5 / any WebDAV server (username+password)
    ftp          — WD MyCloud OS3 (EX4100) / any FTP server (username+password)
    s3           — Amazon S3 / Backblaze B2 / any S3-compatible endpoint

Contract (matches _offsite_sync in backup/universal.py):
    upload_file(local, remote_name) -> {"ok": bool, "remote": str} or {"ok": False, "error": str}
        Single-file upload (the universal backup sends ONE .zip per run).
        The folder-tree upload is gone (audit 2026-10-26 AUD-077): nothing
        called it, and it reported ``ok`` when one file of a thousand landed.
    test_connection() -> {"ok": bool, "message"|"error": str}
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, unquote

import httpx
from kazma_core.http_tls import shared_ssl_context

logger = logging.getLogger(__name__)

__all__ = [
    "get_sync_provider",
    "GoogleDriveSync",
    "OneDriveSync",
    "WebDAVSync",
    "FTPSync",
    "S3Sync",
]

# ─── Provider selection ─────────────────────────────────────────────────


def get_sync_provider() -> "CloudSyncProvider | None":
    """Read config and return the active provider, or None if not configured."""
    provider_name = _read_config("backups.offsite.provider", "")
    if not provider_name:
        return None
    providers: dict[str, type] = {
        "google_drive": GoogleDriveSync,
        "onedrive": OneDriveSync,
        "webdav": WebDAVSync,
        "ftp": FTPSync,
        "s3": S3Sync,
    }
    cls = providers.get(provider_name)
    if cls is None:
        logger.warning("[cloud_sync] unknown provider %r", provider_name)
        return None
    return cls()


def _read_config(key: str, default: str = "") -> str:
    try:
        from kazma_core.config_store import get_config_store
        return str(get_config_store().get(key) or default)
    except Exception:
        return default


def _read_vault(key: str) -> str:
    try:
        from kazma_core.security.vault import retrieve_with_tenant_ladder

        return retrieve_with_tenant_ladder(key)
    except Exception:
        return ""


def _webdav_tls_verify() -> bool:
    """TLS verification for WebDAV uploads (default ON).

    The universal backup zip carries the plaintext ``.env`` (vault recovery
    key), so a non-loopback WebDAV with certificate verification disabled is
    a full secret-exfiltration surface. Verify defaults to on; set
    ``backups.offsite.webdav.tls_verify=false`` only for self-signed lab
    servers (deep-audit 2026-08-19).
    """
    val = str(_read_config("backups.offsite.webdav.tls_verify", "true")).strip().lower()
    return val not in ("0", "false", "no", "off")


def _write_vault(key: str, value: str, category: str = "backups") -> None:
    try:
        from kazma_core.security.vault import get_vault
        vault = get_vault()
        if vault is not None:
            vault.store(key, value, category=category)
    except Exception:
        logger.debug("[cloud_sync] vault write failed for %s", key, exc_info=True)


def _offsite_enabled() -> bool:
    try:
        from kazma_core.config_store import get_config_store

        val = get_config_store().get("backups.offsite.enabled")
        if isinstance(val, bool):
            return val
        if val is not None and str(val).strip() != "":
            return str(val).strip().lower() not in ("0", "false", "no", "off")
    except Exception:
        pass
    return bool(_read_config("backups.offsite.provider", ""))


class CloudSyncProvider(Protocol):
    """Interface each cloud backup provider implements."""

    async def upload_file(self, local: Path, remote_name: str) -> dict[str, Any]:
        """Upload a single file (the backup .zip). Returns {"ok", "remote"} or error."""
        ...

    async def test_connection(self) -> dict[str, Any]:
        """Test connectivity. Returns {"ok": bool, "message" or "error": str}."""
        ...

    async def stat_file(self, remote_name: str) -> dict[str, Any]:
        """Read one uploaded object back: ``{ok, size, error}``.

        Optional. A provider without it cannot have its offsite copy verified,
        and the drill says so rather than assuming success.
        """
        ...

    def status(self) -> dict[str, Any]:
        """Return connection status for the UI."""
        ...


# ─── Google Drive ──────────────────────────────────────────────────────

_GDRIVE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
_GDRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
_GDRIVE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GDRIVE_ROOT_FOLDER = "kazma-backups"

#: Google documents ``uploadType=multipart`` for files of 5 MB or less; a
#: backup archive is larger, so anything above this goes through a resumable
#: session instead of silently failing (AUD-006).
_GDRIVE_MULTIPART_MAX = 5 * 1024 * 1024
#: Resumable chunk size — must be a multiple of 256 KiB (Google's requirement).
_GDRIVE_RESUMABLE_CHUNK = 8 * 1024 * 1024


async def _gdrive_upload(
    client: httpx.AsyncClient,
    token: str,
    *,
    name: str,
    parent_id: str,
    local: Path,
) -> httpx.Response:
    """Upload one file to Drive — multipart under 5 MB, resumable above it.

    Every disk read is offloaded with ``asyncio.to_thread`` so the event loop
    is never blocked on a large archive, and files over Google's 5 MB
    multipart cap go through a chunked resumable session instead of failing.
    """
    import json

    metadata = json.dumps({"name": name, "parents": [parent_id]})
    size = await asyncio.to_thread(lambda: local.stat().st_size)

    if size <= _GDRIVE_MULTIPART_MAX:
        data = await asyncio.to_thread(local.read_bytes)
        return await client.post(
            _GDRIVE_UPLOAD_URL,
            params={"uploadType": "multipart"},
            headers={"Authorization": f"Bearer {token}"},
            files={
                "metadata": (None, metadata, "application/json"),
                "file": (name, data, "application/octet-stream"),
            },
        )

    # Resumable session: initiate, then PUT chunks with a Content-Range header.
    init = await client.post(
        _GDRIVE_UPLOAD_URL,
        params={"uploadType": "resumable"},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "application/octet-stream",
        },
        content=metadata,
    )
    if init.status_code not in (200, 201):
        return init
    session_uri = init.headers.get("Location")
    if not session_uri:
        return init

    def _read_chunk(offset: int) -> bytes:
        with open(local, "rb") as f:
            f.seek(offset)
            return f.read(_GDRIVE_RESUMABLE_CHUNK)

    offset = 0
    last = init
    while offset < size:
        chunk = await asyncio.to_thread(_read_chunk, offset)
        if not chunk:
            break
        end = offset + len(chunk) - 1
        last = await client.put(
            session_uri,
            headers={"Content-Range": f"bytes {offset}-{end}/{size}"},
            content=chunk,
        )
        if last.status_code in (200, 201):
            return last
        # 308 = chunk stored, send the next; anything else is a hard failure.
        if last.status_code != 308:
            return last
        offset = end + 1
    return last

# Google error reasons mapped to actionable guidance. A 403 on Drive is almost
# always one of the first two: the API is off in the Cloud project, or the
# token predates / never received the drive.file scope (Google never adds
# scopes to existing grants).
_GDRIVE_403_GUIDANCE: dict[str, str] = {
    "accessNotConfigured": (
        "the Google Drive API is not enabled for this Google Cloud project — open "
        "console.cloud.google.com → APIs & Services → Library, enable "
        "'Google Drive API', then run the test again"
    ),
    "insufficientPermissions": (
        "the Google token lacks Drive access (scope drive.file) — in the Google "
        "Cloud Console OAuth consent screen add …/auth/drive.file, then reconnect: "
        "Settings → Email → Disconnect → Connect with Google, or the Connect button "
        "on this card. Existing tokens never gain new scopes"
    ),
    "dailyLimitExceeded": "the Drive API quota is exhausted — wait a few minutes and retry",
    "rateLimitExceeded": "the Drive API rate limit was hit — wait a few seconds and retry",
    "userRateLimitExceeded": "the Drive API rate limit was hit — wait a few seconds and retry",
}


def google_error_reason(resp: Any) -> str:
    """Extract the primary Google API error reason/message from a response."""
    try:
        data = resp.json() or {}
        err = data.get("error") or {}
        errs = err.get("errors") or []
        if errs:
            return str(errs[0].get("reason") or "")
        return str(err.get("message") or "")
    except Exception:
        return ""


def _google_drive_error(resp: Any) -> str:
    """Human-readable Drive API failure with actionable guidance where known."""
    base = f"Drive API error: {resp.status_code}"
    reason = google_error_reason(resp)
    guidance = _GDRIVE_403_GUIDANCE.get(reason)
    if guidance:
        return f"{base} — {guidance}"
    if reason:
        return f"{base} — {reason}"
    try:
        snip = (resp.text or "").strip()[:200]
        if snip:
            return f"{base} — {snip}"
    except Exception:
        pass
    return base


class GoogleDriveSync:
    """Google Drive backup via the existing Gmail OAuth tokens.

    Reuses the refresh_token from `email.gmail.refresh_token` in the vault.
    Requires the `drive.file` scope (added to the Gmail OAuth flow).
    """

    async def _get_access_token(self) -> str:
        """Get a valid access token, refreshing if needed."""
        # Try the current access token first
        token = _read_vault("email.gmail.access_token") or os.environ.get(
            "EMAIL_GMAIL_ACCESS_TOKEN", ""
        )
        if token:
            # Check it still works (a cheap metadata call)
            async with httpx.AsyncClient(timeout=10, verify=shared_ssl_context()) as client:
                resp = await client.get(
                    "https://www.googleapis.com/drive/v3/about",
                    params={"fields": "user"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                if resp.status_code == 200:
                    return token

        # Refresh
        refresh_token = _read_vault("email.gmail.refresh_token") or os.environ.get(
            "EMAIL_GMAIL_REFRESH_TOKEN", ""
        )
        if not refresh_token:
            raise RuntimeError("Google Drive not connected — no Gmail refresh token in vault")
        client_id = _read_vault("email.gmail.client_id") or os.environ.get(
            "EMAIL_GMAIL_CLIENT_ID", ""
        )
        client_secret = _read_vault("email.gmail.client_secret") or os.environ.get(
            "EMAIL_GMAIL_CLIENT_SECRET", ""
        )
        if not client_id or not client_secret:
            raise RuntimeError("Google Drive not configured — missing Gmail OAuth client")

        async with httpx.AsyncClient(timeout=15, verify=shared_ssl_context()) as client:
            resp = await client.post(
                _GDRIVE_TOKEN_URL,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Token refresh failed: {resp.text[:200]}")
            data = resp.json()
            new_token = data["access_token"]
            # Persist for next time
            _write_vault("email.gmail.access_token", new_token, category="email")
            os.environ["EMAIL_GMAIL_ACCESS_TOKEN"] = new_token
            return new_token

    async def _ensure_folder(self, token: str, name: str, parent: str | None = None) -> str:
        """Find or create a folder, returns its ID."""
        query = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        if parent:
            query += f" and '{parent}' in parents"
        else:
            query += " and 'root' in parents"
        async with httpx.AsyncClient(timeout=10, verify=shared_ssl_context()) as client:
            resp = await client.get(
                _GDRIVE_FILES_URL,
                params={"q": query, "fields": "files(id,name)"},
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 200:
                files = resp.json().get("files", [])
                if files:
                    return files[0]["id"]

            # Create the folder
            metadata: dict[str, Any] = {
                "name": name,
                "mimeType": "application/vnd.google-apps.folder",
            }
            if parent:
                metadata["parents"] = [parent]
            resp = await client.post(
                _GDRIVE_FILES_URL,
                json=metadata,
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code not in (200, 201):
                raise RuntimeError(f"Folder creation failed: {resp.text[:200]}")
            return resp.json()["id"]

    async def upload_file(self, local: Path, remote_name: str) -> dict[str, Any]:
        token = await self._get_access_token()
        root_id = await self._ensure_folder(token, _GDRIVE_ROOT_FOLDER)
        async with httpx.AsyncClient(timeout=300, verify=shared_ssl_context()) as client:
            resp = await _gdrive_upload(
                client, token, name=remote_name, parent_id=root_id, local=local
            )
        display = f"google_drive:{_GDRIVE_ROOT_FOLDER}/{remote_name}"
        if resp.status_code in (200, 201):
            return {"ok": True, "remote": display, "files": 1}
        return {"ok": False, "remote": display, "error": _google_drive_error(resp)}

    async def test_connection(self) -> dict[str, Any]:
        try:
            token = await self._get_access_token()
            async with httpx.AsyncClient(timeout=10, verify=shared_ssl_context()) as client:
                resp = await client.get(
                    "https://www.googleapis.com/drive/v3/about",
                    params={"fields": "user(displayName,emailAddress)"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                if resp.status_code == 200:
                    user = resp.json().get("user", {})
                    email = user.get("emailAddress", "connected")
                    return {"ok": True, "message": f"Google Drive: {email}"}
                return {"ok": False, "error": _google_drive_error(resp)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def status(self) -> dict[str, Any]:
        has_token = bool(
            _read_vault("email.gmail.refresh_token")
            or os.environ.get("EMAIL_GMAIL_REFRESH_TOKEN")
        )
        # Recorded by the Gmail OAuth flow (oauth_gmail.py) at connect time:
        # "ok", a Google error reason, or "" for tokens connected before the
        # Drive probe existed (unknown — treat as healthy).
        drive_state = _read_vault("email.gmail.drive_ok")
        if drive_state == "ok":
            drive_ok, drive_error = True, ""
        elif drive_state:
            drive_ok, drive_error = False, drive_state
        else:
            drive_ok, drive_error = None, ""
        return {
            "provider": "google_drive",
            "connected": has_token,
            "drive_ok": drive_ok,
            "drive_error": drive_error,
            "remote": f"google_drive:{_GDRIVE_ROOT_FOLDER}",
        }


# ─── OneDrive (Microsoft Graph) ────────────────────────────────────────

_MS_GRAPH_DRIVE = "https://graph.microsoft.com/v1.0/me/drive/root"
_MS_TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
_MS_ROOT_FOLDER = "kazma-backups"

#: A file this size or smaller goes up in one ``PUT …:/content``; anything
#: larger through an upload session (audit 2026-10-26 AUD-113). Graph refuses
#: a simple upload past 250 MB, and a universal backup zip is about 1.3 GB:
#: every OneDrive offsite copy failed. 4 MiB is Graph's long-documented
#: simple-upload size, kept so a small file needs no session.
_MS_SIMPLE_UPLOAD_MAX = 4 * 1024 * 1024
#: Upload-session fragment: Graph requires a multiple of 320 KiB, at most
#: 60 MiB. 10 MiB is 32 x 320 KiB.
_MS_SESSION_CHUNK = 32 * 320 * 1024


def _read_chunk(local: Path, offset: int, length: int) -> bytes:
    """Read *length* bytes of *local* from *offset* (run in a thread)."""
    with open(local, "rb") as f:
        f.seek(offset)
        return f.read(length)


#: Size of each piece of a streamed upload body (WebDAV).
_STREAM_CHUNK = 1024 * 1024


async def _file_chunks(local: Path, size: int) -> Any:
    """Yield *local* in ``_STREAM_CHUNK`` pieces, each read in a thread.

    Stops at *size*: the declared Content-Length is what is sent, and a file
    that shrank under the upload raises rather than sending a short body.
    """
    offset = 0
    while offset < size:
        chunk = await asyncio.to_thread(
            _read_chunk, local, offset, min(_STREAM_CHUNK, size - offset)
        )
        if not chunk:
            raise OSError(f"{local.name} shrank during the upload ({offset} of {size} bytes)")
        offset += len(chunk)
        yield chunk


async def _onedrive_session_upload(
    client: httpx.AsyncClient, token: str, *, path: str, local: Path, size: int,
) -> httpx.Response:
    """Upload *local* through a Graph upload session, one fragment at a time.

    Only one fragment (``_MS_SESSION_CHUNK``) is in memory at once, and each
    read is off the loop. The session URL is pre-authenticated: Graph refuses
    an ``Authorization`` header on it. A fragment refused, or a file that
    changed size under the upload, cancels the session (DELETE) so Graph does
    not keep a half file; the failing response is returned.
    """
    init = await client.post(
        f"{_MS_GRAPH_DRIVE}:/{path}:/createUploadSession",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"item": {"@microsoft.graph.conflictBehavior": "replace"}},
    )
    if init.status_code != 200:
        return init
    try:
        upload_url = str(init.json().get("uploadUrl") or "")
    except ValueError:
        upload_url = ""
    if not upload_url:
        logger.warning("[cloud_sync] OneDrive upload session answered without an uploadUrl")
        return httpx.Response(502, request=init.request)

    offset = 0
    last = init
    while offset < size:
        chunk = await asyncio.to_thread(_read_chunk, local, offset, _MS_SESSION_CHUNK)
        if not chunk:
            # The file shrank under the upload: what was sent is not the file.
            last = httpx.Response(409, request=init.request)
            break
        end = offset + len(chunk) - 1
        last = await client.put(
            upload_url,
            content=chunk,
            headers={"Content-Range": f"bytes {offset}-{end}/{size}"},
        )
        if last.status_code in (200, 201):
            return last
        # 202 = fragment stored, send the next; anything else is a failure.
        if last.status_code != 202:
            break
        offset = end + 1
    try:
        await client.delete(upload_url)
    except httpx.HTTPError as exc:
        logger.warning("[cloud_sync] could not cancel the OneDrive upload session: %s", exc)
    if last.status_code in (200, 201, 202):
        # Every byte was sent and Graph never said the file was complete.
        return httpx.Response(502, request=last.request)
    return last


class OneDriveSync:
    """OneDrive backup via the existing Microsoft OAuth tokens.

    Reuses the refresh_token from `email.microsoft.refresh_token` in the vault.
    Requires the `Files.ReadWrite` scope (added to the Microsoft OAuth flow).
    """

    async def _get_access_token(self) -> str:
        token = _read_vault("email.microsoft.access_token") or os.environ.get(
            "EMAIL_MS_ACCESS_TOKEN", ""
        )
        if token:
            async with httpx.AsyncClient(timeout=10, verify=shared_ssl_context()) as client:
                resp = await client.get(
                    "https://graph.microsoft.com/v1.0/me",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if resp.status_code == 200:
                    return token

        refresh_token = _read_vault("email.microsoft.refresh_token") or os.environ.get(
            "EMAIL_MS_REFRESH_TOKEN", ""
        )
        if not refresh_token:
            raise RuntimeError("OneDrive not connected — no Microsoft refresh token in vault")
        client_id = _read_vault("email.microsoft.client_id") or os.environ.get(
            "EMAIL_MS_CLIENT_ID", ""
        )
        if not client_id:
            raise RuntimeError("OneDrive not configured — missing Microsoft OAuth client")
        # Confidential Azure apps (a client_secret was registered) REQUIRE the
        # secret on the refresh grant — without it AAD rejects with
        # AADSTS70002 "must include a 'client_secret' input parameter"
        # (incident 2026-08-16). Public-client apps have no secret → omit it.
        client_secret = _read_vault("email.microsoft.client_secret") or os.environ.get(
            "EMAIL_MS_CLIENT_SECRET", ""
        )
        tenant = os.environ.get("EMAIL_MS_TENANT_ID") or "common"

        payload = {
            "client_id": client_id,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
            "scope": "https://graph.microsoft.com/.default offline_access",
        }
        if client_secret:
            payload["client_secret"] = client_secret

        async with httpx.AsyncClient(timeout=15, verify=shared_ssl_context()) as client:
            resp = await client.post(
                _MS_TOKEN_URL_TEMPLATE.format(tenant=tenant),
                data=payload,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Token refresh failed: {resp.text[:200]}")
            data = resp.json()
            new_token = data["access_token"]
            new_refresh = data.get("refresh_token", refresh_token)
            _write_vault("email.microsoft.access_token", new_token, category="email")
            _write_vault("email.microsoft.refresh_token", new_refresh, category="email")
            os.environ["EMAIL_MS_ACCESS_TOKEN"] = new_token
            if new_refresh != refresh_token:
                os.environ["EMAIL_MS_REFRESH_TOKEN"] = new_refresh
            return new_token

    async def upload_file(self, local: Path, remote_name: str) -> dict[str, Any]:
        token = await self._get_access_token()
        path = quote(f"{_MS_ROOT_FOLDER}/{remote_name}")
        display = f"onedrive:{_MS_ROOT_FOLDER}/{remote_name}"
        size = await asyncio.to_thread(lambda: local.stat().st_size)
        async with httpx.AsyncClient(timeout=300, verify=shared_ssl_context()) as client:
            if size <= _MS_SIMPLE_UPLOAD_MAX:
                data = await asyncio.to_thread(local.read_bytes)
                resp = await client.put(
                    f"{_MS_GRAPH_DRIVE}:/{path}:/content",
                    content=data,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/octet-stream",
                    },
                )
            else:
                resp = await _onedrive_session_upload(client, token, path=path, local=local, size=size)
        if resp.status_code in (200, 201):
            return {"ok": True, "remote": display, "files": 1, "size": size}
        return {"ok": False, "remote": display, "error": f"Graph API error: {resp.status_code}"}

    async def test_connection(self) -> dict[str, Any]:
        try:
            token = await self._get_access_token()
            async with httpx.AsyncClient(timeout=10, verify=shared_ssl_context()) as client:
                resp = await client.get(
                    "https://graph.microsoft.com/v1.0/me/drive",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if resp.status_code == 200:
                    owner = resp.json().get("owner", {}).get("user", {}).get(
                        "displayName", "connected"
                    )
                    return {"ok": True, "message": f"OneDrive: {owner}"}
                return {"ok": False, "error": f"Graph API error: {resp.status_code}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def status(self) -> dict[str, Any]:
        has_token = bool(
            _read_vault("email.microsoft.refresh_token")
            or os.environ.get("EMAIL_MS_REFRESH_TOKEN")
        )
        return {
            "provider": "onedrive",
            "connected": has_token,
            "remote": f"onedrive:{_MS_ROOT_FOLDER}",
        }


# ─── WebDAV (WD MyCloud OS5 / any WebDAV server) ───────────────────────


class WebDAVSync:
    """WebDAV backup for WD MyCloud OS5 and any WebDAV server.

    Config: backups.offsite.webdav.url, backups.offsite.webdav.username,
    backups.offsite.webdav.password (password in vault).
    """

    _ROOT_FOLDER = "kazma-backups"

    def _get_config(self) -> tuple[str, str, str]:
        url = _read_config("backups.offsite.webdav.url", "").rstrip("/")
        username = _read_config("backups.offsite.webdav.username", "")
        password = _read_vault("backups.offsite.webdav.password")
        return url, username, password

    async def upload_file(self, local: Path, remote_name: str) -> dict[str, Any]:
        url, username, password = self._get_config()
        if not url:
            return {"ok": False, "remote": "webdav", "error": "WebDAV URL not configured"}
        auth = (username, password) if username else None
        host = url.split("//")[1] if "//" in url else url
        display = f"webdav:{host}/{self._ROOT_FOLDER}/{remote_name}"
        size = await asyncio.to_thread(lambda: local.stat().st_size)
        async with httpx.AsyncClient(timeout=300, verify=_webdav_tls_verify()) as client:
            # MKCOL the root folder — idempotent (405/409 = already exists)
            await client.request("MKCOL", f"{url}/{self._ROOT_FOLDER}", auth=auth)
            # Streamed, a chunk at a time read off the loop (audit 2026-10-26
            # AUD-113): the whole archive (about 1.3 GB) was read into the
            # server's memory first. The length is declared, so the server
            # gets a plain body, not a chunked one.
            resp = await client.put(
                f"{url}/{self._ROOT_FOLDER}/{remote_name}",
                content=_file_chunks(local, size),
                headers={"Content-Length": str(size)},
                auth=auth,
            )
        if resp.status_code in (200, 201, 204):
            return {"ok": True, "remote": display, "files": 1, "size": size}
        return {"ok": False, "remote": display, "error": f"WebDAV error: HTTP {resp.status_code}"}

    async def test_connection(self) -> dict[str, Any]:
        url, username, password = self._get_config()
        if not url:
            return {"ok": False, "error": "WebDAV URL not configured"}
        auth = (username, password) if username else None
        try:
            async with httpx.AsyncClient(timeout=10, verify=_webdav_tls_verify()) as client:
                resp = await client.request(
                    "PROPFIND", url, auth=auth, headers={"Depth": "0"}
                )
                if resp.status_code in (200, 207):
                    return {"ok": True, "message": f"WebDAV: {url}"}
                return {"ok": False, "error": f"WebDAV error: HTTP {resp.status_code}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def status(self) -> dict[str, Any]:
        url, username, _ = self._get_config()
        return {
            "provider": "webdav",
            "connected": bool(url and username),
            "remote": f"webdav:{url}" if url else "",
        }


# ─── FTP (WD MyCloud OS3 / any FTP server) ──────────────────────────────


class FTPSync:
    """FTP backup via stdlib ftplib — no SDK, no extra dependencies.

    For WD MyCloud OS3 devices (EX4100 & co.) and any plain FTP server.
    FTP is blocking socket I/O, so every call runs in ``asyncio.to_thread``
    (the Windows SelectorEventLoop constraint applies here too).

    Config: backups.offsite.ftp.host, .port (default 21), .username,
    .password (vault), .path (optional base dir on the server, relative to
    the FTP login directory).
    """

    _ROOT_FOLDER = "kazma-backups"

    def _get_config(self) -> dict[str, Any]:
        try:
            port = int(_read_config("backups.offsite.ftp.port", "21") or 21)
        except (TypeError, ValueError):
            port = 21
        path = _read_config("backups.offsite.ftp.path", "").strip("/")
        return {
            "host": _read_config("backups.offsite.ftp.host", "").strip(),
            "port": port,
            "username": _read_config("backups.offsite.ftp.username", "").strip(),
            "password": _read_vault("backups.offsite.ftp.password"),
            "path": path,
        }

    def _connect(self, cfg: dict[str, Any]) -> Any:
        """Connect, preferring FTPS (explicit TLS) over plaintext FTP.

        This used to be an unconditional ``ftplib.FTP()``. An offsite backup
        run over plaintext FTP puts the operator's **password** and the whole
        archive on the wire in the clear, on the one channel that carries a
        complete copy of the install (audit 2026-09-16 F-7). We now try
        ``FTP_TLS`` first and fall back only when the server refuses AUTH TLS,
        and that fallback is a loud warning, not a silent downgrade.

        Set ``backups.offsite.ftp.require_tls`` to refuse the fallback.
        """
        import ftplib  # nosec B402 - FTPS preferred in _connect; plaintext is a warned fallback

        host, port = cfg["host"], cfg["port"]
        user = cfg["username"] or "anonymous"
        password = cfg["password"] or "kazma@"

        try:
            ftps = ftplib.FTP_TLS()
            ftps.connect(host, port, timeout=15)
            ftps.login(user, password)
            ftps.prot_p()  # encrypt the DATA channel too, not just the control one
            ftps.set_pasv(True)
            logger.info("[backup.ftp] Connected to %s over FTPS (TLS)", host)
            return ftps
        except Exception as exc:
            require_tls = str(
                _read_config("backups.offsite.ftp.require_tls", "") or ""
            ).strip().lower() in ("1", "true", "yes", "on")
            if require_tls:
                raise RuntimeError(
                    f"FTPS required but unavailable for {host}: {exc}. "
                    "Unset backups.offsite.ftp.require_tls to allow plaintext."
                ) from exc
            logger.warning(
                "[backup.ftp] FTPS unavailable for %s (%s) — falling back to "
                "PLAINTEXT FTP. Your password and the entire backup archive "
                "cross the network unencrypted. Prefer an SFTP/S3/rclone "
                "target, or set backups.offsite.ftp.require_tls to refuse "
                "this fallback.",
                host,
                exc,
            )

        ftp = ftplib.FTP()  # nosec B321 - explicit, warned-about fallback above
        ftp.connect(host, port, timeout=15)
        ftp.login(user, password)
        ftp.set_pasv(True)
        return ftp

    @staticmethod
    def _ensure_dir(ftp: Any, path: str) -> None:
        """cwd into ``path`` (relative to the login dir), creating missing dirs."""
        import ftplib  # nosec B402 - FTPS preferred in _connect; plaintext is a warned fallback

        parts = [p for p in path.split("/") if p]
        if not parts:
            return
        try:
            ftp.cwd(path)
            return
        except ftplib.error_perm:
            pass
        for part in parts:
            try:
                ftp.cwd(part)
            except ftplib.error_perm:
                ftp.mkd(part)
                ftp.cwd(part)

    async def upload_file(self, local: Path, remote_name: str) -> dict[str, Any]:
        cfg = self._get_config()
        if not cfg["host"]:
            return {"ok": False, "remote": "ftp", "error": "FTP host not configured"}
        display = f"ftp:{cfg['host']}/{self._ROOT_FOLDER}/{remote_name}"
        root = f"{cfg['path']}/{self._ROOT_FOLDER}" if cfg["path"] else self._ROOT_FOLDER

        def _run() -> None:
            ftp = self._connect(cfg)
            try:
                self._ensure_dir(ftp, root)
                with open(local, "rb") as f:
                    ftp.storbinary(f"STOR {remote_name}", f)
            finally:
                try:
                    ftp.quit()
                except Exception:
                    pass

        try:
            await asyncio.to_thread(_run)
            return {"ok": True, "remote": display, "files": 1}
        except Exception as exc:
            return {"ok": False, "remote": display, "error": f"FTP error: {exc}"}

    async def test_connection(self) -> dict[str, Any]:
        cfg = self._get_config()
        if not cfg["host"]:
            return {"ok": False, "error": "FTP host not configured"}

        def _run() -> str:
            ftp = self._connect(cfg)
            try:
                return cfg["host"]
            finally:
                try:
                    ftp.quit()
                except Exception:
                    pass

        try:
            host = await asyncio.to_thread(_run)
            return {"ok": True, "message": f"FTP: {host}"}
        except Exception as exc:
            return {"ok": False, "error": f"FTP error: {exc}"}

    def status(self) -> dict[str, Any]:
        cfg = self._get_config()
        connected = bool(cfg["host"] and cfg["username"])
        return {
            "provider": "ftp",
            "connected": connected,
            "remote": f"ftp:{cfg['host']}" if connected else "",
        }


# ─── S3 (Amazon S3 / Backblaze B2 / S3-compatible) ─────────────────────


#: A file this size or smaller is one signed PUT; anything larger a multipart
#: upload (audit 2026-10-26 AUD-113). S3 parts are 5 MiB to 5 GiB (the last
#: may be smaller), at most 10,000: 16 MiB parts cover 156 GiB.
_S3_SINGLE_PUT_MAX = 16 * 1024 * 1024
_S3_PART_SIZE = 16 * 1024 * 1024


def _sigv4_query(query: str) -> str:
    """The SigV4 canonical query string of *query* (as sent on the URL)."""
    if not query:
        return ""
    pairs: list[tuple[str, str]] = []
    for item in query.split("&"):
        if not item:
            continue
        name, _, value = item.partition("=")
        pairs.append((
            quote(unquote(name), safe="-_.~"),
            quote(unquote(value), safe="-_.~"),
        ))
    return "&".join(f"{n}={v}" for n, v in sorted(pairs))


def _s3_xml_text(body: bytes, tag: str) -> str:
    """The text of the first *tag* element in an S3 XML answer, else ''.

    S3 answers in its own namespace; the tag is matched by local name. The
    body comes from the network, so it is parsed with the guarded parser.
    """
    from kazma_core.security.safe_xml import UnsafeXMLError, parse_untrusted_xml

    try:
        root = parse_untrusted_xml(body)
    except (UnsafeXMLError, ValueError, SyntaxError) as exc:
        logger.warning("[cloud_sync] S3 answered XML that could not be read: %s", exc)
        return ""
    if root.tag.rsplit("}", 1)[-1] == "Error":
        return ""
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == tag:
            return (element.text or "").strip()
    return ""


class S3Sync:
    """S3-compatible backup via raw httpx with SigV4 signing.

    No boto3 — httpx + cryptography (both already installed) handle the
    SigV4 signing in ~40 lines, consistent with Kazma's no-SDK pattern.

    Config: backups.offsite.s3.access_key, backups.offsite.s3.secret_key (vault),
    backups.offsite.s3.bucket, backups.offsite.s3.endpoint (optional for B2/MinIO),
    backups.offsite.s3.region (default us-east-1).
    """

    _ROOT_FOLDER = "kazma-backups"

    def _get_config(self) -> dict[str, str]:
        return {
            "access_key": _read_config("backups.offsite.s3.access_key", ""),
            "secret_key": _read_vault("backups.offsite.s3.secret_key"),
            "bucket": _read_config("backups.offsite.s3.bucket", ""),
            "endpoint": _read_config("backups.offsite.s3.endpoint", ""),
            "region": _read_config("backups.offsite.s3.region", "us-east-1"),
        }

    def _sign_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        payload: bytes | None,
        config: dict[str, str],
    ) -> dict[str, str]:
        """AWS Signature Version 4 signing."""
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = parsed.hostname or ""
        canonical_uri = quote(parsed.path or "/", safe="/")
        # The multipart calls carry ``?uploads``, ``?partNumber=&uploadId=``
        # and ``?uploadId=`` (audit 2026-10-26 AUD-113): SigV4 signs them as
        # name=value pairs, both URI-encoded, sorted by name (a bare name is
        # ``name=``).
        canonical_querystring = _sigv4_query(parsed.query)

        # Canonical headers (must be sorted)
        signed_headers_map = {"host": host}
        for k, v in headers.items():
            signed_headers_map[k.lower()] = v
        sorted_keys = sorted(signed_headers_map.keys())
        canonical_headers = "".join(f"{k}:{signed_headers_map[k]}\n" for k in sorted_keys)
        signed_headers = ";".join(sorted_keys)

        # Payload hash
        payload_hash = hashlib.sha256(payload or b"").hexdigest()

        canonical_request = (
            f"{method}\n{canonical_uri}\n{canonical_querystring}\n"
            f"{canonical_headers}\n{signed_headers}\n{payload_hash}"
        )

        # Signing key
        now = datetime.now(timezone.utc)
        date_stamp = now.strftime("%Y%m%d")
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        region = config["region"]
        service = "s3"
        secret = config["secret_key"]

        def _hmac(key: bytes, msg: str) -> bytes:
            return hmac.new(key, msg.encode(), hashlib.sha256).digest()

        k_date = _hmac(f"AWS4{secret}".encode(), date_stamp)
        k_region = _hmac(k_date, region)
        k_service = _hmac(k_region, service)
        k_signing = _hmac(k_service, "aws4_request")

        credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
        string_to_sign = (
            f"AWS4-HMAC-SHA256\n{amz_date}\n{credential_scope}\n"
            + hashlib.sha256(canonical_request.encode()).hexdigest()
        )
        signature = hmac.new(
            k_signing, string_to_sign.encode(), hashlib.sha256
        ).hexdigest()

        headers = dict(headers)
        headers["x-amz-date"] = amz_date
        headers["x-amz-content-sha256"] = payload_hash
        headers["Authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={config['access_key']}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        return headers

    def _build_url(self, config: dict[str, str], key: str) -> str:
        if config["endpoint"]:
            return f"{config['endpoint']}/{config['bucket']}/{key}"
        return f"https://{config['bucket']}.s3.{config['region']}.amazonaws.com/{key}"

    async def upload_file(self, local: Path, remote_name: str) -> dict[str, Any]:
        config = self._get_config()
        if not config["access_key"] or not config["bucket"]:
            return {"ok": False, "remote": "s3", "error": "S3 not configured"}
        key = f"{self._ROOT_FOLDER}/{remote_name}"
        url = self._build_url(config, key)
        display = f"s3:{config['bucket']}/{key}"
        size = await asyncio.to_thread(lambda: local.stat().st_size)
        async with httpx.AsyncClient(timeout=300, verify=shared_ssl_context()) as client:
            if size <= _S3_SINGLE_PUT_MAX:
                base_headers = {"content-type": "application/octet-stream"}
                payload = await asyncio.to_thread(local.read_bytes)
                signed = await asyncio.to_thread(
                    self._sign_request, "PUT", url, base_headers, payload, config
                )
                resp = await client.put(url, content=payload, headers=signed)
                error = "" if resp.status_code in (200, 201) else f"S3 error: HTTP {resp.status_code}"
            else:
                # Multipart (audit 2026-10-26 AUD-113): the whole archive was
                # read into the server's memory and signed in one piece.
                error = await self._multipart_upload(client, config, url, local, size)
        if not error:
            # The byte count is recorded so the restore drill can HEAD the
            # object later and compare. "The upload returned 200" and "the
            # object is there, whole" are different claims, and only the
            # second one is a backup.
            return {"ok": True, "remote": display, "files": 1, "size": size}
        return {"ok": False, "remote": display, "error": error}

    async def _multipart_upload(
        self,
        client: httpx.AsyncClient,
        config: dict[str, str],
        url: str,
        local: Path,
        size: int,
    ) -> str:
        """Upload *local* as an S3 multipart upload; '' on success, else the error.

        One part (``_S3_PART_SIZE``) is in memory at a time; each read and
        each SigV4 hash runs in a thread. A failure aborts the upload, so the
        bucket keeps no parts it bills for and no half object appears.
        """
        async def signed(method: str, target: str, payload: bytes | None, headers: dict[str, str]) -> dict[str, str]:
            return await asyncio.to_thread(self._sign_request, method, target, headers, payload, config)

        init_url = f"{url}?uploads"
        init = await client.post(
            init_url,
            headers=await signed("POST", init_url, None, {"content-type": "application/octet-stream"}),
        )
        if init.status_code != 200:
            return f"S3 error: HTTP {init.status_code} starting a multipart upload"
        upload_id = _s3_xml_text(init.content, "UploadId")
        if not upload_id:
            return "S3 error: the multipart upload started without an UploadId"
        qid = quote(upload_id, safe="")

        parts: list[tuple[int, str]] = []
        offset = 0
        number = 0
        error = ""
        while offset < size:
            number += 1
            chunk = await asyncio.to_thread(_read_chunk, local, offset, min(_S3_PART_SIZE, size - offset))
            if not chunk:
                error = f"S3 upload stopped: {local.name} shrank during the upload"
                break
            part_url = f"{url}?partNumber={number}&uploadId={qid}"
            resp = await client.put(part_url, content=chunk, headers=await signed("PUT", part_url, chunk, {}))
            etag = resp.headers.get("etag", "")
            if resp.status_code != 200 or not etag:
                error = f"S3 error: HTTP {resp.status_code} on part {number}"
                break
            parts.append((number, etag))
            offset += len(chunk)

        if not error:
            body = (
                "<CompleteMultipartUpload>"
                + "".join(
                    f"<Part><PartNumber>{n}</PartNumber><ETag>{html.escape(tag, quote=False)}</ETag></Part>"
                    for n, tag in parts
                )
                + "</CompleteMultipartUpload>"
            ).encode()
            done_url = f"{url}?uploadId={qid}"
            done = await client.post(
                done_url, content=body,
                headers=await signed("POST", done_url, body, {"content-type": "application/xml"}),
            )
            # S3 can answer 200 to a completion that failed, with an <Error>
            # body: only a CompleteMultipartUploadResult is a stored object.
            if done.status_code == 200 and _s3_xml_text(done.content, "ETag"):
                return ""
            error = f"S3 error: HTTP {done.status_code} completing the multipart upload"

        abort_url = f"{url}?uploadId={qid}"
        try:
            abort = await client.delete(abort_url, headers=await signed("DELETE", abort_url, None, {}))
            if abort.status_code not in (200, 204):
                logger.warning("[cloud_sync] S3 abort of upload %s answered HTTP %s", upload_id, abort.status_code)
        except httpx.HTTPError as exc:
            logger.warning("[cloud_sync] S3 abort of upload %s failed: %s", upload_id, exc)
        return error

    async def stat_file(self, remote_name: str) -> dict[str, Any]:
        """HEAD one uploaded object. Returns ``{ok, size, error}``.

        The offsite half of a backup was never read back: a manifest saying
        ``offsite.ok: true`` records that a PUT returned 200, which a remote
        that accepts writes and stores nothing also does.
        """
        config = self._get_config()
        if not config["access_key"] or not config["bucket"]:
            return {"ok": False, "error": "S3 not configured"}
        key = f"{self._ROOT_FOLDER}/{remote_name}"
        url = self._build_url(config, key)
        try:
            signed = self._sign_request("HEAD", url, {}, None, config)
            async with httpx.AsyncClient(timeout=30, verify=shared_ssl_context()) as client:
                resp = await client.head(url, headers=signed)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)[:200]}
        if resp.status_code == 404:
            return {"ok": False, "error": "the object is not there"}
        if resp.status_code != 200:
            return {"ok": False, "error": f"HTTP {resp.status_code}"}
        try:
            size = int(resp.headers.get("content-length") or 0)
        except ValueError:
            size = 0
        return {"ok": True, "size": size}

    async def test_connection(self) -> dict[str, Any]:
        config = self._get_config()
        if not config["access_key"] or not config["bucket"]:
            return {"ok": False, "error": "S3 access key and bucket are required"}
        url = self._build_url(config, "")
        try:
            signed = self._sign_request("HEAD", url, {}, None, config)
            async with httpx.AsyncClient(timeout=10, verify=shared_ssl_context()) as client:
                resp = await client.head(url, headers=signed)
                if resp.status_code in (200, 403):
                    # 403 means the bucket exists but access is restricted — still connected
                    bucket = config["bucket"]
                    return {"ok": True, "message": f"S3 bucket: {bucket}"}
                return {"ok": False, "error": f"S3 error: HTTP {resp.status_code}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def status(self) -> dict[str, Any]:
        config = self._get_config()
        connected = bool(config["access_key"] and config["bucket"])
        return {
            "provider": "s3",
            "connected": connected,
            "remote": f"s3:{config['bucket']}" if connected else "",
        }
