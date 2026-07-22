"""Resolve email provider → backend (auto / sandbox / gmail OAuth|app-password / microsoft / imap)."""

from __future__ import annotations

import logging
import os
from typing import Any

from kazma_skills.native.email_manager.backends.sandbox import SandboxBackend
from kazma_skills.native.email_manager.credentials import (
    account_config,
    cred,
    list_account_aliases,
    vault_retrieve,
)

logger = logging.getLogger(__name__)


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _gmail_oauth_ready() -> bool:
    return bool(
        cred("EMAIL_GMAIL_ACCESS_TOKEN", "email.gmail.access_token")
        or cred("EMAIL_GMAIL_REFRESH_TOKEN", "email.gmail.refresh_token")
    )


def _gmail_app_password_ready() -> bool:
    return bool(
        cred("EMAIL_GMAIL_ADDRESS", "email.gmail.address")
        and cred("EMAIL_GMAIL_APP_PASSWORD", "email.gmail.app_password")
    )


def detect_available_provider() -> str:
    """Return first real provider with credentials, else sandbox."""
    # Prefer OAuth Gmail over app password
    if _gmail_oauth_ready() or _gmail_app_password_ready():
        return "gmail"
    if cred("EMAIL_MS_ACCESS_TOKEN", "email.microsoft.access_token") or cred(
        "EMAIL_MS_REFRESH_TOKEN", "email.microsoft.refresh_token"
    ):
        return "microsoft"
    if cred("EMAIL_ADDRESS") and cred("EMAIL_PASSWORD", "email.imap.password"):
        if _env("EMAIL_IMAP_HOST"):
            return "imap"
    for alias in list_account_aliases():
        cfg = account_config(alias)
        t = (cfg.get("type") or "").lower()
        if t == "gmail" and (
            cfg.get("password") or cfg.get("access_token") or cfg.get("refresh_token")
        ):
            return f"account:{alias}"
        if t in ("microsoft", "microsoft_graph", "outlook") and (
            cfg.get("access_token") or cfg.get("refresh_token")
        ):
            return f"account:{alias}"
        if t == "imap" and cfg.get("address") and cfg.get("password") and cfg.get("imap_host"):
            return f"account:{alias}"
    return "sandbox"


def resolve_provider(provider: str | None = None, account: str | None = None) -> str:
    if account and str(account).strip():
        return f"account:{str(account).strip()}"
    p = (provider or _env("EMAIL_DEFAULT_PROVIDER", "auto") or "auto").strip().lower()
    if p in ("", "auto"):
        return detect_available_provider()
    if p.startswith("account:"):
        return p
    aliases = list_account_aliases()
    if p in {a.lower() for a in aliases}:
        return f"account:{p}"
    if p in ("sandbox", "gmail", "microsoft", "microsoft_graph", "outlook", "imap"):
        if p in ("microsoft_graph", "outlook"):
            return "microsoft"
        return p
    return "sandbox"


def _gmail_imap_backend(
    *,
    address: str,
    password: str,
    imap_host: str = "",
    imap_port: int = 993,
    smtp_host: str = "",
    smtp_port: int = 587,
    name: str = "gmail",
) -> Any:
    from kazma_skills.native.email_manager.backends.imap_smtp import ImapSmtpBackend

    return ImapSmtpBackend(
        name=name,
        address=address,
        password=password,
        imap_host=imap_host or "imap.gmail.com",
        imap_port=imap_port,
        smtp_host=smtp_host or "smtp.gmail.com",
        smtp_port=smtp_port,
        smtp_starttls=True,
    )


def _gmail_oauth_backend(name: str = "gmail_oauth") -> Any:
    from kazma_skills.native.email_manager.backends.gmail_api import GmailApiBackend

    access = cred("EMAIL_GMAIL_ACCESS_TOKEN", "email.gmail.access_token")
    refresh = cred("EMAIL_GMAIL_REFRESH_TOKEN", "email.gmail.refresh_token")
    if not access and not refresh:
        return None
    return GmailApiBackend(
        access_token=access or "pending_refresh",
        refresh_token=refresh,
        client_id=cred("EMAIL_GMAIL_CLIENT_ID", "email.gmail.client_id")
        or cred("GOOGLE_OAUTH_CLIENT_ID", "email.gmail.client_id"),
        client_secret=cred("EMAIL_GMAIL_CLIENT_SECRET", "email.gmail.client_secret")
        or cred("GOOGLE_OAUTH_CLIENT_SECRET", "email.gmail.client_secret"),
        email_address=cred("EMAIL_GMAIL_ADDRESS", "email.gmail.address"),
    )


def get_backend(provider: str | None = None, account: str | None = None) -> Any:
    name = resolve_provider(provider, account)

    if name == "sandbox":
        return SandboxBackend()

    if name.startswith("account:"):
        alias = name.split(":", 1)[1]
        cfg = account_config(alias)
        t = (cfg.get("type") or "sandbox").lower()
        if t == "gmail":
            # OAuth tokens on alias
            if cfg.get("access_token") or cfg.get("refresh_token"):
                from kazma_skills.native.email_manager.backends.gmail_api import (
                    GmailApiBackend,
                )

                return GmailApiBackend(
                    access_token=cfg.get("access_token") or "pending_refresh",
                    refresh_token=cfg.get("refresh_token") or "",
                    client_id=cfg.get("client_id")
                    or cred("EMAIL_GMAIL_CLIENT_ID", "email.gmail.client_id"),
                    client_secret=cfg.get("client_secret")
                    or cred("EMAIL_GMAIL_CLIENT_SECRET", "email.gmail.client_secret"),
                    email_address=cfg.get("address") or "",
                )
            if not cfg.get("address") or not cfg.get("password"):
                logger.info("[email] account %s gmail incomplete → sandbox", alias)
                return SandboxBackend()
            return _gmail_imap_backend(
                address=cfg["address"],
                password=cfg["password"],
                imap_host=cfg.get("imap_host") or "imap.gmail.com",
                imap_port=int(cfg.get("imap_port") or "993"),
                smtp_host=cfg.get("smtp_host") or "smtp.gmail.com",
                smtp_port=int(cfg.get("smtp_port") or "587"),
                name=f"gmail:{alias}",
            )
        if t in ("microsoft", "microsoft_graph", "outlook"):
            token = cfg.get("access_token") or ""
            refresh = cfg.get("refresh_token") or ""
            if not token and not refresh:
                return SandboxBackend()
            from kazma_skills.native.email_manager.backends.microsoft_graph import (
                MicrosoftGraphBackend,
            )

            return MicrosoftGraphBackend(
                access_token=token or "pending_refresh",
                refresh_token=refresh,
                client_id=cfg.get("client_id")
                or cred("EMAIL_MS_CLIENT_ID", "email.microsoft.client_id"),
                client_secret=cfg.get("client_secret")
                or cred("EMAIL_MS_CLIENT_SECRET", "email.microsoft.client_secret"),
                tenant_id=cfg.get("tenant_id") or _env("EMAIL_MS_TENANT_ID", "common") or "common",
                account_alias=alias,
            )
        if t == "imap":
            from kazma_skills.native.email_manager.backends.imap_smtp import ImapSmtpBackend

            if not cfg.get("address") or not cfg.get("password") or not cfg.get("imap_host"):
                return SandboxBackend()
            return ImapSmtpBackend(
                name=f"imap:{alias}",
                address=cfg["address"],
                password=cfg["password"],
                imap_host=cfg["imap_host"],
                imap_port=int(cfg.get("imap_port") or "993"),
                smtp_host=cfg.get("smtp_host") or cfg["imap_host"].replace("imap", "smtp"),
                smtp_port=int(cfg.get("smtp_port") or "587"),
                smtp_starttls=True,
            )
        return SandboxBackend()

    if name == "gmail":
        # Prefer OAuth Gmail API (Workspace-friendly)
        oauth_b = _gmail_oauth_backend()
        if oauth_b is not None:
            return oauth_b
        address = cred("EMAIL_GMAIL_ADDRESS", "email.gmail.address")
        password = cred("EMAIL_GMAIL_APP_PASSWORD", "email.gmail.app_password")
        if not address or not password:
            logger.info("[email] gmail requested but missing OAuth/app-password → sandbox")
            return SandboxBackend()
        return _gmail_imap_backend(
            address=address,
            password=password,
            imap_host=_env("EMAIL_IMAP_HOST", "imap.gmail.com") or "imap.gmail.com",
            imap_port=int(_env("EMAIL_IMAP_PORT", "993") or "993"),
            smtp_host=_env("EMAIL_SMTP_HOST", "smtp.gmail.com") or "smtp.gmail.com",
            smtp_port=int(_env("EMAIL_SMTP_PORT", "587") or "587"),
        )

    if name == "imap":
        from kazma_skills.native.email_manager.backends.imap_smtp import ImapSmtpBackend

        address = cred("EMAIL_ADDRESS")
        password = cred("EMAIL_PASSWORD", "email.imap.password")
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

        token = graph_token_from_env() or vault_retrieve("email.microsoft.access_token")
        refresh = cred("EMAIL_MS_REFRESH_TOKEN", "email.microsoft.refresh_token")
        if not token and not refresh:
            logger.info("[email] microsoft requested but missing tokens → sandbox")
            return SandboxBackend()
        return MicrosoftGraphBackend(
            access_token=token or "pending_refresh",
            refresh_token=refresh,
            client_id=cred("EMAIL_MS_CLIENT_ID", "email.microsoft.client_id"),
            client_secret=cred("EMAIL_MS_CLIENT_SECRET", "email.microsoft.client_secret"),
            tenant_id=_env("EMAIL_MS_TENANT_ID", "common") or "common",
            account_alias="",
        )

    return SandboxBackend()


def mode_banner(backend: Any) -> str:
    return f"[{getattr(backend, 'name', 'unknown')} mode]"
