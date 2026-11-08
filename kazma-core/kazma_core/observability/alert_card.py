"""One professional operator-card layout for Guard, Ops, System and Watch.

Guard cannot import this module (stdlib-only supervisor), and Kazma Watch
runs on Cloudflare. Keep the layout identical to ``format_operator_card`` in
``kazma_guard.py`` and ``formatOperatorCard`` in
``deploy/observer/src/card.js``: ``tests/fixtures/alert_card/cards.json``
holds the exact text of each form, and all three are checked against it
(``tests/test_alert_catalog.py``, the observer's own Node tests).

Three forms (the unified alerts plan, "One card")::

    🔴 [Watch] Kazma cannot be reached
    The tunnel answers, the machine does not.
    Since 14:02 UTC (12 min) · Open: https://my.kazma.ai/dashboard

    🔴 ⏰ Still: [Watch] Kazma cannot be reached        (a reminder)

    🟢 [Watch] Kazma is back: it lasted 12 min.         (a recovery)

The last line is there only when ``since`` or ``link`` is given; a recovery
says how long it lasted in its first line and shows no "Since".
"""

from __future__ import annotations

__all__ = ["CARD_FORMS", "CARD_TAGS", "format_operator_card", "is_operator_card"]

_ICONS = {
    "info": "\U0001f535",       # blue circle
    "success": "\U0001f7e2",    # green circle
    "warn": "\U0001f7e1",       # yellow circle
    "error": "\U0001f534",      # red circle
    "critical": "\U0001f6a8",   # rotating light
}

# Source-scoped icon overrides. Guard info cards (reload/restart pulses)
# use the ORANGE circle so the supervisor's notices are visually distinct
# from app-level info at a glance (operator request 2026-09-05). Orange is
# otherwise unused in the severity palette.
_SOURCE_ICON_OVERRIDES = {
    "guard": {"info": "\U0001f7e0"},  # orange circle
}

_SEVERITY = {
    "info": "Info",
    "success": "Success",
    "warn": "Warn",
    "error": "Error",
    "critical": "Critical",
}

_SOURCES = {
    "guard": "Guard",
    "ops": "Ops",
    "system": "System",
    "watch": "Watch",
    "cloudflare": "Cloudflare",
}

#: ``page`` (the default), ``reminder`` (the same problem, still there) and
#: ``recovery`` (it is over). Any other value is a page.
CARD_FORMS = ("page", "reminder", "recovery")
_REMINDER = "⏰ Still:"  # alarm clock


def format_operator_card(
    source: str,
    severity: str,
    title: str,
    detail: str = "",
    *,
    form: str = "page",
    since: str = "",
    duration: str = "",
    link: str = "",
) -> str:
    """Build the operator-visible card.

    Source is the sentence start, always capitalized in brackets::

        🟡 [Guard] Kazma is restarting for an operator reload.
        Back in a moment — no action needed.

    ``since`` (a time, already written for the reader) and ``duration`` ("12
    min") make the last line's "Since 14:02 UTC (12 min)"; ``link`` its
    "Open: <link>". A ``recovery`` is always green and puts ``duration`` in
    its first line: "<title> is back: it lasted 12 min."
    """
    src_key = str(source or "").strip().lower().strip("[]")
    src = _SOURCES.get(src_key, str(source or "Ops").strip() or "Ops")
    if src.lower() == "gaurd":
        src = "Guard"
    form = str(form or "page").strip().lower()
    sev_key = "success" if form == "recovery" else str(severity or "warn").strip().lower()
    icon = _SOURCE_ICON_OVERRIDES.get(src_key, {}).get(sev_key) or _ICONS.get(sev_key, _ICONS["warn"])
    head = str(title or "").strip() or _SEVERITY.get(sev_key, "Warn")
    lasted = str(duration or "").strip()
    if form == "reminder":
        first = f"{icon} {_REMINDER} [{src}] {head}"
    elif form == "recovery":
        first = f"{icon} [{src}] {head} is back" + (f": it lasted {lasted}." if lasted else ".")
    else:
        first = f"{icon} [{src}] {head}"
    lines = [first]
    body = str(detail or "").strip()
    if body:
        lines.append(body)
    footer = []
    when = str(since or "").strip()
    if when and form != "recovery":
        footer.append(f"Since {when}" + (f" ({lasted})" if lasted else ""))
    target = str(link or "").strip()
    if target:
        footer.append(f"Open: {target}")
    if footer:
        lines.append(" · ".join(footer))
    return "\n".join(lines)


#: The source tags a finished card's first line carries. ``[X]`` is X's own
#: notices (schedule outcomes, replies to review, threads), delivered by the
#: X notice outbox (``x_api/notifications.py``).
CARD_TAGS = ("[Guard]", "[Ops]", "[System]", "[X]", "[Watch]", "[Cloudflare]")


def is_operator_card(content: str) -> bool:
    """True when ``content`` is already a finished Kazma operator card."""
    first = (content or "").split("\n", 1)[0]
    return any(tag in first for tag in CARD_TAGS)
