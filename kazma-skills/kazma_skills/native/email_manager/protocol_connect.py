"""Save / clear IMAP+POP protocol credentials for Gmail, Microsoft, generic."""

from __future__ import annotations

import logging
import os
from typing import Any

from kazma_skills.native.email_manager.credentials import vault_store
from kazma_skills.native.email_manager.presets import get_preset

logger = logging.getLogger(__name__)


def _set_env(key: str, value: str) -> None:
    if value:
        os.environ[key] = value
    else:
        os.environ.pop(key, None)


def _vault_delete(*names: str) -> None:
    try:
        from kazma_core.security.vault import SecretVault, get_vault
        from kazma_core.paths import vault_db_path

        v = get_vault() or SecretVault(db_path=vault_db_path())
        for n in names:
            try:
                v.delete(n)
            except Exception:
                pass
    except Exception as exc:
        logger.debug("[email.protocol] vault delete: %s", exc)


def connect_protocol(
    *,
    provider: str,
    protocol: str,
    address: str,
    password: str,
    imap_host: str = "",
    imap_port: int | None = None,
    pop_host: str = "",
    pop_port: int | None = None,
    smtp_host: str = "",
    smtp_port: int | None = None,
) -> dict[str, Any]:
    """Persist IMAP or POP credentials. ``provider``: gmail|microsoft|generic."""
    p = (provider or "").strip().lower()
    proto = (protocol or "").strip().lower()
    if p in ("microsoft_graph", "outlook", "m365", "office365"):
        p = "microsoft"
    if p in ("google", "workspace"):
        p = "gmail"
    if proto not in ("imap", "pop"):
        return {"ok": False, "error": "protocol must be imap or pop"}
    address = (address or "").strip()
    password = (password or "").strip().replace(" ", "")
    if "@" not in address:
        return {"ok": False, "error": "Invalid email address"}
    if len(password) < 4:
        return {"ok": False, "error": "Password / app password required"}

    preset = get_preset(p if p != "generic" else "", proto)
    if proto == "imap":
        ih = (imap_host or preset.get("imap_host") or "").strip()
        ip = int(imap_port or preset.get("imap_port") or 993)
        if not ih and p == "generic":
            return {"ok": False, "error": "imap_host required for generic IMAP"}
        if not ih:
            return {"ok": False, "error": f"No IMAP host preset for {p}"}
    else:
        ph = (pop_host or preset.get("pop_host") or "").strip()
        pp = int(pop_port or preset.get("pop_port") or 995)
        if not ph and p == "generic":
            return {"ok": False, "error": "pop_host required for generic POP"}
        if not ph:
            return {"ok": False, "error": f"No POP host preset for {p}"}

    sh = (smtp_host or preset.get("smtp_host") or "").strip()
    sp = int(smtp_port or preset.get("smtp_port") or 587)

    if p == "gmail":
        _set_env("EMAIL_GMAIL_ADDRESS", address)
        _set_env("EMAIL_GMAIL_APP_PASSWORD", password)
        _set_env("EMAIL_GMAIL_AUTH", proto)
        vault_store("email.gmail.address", address, category="email")
        vault_store("email.gmail.app_password", password, category="email")
        vault_store("email.gmail.auth", proto, category="email")
        if proto == "imap":
            _set_env("EMAIL_IMAP_HOST", imap_host or preset.get("imap_host") or "imap.gmail.com")
            _set_env("EMAIL_IMAP_PORT", str(imap_port or preset.get("imap_port") or 993))
        else:
            _set_env("EMAIL_POP_HOST", pop_host or preset.get("pop_host") or "pop.gmail.com")
            _set_env("EMAIL_POP_PORT", str(pop_port or preset.get("pop_port") or 995))
        _set_env("EMAIL_SMTP_HOST", sh or "smtp.gmail.com")
        _set_env("EMAIL_SMTP_PORT", str(sp))
        return {
            "ok": True,
            "provider": "gmail",
            "protocol": proto,
            "address": address,
            "message": f"Gmail connected via {proto.upper()} (app password).",
        }

    if p == "microsoft":
        _set_env("EMAIL_MS_ADDRESS", address)
        _set_env("EMAIL_MS_PASSWORD", password)
        _set_env("EMAIL_MS_AUTH", proto)
        vault_store("email.microsoft.address", address, category="email")
        vault_store("email.microsoft.password", password, category="email")
        vault_store("email.microsoft.auth", proto, category="email")
        if proto == "imap":
            _set_env(
                "EMAIL_MS_IMAP_HOST",
                imap_host or preset.get("imap_host") or "outlook.office365.com",
            )
            _set_env(
                "EMAIL_MS_IMAP_PORT",
                str(imap_port or preset.get("imap_port") or 993),
            )
        else:
            _set_env(
                "EMAIL_MS_POP_HOST",
                pop_host or preset.get("pop_host") or "outlook.office365.com",
            )
            _set_env(
                "EMAIL_MS_POP_PORT",
                str(pop_port or preset.get("pop_port") or 995),
            )
        _set_env("EMAIL_MS_SMTP_HOST", sh or "smtp.office365.com")
        _set_env("EMAIL_MS_SMTP_PORT", str(sp))
        return {
            "ok": True,
            "provider": "microsoft",
            "protocol": proto,
            "address": address,
            "message": (
                f"Microsoft connected via {proto.upper()}. "
                "Basic auth may be disabled on some tenants — use OAuth if login fails."
            ),
        }

    # generic
    _set_env("EMAIL_ADDRESS", address)
    _set_env("EMAIL_PASSWORD", password)
    _set_env("EMAIL_PROTOCOL", proto)
    vault_store("email.imap.password", password, category="email")
    vault_store("email.generic.address", address, category="email")
    vault_store("email.generic.auth", proto, category="email")
    if proto == "imap":
        _set_env("EMAIL_IMAP_HOST", imap_host or "")
        _set_env("EMAIL_IMAP_PORT", str(imap_port or 993))
    else:
        _set_env("EMAIL_POP_HOST", pop_host or "")
        _set_env("EMAIL_POP_PORT", str(pop_port or 995))
    if sh:
        _set_env("EMAIL_SMTP_HOST", sh)
    _set_env("EMAIL_SMTP_PORT", str(sp))
    return {
        "ok": True,
        "provider": "generic",
        "protocol": proto,
        "address": address,
        "message": f"Generic {proto.upper()} account saved.",
    }


def disconnect_protocol(provider: str) -> dict[str, Any]:
    """Clear protocol credentials for provider (gmail|microsoft|generic). Does not clear OAuth app client ids."""
    p = (provider or "").strip().lower()
    if p in ("microsoft_graph", "outlook", "m365"):
        p = "microsoft"
    if p in ("google", "workspace"):
        p = "gmail"

    if p == "gmail":
        for k in (
            "EMAIL_GMAIL_ADDRESS",
            "EMAIL_GMAIL_APP_PASSWORD",
            "EMAIL_GMAIL_AUTH",
        ):
            os.environ.pop(k, None)
        _vault_delete(
            "email.gmail.address",
            "email.gmail.app_password",
            "email.gmail.auth",
            "email.gmail.access_token",
            "email.gmail.refresh_token",
        )
        for k in (
            "EMAIL_GMAIL_ACCESS_TOKEN",
            "EMAIL_GMAIL_REFRESH_TOKEN",
        ):
            os.environ.pop(k, None)
        return {"ok": True, "message": "Gmail credentials cleared."}

    if p == "microsoft":
        for k in (
            "EMAIL_MS_ADDRESS",
            "EMAIL_MS_PASSWORD",
            "EMAIL_MS_AUTH",
            "EMAIL_MS_IMAP_HOST",
            "EMAIL_MS_IMAP_PORT",
            "EMAIL_MS_POP_HOST",
            "EMAIL_MS_POP_PORT",
            "EMAIL_MS_SMTP_HOST",
            "EMAIL_MS_SMTP_PORT",
            "EMAIL_MS_ACCESS_TOKEN",
            "EMAIL_MS_REFRESH_TOKEN",
        ):
            os.environ.pop(k, None)
        _vault_delete(
            "email.microsoft.address",
            "email.microsoft.password",
            "email.microsoft.auth",
            "email.microsoft.access_token",
            "email.microsoft.refresh_token",
        )
        return {"ok": True, "message": "Microsoft credentials cleared."}

    if p in ("generic", "imap", "pop"):
        for k in (
            "EMAIL_ADDRESS",
            "EMAIL_PASSWORD",
            "EMAIL_PROTOCOL",
            "EMAIL_IMAP_HOST",
            "EMAIL_IMAP_PORT",
            "EMAIL_POP_HOST",
            "EMAIL_POP_PORT",
            "EMAIL_SMTP_HOST",
            "EMAIL_SMTP_PORT",
        ):
            os.environ.pop(k, None)
        _vault_delete(
            "email.imap.password",
            "email.generic.address",
            "email.generic.auth",
        )
        return {"ok": True, "message": "Generic IMAP/POP credentials cleared."}

    return {"ok": False, "error": f"Unknown provider: {provider}"}
