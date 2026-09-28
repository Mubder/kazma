"""Calendar credential resolution — vault first, env as override.

The 2026-09-08 live incident: ``list_events(provider=google)`` returned
``Calendar: sandbox, No events found`` after a successful Gmail reconnect.
Root cause: the calendar router only read ``GOOGLE_CALENDAR_TOKEN`` from
the process env, and its ``_vault_get`` stub always returned ``""``.
Gmail OAuth stores ``email.gmail.*`` in the vault; Calendar never saw it.

This module is the single SoT for calendar tokens. It reuses the email
manager's vault helpers (global-tenant pin) so a Settings save and a
background refresh hit the same row.

A calendar the owner disconnects stays off (``calendar.<provider>.off``)
until it is connected again from the calendar card. The mail sign-in covers
Calendar too -- Google and Microsoft grant it with the mailbox -- so deleting
the calendar's own tokens was not enough: the calendar kept reading through
the mail grant, and the next mail refresh wrote a fresh copy back. Until
2026-09-28 "Disconnect Calendar" changed nothing while Gmail was connected.
While a calendar is off nothing reads its tokens and nothing writes them.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

VAULT_GOOGLE_ACCESS = "calendar.google.access_token"
VAULT_GOOGLE_REFRESH = "calendar.google.refresh_token"
VAULT_GOOGLE_SCOPES = "calendar.google.scopes"
VAULT_GOOGLE_ADDRESS = "calendar.google.address"
VAULT_GOOGLE_CONNECTED_AT = "calendar.google.connected_at"
VAULT_GOOGLE_OK = "calendar.google.ok"

VAULT_MS_ACCESS = "calendar.microsoft.access_token"
VAULT_MS_REFRESH = "calendar.microsoft.refresh_token"
VAULT_MS_SCOPES = "calendar.microsoft.scopes"
VAULT_MS_ADDRESS = "calendar.microsoft.address"
VAULT_MS_CONNECTED_AT = "calendar.microsoft.connected_at"

#: When the owner disconnected the calendar (epoch seconds); absent = on.
VAULT_GOOGLE_OFF = "calendar.google.off"
VAULT_MS_OFF = "calendar.microsoft.off"
_OFF_KEYS = {"google": VAULT_GOOGLE_OFF, "microsoft": VAULT_MS_OFF}

CALENDAR_SCOPE_MARKERS = (
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
    "calendar.events",
    "calendar.readonly",
)

MS_CALENDAR_SCOPE_MARKERS = (
    "calendars.read",
    "calendars.readwrite",
    "calendars.read.shared",
)


def _email_cred(env_key: str, vault_key: str = "") -> str:
    from kazma_skills.native.email_manager.credentials import cred

    return cred(env_key, vault_key)


def _vault_get(name: str) -> str:
    from kazma_skills.native.email_manager.credentials import vault_retrieve

    return vault_retrieve(name)


def _vault_store(name: str, value: str, category: str = "calendar") -> bool:
    from kazma_skills.native.email_manager.credentials import vault_store

    return vault_store(name, value, category=category)


def _vault_delete(*names: str) -> None:
    try:
        from kazma_core.paths import vault_db_path
        from kazma_core.security.vault import SecretVault, get_vault
        from kazma_core.tenant_context import reset_current_tenant_id, set_current_tenant_id

        v = get_vault()
        if v is None:
            v = SecretVault(db_path=vault_db_path())
        token = set_current_tenant_id(None)
        try:
            # A missing name is not an error (delete returns False); what
            # raises is a vault that cannot write, which the caller must hear
            # of -- a disconnect that silently kept the tokens used to pass.
            for n in names:
                v.delete(n)
        finally:
            reset_current_tenant_id(token)
    except Exception:
        logger.warning("[calendar.creds] vault delete of %s failed", ", ".join(names), exc_info=True)


def calendar_off(provider: str) -> bool:
    """True when the owner disconnected this calendar (``google`` or
    ``microsoft``) and has not connected it again."""
    return bool(_vault_get(_OFF_KEYS[provider]))


def turn_calendar_on(provider: str) -> None:
    """Lift the owner's disconnect: called by a sign-in started from the
    calendar card, before it stores the calendar's tokens. Raises when the
    mark stays, so the sign-in reports a failure instead of a connection the
    card would then show as off."""
    _vault_delete(_OFF_KEYS[provider])
    if calendar_off(provider):
        raise RuntimeError(f"could not turn the {provider} calendar back on")


def scopes_include_google_calendar(scope_str: str) -> bool:
    s = (scope_str or "").lower().replace("%2f", "/")
    parts = {p.strip() for p in s.replace(",", " ").split() if p.strip()}
    return any(m in s or m in parts for m in CALENDAR_SCOPE_MARKERS)


def scopes_include_ms_calendar(scope_str: str) -> bool:
    s = (scope_str or "").lower()
    return any(m in s for m in MS_CALENDAR_SCOPE_MARKERS)


def persist_google_tokens(
    access: str,
    refresh: str = "",
    email: str = "",
    scopes: str = "",
    *,
    probe_ok: str = "",
) -> None:
    """Write Google Calendar tokens to env + vault (global tenant).

    Nothing while the owner has the calendar off: the Gmail sign-in and the
    Gmail refresh both copy their grant here, and each copy used to undo a
    disconnect.
    """
    if calendar_off("google"):
        logger.debug("[calendar.creds] Google Calendar is off; tokens not kept")
        return
    if access:
        os.environ["GOOGLE_CALENDAR_TOKEN"] = access
        _vault_store(VAULT_GOOGLE_ACCESS, access)
    if refresh:
        os.environ["GOOGLE_CALENDAR_REFRESH"] = refresh
        _vault_store(VAULT_GOOGLE_REFRESH, refresh)
        _vault_store(VAULT_GOOGLE_CONNECTED_AT, str(int(time.time())))
    if email:
        os.environ["GOOGLE_CALENDAR_ADDRESS"] = email
        _vault_store(VAULT_GOOGLE_ADDRESS, email)
    if scopes:
        os.environ["GOOGLE_CALENDAR_SCOPES"] = scopes
        _vault_store(VAULT_GOOGLE_SCOPES, scopes)
    if probe_ok:
        _vault_store(VAULT_GOOGLE_OK, probe_ok)


def persist_microsoft_tokens(
    access: str,
    refresh: str = "",
    scopes: str = "",
    address: str = "",
) -> None:
    """Write Outlook Calendar tokens to env + vault (global tenant); nothing
    while the owner has the calendar off (every Microsoft mail sign-in
    copies its grant here). *address* is the account's, from its sign-in."""
    if calendar_off("microsoft"):
        logger.debug("[calendar.creds] Outlook Calendar is off; tokens not kept")
        return
    if access:
        os.environ["MS_CALENDAR_TOKEN"] = access
        _vault_store(VAULT_MS_ACCESS, access)
    if refresh:
        os.environ["MS_CALENDAR_REFRESH"] = refresh
        _vault_store(VAULT_MS_REFRESH, refresh)
        _vault_store(VAULT_MS_CONNECTED_AT, str(int(time.time())))
    if scopes:
        _vault_store(VAULT_MS_SCOPES, scopes)
    if address:
        _vault_store(VAULT_MS_ADDRESS, address)


def _turn_calendar_off(provider: str) -> bool:
    """Record the owner's disconnect; False when the vault would not keep it."""
    return _vault_store(_OFF_KEYS[provider], str(int(time.time()))) and calendar_off(provider)


def clear_google_tokens() -> dict[str, Any]:
    """Disconnect Google Calendar: off first, so no concurrent Gmail refresh
    can copy its grant back in between, then the calendar's own tokens.
    Gmail stays connected."""
    if not _turn_calendar_off("google"):
        return {"ok": False, "error": "Could not record the disconnect; the vault did not keep it."}
    for k in (
        "GOOGLE_CALENDAR_TOKEN",
        "GOOGLE_CALENDAR_REFRESH",
        "GOOGLE_CALENDAR_SCOPES",
        "GOOGLE_CALENDAR_ADDRESS",
        "GOOGLE_OAUTH_TOKEN",
    ):
        os.environ.pop(k, None)
    _vault_delete(
        VAULT_GOOGLE_ACCESS,
        VAULT_GOOGLE_REFRESH,
        VAULT_GOOGLE_SCOPES,
        VAULT_GOOGLE_ADDRESS,
        VAULT_GOOGLE_CONNECTED_AT,
        VAULT_GOOGLE_OK,
    )
    return {"ok": True, "message": "Google Calendar disconnected. Gmail stays connected."}


def clear_microsoft_calendar_tokens() -> dict[str, Any]:
    """Disconnect Outlook Calendar the same way; Microsoft mail stays."""
    if not _turn_calendar_off("microsoft"):
        return {"ok": False, "error": "Could not record the disconnect; the vault did not keep it."}
    for k in ("MS_CALENDAR_TOKEN", "MS_CALENDAR_REFRESH", "MS_GRAPH_TOKEN"):
        os.environ.pop(k, None)
    _vault_delete(
        VAULT_MS_ACCESS, VAULT_MS_REFRESH, VAULT_MS_SCOPES, VAULT_MS_ADDRESS, VAULT_MS_CONNECTED_AT
    )
    return {"ok": True, "message": "Outlook Calendar disconnected. Microsoft mail stays connected."}


def google_access_token() -> str:
    """Access token the Google Calendar backend should send.

    Order: calendar env/vault, then the Gmail grant *only if* that grant
    actually includes a Calendar scope (a gmail.modify token must never
    be sent to Calendar — that was the 'sandbox, no events' lie). Nothing
    while the owner has the calendar off.
    """
    if calendar_off("google"):
        return ""
    tok = _email_cred("GOOGLE_CALENDAR_TOKEN", VAULT_GOOGLE_ACCESS) or _email_cred(
        "GOOGLE_OAUTH_TOKEN", VAULT_GOOGLE_ACCESS
    )
    if tok:
        return tok
    gmail_scopes = _email_cred("EMAIL_GMAIL_SCOPES", "email.gmail.scopes")
    if scopes_include_google_calendar(gmail_scopes):
        return _email_cred("EMAIL_GMAIL_ACCESS_TOKEN", "email.gmail.access_token")
    return ""


def google_refresh_token() -> str:
    if calendar_off("google"):
        return ""
    tok = _email_cred("GOOGLE_CALENDAR_REFRESH", VAULT_GOOGLE_REFRESH)
    if tok:
        return tok
    gmail_scopes = _email_cred("EMAIL_GMAIL_SCOPES", "email.gmail.scopes")
    if scopes_include_google_calendar(gmail_scopes):
        return _email_cred("EMAIL_GMAIL_REFRESH_TOKEN", "email.gmail.refresh_token")
    return ""


def google_connected() -> bool:
    return bool(google_access_token() or google_refresh_token())


def microsoft_access_token() -> str:
    if calendar_off("microsoft"):
        return ""
    tok = (
        _email_cred("MS_CALENDAR_TOKEN", VAULT_MS_ACCESS)
        or _email_cred("MS_GRAPH_TOKEN", VAULT_MS_ACCESS)
    )
    if tok:
        return tok
    ms_scopes = _email_cred("", VAULT_MS_SCOPES) or _email_cred(
        "", "email.microsoft.scopes"
    )
    if scopes_include_ms_calendar(ms_scopes):
        return _email_cred("EMAIL_MS_ACCESS_TOKEN", "email.microsoft.access_token")
    return ""


def microsoft_refresh_token() -> str:
    if calendar_off("microsoft"):
        return ""
    tok = _email_cred("MS_CALENDAR_REFRESH", VAULT_MS_REFRESH)
    if tok:
        return tok
    ms_scopes = _email_cred("", VAULT_MS_SCOPES) or _email_cred(
        "", "email.microsoft.scopes"
    )
    if scopes_include_ms_calendar(ms_scopes):
        return _email_cred("EMAIL_MS_REFRESH_TOKEN", "email.microsoft.refresh_token")
    return ""


def microsoft_connected() -> bool:
    return bool(microsoft_access_token() or microsoft_refresh_token())


def status_summary() -> dict[str, Any]:
    """Non-secret status for Settings / API."""
    gmail_client = bool(
        _email_cred("EMAIL_GMAIL_CLIENT_ID", "email.gmail.client_id")
        or _email_cred("GOOGLE_OAUTH_CLIENT_ID", "email.gmail.client_id")
    )
    ms_client = bool(_email_cred("EMAIL_MS_CLIENT_ID", "email.microsoft.client_id"))
    google_ok = _vault_get(VAULT_GOOGLE_OK)
    google_on = google_connected()
    outlook_on = microsoft_connected()
    return {
        "google_connected": google_on,
        "google_address": (
            _email_cred("GOOGLE_CALENDAR_ADDRESS", VAULT_GOOGLE_ADDRESS) if google_on else ""
        ),
        "google_ok": google_ok,
        "google_oauth_client_set": gmail_client,
        "outlook_connected": outlook_on,
        # Calendars connected before the calendar kept its own address read
        # through the mail grant, so the mail sign-in's address stands in.
        "outlook_address": (
            (_vault_get(VAULT_MS_ADDRESS) or _vault_get("email.microsoft.oauth_address"))
            if outlook_on
            else ""
        ),
        "outlook_oauth_client_set": ms_client,
        "active_provider": "google" if google_on else "outlook" if outlook_on else "sandbox",
    }
