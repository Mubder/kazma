"""Calendar credential resolution — vault first, env as override.

The 2026-09-08 live incident: ``list_events(provider=google)`` returned
``Calendar: sandbox, No events found`` after a successful Gmail reconnect.
Root cause: the calendar router only read ``GOOGLE_CALENDAR_TOKEN`` from
the process env, and its ``_vault_get`` stub always returned ``""``.
Gmail OAuth stores ``email.gmail.*`` in the vault; Calendar never saw it.

This module is the single SoT for calendar tokens. It reuses the email
manager's vault helpers (global-tenant pin) so a Settings save and a
background refresh hit the same row.
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
VAULT_MS_CONNECTED_AT = "calendar.microsoft.connected_at"

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
            for n in names:
                try:
                    v.delete(n)
                except Exception:
                    pass
        finally:
            reset_current_tenant_id(token)
    except Exception as exc:
        logger.debug("[calendar.creds] vault delete: %s", exc)


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
    """Write Google Calendar tokens to env + vault (global tenant)."""
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
) -> None:
    if access:
        os.environ["MS_CALENDAR_TOKEN"] = access
        _vault_store(VAULT_MS_ACCESS, access)
    if refresh:
        os.environ["MS_CALENDAR_REFRESH"] = refresh
        _vault_store(VAULT_MS_REFRESH, refresh)
        _vault_store(VAULT_MS_CONNECTED_AT, str(int(time.time())))
    if scopes:
        _vault_store(VAULT_MS_SCOPES, scopes)


def clear_google_tokens() -> dict[str, Any]:
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
    return {"ok": True, "message": "Google Calendar tokens cleared."}


def clear_microsoft_calendar_tokens() -> dict[str, Any]:
    for k in ("MS_CALENDAR_TOKEN", "MS_CALENDAR_REFRESH", "MS_GRAPH_TOKEN"):
        os.environ.pop(k, None)
    _vault_delete(VAULT_MS_ACCESS, VAULT_MS_REFRESH, VAULT_MS_SCOPES, VAULT_MS_CONNECTED_AT)
    return {"ok": True, "message": "Outlook Calendar tokens cleared."}


def google_access_token() -> str:
    """Access token the Google Calendar backend should send.

    Order: calendar env/vault, then the Gmail grant *only if* that grant
    actually includes a Calendar scope (a gmail.modify token must never
    be sent to Calendar — that was the 'sandbox, no events' lie).
    """
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
    return {
        "google_connected": google_connected(),
        "google_address": _email_cred("GOOGLE_CALENDAR_ADDRESS", VAULT_GOOGLE_ADDRESS),
        "google_ok": google_ok,
        "google_oauth_client_set": gmail_client,
        "outlook_connected": microsoft_connected(),
        "outlook_oauth_client_set": ms_client,
        "active_provider": (
            "google"
            if google_connected()
            else "outlook"
            if microsoft_connected()
            else "sandbox"
        ),
    }
