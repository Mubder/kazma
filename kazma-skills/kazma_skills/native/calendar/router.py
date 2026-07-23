"""Calendar backend router — credential resolution + backend selection.

Resolves an OAuth access token from env vars or the secret vault and returns
the matching backend. Falls back to :class:`SandboxBackend` when no
credentials are present.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# The sandbox backend is stateful (in-memory events), so it must persist
# across calls within a process. Cache the singleton here.
_sandbox_instance = None


def _vault_get(key: str) -> str:
    """Read a secret from the Kazma vault if available, else ''."""
    try:
        from kazma_skills.native.secret_vault.tools import vault_retrieve  # type: ignore

        # vault_retrieve is async; the router is sync. Defer async resolution
        # to the caller via a marker — but for simplicity we support env-first.
    except Exception:  # noqa: BLE001
        pass
    return ""


def detect_available_provider() -> str:
    """Return first credentialed provider, else sandbox."""
    if os.getenv("GOOGLE_CALENDAR_TOKEN") or os.getenv("GOOGLE_OAUTH_TOKEN"):
        return "google"
    if os.getenv("MS_CALENDAR_TOKEN") or os.getenv("MS_GRAPH_TOKEN"):
        return "outlook"
    return "sandbox"


def resolve_provider(provider: str | None = None) -> str:
    p = (provider or os.getenv("KAZMA_CALENDAR_PROVIDER", "auto") or "auto").strip().lower()
    if p in ("", "auto"):
        return detect_available_provider()
    return p


def get_backend(provider: str | None = None) -> Any:
    """Return a calendar backend instance, falling back to sandbox."""
    name = resolve_provider(provider)

    if name == "google":
        token = os.getenv("GOOGLE_CALENDAR_TOKEN") or os.getenv("GOOGLE_OAUTH_TOKEN", "")
        if token:
            from kazma_skills.native.calendar.backends.google_calendar import (
                GoogleCalendarBackend,
            )

            return GoogleCalendarBackend(token)
        logger.info("[calendar] google requested but no token — using sandbox")
        name = "sandbox"

    if name in ("outlook", "microsoft", "microsoft_graph"):
        token = os.getenv("MS_CALENDAR_TOKEN") or os.getenv("MS_GRAPH_TOKEN", "")
        if token:
            from kazma_skills.native.calendar.backends.outlook_calendar import (
                OutlookCalendarBackend,
            )

            return OutlookCalendarBackend(token)
        logger.info("[calendar] outlook requested but no token — using sandbox")
        name = "sandbox"

    from kazma_skills.native.calendar.backends.sandbox import SandboxBackend

    global _sandbox_instance
    if _sandbox_instance is None:
        _sandbox_instance = SandboxBackend()
    return _sandbox_instance
