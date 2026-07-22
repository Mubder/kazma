"""Resolve email provider → backend (auto / sandbox / gmail / microsoft / imap)."""

from __future__ import annotations

import logging
import os
from typing import Any

from kazma_skills.native.email_manager.backends.sandbox import SandboxBackend

logger = logging.getLogger(__name__)


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _vault_get(key: str) -> str:
    """Best-effort sync vault read (may fail closed)."""
    try:
        from kazma_core.security.vault import SecretVault

        # Prefer config store path if available
        try:
            from kazma_core.paths import vault_db_path

            v = SecretVault(db_path=vault_db_path())
        except Exception:
            v = SecretVault()
        # SecretVault API may be async or sync — try common patterns
        if hasattr(v, "get_sync"):
            return str(v.get_sync(key) or "")
        # Fall through: many vaults need async; skip
    except Exception as exc:
        logger.debug("[email] vault get %s failed: %s", key, exc)
    return ""


def _cred(env_key: str, vault_key: str = "") -> str:
    val = _env(env_key)
    if val:
        return val
    if vault_key:
        return _vault_get(vault_key)
    return ""


def detect_available_provider() -> str:
    """Return first real provider with credentials, else sandbox."""
    if _cred("EMAIL_GMAIL_ADDRESS") and _cred(
        "EMAIL_GMAIL_APP_PASSWORD", "email.gmail.app_password"
    ):
        return "gmail"
    if _cred("EMAIL_MS_ACCESS_TOKEN") or _cred(
        "EMAIL_MS_REFRESH_TOKEN", "email.microsoft.refresh_token"
    ):
        return "microsoft"
    if _cred("EMAIL_ADDRESS") and _cred("EMAIL_PASSWORD", "email.imap.password"):
        return "imap"
    return "sandbox"


def resolve_provider(provider: str | None = None) -> str:
    p = (provider or _env("EMAIL_DEFAULT_PROVIDER", "auto") or "auto").strip().lower()
    if p in ("", "auto"):
        return detect_available_provider()
    if p in ("sandbox", "gmail", "microsoft", "microsoft_graph", "outlook", "imap"):
        if p in ("microsoft_graph", "outlook"):
            return "microsoft"
        return p
    return "sandbox"


def get_backend(provider: str | None = None) -> Any:
    """Instantiate backend for *provider* (after resolve)."""
    name = resolve_provider(provider)

    if name == "sandbox":
        return SandboxBackend()

    if name == "gmail":
        from kazma_skills.native.email_manager.backends.imap_smtp import ImapSmtpBackend

        address = _cred("EMAIL_GMAIL_ADDRESS", "email.gmail.address")
        password = _cred("EMAIL_GMAIL_APP_PASSWORD", "email.gmail.app_password")
        if not address or not password:
            logger.info("[email] gmail requested but missing creds → sandbox")
            return SandboxBackend()
        return ImapSmtpBackend(
            name="gmail",
            address=address,
            password=password,
            imap_host=_env("EMAIL_IMAP_HOST", "imap.gmail.com") or "imap.gmail.com",
            imap_port=int(_env("EMAIL_IMAP_PORT", "993") or "993"),
            smtp_host=_env("EMAIL_SMTP_HOST", "smtp.gmail.com") or "smtp.gmail.com",
            smtp_port=int(_env("EMAIL_SMTP_PORT", "587") or "587"),
            smtp_starttls=True,
        )

    if name == "imap":
        from kazma_skills.native.email_manager.backends.imap_smtp import ImapSmtpBackend

        address = _cred("EMAIL_ADDRESS")
        password = _cred("EMAIL_PASSWORD", "email.imap.password")
        host = _env("EMAIL_IMAP_HOST")
        if not address or not password or not host:
            logger.info("[email] imap requested but missing creds → sandbox")
            return SandboxBackend()
        return ImapSmtpBackend(
            name="imap",
            address=address,
            password=password,
            imap_host=host,
            imap_port=int(_env("EMAIL_IMAP_PORT", "993") or "993"),
            smtp_host=_env("EMAIL_SMTP_HOST", host.replace("imap", "smtp")),
            smtp_port=int(_env("EMAIL_SMTP_PORT", "587") or "587"),
            smtp_starttls=_env("EMAIL_SMTP_SSL", "").lower() not in ("1", "true", "ssl"),
        )

    if name == "microsoft":
        from kazma_skills.native.email_manager.backends.microsoft_graph import (
            MicrosoftGraphBackend,
            graph_token_from_env,
        )

        token = graph_token_from_env() or _vault_get("email.microsoft.access_token")
        refresh = _cred("EMAIL_MS_REFRESH_TOKEN", "email.microsoft.refresh_token")
        if not token and not refresh:
            logger.info("[email] microsoft requested but missing tokens → sandbox")
            return SandboxBackend()
        return MicrosoftGraphBackend(
            access_token=token or "pending_refresh",
            refresh_token=refresh,
            client_id=_cred("EMAIL_MS_CLIENT_ID", "email.microsoft.client_id"),
            client_secret=_cred("EMAIL_MS_CLIENT_SECRET", "email.microsoft.client_secret"),
            tenant_id=_env("EMAIL_MS_TENANT_ID", "common") or "common",
        )

    return SandboxBackend()


def mode_banner(backend: Any) -> str:
    return f"[{getattr(backend, 'name', 'unknown')} mode]"
