"""Email credential resolution + vault persistence."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _force_global_scope() -> tuple[Any, Any]:
    """Pin the vault scope to global for one read/write.

    Email/OAuth credentials are installation-level (one Google/Microsoft
    client for the whole install), NOT per-tenant data. Without this pin,
    a save from the Web UI lands in the request's tenant scope ('default')
    while background paths (backup refresh, cron) read the global scope —
    producing duplicate rows where a stale rotated secret keeps shadowing
    the current one (incident 2026-08-16: invalid_client on every refresh).
    Returns (token, reset) for try/finally use.
    """
    from kazma_core.tenant_context import reset_current_tenant_id, set_current_tenant_id

    token = set_current_tenant_id(None)
    return token, reset_current_tenant_id


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
        token, reset = _force_global_scope()
        try:
            val = v.retrieve(name)
        finally:
            reset(token)
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
        token, reset = _force_global_scope()
        try:
            v.store(name, value, category=category)
        finally:
            reset(token)
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


def vault_delete(*names: str) -> None:
    """Delete vault secrets by name (global scope, like :func:`vault_store`).
    A missing name is not an error; a vault that cannot write is logged."""
    try:
        from kazma_core.security.vault import SecretVault, get_vault
        from kazma_core.paths import vault_db_path

        v = get_vault() or SecretVault(db_path=vault_db_path())
        token, reset = _force_global_scope()
        try:
            for name in names:
                v.delete(name)
        finally:
            reset(token)
    except Exception:
        logger.warning("[email.creds] vault delete of %s failed", ", ".join(names), exc_info=True)


def env_account_aliases() -> list[str]:
    """Accounts written in .env: EMAIL_ACCOUNTS=a,b,c."""
    raw = _env("EMAIL_ACCOUNTS")
    if not raw:
        return []
    return [a.strip() for a in raw.split(",") if a.strip()]


def list_account_aliases() -> list[str]:
    """Every extra account chat can name: those in .env, then those added in
    Settings (``accounts.stored_accounts``)."""
    from kazma_skills.native.email_manager.accounts import normalize_alias, stored_accounts

    names = env_account_aliases()
    seen = {normalize_alias(a) for a in names}
    names += [row["alias"] for row in stored_accounts() if row["alias"] not in seen]
    return names


def account_config(alias: str) -> dict[str, str]:
    """An extra account's settings: from .env for an account written there,
    else from Settings (the list plus the account's own vault keys)."""
    from kazma_skills.native.email_manager.accounts import (
        account_secret,
        normalize_alias,
        stored_account,
    )

    key = normalize_alias(alias)
    if key not in {normalize_alias(a) for a in env_account_aliases()}:
        row = stored_account(key)
        if row is not None:
            out = {"alias": key}
            for field in ("type", "address", "imap_host", "imap_port", "pop_host",
                          "pop_port", "smtp_host", "smtp_port"):
                out[field] = str(row.get(field) or "")
            for field in ("password", "access_token", "refresh_token", "scopes"):
                out[field] = account_secret(key, field)
            # The main app registration signs every account in.
            out.update(client_id="", client_secret="", tenant_id="")
            return out
    return _env_account_config(alias)


def _env_account_config(alias: str) -> dict[str, str]:
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
    # An OAuth login is named by its own sign-in (oauth_ms.store_microsoft_tokens);
    # the protocol address belongs to an IMAP/POP login and may be a leftover.
    ms_addr = (
        vault_retrieve("email.microsoft.oauth_address")
        if ms_mode == "oauth"
        else cred("EMAIL_MS_ADDRESS", "email.microsoft.address")
    )
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
        "microsoft_address": (ms_addr or "") if ms_configured else "",
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
