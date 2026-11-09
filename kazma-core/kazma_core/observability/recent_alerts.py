"""The last days of alerts from every sender, for Settings -> Alerts.

The unified alerts plan, Stage 3, "Recent alerts": one list, newest first, of
what paged the owner, each with its sender, how bad it was, when, whether a
channel took it, and the page of this site it is about.

* Kazma's own alerts come from the application log the daily digest reads
  (``[ops_alert] <key> | <title>``, and the receipt line ``ops_alerts``
  writes when the delivery ends: ``[ops_alerts] receipt <key>: delivered``).
  A repeat the cooldown held back is counted on the alert before it.
* The guard's pages come from its own log (``notify.sent`` /
  ``notify.failed`` / ``notify.skipped``, which name the card's first line).
* Kazma Watch's come from its summary (``/v1/summary``): each incident of the
  week is a page when it opened (taken when ``paged_at`` is set) and, once it
  ended, a recovery note -- unless Kazma Watch held it (``held_by``: another
  sender paged it, the unified alerts plan's Stage 2).

An alert a sender held because another one paged the same problem (Kazma's
``[ops_alert_held]`` line, the guard's ``guard.page_held``) is not listed:
the list is what paged the owner, and the page that did is in it.

Severity, the Arabic title and the page come from the alert catalog where an
entry names the alert. Blocking (log files, a signed request): call it off
the loop.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["recent_alerts"]

DAYS = 7
MAX_ITEMS = 300

#: The card's first character says its severity (alert_card.ICONS; the
#: guard's info is orange).
_ICON_SEVERITY = {
    "\U0001f535": "info",
    "\U0001f7e0": "info",
    "\U0001f7e2": "success",
    "\U0001f7e1": "warn",
    "\U0001f534": "error",
    "\U0001f6a8": "critical",
}


def _entry(key: str) -> dict[str, Any]:
    from kazma_core.observability.alert_catalog import entry_for

    entry = entry_for(key)
    return entry.as_dict() if entry is not None else {}


def _item(source: str, key: str, title: str, at: float, *, severity: str = "",
          delivered: Any = None, link: str = "") -> dict[str, Any]:
    entry = _entry(key) if key else {}
    return {
        "source": source,
        "key": key,
        "title": title or str(entry.get("title") or key),
        "title_ar": str(entry.get("title_ar") or ""),
        "severity": severity or str(entry.get("severity") or "warn"),
        "at": at,
        "delivered": delivered,
        "held": 0,
        "link": link or str(entry.get("page") or "/dashboard"),
    }


def _kazma_alerts(since: float) -> list[dict[str, Any]]:
    """Kazma's alerts from the application log, with their receipts."""
    from kazma_core.observability.daily_digest import (
        _ALERT_MARKER,
        _THROTTLED,
        _app_line,
        _app_log_path,
        _records,
    )
    from kazma_core.observability.ops_alerts import RECEIPT_KEPT, RECEIPT_MARKER, RECEIPT_OUTCOMES

    words = {word: value for value, word in RECEIPT_OUTCOMES.items()}
    words[RECEIPT_KEPT] = "kept"
    lines = sorted(
        ((ts, str(rec.get("message", "")), str(rec.get("level", ""))) for ts, rec in
         _records(_app_log_path, since, _app_line, (_ALERT_MARKER, RECEIPT_MARKER))),
        key=lambda row: row[0],
    )
    items: list[dict[str, Any]] = []
    waiting: dict[str, list[dict[str, Any]]] = {}
    last: dict[str, dict[str, Any]] = {}
    for ts, msg, level in lines:
        if msg.startswith(RECEIPT_MARKER):
            key, _sep, outcome = msg[len(RECEIPT_MARKER):].rpartition(": ")
            queue = waiting.get(key.strip())
            if queue and outcome.strip() in words:
                queue.pop(0)["delivered"] = words[outcome.strip()]
            continue
        if not msg.startswith(_ALERT_MARKER):
            continue
        key, _sep, title = msg[len(_ALERT_MARKER):].partition(" | ")
        key = key.strip()
        if title.endswith(_THROTTLED):
            if key in last:
                last[key]["held"] += 1
            continue
        item = _item("kazma", key, title.strip(), ts)
        if not _entry(key) and level.upper() == "INFO":
            item["severity"] = "info"
        items.append(item)
        waiting.setdefault(key, []).append(item)
        last[key] = item
    return items


def _guard_pages(since: float) -> list[dict[str, Any]]:
    """The guard's pages from its log (the card's first line, ``head``)."""
    from kazma_core.observability.alert_catalog import CATALOG
    from kazma_core.observability.daily_digest import _guard_line, _guard_log_path, _records

    titles = {e.title: e for e in CATALOG if e.sender == "guard"}
    items: list[dict[str, Any]] = []
    events = {"notify.sent": True, "notify.failed": False, "notify.skipped": False}
    for ts, rec in _records(_guard_log_path, since, _guard_line, tuple(f'"{e}"' for e in events)):
        event = str(rec.get("event") or "")
        if event not in events:
            continue
        head = str(rec.get("head") or str(rec.get("text") or "").split("\n", 1)[0]).strip()
        if not head or "] " not in head:
            continue  # a line from before the guard named its pages
        icon, _sep, rest = head.partition(" ")
        title = rest.partition("] ")[2].strip()
        entry = titles.get(title)
        items.append({
            "source": "guard",
            "key": entry.key if entry else "",
            "title": title,
            "title_ar": entry.title_ar if entry else "",
            "severity": _ICON_SEVERITY.get(icon, "warn"),
            "at": ts,
            "delivered": events[event],
            "held": 0,
            "link": entry.page if entry else "/dashboard",
        })
    return items


def _taken(incident: dict[str, Any], field: str) -> bool | None:
    """Whether a channel took the page (*field* set), or None from an
    observer that does not say (one older than the field)."""
    return incident.get(field) is not None if field in incident else None


def _watch_alerts(summary: dict[str, Any], since: float) -> list[dict[str, Any]]:
    """Kazma Watch's pages: each incident's page, and its recovery note.

    An incident Kazma Watch held (``held_by``: Kazma or its guard paged it,
    or a Cloudflare notice did; one page per problem) and never paged is not
    one of its pages: the page that was sent is listed by its own sender, and
    no recovery of Kazma Watch's was due."""
    items: list[dict[str, Any]] = []
    for incident in summary.get("incidents_7d") or []:
        if not isinstance(incident, dict):
            continue
        if incident.get("held_by") and incident.get("paged_at") is None:
            continue
        cause = str(incident.get("cause") or "")
        started = incident.get("started_at")
        if isinstance(started, (int, float)) and started >= since:
            items.append(_item("watch", f"watch.{cause}", "", float(started),
                               delivered=_taken(incident, "paged_at")))
        ended = incident.get("ended_at")
        if isinstance(ended, (int, float)) and ended >= since:
            items.append(_item("watch", "watch.recovered", "", float(ended), severity="success",
                               delivered=_taken(incident, "recovery_paged_at")))
    return items


def recent_alerts(*, days: int = DAYS, now: float | None = None, watch_summary: dict | None = None,
                  ) -> dict[str, Any]:
    """``{"items": [...], "errors": [...]}``: the alerts of the last *days*,
    newest first. ``errors`` names a source that could not be read (``kazma``,
    ``guard``, ``watch``), never as no alerts. *watch_summary* is Kazma
    Watch's summary when the caller has it (else read here, if paired)."""
    now = time.time() if now is None else now
    since = now - days * 86400
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    for name, read in (("kazma", _kazma_alerts), ("guard", _guard_pages)):
        try:
            items.extend(read(since))
        except (OSError, ValueError, KeyError, TypeError, ImportError) as exc:
            logger.warning("[alerts] recent %s alerts could not be read: %s", name, type(exc).__name__)
            errors.append(name)
    summary = watch_summary
    if summary is None:
        from kazma_core.observability.outside_watch import outside_status

        status = outside_status()
        summary = (status.get("observer") or {}) if status.get("paired") else {}
    if summary.get("ok"):
        items.extend(_watch_alerts(summary, since))
    elif summary:
        errors.append("watch")
    items.sort(key=lambda item: item["at"], reverse=True)
    return {"items": items[:MAX_ITEMS], "errors": errors, "days": days}
