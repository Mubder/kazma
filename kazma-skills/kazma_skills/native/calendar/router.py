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
    "(Calendar scope is included) or Connect Calendar. Enable the Google "
    "Calendar API in the Cloud project. Gmail-only tokens cannot list events."
)
OUTLOOK_NOT_CONNECTED = (
    "Outlook Calendar is not connected. Settings → Email → Connect with "
    "Microsoft (Calendars.ReadWrite is included). A mail-only Graph token "
    "cannot list calendar events — reconnect Microsoft after this update."
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


def get_backend(provider: str | None = None) -> Any:
    """Return a calendar backend. Explicit providers fail closed."""
    requested = (provider or "auto").strip().lower()
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
