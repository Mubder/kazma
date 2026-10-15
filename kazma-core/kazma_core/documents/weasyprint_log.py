"""WeasyPrint's complaint about its own stylesheet, left out of Kazma's log.

WeasyPrint 70's built-in HTML stylesheet (``css/html5_ua.css``) uses the
pseudo-elements ``::note-call``, ``::note-marker`` and ``::note-callback``,
and WeasyPrint's own CSS parser does not support them: every process that
renders HTML logs three WARNINGs "Invalid or unsupported selector" about a
file Kazma does not ship and cannot change (seen on the live install,
2026-10-15). They are not about the document being rendered, and they made
every boot's warning count look like a fault.

Only those three selectors are filtered; any other selector WeasyPrint
refuses -- one in a stylesheet Kazma writes -- is still logged. Standard
library only: the renderer worker imports it.
"""

from __future__ import annotations

import logging

__all__ = ["quiet_own_stylesheet_warnings"]

#: The pseudo-elements of WeasyPrint's own stylesheet its parser refuses.
_OWN_SELECTORS = ("::note-call", "::note-marker", "::note-callback")


class _OwnStylesheetSelectors(logging.Filter):
    """Drops WeasyPrint's warning about a selector of its own stylesheet."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno != logging.WARNING:
            return True
        try:
            message = record.getMessage()
        except (TypeError, ValueError):
            return True
        if not message.startswith("Invalid or unsupported selector"):
            return True
        selector = message.split(",", 1)[-1].strip().strip("'").strip()
        return selector not in _OWN_SELECTORS


def quiet_own_stylesheet_warnings() -> None:
    """Install the filter on WeasyPrint's logger, once per process."""
    weasy = logging.getLogger("weasyprint")
    if not any(isinstance(f, _OwnStylesheetSelectors) for f in weasy.filters):
        weasy.addFilter(_OwnStylesheetSelectors())
