"""Email credential resolution + vault persistence."""

from __future__ import annotations

import json
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

    Fields: TYPE (gmail|microsoft|imap|sandbox), ADDRESS, PASSWORD,
    IMAP_HOST, IMAP_PORT, SMTP_HOST, SMTP_PORT, CLIENT_ID, CLIENT_SECRET,
    TENANT_ID, ACCESS_TOKEN, REFRESH_TOKEN.
    """
    prefix = f"EMAIL_ACCOUNT_{alias.upper().replace('-', '_')}_"
    fields = (
        "TYPE",
        "ADDRESS",
        "PASSWORD",
        "IMAP_HOST",
        "IMAP_PORT",
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


def status_summary() -> dict[str, Any]:
    """Non-secret status for Settings / API."""
    aliases = list_account_aliases()
    gmail = bool(cred("EMAIL_GMAIL_ADDRESS") and cred("EMAIL_GMAIL_APP_PASSWORD", "email.gmail.app_password"))
    ms = bool(
        cred("EMAIL_MS_ACCESS_TOKEN", "email.microsoft.access_token")
        or cred("EMAIL_MS_REFRESH_TOKEN", "email.microsoft.refresh_token")
    )
    imap = bool(cred("EMAIL_ADDRESS") and cred("EMAIL_PASSWORD", "email.imap.password") and _env("EMAIL_IMAP_HOST"))
    return {
        "default_provider": _env("EMAIL_DEFAULT_PROVIDER", "auto") or "auto",
        "gmail_configured": gmail,
        "microsoft_configured": ms,
        "imap_configured": imap,
        "sandbox_always": True,
        "accounts": aliases,
        "ms_client_id_set": bool(_env("EMAIL_MS_CLIENT_ID") or vault_retrieve("email.microsoft.client_id")),
    }
