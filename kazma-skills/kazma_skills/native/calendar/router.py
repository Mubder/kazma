"""Calendar backend router — vault-backed credential resolution.

Sandbox is the auto fallback when *no* real account is connected.
An explicit ``provider=google`` / ``provider=outlook`` NEVER silently
falls back to sandbox — that was the 2026-09-08 lie
(``Calendar: sandbox, No events found`` after Gmail reconnect).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_sandbox_instance = None

GOOGLE_NOT_CONNECTED = (
    "Google Calendar is not connected. Settings → Email → Connect with Google "
    "(Calendar scope is included) or Connect Google Calendar. Enable the Google "
    "Calendar API in the Cloud project. Gmail-only tokens cannot list events."
)
OUTLOOK_NOT_CONNECTED = (
    "Outlook Calendar is not connected. Settings → Email → Connect with "
    "Microsoft or Connect Outlook Calendar (Calendars.ReadWrite). A mail-only "
    "Graph token cannot list calendar events."
)


class CalendarNotConnectedError(RuntimeError):
    """Raised when an explicit provider is requested but has no credentials."""

    def __init__(self, provider: str, hint: str) -> None:
        super().__init__(hint)
        self.provider = provider
        self.hint = hint


def detect_available_provider() -> str:
    """Return first credentialed provider, else sandbox."""
    from kazma_skills.native.calendar.credentials import (
        google_connected,
        microsoft_connected,
    )

    if google_connected():
        return "google"
    if microsoft_connected():
        return "outlook"
    return "sandbox"


def resolve_provider(provider: str | None = None) -> str:
    import os

    p = (provider or os.getenv("KAZMA_CALENDAR_PROVIDER", "auto") or "auto").strip().lower()
    if p in ("", "auto"):
        return detect_available_provider()
    if p in ("microsoft", "microsoft_graph", "ms", "graph"):
        return "outlook"
    return p


def _account_backend(account: str) -> Any:
    """The calendar of the mail account chat named (``account=``: a name, an
    address, or a main account's word): the main Google or Outlook calendar,
    or an extra account's own (its sign-in's grant, kept under its name)."""
    from kazma_skills.native.email_manager.accounts import account_calendar, resolve_account

    try:
        target = resolve_account(account)
    except LookupError as exc:
        raise CalendarNotConnectedError(account, str(exc)) from None
    if target == "gmail":
        return get_backend("google")
    if target == "microsoft":
        return get_backend("outlook")
    if not target.startswith("account:"):
        raise CalendarNotConnectedError(
            account, f"{account} is a mailbox with no calendar Kazma can use."
        )
    alias = target.split(":", 1)[1]
    try:
        cal = account_calendar(alias)
    except LookupError as exc:
        raise CalendarNotConnectedError(alias, str(exc)) from None
    if cal["kind"] == "gmail":
        from kazma_skills.native.calendar.backends.google_calendar import GoogleCalendarBackend

        return GoogleCalendarBackend(
            cal["access"] or "pending_refresh", refresh_token=cal["refresh"], account_alias=alias
        )
    import os

    from kazma_skills.native.calendar.backends.outlook_calendar import OutlookCalendarBackend
    from kazma_skills.native.email_manager.credentials import cred

    return OutlookCalendarBackend(
        cal["access"] or "pending_refresh",
        refresh_token=cal["refresh"],
        client_id=cred("EMAIL_MS_CLIENT_ID", "email.microsoft.client_id"),
        client_secret=cred("EMAIL_MS_CLIENT_SECRET", "email.microsoft.client_secret"),
        tenant_id=(os.environ.get("EMAIL_MS_TENANT_ID") or "common").strip() or "common",
        account_alias=alias,
    )


def get_backend(provider: str | None = None, account: str | None = None) -> Any:
    """Return a calendar backend. Explicit providers fail closed.

    *account* (a mail account's name or address) picks that account's
    calendar and wins over *provider*.

    A provider named by ``KAZMA_CALENDAR_PROVIDER`` is as explicit as one
    passed in: judged from the argument alone, ``KAZMA_CALENDAR_PROVIDER=google``
    with no Google token quietly answered from the sandbox — the "Calendar:
    sandbox, No events found" shape of the 2026-09-08 incident (AGENTS.md §34).
    """
    import os

    if account and str(account).strip():
        return _account_backend(str(account).strip())
    requested = (
        provider or os.getenv("KAZMA_CALENDAR_PROVIDER", "auto") or "auto"
    ).strip().lower()
    explicit = requested not in ("", "auto")
    name = resolve_provider(provider)

    if name == "google":
        from kazma_skills.native.calendar.credentials import (
            google_access_token,
            google_refresh_token,
        )

        token = google_access_token()
        refresh = google_refresh_token()
        if token or refresh:
            from kazma_skills.native.calendar.backends.google_calendar import (
                GoogleCalendarBackend,
            )

            return GoogleCalendarBackend(
                token or "pending_refresh", refresh_token=refresh
            )
        logger.info("[calendar] google requested but no token")
        if explicit:
            raise CalendarNotConnectedError("google", GOOGLE_NOT_CONNECTED)
        name = "sandbox"

    if name == "outlook":
        from kazma_skills.native.calendar.credentials import (
            microsoft_access_token,
            microsoft_refresh_token,
        )
        from kazma_skills.native.email_manager.credentials import cred

        token = microsoft_access_token()
        refresh = microsoft_refresh_token()
        if token or refresh:
            from kazma_skills.native.calendar.backends.outlook_calendar import (
                OutlookCalendarBackend,
            )

            return OutlookCalendarBackend(
                token or "pending_refresh",
                refresh_token=refresh,
                client_id=cred("EMAIL_MS_CLIENT_ID", "email.microsoft.client_id"),
                client_secret=cred("EMAIL_MS_CLIENT_SECRET", "email.microsoft.client_secret"),
                tenant_id=cred("EMAIL_MS_TENANT_ID", "") or "common",
            )
        logger.info("[calendar] outlook requested but no token")
        if explicit:
            raise CalendarNotConnectedError("outlook", OUTLOOK_NOT_CONNECTED)
        name = "sandbox"

    from kazma_skills.native.calendar.backends.sandbox import SandboxBackend

    global _sandbox_instance
    if _sandbox_instance is None:
        _sandbox_instance = SandboxBackend()
    return _sandbox_instance
