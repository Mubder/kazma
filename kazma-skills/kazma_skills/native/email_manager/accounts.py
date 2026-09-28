"""More mail accounts than one Gmail and one Microsoft (2026-09-29).

The main accounts are the ones on the Gmail and Microsoft cards in Settings
(``email.gmail.*`` / ``email.microsoft.*``). Every other mailbox is an
*extra account*: a short name the owner picks ("work"), a type (``gmail``,
``microsoft``, ``imap``, ``pop``) and an address. Chat names one by that name
or by its address (``account="work"`` / ``account="me@company.com"``); with no
account named, the main one of the provider answers, as before.

Where things live:

- the list (name, type, address, how it signs in, whether its sign-in covers
  a calendar) is the settings key ``email.accounts`` -- nothing secret;
- its tokens and password are in the vault under ``email.account.<name>.*``
  (``email.*`` is install-scoped, like every mail secret);
- accounts written into ``.env`` (``EMAIL_ACCOUNTS`` +
  ``EMAIL_ACCOUNT_<NAME>_*``) keep working and are listed as such; Settings
  cannot change them.

**Every token write names its account.** A sign-in or refresh of an extra
account writes that account's keys and nothing else. Until this module the
extra accounts shared the main ones' writers: refreshing a second Google or
Microsoft account wrote its tokens over the main account's, and from then on
"my inbox" read the other mailbox.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "ACCOUNT_TYPES",
    "account_calendar",
    "accounts_overview",
    "add_password_account",
    "alias_problem",
    "normalize_alias",
    "persist_account_tokens",
    "remove_account",
    "resolve_account",
    "stored_account",
    "stored_accounts",
    "upsert_oauth_account",
]

#: The settings key holding the list of extra accounts.
ACCOUNTS_KEY = "email.accounts"

#: What an extra account can be.
ACCOUNT_TYPES = ("gmail", "microsoft", "imap", "pop")

#: Names that already mean something where an account is named: providers and
#: the words chat uses for the main accounts.
RESERVED_NAMES = frozenset({
    "auto", "default", "main", "sandbox", "gmail", "google", "workspace",
    "microsoft", "microsoft_graph", "graph", "outlook", "msn", "hotmail", "live",
    "office365", "m365", "imap", "pop", "generic",
})

_ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_PASSWORD_FIELDS = ("imap_host", "imap_port", "pop_host", "pop_port", "smtp_host", "smtp_port")
_SECRET_FIELDS = ("access_token", "refresh_token", "password", "scopes")

#: Words that name a main account in ``account=``.
_MAIN_NAMES = {
    "gmail": "gmail", "google": "gmail", "workspace": "gmail",
    "microsoft": "microsoft", "outlook": "microsoft", "msn": "microsoft",
    "hotmail": "microsoft", "live": "microsoft", "office365": "microsoft",
    "m365": "microsoft", "microsoft_graph": "microsoft", "graph": "microsoft",
    "imap": "imap", "pop": "pop",
}


# ── names ────────────────────────────────────────────────────────────────


def normalize_alias(name: str) -> str:
    """The account name as stored: lower case, spaces and underscores as
    hyphens, anything else dropped."""
    raw = (name or "").strip().lower().replace(" ", "-").replace("_", "-")
    return re.sub(r"[^a-z0-9-]", "", raw).strip("-")[:32]


def _env_aliases() -> list[str]:
    from kazma_skills.native.email_manager.credentials import env_account_aliases

    return env_account_aliases()


def alias_problem(alias: str, *, kind: str) -> str | None:
    """Why *alias* cannot name a new *kind* account, or None. An existing
    account of the same kind may be signed in again under its name."""
    if kind not in ACCOUNT_TYPES:
        return f"Unknown account type {kind!r}."
    if not alias or not _ALIAS_RE.match(alias):
        return "Name the account with letters, digits and hyphens (for example: work)."
    if alias in RESERVED_NAMES:
        return f"“{alias}” already means the main account or a provider; pick another name."
    if alias in {a.lower() for a in _env_aliases()}:
        return f"“{alias}” is an account set in .env; pick another name."
    existing = stored_account(alias)
    if existing and existing.get("type") != kind:
        return f"“{alias}” is already a {existing.get('type')} account; pick another name."
    return None


# ── the stored list ──────────────────────────────────────────────────────


def _store():
    from kazma_core.config_store import get_config_store

    return get_config_store()


def stored_accounts() -> list[dict[str, Any]]:
    """The extra accounts added in Settings, as saved (a malformed row is
    skipped, never guessed)."""
    try:
        raw = _store().get(ACCOUNTS_KEY)
    except Exception:
        logger.warning("[email.accounts] the account list could not be read", exc_info=True)
        return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for row in raw:
        if (
            isinstance(row, dict)
            and _ALIAS_RE.match(str(row.get("alias") or ""))
            and row.get("type") in ACCOUNT_TYPES
        ):
            out.append(dict(row))
    return out


def stored_account(alias: str) -> dict[str, Any] | None:
    key = normalize_alias(alias)
    return next((a for a in stored_accounts() if a["alias"] == key), None)


def _save(rows: list[dict[str, Any]]) -> None:
    _store().set(ACCOUNTS_KEY, rows, category="email")


#: The list is read, changed and written back; two sign-ins finishing at once
#: must not lose one of them.
_LIST_LOCK = threading.Lock()


def _upsert(row: dict[str, Any]) -> dict[str, Any]:
    with _LIST_LOCK:
        rows = [a for a in stored_accounts() if a["alias"] != row["alias"]]
        rows.append(row)
        rows.sort(key=lambda a: a.get("added_at") or 0)
        _save(rows)
    return row


# ── secrets ──────────────────────────────────────────────────────────────


def _vault_key(alias: str, field: str) -> str:
    return f"email.account.{alias}.{field}"


def _vault_put(alias: str, field: str, value: str) -> None:
    from kazma_skills.native.email_manager.credentials import vault_store

    if value and not vault_store(_vault_key(alias, field), value, category="email"):
        raise RuntimeError(f"the vault did not keep {field} for account {alias}")


def account_secret(alias: str, field: str) -> str:
    from kazma_skills.native.email_manager.credentials import vault_retrieve

    return vault_retrieve(_vault_key(alias, field))


def _vault_drop(alias: str) -> None:
    from kazma_skills.native.email_manager.credentials import vault_delete

    vault_delete(*(_vault_key(alias, f) for f in _SECRET_FIELDS))


def persist_account_tokens(alias: str, access: str = "", refresh: str = "", scopes: str = "") -> None:
    """Keep a refreshed grant of ONE extra account: its own vault keys only,
    never the main account's (the bug this module ends)."""
    if access and access != "pending_refresh":
        _vault_put(alias, "access_token", access)
    if refresh:
        _vault_put(alias, "refresh_token", refresh)
    if scopes:
        _vault_put(alias, "scopes", scopes)


# ── who holds an address ─────────────────────────────────────────────────


def _main_addresses() -> dict[str, str]:
    """``{address: provider}`` of the main accounts that are connected."""
    from kazma_skills.native.email_manager.credentials import status_summary

    s = status_summary()
    out: dict[str, str] = {}
    if s.get("gmail_configured") and s.get("gmail_address"):
        out[str(s["gmail_address"]).lower()] = "gmail"
    if s.get("microsoft_configured") and s.get("microsoft_address"):
        out[str(s["microsoft_address"]).lower()] = "microsoft"
    from kazma_skills.native.email_manager.credentials import cred, vault_retrieve

    generic = cred("EMAIL_ADDRESS") or vault_retrieve("email.generic.address")
    if generic and (s.get("imap_configured") or s.get("pop_configured")):
        out.setdefault(generic.lower(), "imap" if s.get("imap_configured") else "pop")
    return out


def _address_owner(address: str) -> str | None:
    """Which account holds *address*: ``gmail`` / ``microsoft`` / ``imap`` /
    ``pop`` for a main account, ``account:<name>`` for an extra one."""
    addr = (address or "").strip().lower()
    if not addr:
        return None
    main = _main_addresses().get(addr)
    if main:
        return main
    for row in stored_accounts():
        if str(row.get("address") or "").lower() == addr:
            return f"account:{row['alias']}"
    from kazma_skills.native.email_manager.credentials import account_config

    for alias in _env_aliases():
        if str(account_config(alias).get("address") or "").lower() == addr:
            return f"account:{alias.lower()}"
    return None


def resolve_account(account: str) -> str:
    """``account=`` as chat gives it (a name, an address, or a main account's
    provider word) as the router's provider name: ``gmail``, ``microsoft``,
    ``imap``, ``pop`` or ``account:<name>``. Raises LookupError naming what is
    connected when nothing matches."""
    value = (account or "").strip()
    if "@" in value:
        owner = _address_owner(value)
        if owner:
            return owner
        raise LookupError(
            f"No connected mail account has the address {value}. "
            + _known_accounts_sentence()
        )
    key = normalize_alias(value)
    if key in _MAIN_NAMES:
        return _MAIN_NAMES[key]
    return f"account:{key or value.lower()}"


def _known_accounts_sentence() -> str:
    names = [f"{r['name']} ({r['address']})" if r.get("address") else r["name"]
             for r in accounts_overview()]
    return "Connected: " + (", ".join(names) if names else "none") + "."


# ── adding and removing ──────────────────────────────────────────────────


def _refuse_duplicate(alias: str, address: str) -> None:
    owner = _address_owner(address)
    if owner and owner != f"account:{alias}":
        where = owner.split(":", 1)[1] if owner.startswith("account:") else f"the main {owner} account"
        raise ValueError(f"{address} is already connected as {where}.")


def upsert_oauth_account(
    alias: str, kind: str, address: str, access: str, refresh: str, scopes: str
) -> dict[str, Any]:
    """Keep a signed-in extra account (a new one, or one signed in again)."""
    alias = normalize_alias(alias)
    problem = alias_problem(alias, kind=kind)
    if problem:
        raise ValueError(problem)
    if kind not in ("gmail", "microsoft"):
        raise ValueError(f"A {kind} account signs in with a password, not OAuth.")
    if not access and not refresh:
        raise ValueError("The sign-in returned no token.")
    if address:
        _refuse_duplicate(alias, address)
    previous = stored_account(alias) or {}
    persist_account_tokens(alias, access, refresh, scopes)
    row = {
        "alias": alias,
        "type": kind,
        "address": address or previous.get("address") or "",
        "auth": "oauth",
        "calendar": _scopes_cover_calendar(kind, scopes),
        "added_at": previous.get("added_at") or int(time.time()),
        "updated_at": int(time.time()),
    }
    _upsert(row)
    logger.info("[email.accounts] %s account %s signed in (%s)", kind, alias, address or "no address")
    return row


def add_password_account(alias: str, kind: str, address: str, password: str, **hosts: Any) -> dict[str, Any]:
    """Keep an extra account that signs in with a password (an app password
    for Gmail). The caller has already checked the login works."""
    alias = normalize_alias(alias)
    problem = alias_problem(alias, kind=kind)
    if problem:
        raise ValueError(problem)
    address = (address or "").strip()
    if "@" not in address:
        raise ValueError("Enter the account's email address.")
    if not (password or "").strip():
        raise ValueError("Enter the account's password (an app password for Gmail).")
    _refuse_duplicate(alias, address)
    previous = stored_account(alias) or {}
    _vault_put(alias, "password", password.strip())
    row = {
        "alias": alias,
        "type": kind,
        "address": address,
        "auth": "password",
        "calendar": False,
        "added_at": previous.get("added_at") or int(time.time()),
        "updated_at": int(time.time()),
    }
    for field in _PASSWORD_FIELDS:
        value = hosts.get(field)
        if value not in (None, ""):
            row[field] = str(value).strip()
    _upsert(row)
    logger.info("[email.accounts] %s account %s added with a password (%s)", kind, alias, address)
    return row


def remove_account(alias: str) -> dict[str, Any]:
    """Forget an extra account added in Settings: its tokens and password go,
    and chat can no longer name it. An account written in .env is refused."""
    key = normalize_alias(alias)
    if key in {a.lower() for a in _env_aliases()}:
        return {"ok": False, "error": f"“{key}” is set in .env; remove it there."}
    row = stored_account(key)
    if row is None:
        return {"ok": False, "error": f"No account named “{key}”."}
    with _LIST_LOCK:
        _save([a for a in stored_accounts() if a["alias"] != key])
    _vault_drop(key)
    logger.info("[email.accounts] account %s removed", key)
    return {"ok": True, "alias": key, "message": f"Account “{key}” removed."}


# ── what chat and Settings are shown ─────────────────────────────────────


def _scopes_cover_calendar(kind: str, scopes: str) -> bool:
    from kazma_skills.native.calendar.credentials import (
        scopes_include_google_calendar,
        scopes_include_ms_calendar,
    )

    if kind == "gmail":
        return scopes_include_google_calendar(scopes)
    if kind == "microsoft":
        return scopes_include_ms_calendar(scopes)
    return False


def accounts_overview() -> list[dict[str, Any]]:
    """Every mail account Kazma can use: the main ones first, then the extra
    ones. Nothing secret."""
    from kazma_skills.native.calendar.credentials import google_connected, microsoft_connected
    from kazma_skills.native.email_manager.credentials import account_config, status_summary

    s = status_summary()
    rows: list[dict[str, Any]] = []
    if s.get("gmail_configured"):
        rows.append({
            "name": "gmail", "alias": "gmail", "type": "gmail", "address": s.get("gmail_address") or "",
            "auth": "oauth" if s.get("gmail_oauth") else "password",
            "calendar": google_connected(), "source": "main", "removable": False,
        })
    if s.get("microsoft_configured"):
        rows.append({
            "name": "microsoft", "alias": "microsoft", "type": "microsoft",
            "address": s.get("microsoft_address") or "",
            "auth": "oauth" if s.get("microsoft_oauth") else "password",
            "calendar": microsoft_connected(), "source": "main", "removable": False,
        })
    for row in stored_accounts():
        has_secret = bool(
            account_secret(row["alias"], "refresh_token") or account_secret(row["alias"], "access_token")
            if row.get("auth") == "oauth"
            else account_secret(row["alias"], "password")
        )
        rows.append({
            "name": row["alias"], "alias": row["alias"], "type": row["type"],
            "address": row.get("address") or "",
            "auth": row.get("auth") if has_secret else "incomplete",
            "calendar": bool(row.get("calendar")) and has_secret,
            "source": "settings", "removable": True,
        })
    for alias in _env_aliases():
        cfg = account_config(alias)
        signed = bool(cfg.get("access_token") or cfg.get("refresh_token"))
        rows.append({
            "name": alias.lower(), "alias": alias.lower(), "type": (cfg.get("type") or "").lower(),
            "address": cfg.get("address") or "",
            "auth": "oauth" if signed else ("password" if cfg.get("password") else "incomplete"),
            "calendar": False, "source": "env", "removable": False,
        })
    return rows


def account_calendar(alias: str) -> dict[str, Any]:
    """The calendar of an extra account: ``{kind, access, refresh, alias}``.
    Raises LookupError when the account has none (a password account, or a
    sign-in that did not grant calendar access)."""
    from kazma_skills.native.email_manager.credentials import account_config

    key = normalize_alias(alias)
    cfg = account_config(key)
    kind = (cfg.get("type") or "").lower()
    if kind not in ("gmail", "microsoft"):
        raise LookupError(
            f"Account “{key}” is not connected." if not kind
            else f"Account “{key}” is a {kind} mailbox; it has no calendar Kazma can use."
        )
    access, refresh = cfg.get("access_token") or "", cfg.get("refresh_token") or ""
    if not (access or refresh):
        raise LookupError(f"Account “{key}” signs in with a password; its calendar needs a sign-in (Settings → Email → Other accounts).")
    if not _scopes_cover_calendar(kind, cfg.get("scopes") or ""):
        raise LookupError(
            f"Account “{key}” did not grant calendar access when it signed in. "
            "Settings → Email → Other accounts → Reconnect, and allow the calendar."
        )
    return {"kind": kind, "access": access, "refresh": refresh, "alias": key}
