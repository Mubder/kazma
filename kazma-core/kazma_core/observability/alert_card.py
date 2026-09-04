"""One professional operator-card layout for Guard, Ops, and System.

Guard cannot import this module (stdlib-only supervisor). Keep the
layout identical to ``format_operator_card`` in ``kazma_guard.py``.
"""

from __future__ import annotations

__all__ = ["format_operator_card", "is_operator_card"]

_ICONS = {
    "info": "\U0001f535",       # blue circle
    "success": "\U0001f7e2",    # green circle
    "warn": "\U0001f7e1",       # yellow circle
    "error": "\U0001f534",      # red circle
    "critical": "\U0001f6a8",   # rotating light
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
}


def format_operator_card(
    source: str,
    severity: str,
    title: str,
    detail: str = "",
) -> str:
    """Build the operator-visible card.

    Source is the sentence start, always capitalized in brackets::

        🟡 [Guard] Kazma is restarting for an operator reload.
        Back in a moment — no action needed.
    """
    src_key = str(source or "").strip().lower().strip("[]")
    src = _SOURCES.get(src_key, str(source or "Ops").strip() or "Ops")
    if src.lower() == "gaurd":
        src = "Guard"
    sev_key = str(severity or "warn").strip().lower()
    icon = _ICONS.get(sev_key, _ICONS["warn"])
    head = str(title or "").strip() or _SEVERITY.get(sev_key, "Warn")
    lines = [f"{icon} [{src}] {head}"]
    body = str(detail or "").strip()
    if body:
        lines.append(body)
    return "\n".join(lines)


def is_operator_card(content: str) -> bool:
    """True when ``content`` is already a finished Kazma operator card."""
    first = (content or "").split("\n", 1)[0]
    return any(tag in first for tag in ("[Guard]", "[Ops]", "[System]"))
