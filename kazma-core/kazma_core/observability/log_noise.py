"""Quiet logs: routine lines that repeat every minute stay out (resilience plan G1).

A live day (2026-11-05) wrote 32,090 lines, 31,990 of them INFO: 12,459
"Batch set" lines from the settings store and 11,200 access lines of the
pages' and the guard's polls (``/api/alerts/recent`` every ten seconds a
tab, ``/health/ready`` twice a minute). The 100 WARNING and ERROR lines that
mattered were buried. The settings store's batch line and the X mentions
poll are DEBUG now, and :class:`QuietPolls` keeps a polled route's answered
access lines out (an error status is always kept).
``tests/test_quiet_logs.py`` replays that recorded day against the code.
"""

from __future__ import annotations

import logging
import re

__all__ = ["POLLED_ROUTES", "NoTokensInPaths", "QuietPolls"]

#: Routes the pages and the guard poll on a timer (path patterns, query
#: ignored). Their 2xx/3xx access lines say nothing a day needs.
POLLED_ROUTES = (
    r"/api/alerts/recent",
    r"/api/pending-approvals",
    r"/api/notifications/turn-complete",
    r"/api/chat/sessions/[^/]+/status",
    r"/health/(?:live|ready|activity)",
)
_POLLED = re.compile(r"^(?:" + "|".join(POLLED_ROUTES) + r")$")


def _polled(path: str) -> bool:
    """Whether *path* (a request path, its query allowed) is a polled route."""
    return bool(_POLLED.match(str(path or "").split("?", 1)[0]))


class QuietPolls(logging.Filter):
    """On ``uvicorn.access``: drop the answered requests of polled routes.

    uvicorn logs ``(client, method, path, http_version, status)`` as the
    record's args; a record of any other shape passes untouched, and so does
    an error status (400 and up)."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) != 5:
            return True
        _client, _method, path, _version, status = args
        try:
            failed = int(status) >= 400
        except (TypeError, ValueError):
            return True
        return failed or not _polled(str(path))


#: A path whose rest is a one-use credential: an invitation link. Its links
#: are ``/invite#<token>`` (the token after the '#', which a browser never
#: sends); an old or hand-made ``/invite/<token>`` is cut from the access log
#: all the same (the review of S4-02, item 6).
_TOKEN_PATH = re.compile(r"^(/invite/)\S*")


class NoTokensInPaths(logging.Filter):
    """On ``uvicorn.access``: a path that carries a one-use token keeps only
    its route (``/invite/…``). Never drops a line."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            cut = _TOKEN_PATH.sub(r"\1…", args[2])
            if cut != args[2]:
                record.args = (args[0], args[1], cut, args[3], args[4])
        return True
