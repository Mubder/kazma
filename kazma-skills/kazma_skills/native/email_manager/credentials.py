"""Email credential resolution + vault persistence."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def vault_retrieve(name: str) -> str:
    """Decrypt a vault secret by name; empty if vault disabled/missing."""
    try:
        from kazma_core.security.vault import SecretVault, get_vault
        from kazma_core.paths import vault_db_path

        v = get_vault()
        if v is None:
            try:
                v = SecretVault(db_path=vault_db_path())
            except Exception:
                return ""
        val = v.retrieve(name)
        return str(val) if val else ""
    except Exception as exc:
        logger.debug("[email.creds] vault retrieve %s: %s", name, exc)
        return ""


def vault_store(name: str, value: str, category: str = "email") -> bool:
    """Encrypt and store a secret. Returns False if vault unavailable."""
    if not value:
        return False
    try:
        from kazma_core.security.vault import SecretVault, get_vault
        from kazma_core.paths import vault_db_path

        v = get_vault()
        if v is None:
            v = SecretVault(db_path=vault_db_path())
        v.store(name, value, category=category)
        return True
    except Exception as exc:
        logger.warning("[email.creds] vault store %s failed: %s", name, exc)
        return False


def cred(env_key: str, vault_key: str = "") -> str:
    val = _env(env_key)
    if val:
        return val
    if vault_key:
        return vault_retrieve(vault_key)
    return ""


def list_account_aliases() -> list[str]:
    """Configured multi-account aliases from EMAIL_ACCOUNTS=a,b,c."""
    raw = _env("EMAIL_ACCOUNTS")
    if not raw:
        return []
    return [a.strip() for a in raw.split(",") if a.strip()]


def account_config(alias: str) -> dict[str, str]:
    """Load per-account env map: EMAIL_ACCOUNT_{ALIAS}_{FIELD}.

    Fields: TYPE (gmail|microsoft|imap|pop|sandbox), ADDRESS, PASSWORD,
    IMAP_HOST, IMAP_PORT, POP_HOST, POP_PORT, SMTP_HOST, SMTP_PORT, CLIENT_ID,
    CLIENT_SECRET, TENANT_ID, ACCESS_TOKEN, REFRESH_TOKEN.
    """
    prefix = f"EMAIL_ACCOUNT_{alias.upper().replace('-', '_')}_"
    fields = (
        "TYPE",
        "ADDRESS",
        "PASSWORD",
        "IMAP_HOST",
        "IMAP_PORT",
        "POP_HOST",
        "POP_PORT",
        "SMTP_HOST",
        "SMTP_PORT",
        "CLIENT_ID",
        "CLIENT_SECRET",
        "TENANT_ID",
        "ACCESS_TOKEN",
        "REFRESH_TOKEN",
    )
    out: dict[str, str] = {"alias": alias}
    for f in fields:
        out[f.lower()] = _env(prefix + f)
    # Vault fallbacks per alias
    if not out.get("password"):
        out["password"] = vault_retrieve(f"email.account.{alias}.password")
    if not out.get("refresh_token"):
        out["refresh_token"] = vault_retrieve(f"email.account.{alias}.refresh_token")
    if not out.get("access_token"):
        out["access_token"] = vault_retrieve(f"email.account.{alias}.access_token")
    return out


def gmail_auth_mode() -> str:
    """oauth | imap | pop | app_password | none."""
    auth = (cred("EMAIL_GMAIL_AUTH", "email.gmail.auth") or "").lower()
    if auth in ("oauth", "imap", "pop", "app_password"):
        if auth == "app_password":
            return "imap"
        return auth
    if cred("EMAIL_GMAIL_ACCESS_TOKEN", "email.gmail.access_token") or cred(
        "EMAIL_GMAIL_REFRESH_TOKEN", "email.gmail.refresh_token"
    ):
        return "oauth"
    if cred("EMAIL_GMAIL_ADDRESS", "email.gmail.address") and cred(
        "EMAIL_GMAIL_APP_PASSWORD", "email.gmail.app_password"
    ):
        return "imap"
    return "none"


def microsoft_auth_mode() -> str:
    """oauth | imap | pop | none."""
    auth = (cred("EMAIL_MS_AUTH", "email.microsoft.auth") or "").lower()
    if auth in ("oauth", "imap", "pop"):
        return auth
    if cred("EMAIL_MS_ACCESS_TOKEN", "email.microsoft.access_token") or cred(
        "EMAIL_MS_REFRESH_TOKEN", "email.microsoft.refresh_token"
    ):
        return "oauth"
    if cred("EMAIL_MS_ADDRESS", "email.microsoft.address") and cred(
        "EMAIL_MS_PASSWORD", "email.microsoft.password"
    ):
        # default protocol when only password set
        return "imap"
    return "none"


def status_summary() -> dict[str, Any]:
    """Non-secret status for Settings / API."""
    aliases = list_account_aliases()
    gmail_addr = cred("EMAIL_GMAIL_ADDRESS", "email.gmail.address")
    gmail_pw = bool(cred("EMAIL_GMAIL_APP_PASSWORD", "email.gmail.app_password"))
    gmail_oauth = bool(
        cred("EMAIL_GMAIL_ACCESS_TOKEN", "email.gmail.access_token")
        or cred("EMAIL_GMAIL_REFRESH_TOKEN", "email.gmail.refresh_token")
    )
    gmail_mode = gmail_auth_mode()
    gmail_configured = gmail_mode != "none"

    ms_mode = microsoft_auth_mode()
    ms_addr = cred("EMAIL_MS_ADDRESS", "email.microsoft.address")
    ms_configured = ms_mode != "none"

    generic_proto = (_env("EMAIL_PROTOCOL") or vault_retrieve("email.generic.auth") or "").lower()
    generic_addr = _env("EMAIL_ADDRESS") or vault_retrieve("email.generic.address")
    generic_pw = bool(cred("EMAIL_PASSWORD", "email.imap.password"))
    imap_host = _env("EMAIL_IMAP_HOST")
    pop_host = _env("EMAIL_POP_HOST")
    if not generic_proto:
        if generic_addr and generic_pw and imap_host:
            generic_proto = "imap"
        elif generic_addr and generic_pw and pop_host:
            generic_proto = "pop"
    imap_configured = bool(
        generic_addr and generic_pw and (generic_proto == "imap" or imap_host)
    )
    pop_configured = bool(
        generic_addr and generic_pw and (generic_proto == "pop" or pop_host)
    )

    ms_cid = _env("EMAIL_MS_CLIENT_ID") or vault_retrieve("email.microsoft.client_id")
    return {
        "default_provider": _env("EMAIL_DEFAULT_PROVIDER", "auto") or "auto",
        "gmail_configured": gmail_configured,
        "gmail_address": gmail_addr if gmail_configured else "",
        "gmail_auth_mode": gmail_mode,
        "gmail_oauth": gmail_oauth and gmail_mode == "oauth",
        "gmail_app_password": gmail_pw and gmail_mode in ("imap", "pop", "app_password"),
        "gmail_imap": gmail_mode == "imap",
        "gmail_pop": gmail_mode == "pop",
        "microsoft_configured": ms_configured,
        "microsoft_address": ms_addr if ms_configured and ms_mode in ("imap", "pop") else "",
        "microsoft_auth_mode": ms_mode,
        "microsoft_oauth": ms_mode == "oauth",
        "microsoft_imap": ms_mode == "imap",
        "microsoft_pop": ms_mode == "pop",
        "imap_configured": imap_configured,
        "pop_configured": pop_configured,
        "generic_protocol": generic_proto or "",
        "sandbox_always": True,
        "accounts": aliases,
        "ms_client_id_set": bool(ms_cid),
        "ms_tenant_id": _env("EMAIL_MS_TENANT_ID", "common") or "common",
        "presets": {
            "gmail": {"imap": "imap.gmail.com", "pop": "pop.gmail.com", "smtp": "smtp.gmail.com"},
            "microsoft": {
                "imap": "outlook.office365.com",
                "pop": "outlook.office365.com",
                "smtp": "smtp.office365.com",
            },
        },
    }
