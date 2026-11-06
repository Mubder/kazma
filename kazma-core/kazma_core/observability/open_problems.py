"""One list of open problems (resilience plan G2).

Open alerts lived in Telegram's history, the bell and the daily digest; the
owner could not see at a glance what was still wrong. This is the one list,
built each time from where each problem lives -- so a problem that is
resolved leaves by itself:

* the bell's open alerts (``AlertDispatcher``: a model fallback, a broken
  service ... each closes when its subsystem recovers);
* open run incidents (``runs.incidents``);
* the acquisition monitor's open research and crawl alerts;
* unmet install requirements (``install_requirements``);
* open dependency advisories (``security.advisory_watch``);
* the outside observer's open incident (``observability.outside_watch``).

Each problem says when it is resolved, and can be acknowledged (hidden until
it changes) or snoozed for 24 hours; those choices are kept under
``system.problems.seen`` (Kazma's own state). The Dashboard's card
(``GET /api/problems``) and the daily digest read this same list.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import time
from dataclasses import asdict, dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["SEEN_KEY", "SNOOZE_S", "Problem", "acknowledge", "open_problems", "snooze"]

#: Where acknowledgements and snoozes are kept: {id: {"until": epoch|0, "by", "at"}}.
SEEN_KEY = "system.problems.seen"
SNOOZE_S = 24 * 3600.0
_KEEP_SEEN = 500


@dataclass
class Problem:
    id: str
    source: str  # alert | incident | acquisition | requirements | advisory | observer
    title: str
    detail: str
    since: float | None
    link: str = ""
    severity: str = "warn"
    #: What fixes it (a command, a version), shown as written; "" when the
    #: source says it closes by itself. When it is resolved is the page's
    #: sentence for its source (``problems.resolved.<source>``).
    fix: str = ""


def _pid(*parts: Any) -> str:
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def _bell(source: str, row: Any) -> Problem | None:
    """A bell row (``AlertPayload`` or its dict: id, title, reason,
    callback_id ``link:/...``, timestamp, severity) as a problem."""
    data = row.to_dict() if hasattr(row, "to_dict") else row
    if not isinstance(data, dict):
        return None
    callback = str(data.get("callback_id") or "")
    severity = "critical" if str(data.get("severity") or "").upper() in ("ERROR", "CRITICAL") else "warn"
    return Problem(
        _pid(source, data.get("id") or data.get("subsystem"), data.get("title")), source,
        str(data.get("title") or data.get("subsystem") or ""), str(data.get("reason") or ""),
        data.get("timestamp"), callback[5:] if callback.startswith("link:") else "", severity)


def _alerts(language: str) -> list[Problem]:
    from kazma_core.observability.alerts import AlertDispatcher

    return [p for p in (_bell("alert", a) for a in AlertDispatcher.get_recent_alerts()) if p]


def _incidents(language: str) -> list[Problem]:
    from kazma_core.runs.notify import bell_alerts

    return [p for p in (_bell("incident", r) for r in bell_alerts(language)) if p]


def _acquisition(language: str) -> list[Problem]:
    from kazma_core.web_acquire.monitor import recent_alerts

    return [p for p in (_bell("acquisition", r) for r in recent_alerts(language)) if p]


def _requirements(language: str) -> list[Problem]:
    from kazma_core.install_requirements import last_requirements_check

    check = last_requirements_check() or {}
    unmet = check.get("unmet") or []
    if not unmet:
        return []
    names = ", ".join(str(u.get("requirement") if isinstance(u, dict) else u) for u in unmet[:8])
    return [Problem(_pid("requirements", names), "requirements", names, "", check.get("at"),
                    "/settings?tab=packages", fix="kazma update --reinstall -y")]


def _advisories(language: str) -> list[Problem]:
    from kazma_core.security.advisory_watch import open_advisories

    out = []
    for adv in open_advisories() or []:
        if not isinstance(adv, dict):
            continue
        ident = adv.get("id") or adv.get("advisory")
        out.append(Problem(
            _pid("advisory", ident), "advisory", f"{adv.get('package')}: {ident}",
            str(adv.get("summary") or ""), adv.get("first_seen") or adv.get("at"),
            "/settings?tab=packages", "warn", fix=str(adv.get("fixed") or "")))
    return out


def _observer(language: str) -> list[Problem]:
    from kazma_core.observability.outside_watch import outside_status

    observer = (outside_status().get("observer") or {})
    if not observer.get("ok"):
        return []
    open_ones = [i for i in observer.get("incidents_7d") or [] if isinstance(i, dict) and not i.get("ended_at")]
    return [Problem(_pid("observer", i.get("id") or i.get("started_at")), "observer",
                    str(i.get("title") or i.get("condition") or ""), str(i.get("detail") or ""),
                    i.get("started_at"), "/settings?tab=system", "critical")
            for i in open_ones]


_SOURCES = (_alerts, _incidents, _acquisition, _requirements, _advisories, _observer)


def _seen() -> dict[str, dict[str, Any]]:
    from kazma_core.config_store import get_config_store

    value = get_config_store().get(SEEN_KEY) or {}
    return value if isinstance(value, dict) else {}


def open_problems(language: str = "en", *, now: float | None = None, include_hidden: bool = False,
                  sources: tuple | None = None) -> dict[str, Any]:
    """``{"problems": [...], "hidden": n, "unreadable": [source...]}``: every open
    problem, newest first, without the acknowledged and snoozed ones (unless
    *include_hidden*). A source that cannot be read is named, never "none".
    Blocking (stores, files, the observer): call it from a thread."""
    now = time.time() if now is None else now
    sources = _SOURCES if sources is None else sources
    found: list[Problem] = []
    unreadable: list[str] = []
    for source in sources:
        try:
            found.extend(source(language))
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, ImportError, sqlite3.Error) as exc:
            unreadable.append(source.__name__.lstrip("_"))
            logger.warning("[problems] %s could not be read: %s", source.__name__, exc)
    seen = _seen()
    shown, hidden = [], 0
    for problem in found:
        mark = seen.get(problem.id) or {}
        until = float(mark.get("until") or 0)
        hide = bool(mark) and (until == 0 or until > now)
        if hide and not include_hidden:
            hidden += 1
            continue
        row = asdict(problem)
        row["acknowledged"] = bool(mark) and until == 0
        row["snoozed_until"] = until if until > now else None
        shown.append(row)
    shown.sort(key=lambda r: -(float(r["since"] or 0)))
    return {"problems": shown, "hidden": hidden, "unreadable": unreadable}


def _mark(problem_id: str, until: float, actor: str) -> None:
    from kazma_core.config_store import get_config_store

    def change(current: Any) -> dict[str, Any]:
        seen = dict(current) if isinstance(current, dict) else {}
        seen[problem_id] = {"until": until, "by": actor, "at": time.time()}
        while len(seen) > _KEEP_SEEN:
            seen.pop(next(iter(seen)))
        return seen

    get_config_store().atomic_update(SEEN_KEY, change, category="system")


def acknowledge(problem_id: str, actor: str) -> None:
    """Hide *problem_id* until it changes (a changed problem has another id)."""
    _mark(problem_id, 0.0, actor)


def snooze(problem_id: str, actor: str, *, now: float | None = None) -> None:
    """Hide *problem_id* for 24 hours."""
    _mark(problem_id, (time.time() if now is None else now) + SNOOZE_S, actor)
