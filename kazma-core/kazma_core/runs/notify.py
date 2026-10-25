"""Telling the owner about an incident: the card, its delivery, the bell.

An incident goes to the chat its run started from, with buttons
(``run_card``, rendered by the chat app's own send path), when that chat is
known and reachable; otherwise to the ops channels through ``ops_alerts`` (no
fourth notifier, AGENTS.md §33), as text that names ``/runs act`` and the
Dashboard. Either way the incident's outbox row (``incidents.notify``) records
what happened: ``delivered`` only when a platform took it.

The chat apps' messages are English (the chat apps' rule); the bell, read by
the web page, is in the page's language.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from kazma_core.runs.kinds import KINDS
from kazma_core.runs.text import action_word, ago

__all__ = ["bell_alerts", "deliver", "send_diagnosis", "send_recovery"]

logger = logging.getLogger(__name__)

_CHAT_PLATFORMS = frozenset({"telegram", "discord", "slack"})

_CONDITION: dict[str, tuple[str, str]] = {
    "stalled": ("has made no progress", "توقف عن التقدم"),
    "dead": ("is stuck: nothing is running it", "عالق: لا شيء يشغّله"),
    "failed": ("failed", "فشل"),
}
_ICON = {"stalled": "⚠️", "dead": "⚠️", "failed": "❌"}
_SEVERITY = {"stalled": "warn", "dead": "error", "failed": "error"}


async def _send(target: str, text: str, *, backend: str, **kwargs: Any) -> str:
    """``send_message`` (the one place this module sends)."""
    from kazma_core.tools.send_message import send_message

    return await send_message(target, text, backend=backend, **kwargs)


def _label(kind: str, lang: str = "en") -> str:
    spec = KINDS.get(kind)
    if spec is None:
        return kind
    return spec.label_ar if lang == "ar" else spec.label


def _card_text(row: dict[str, Any], *, now: float | None = None, with_commands: bool = False) -> str:
    """The message an incident sends (English)."""
    stamp = time.time() if now is None else now
    condition = row["condition"]
    lines = [f"{_ICON.get(condition, '⚠️')} {_label(row['kind'])} {_CONDITION.get(condition, ('needs attention',))[0]}"]
    if row.get("title"):
        lines.append(f"“{row['title']}”")
    if condition in ("stalled", "dead"):
        lines.append(f"Noticed {ago(stamp - float(row['opened_at']))} ago.")
    if row.get("detail"):
        lines.append(f"Reason: {row['detail']}")
    if row.get("effects") == "unknown":
        lines.append("Its effects outside Kazma are unknown: check them before you retry.")
    if row.get("decided_action") and row.get("decided_platform") == "runbook":
        outcome = f" Outcome: {row['outcome_message']}" if row.get("outcome_message") else ""
        lines.append(f"Kazma already did its automatic first step: {action_word(row['decided_action'])}.{outcome}")
    lines.append(f"Run {row['run_id']} · incident {row['id']}")
    if with_commands and row.get("state") == "open":
        offered = ", ".join(row.get("actions") or [])
        lines.append(f"Act on it: /runs act {row['id']} <action> ({offered}), or on the Dashboard.")
    return "\n".join(lines)


class _Receipt:
    """``ops_alerts`` calls this with True / False / None once delivery settles;
    it records the outcome on the incident's outbox row, off the loop."""

    def __init__(self, incident_id: str) -> None:
        self.incident_id = incident_id

    def _write(self, accepted: bool | None) -> None:
        from kazma_core.runs.incidents import get_incident_store

        outcome = "uncertain" if accepted is None else ("delivered" if accepted else "refused")
        detail = {"delivered": "ops channels", "refused": "no ops channel took it",
                  "uncertain": "delivery was interrupted"}[outcome]
        get_incident_store().finish_notification(self.incident_id, outcome=outcome, detail=detail)

    def __call__(self, accepted: bool | None) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self._write(accepted)
            return
        from kazma_core.background import spawn_background

        spawn_background(asyncio.to_thread(self._write, accepted), name=f"run-incident-receipt:{self.incident_id}")


async def deliver(row: dict[str, Any]) -> str:
    """Send a pending incident once. Returns the outbox state it ended in."""
    from kazma_core.runs.incidents import get_incident_store

    store = get_incident_store()
    if not await asyncio.to_thread(store.claim_notification, row["id"]):
        return "already_claimed"
    origin = row.get("origin") or {}
    platform = str(origin.get("platform") or "")
    target = str(origin.get("target") or "")
    if platform in _CHAT_PLATFORMS and target:
        from kazma_core.tools.send_message import send_failed

        offered = list(row.get("actions") or []) if row.get("state") == "open" else []
        card = {"incident_id": row["id"], "actions": offered,
                "labels": {a: action_word(a) for a in offered}}
        try:
            result = await _send(target, _card_text(row), backend=platform, run_card=card)
        except asyncio.CancelledError:
            raise
        except (RuntimeError, OSError, ValueError) as exc:
            result = f"Error: {type(exc).__name__}: {exc}"
        if not send_failed(result):
            await asyncio.to_thread(store.finish_notification, row["id"], outcome="delivered", detail=platform)
            logger.info("[runs] incident %s sent to %s", row["id"], platform)
            return "delivered"
        logger.warning("[runs] incident %s: %s did not take the card (%s); trying the ops channels",
                       row["id"], platform, str(result)[:200])
    from kazma_core.observability.ops_alerts import alert

    requested = alert(
        f"runs.{row['condition']}.{row['id']}",
        f"{_label(row['kind'])} {_CONDITION.get(row['condition'], ('needs attention',))[0]}",
        _card_text(row, with_commands=True),
        severity=_SEVERITY.get(row["condition"], "warn"), cooldown_s=0,
        on_delivery=_Receipt(row["id"]),
    )
    if not requested:
        await asyncio.to_thread(store.finish_notification, row["id"], outcome="refused",
                                detail="ops alerts are switched off or refused it")
        return "refused"
    return "sending"


async def _send_text(row: dict[str, Any], text: str, key: str) -> None:
    """A follow-up (recovery, diagnosis) to where the incident went; best effort."""
    origin = row.get("origin") or {}
    platform = str(origin.get("platform") or "")
    target = str(origin.get("target") or "")
    if platform in _CHAT_PLATFORMS and target:
        from kazma_core.tools.send_message import send_failed

        try:
            result = await _send(target, text, backend=platform)
        except (RuntimeError, OSError, ValueError) as exc:
            result = f"Error: {exc}"
        if not send_failed(result):
            return
        logger.warning("[runs] %s for %s not taken by %s: %s", key, row["id"], platform, str(result)[:200])
    from kazma_core.observability.ops_alerts import alert

    alert(f"runs.{key}.{row['id']}", text.split("\n", 1)[0], text, severity="info", cooldown_s=0)


async def send_recovery(row: dict[str, Any], *, finished: bool) -> None:
    """One note when a stalled or stuck run moves again (or finishes)."""
    what = "finished" if finished else "is making progress again"
    await _send_text(row, f"✅ {_label(row['kind'])} {what}: “{row.get('title', '')}” "
                          f"(incident {row['id']} closed by itself).", "recovered")


async def send_diagnosis(row: dict[str, Any], text: str) -> None:
    """The diagnosis, to where the incident went."""
    await _send_text(row, f"🔎 Diagnosis of {row['run_id']} (incident {row['id']}):\n{text}", "diagnosis")


def bell_alerts(language: str = "en") -> list[dict[str, Any]]:
    """Open incidents in the notification bell's shape (``/api/alerts/recent``)."""
    from kazma_core.runs.incidents import get_incident_store

    ar = language == "ar"
    out: list[dict[str, Any]] = []
    for row in get_incident_store().live():
        condition = row["condition"]
        out.append({
            "id": f"run-incident-{row['id']}",
            "title": f"{_label(row['kind'], language)}: {_CONDITION.get(condition, ('', ''))[1 if ar else 0]}",
            "subsystem": "Runs", "status": condition,
            "reason": row.get("title") or row["run_id"],
            "callback_id": "link:/dashboard?incident=" + row["id"],
            "button_text": "عرض واتخاذ إجراء" if ar else "View and act",
            "timestamp": float(row["opened_at"]),
            "severity": "WARNING" if condition == "stalled" else "ERROR",
        })
    return out
