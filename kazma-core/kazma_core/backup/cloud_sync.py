"""Native cloud backup providers — OAuth-integrated, no rclone.

Replaces the rclone subprocess with Python-native uploads using the same
httpx REST pattern Kazma uses for Gmail and Microsoft Graph. Reuses existing
OAuth tokens from the vault where possible.

Providers:
    google_drive — reuses Gmail OAuth tokens (adds drive.file scope)
    onedrive     — reuses Microsoft OAuth tokens (adds Files.ReadWrite scope)
    webdav       — WD MyCloud OS5 / any WebDAV server (username+password)
    s3           — Amazon S3 / Backblaze B2 / any S3-compatible endpoint

Contract (matches the old _offsite_sync):
    upload_directory(dest, remote_path) -> {"ok": bool, "remote": str} or {"ok": False, "error": str}
    test_connection() -> {"ok": bool, "message"|"error": str}
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

__all__ = [
    "get_sync_provider",
    "GoogleDriveSync",
    "OneDriveSync",
    "WebDAVSync",
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
        from kazma_core.security.vault import get_vault
        vault = get_vault()
        if vault is None:
            return ""
        return str(vault.retrieve(key) or "")
    except Exception:
        return ""


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

    async def upload_directory(self, dest: Path, remote_path: str) -> dict[str, Any]:
        """Upload a directory tree. Returns {"ok": bool, "remote": str} or error."""
        ...

    async def test_connection(self) -> dict[str, Any]:
        """Test connectivity. Returns {"ok": bool, "message" or "error": str}."""
        ...

    def status(self) -> dict[str, Any]:
        """Return connection status for the UI."""
        ...


async def _upload_all_files(
    dest: Path,
    upload_one: Any,  # Callable[[Path, str], Awaitable[bool]]
    remote_path: str,
    remote_display: str,
) -> dict[str, Any]:
    """Walk dest/ and upload each file via the provided callable.

    The callable receives (local_path, relative_remote_path) and returns True
    on success. Used by all providers to share the tree-walking logic.
    """
    files = [f for f in dest.rglob("*") if f.is_file()]
    if not files:
        return {"ok": False, "remote": remote_display, "error": "no files to upload"}
    uploaded = 0
    failed: list[str] = []
    for f in files:
        rel = f.relative_to(dest).as_posix()
        try:
            if await upload_one(f, rel):
                uploaded += 1
            else:
                failed.append(rel)
        except Exception as exc:
            logger.warning("[cloud_sync] upload %s failed: %s", rel, exc)
            failed.append(rel)
    if failed:
        return {
            "ok": uploaded > 0,
            "remote": remote_display,
            "error": f"{len(failed)}/{len(files)} files failed: {failed[:3]}",
        }
    return {"ok": True, "remote": remote_display, "files": uploaded}


# ─── Google Drive ──────────────────────────────────────────────────────

_GDRIVE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
_GDRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
_GDRIVE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GDRIVE_ROOT_FOLDER = "kazma-backups"

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
            async with httpx.AsyncClient(timeout=10) as client:
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

        async with httpx.AsyncClient(timeout=15) as client:
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
        async with httpx.AsyncClient(timeout=10) as client:
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

    async def upload_directory(self, dest: Path, remote_path: str) -> dict[str, Any]:
        token = await self._get_access_token()

        # Ensure folder chain: kazma-backups/<remote_path>/
        root_id = await self._ensure_folder(token, _GDRIVE_ROOT_FOLDER)
        backup_id = await self._ensure_folder(token, remote_path, parent=root_id)

        async def upload_one(local: Path, rel: str) -> bool:
            # Create parent folders if the file is nested
            parts = rel.split("/")
            parent_id = backup_id
            for folder in parts[:-1]:
                parent_id = await self._ensure_folder(token, folder, parent=parent_id)

            async with httpx.AsyncClient(timeout=300) as client:
                with open(local, "rb") as f:
                    resp = await client.post(
                        _GDRIVE_UPLOAD_URL,
                        params={"uploadType": "multipart"},
                        headers={"Authorization": f"Bearer {token}"},
                        files={
                            "metadata": (
                                None,
                                f'{{"name": "{parts[-1]}", "parents": ["{parent_id}"]}}',
                                "application/json",
                            ),
                            "file": (parts[-1], f, "application/octet-stream"),
                        },
                    )
                    return resp.status_code in (200, 201)

        display = f"google_drive:{_GDRIVE_ROOT_FOLDER}/{remote_path}"
        return await _upload_all_files(dest, upload_one, remote_path, display)

    async def test_connection(self) -> dict[str, Any]:
        try:
            token = await self._get_access_token()
            async with httpx.AsyncClient(timeout=10) as client:
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
            async with httpx.AsyncClient(timeout=10) as client:
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

        async with httpx.AsyncClient(timeout=15) as client:
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

    async def upload_directory(self, dest: Path, remote_path: str) -> dict[str, Any]:
        token = await self._get_access_token()

        async def upload_one(local: Path, rel: str) -> bool:
            # PUT to /me/drive/root:/kazma-backups/<timestamp>/<rel>:/content
            path = quote(f"{_MS_ROOT_FOLDER}/{remote_path}/{rel}")
            url = f"{_MS_GRAPH_DRIVE}:/{path}:/content"
            async with httpx.AsyncClient(timeout=300) as client:
                # bytes read up-front: httpx 0.28 async clients reject sync
                # file objects as content
                with open(local, "rb") as f:
                    data = f.read()
                resp = await client.put(
                    url,
                    content=data,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/octet-stream",
                    },
                )
                return resp.status_code in (200, 201)

        display = f"onedrive:{_MS_ROOT_FOLDER}/{remote_path}"
        return await _upload_all_files(dest, upload_one, remote_path, display)

    async def test_connection(self) -> dict[str, Any]:
        try:
            token = await self._get_access_token()
            async with httpx.AsyncClient(timeout=10) as client:
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

    def _get_config(self) -> tuple[str, str, str]:
        url = _read_config("backups.offsite.webdav.url", "").rstrip("/")
        username = _read_config("backups.offsite.webdav.username", "")
        password = _read_vault("backups.offsite.webdav.password")
        return url, username, password

    async def upload_directory(self, dest: Path, remote_path: str) -> dict[str, Any]:
        url, username, password = self._get_config()
        if not url:
            return {"ok": False, "remote": "webdav", "error": "WebDAV URL not configured"}
        auth = (username, password) if username else None
        display = f"webdav:{url.split('//')[1] if '//' in url else url}/{remote_path}"

        async def upload_one(local: Path, rel: str) -> bool:
            # MKCOL parent directories first
            parts = rel.split("/")[:-1]
            current = f"{url}/{remote_path}"
            async with httpx.AsyncClient(timeout=300) as client:
                for part in parts:
                    current = f"{current}/{part}"
                    # MKCOL is idempotent (409 = already exists is fine)
                    await client.request(
                        "MKCOL", current, auth=auth
                    )
                # PUT the file — bytes are read up-front because httpx 0.28
                # async clients reject sync file objects as content
                with open(local, "rb") as f:
                    data = f.read()
                resp = await client.put(
                    f"{url}/{remote_path}/{rel}",
                    content=data,
                    auth=auth,
                )
                return resp.status_code in (200, 201, 204)

        return await _upload_all_files(dest, upload_one, remote_path, display)

    async def test_connection(self) -> dict[str, Any]:
        url, username, password = self._get_config()
        if not url:
            return {"ok": False, "error": "WebDAV URL not configured"}
        auth = (username, password) if username else None
        try:
            async with httpx.AsyncClient(timeout=10, verify=False) as client:
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


# ─── S3 (Amazon S3 / Backblaze B2 / S3-compatible) ─────────────────────


class S3Sync:
    """S3-compatible backup via raw httpx with SigV4 signing.

    No boto3 — httpx + cryptography (both already installed) handle the
    SigV4 signing in ~40 lines, consistent with Kazma's no-SDK pattern.

    Config: backups.offsite.s3.access_key, backups.offsite.s3.secret_key (vault),
    backups.offsite.s3.bucket, backups.offsite.s3.endpoint (optional for B2/MinIO),
    backups.offsite.s3.region (default us-east-1).
    """

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
        canonical_querystring = ""  # we don't use query params for PUT

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

    async def upload_directory(self, dest: Path, remote_path: str) -> dict[str, Any]:
        config = self._get_config()
        if not config["access_key"] or not config["bucket"]:
            return {"ok": False, "remote": "s3", "error": "S3 not configured"}
        display = f"s3:{config['bucket']}/{remote_path}"

        async def upload_one(local: Path, rel: str) -> bool:
            key = f"kazma-backups/{remote_path}/{rel}"
            url = self._build_url(config, key)
            content_type = "application/octet-stream"
            base_headers = {"content-type": content_type}
            async with httpx.AsyncClient(timeout=300) as client:
                with open(local, "rb") as f:
                    payload = f.read()
                signed = self._sign_request("PUT", url, base_headers, payload, config)
                resp = await client.put(url, content=payload, headers=signed)
                return resp.status_code in (200, 201)

        return await _upload_all_files(dest, upload_one, remote_path, display)

    async def test_connection(self) -> dict[str, Any]:
        config = self._get_config()
        if not config["access_key"] or not config["bucket"]:
            return {"ok": False, "error": "S3 access key and bucket are required"}
        url = self._build_url(config, "")
        try:
            signed = self._sign_request("HEAD", url, {}, None, config)
            async with httpx.AsyncClient(timeout=10) as client:
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
