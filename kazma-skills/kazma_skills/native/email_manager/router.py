"""Resolve email provider → backend (auto / sandbox / gmail / microsoft / imap / pop).

The sandbox mailbox answers only when nothing was named and nothing is
connected, or when the sandbox itself was asked for. A provider or account
named explicitly -- in the call, by ``EMAIL_DEFAULT_PROVIDER``, or as an
account alias -- that is not connected raises :class:`EmailNotConnectedError`
instead: "Sandbox sent to …" after asking for Gmail is a send that did not
happen, reported as one that did (the AGENTS.md §34 rule, already Calendar's).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from kazma_skills.native.email_manager.backends.sandbox import SandboxBackend
from kazma_skills.native.email_manager.credentials import (
    account_config,
    cred,
    gmail_auth_mode,
    list_account_aliases,
    microsoft_auth_mode,
    vault_retrieve,
)
from kazma_skills.native.email_manager.presets import get_preset

logger = logging.getLogger(__name__)

GMAIL_NOT_CONNECTED = (
    "Gmail is not connected. Settings → Email → Connect with Google, or set an "
    "app password for IMAP/POP."
)
MICROSOFT_NOT_CONNECTED = (
    "Microsoft mail is not connected. Settings → Email → Connect with Microsoft, "
    "or set an address and password for IMAP/POP."
)
IMAP_NOT_CONNECTED = (
    "IMAP is not configured. Set the address, password and IMAP host "
    "(EMAIL_ADDRESS, EMAIL_PASSWORD, EMAIL_IMAP_HOST) in Settings → Email."
)
POP_NOT_CONNECTED = (
    "POP is not configured. Set the address, password and POP host "
    "(EMAIL_ADDRESS, EMAIL_PASSWORD, EMAIL_POP_HOST) in Settings → Email."
)
_KNOWN_PROVIDERS = (
    "sandbox", "gmail", "microsoft", "microsoft_graph", "outlook", "imap", "pop",
)


class EmailNotConnectedError(RuntimeError):
    """An email provider or account was named explicitly and is not connected."""

    def __init__(self, target: str, hint: str) -> None:
        super().__init__(hint)
        self.target = target
        self.hint = hint


def _account_hint(alias: str, detail: str) -> str:
    return (
        f"Email account '{alias}' {detail}. Configure it in Settings → Email "
        "(EMAIL_ACCOUNTS + EMAIL_ACCOUNT_<ALIAS>_*), or name another account."
    )


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _gmail_oauth_ready() -> bool:
    return bool(
        cred("EMAIL_GMAIL_ACCESS_TOKEN", "email.gmail.access_token")
        or cred("EMAIL_GMAIL_REFRESH_TOKEN", "email.gmail.refresh_token")
    )


def _gmail_password_ready() -> bool:
    return bool(
        cred("EMAIL_GMAIL_ADDRESS", "email.gmail.address")
        and cred("EMAIL_GMAIL_APP_PASSWORD", "email.gmail.app_password")
    )


def _ms_oauth_ready() -> bool:
    return bool(
        cred("EMAIL_MS_ACCESS_TOKEN", "email.microsoft.access_token")
        or cred("EMAIL_MS_REFRESH_TOKEN", "email.microsoft.refresh_token")
    )


def _ms_password_ready() -> bool:
    return bool(
        cred("EMAIL_MS_ADDRESS", "email.microsoft.address")
        and cred("EMAIL_MS_PASSWORD", "email.microsoft.password")
    )


def detect_available_provider() -> str:
    """Return first real provider with credentials, else sandbox."""
    mode = gmail_auth_mode()
    if mode == "oauth" and _gmail_oauth_ready():
        return "gmail"
    if mode in ("imap", "pop") and _gmail_password_ready():
        return "gmail"
    if _gmail_oauth_ready() or _gmail_password_ready():
        return "gmail"

    ms_mode = microsoft_auth_mode()
    if ms_mode == "oauth" and _ms_oauth_ready():
        return "microsoft"
    if ms_mode in ("imap", "pop") and _ms_password_ready():
        return "microsoft"
    if _ms_oauth_ready() or _ms_password_ready():
        return "microsoft"

    if cred("EMAIL_ADDRESS") and cred("EMAIL_PASSWORD", "email.imap.password"):
        proto = (_env("EMAIL_PROTOCOL") or vault_retrieve("email.generic.auth") or "").lower()
        if proto == "pop" or _env("EMAIL_POP_HOST"):
            if proto == "pop" or (_env("EMAIL_POP_HOST") and not _env("EMAIL_IMAP_HOST")):
                return "pop"
        if _env("EMAIL_IMAP_HOST") or proto == "imap":
            return "imap"
        if _env("EMAIL_POP_HOST"):
            return "pop"

    for alias in list_account_aliases():
        cfg = account_config(alias)
        t = (cfg.get("type") or "").lower()
        if t == "gmail" and (
            cfg.get("password") or cfg.get("access_token") or cfg.get("refresh_token")
        ):
            return f"account:{alias}"
        if t in ("microsoft", "microsoft_graph", "outlook") and (
            cfg.get("access_token")
            or cfg.get("refresh_token")
            or (cfg.get("address") and cfg.get("password"))
        ):
            return f"account:{alias}"
        if t == "imap" and cfg.get("address") and cfg.get("password") and cfg.get("imap_host"):
            return f"account:{alias}"
        if t == "pop" and cfg.get("address") and cfg.get("password") and (
            cfg.get("pop_host") or cfg.get("imap_host")
        ):
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
    if p in _KNOWN_PROVIDERS:
        if p in ("microsoft_graph", "outlook"):
            return "microsoft"
        return p
    # Neither a provider nor a configured alias: say so, never the sandbox.
    raise EmailNotConnectedError(
        p,
        f"Unknown email provider or account '{p}'. Use auto, gmail, microsoft, "
        f"imap, pop, sandbox, or a configured account ({', '.join(aliases) or 'none'}).",
    )


def _imap_backend(
    *,
    name: str,
    address: str,
    password: str,
    imap_host: str,
    imap_port: int = 993,
    smtp_host: str = "",
    smtp_port: int = 587,
    smtp_starttls: bool = True,
) -> Any:
    from kazma_skills.native.email_manager.backends.imap_smtp import ImapSmtpBackend

    return ImapSmtpBackend(
        name=name,
        address=address,
        password=password,
        imap_host=imap_host,
        imap_port=imap_port,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_starttls=smtp_starttls,
    )


def _pop_backend(
    *,
    name: str,
    address: str,
    password: str,
    pop_host: str,
    pop_port: int = 995,
    smtp_host: str = "",
    smtp_port: int = 587,
    smtp_starttls: bool = True,
) -> Any:
    from kazma_skills.native.email_manager.backends.pop_smtp import PopSmtpBackend

    return PopSmtpBackend(
        name=name,
        address=address,
        password=password,
        pop_host=pop_host,
        pop_port=pop_port,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_starttls=smtp_starttls,
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


def _unconnected(target: str, hint: str, explicit: bool) -> Any:
    """The sandbox for an auto choice; a refusal for an explicit one."""
    if explicit:
        raise EmailNotConnectedError(target, hint)
    logger.info("[email] %s not connected → sandbox (nothing was named)", target)
    return SandboxBackend()


def _gmail_backend(explicit: bool = True) -> Any:
    mode = gmail_auth_mode()
    # Explicit IMAP/POP wins over OAuth when user chose protocol
    if mode in ("imap", "pop") and _gmail_password_ready():
        address = cred("EMAIL_GMAIL_ADDRESS", "email.gmail.address")
        password = cred("EMAIL_GMAIL_APP_PASSWORD", "email.gmail.app_password")
        if mode == "pop":
            preset = get_preset("gmail", "pop")
            return _pop_backend(
                name="gmail_pop",
                address=address,
                password=password,
                pop_host=_env("EMAIL_POP_HOST") or preset.get("pop_host") or "pop.gmail.com",
                pop_port=int(_env("EMAIL_POP_PORT") or preset.get("pop_port") or 995),
                smtp_host=_env("EMAIL_SMTP_HOST") or preset.get("smtp_host") or "smtp.gmail.com",
                smtp_port=int(_env("EMAIL_SMTP_PORT") or preset.get("smtp_port") or 587),
            )
        preset = get_preset("gmail", "imap")
        return _imap_backend(
            name="gmail",
            address=address,
            password=password,
            imap_host=_env("EMAIL_IMAP_HOST") or preset.get("imap_host") or "imap.gmail.com",
            imap_port=int(_env("EMAIL_IMAP_PORT") or preset.get("imap_port") or 993),
            smtp_host=_env("EMAIL_SMTP_HOST") or preset.get("smtp_host") or "smtp.gmail.com",
            smtp_port=int(_env("EMAIL_SMTP_PORT") or preset.get("smtp_port") or 587),
        )

    oauth_b = _gmail_oauth_backend()
    if oauth_b is not None and mode in ("oauth", "none", ""):
        return oauth_b
    if oauth_b is not None and mode == "oauth":
        return oauth_b

    if _gmail_password_ready():
        address = cred("EMAIL_GMAIL_ADDRESS", "email.gmail.address")
        password = cred("EMAIL_GMAIL_APP_PASSWORD", "email.gmail.app_password")
        preset = get_preset("gmail", "imap")
        return _imap_backend(
            name="gmail",
            address=address,
            password=password,
            imap_host=_env("EMAIL_IMAP_HOST") or preset.get("imap_host") or "imap.gmail.com",
            imap_port=int(_env("EMAIL_IMAP_PORT") or preset.get("imap_port") or 993),
            smtp_host=_env("EMAIL_SMTP_HOST") or preset.get("smtp_host") or "smtp.gmail.com",
            smtp_port=int(_env("EMAIL_SMTP_PORT") or preset.get("smtp_port") or 587),
        )
    if oauth_b is not None:
        return oauth_b
    return _unconnected("gmail", GMAIL_NOT_CONNECTED, explicit)


def _microsoft_backend(explicit: bool = True) -> Any:
    mode = microsoft_auth_mode()
    if mode in ("imap", "pop") and _ms_password_ready():
        address = cred("EMAIL_MS_ADDRESS", "email.microsoft.address")
        password = cred("EMAIL_MS_PASSWORD", "email.microsoft.password")
        if mode == "pop":
            preset = get_preset("microsoft", "pop")
            return _pop_backend(
                name="microsoft_pop",
                address=address,
                password=password,
                pop_host=_env("EMAIL_MS_POP_HOST")
                or preset.get("pop_host")
                or "outlook.office365.com",
                pop_port=int(
                    _env("EMAIL_MS_POP_PORT") or preset.get("pop_port") or 995
                ),
                smtp_host=_env("EMAIL_MS_SMTP_HOST")
                or preset.get("smtp_host")
                or "smtp.office365.com",
                smtp_port=int(
                    _env("EMAIL_MS_SMTP_PORT") or preset.get("smtp_port") or 587
                ),
            )
        preset = get_preset("microsoft", "imap")
        return _imap_backend(
            name="microsoft_imap",
            address=address,
            password=password,
            imap_host=_env("EMAIL_MS_IMAP_HOST")
            or preset.get("imap_host")
            or "outlook.office365.com",
            imap_port=int(
                _env("EMAIL_MS_IMAP_PORT") or preset.get("imap_port") or 993
            ),
            smtp_host=_env("EMAIL_MS_SMTP_HOST")
            or preset.get("smtp_host")
            or "smtp.office365.com",
            smtp_port=int(
                _env("EMAIL_MS_SMTP_PORT") or preset.get("smtp_port") or 587
            ),
        )

    from kazma_skills.native.email_manager.backends.microsoft_graph import (
        MicrosoftGraphBackend,
        graph_token_from_env,
    )

    token = graph_token_from_env() or vault_retrieve("email.microsoft.access_token")
    refresh = cred("EMAIL_MS_REFRESH_TOKEN", "email.microsoft.refresh_token")
    if token or refresh:
        return MicrosoftGraphBackend(
            access_token=token or "pending_refresh",
            refresh_token=refresh,
            client_id=cred("EMAIL_MS_CLIENT_ID", "email.microsoft.client_id"),
            client_secret=cred("EMAIL_MS_CLIENT_SECRET", "email.microsoft.client_secret"),
            tenant_id=_env("EMAIL_MS_TENANT_ID", "common") or "common",
            account_alias="",
        )

    if _ms_password_ready():
        # fallback password → IMAP
        address = cred("EMAIL_MS_ADDRESS", "email.microsoft.address")
        password = cred("EMAIL_MS_PASSWORD", "email.microsoft.password")
        preset = get_preset("microsoft", "imap")
        return _imap_backend(
            name="microsoft_imap",
            address=address,
            password=password,
            imap_host=preset.get("imap_host") or "outlook.office365.com",
            imap_port=int(preset.get("imap_port") or 993),
            smtp_host=preset.get("smtp_host") or "smtp.office365.com",
            smtp_port=int(preset.get("smtp_port") or 587),
        )

    return _unconnected("microsoft", MICROSOFT_NOT_CONNECTED, explicit)


def get_backend(provider: str | None = None, account: str | None = None) -> Any:
    """Return the backend for *provider* / *account*. Explicit choices fail closed.

    Explicit = an account alias, or a provider other than ``auto`` named in the
    call or by ``EMAIL_DEFAULT_PROVIDER``. Only ``auto`` with nothing connected,
    or ``sandbox`` itself, answers from the sandbox mailbox.
    """
    requested = (provider or _env("EMAIL_DEFAULT_PROVIDER", "auto") or "auto").strip().lower()
    explicit = bool(account and str(account).strip()) or requested not in ("", "auto")
    name = resolve_provider(provider, account)

    if name == "sandbox":
        return SandboxBackend()

    if name.startswith("account:"):
        alias = name.split(":", 1)[1]
        cfg = account_config(alias)
        t = (cfg.get("type") or "").lower()
        if not t:
            # No TYPE: a typo'd alias, or one set up without it. Both used to
            # default to the sandbox and "send" there.
            configured = any(v for k, v in cfg.items() if k != "alias")
            raise EmailNotConnectedError(
                alias,
                _account_hint(
                    alias,
                    "has no TYPE (gmail, microsoft, imap, pop or sandbox)"
                    if configured else "is not configured",
                ),
            )
        if t == "sandbox":
            return SandboxBackend()
        if t == "gmail":
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
                raise EmailNotConnectedError(
                    alias, _account_hint(alias, "(Gmail) has no token and no address/password")
                )
            # Optional: TYPE=gmail with POP_HOST → pop
            if cfg.get("pop_host") and not cfg.get("imap_host"):
                return _pop_backend(
                    name=f"gmail_pop:{alias}",
                    address=cfg["address"],
                    password=cfg["password"],
                    pop_host=cfg["pop_host"],
                    pop_port=int(cfg.get("pop_port") or "995"),
                    smtp_host=cfg.get("smtp_host") or "smtp.gmail.com",
                    smtp_port=int(cfg.get("smtp_port") or "587"),
                )
            return _imap_backend(
                name=f"gmail:{alias}",
                address=cfg["address"],
                password=cfg["password"],
                imap_host=cfg.get("imap_host") or "imap.gmail.com",
                imap_port=int(cfg.get("imap_port") or "993"),
                smtp_host=cfg.get("smtp_host") or "smtp.gmail.com",
                smtp_port=int(cfg.get("smtp_port") or "587"),
            )
        if t in ("microsoft", "microsoft_graph", "outlook"):
            token = cfg.get("access_token") or ""
            refresh = cfg.get("refresh_token") or ""
            if token or refresh:
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
                    tenant_id=cfg.get("tenant_id")
                    or _env("EMAIL_MS_TENANT_ID", "common")
                    or "common",
                    account_alias=alias,
                )
            if cfg.get("address") and cfg.get("password"):
                if cfg.get("pop_host") and not cfg.get("imap_host"):
                    return _pop_backend(
                        name=f"microsoft_pop:{alias}",
                        address=cfg["address"],
                        password=cfg["password"],
                        pop_host=cfg["pop_host"],
                        pop_port=int(cfg.get("pop_port") or "995"),
                        smtp_host=cfg.get("smtp_host") or "smtp.office365.com",
                        smtp_port=int(cfg.get("smtp_port") or "587"),
                    )
                preset = get_preset("microsoft", "imap")
                return _imap_backend(
                    name=f"microsoft_imap:{alias}",
                    address=cfg["address"],
                    password=cfg["password"],
                    imap_host=cfg.get("imap_host")
                    or preset.get("imap_host")
                    or "outlook.office365.com",
                    imap_port=int(cfg.get("imap_port") or "993"),
                    smtp_host=cfg.get("smtp_host")
                    or preset.get("smtp_host")
                    or "smtp.office365.com",
                    smtp_port=int(cfg.get("smtp_port") or "587"),
                )
            raise EmailNotConnectedError(
                alias, _account_hint(alias, "(Microsoft) has no token and no address/password")
            )
        if t == "imap":
            if not cfg.get("address") or not cfg.get("password") or not cfg.get("imap_host"):
                raise EmailNotConnectedError(
                    alias, _account_hint(alias, "(IMAP) needs an address, password and IMAP host")
                )
            return _imap_backend(
                name=f"imap:{alias}",
                address=cfg["address"],
                password=cfg["password"],
                imap_host=cfg["imap_host"],
                imap_port=int(cfg.get("imap_port") or "993"),
                smtp_host=cfg.get("smtp_host")
                or cfg["imap_host"].replace("imap", "smtp"),
                smtp_port=int(cfg.get("smtp_port") or "587"),
            )
        if t == "pop":
            host = cfg.get("pop_host") or cfg.get("imap_host") or ""
            if not cfg.get("address") or not cfg.get("password") or not host:
                raise EmailNotConnectedError(
                    alias, _account_hint(alias, "(POP) needs an address, password and POP host")
                )
            return _pop_backend(
                name=f"pop:{alias}",
                address=cfg["address"],
                password=cfg["password"],
                pop_host=host,
                pop_port=int(cfg.get("pop_port") or "995"),
                smtp_host=cfg.get("smtp_host") or host.replace("pop", "smtp"),
                smtp_port=int(cfg.get("smtp_port") or "587"),
            )
        raise EmailNotConnectedError(
            alias, _account_hint(alias, f"has an unknown type '{t}'")
        )

    if name == "gmail":
        return _gmail_backend(explicit)

    if name == "imap":
        address = cred("EMAIL_ADDRESS") or vault_retrieve("email.generic.address")
        password = cred("EMAIL_PASSWORD", "email.imap.password")
        host = _env("EMAIL_IMAP_HOST")
        if not address or not password or not host:
            return _unconnected("imap", IMAP_NOT_CONNECTED, explicit)
        return _imap_backend(
            name="imap",
            address=address,
            password=password,
            imap_host=host,
            imap_port=int(_env("EMAIL_IMAP_PORT", "993") or "993"),
            smtp_host=_env("EMAIL_SMTP_HOST", host.replace("imap", "smtp")),
            smtp_port=int(_env("EMAIL_SMTP_PORT", "587") or "587"),
            smtp_starttls=_env("EMAIL_SMTP_SSL", "").lower() not in ("1", "true", "ssl"),
        )

    if name == "pop":
        address = cred("EMAIL_ADDRESS") or vault_retrieve("email.generic.address")
        password = cred("EMAIL_PASSWORD", "email.imap.password")
        host = _env("EMAIL_POP_HOST")
        if not address or not password or not host:
            return _unconnected("pop", POP_NOT_CONNECTED, explicit)
        return _pop_backend(
            name="pop",
            address=address,
            password=password,
            pop_host=host,
            pop_port=int(_env("EMAIL_POP_PORT", "995") or "995"),
            smtp_host=_env("EMAIL_SMTP_HOST", host.replace("pop", "smtp")),
            smtp_port=int(_env("EMAIL_SMTP_PORT", "587") or "587"),
            smtp_starttls=_env("EMAIL_SMTP_SSL", "").lower() not in ("1", "true", "ssl"),
        )

    if name == "microsoft":
        return _microsoft_backend(explicit)

    raise EmailNotConnectedError(name, f"Unknown email provider '{name}'.")


def mode_banner(backend: Any) -> str:
    return f"[{getattr(backend, 'name', 'unknown')} mode]"
