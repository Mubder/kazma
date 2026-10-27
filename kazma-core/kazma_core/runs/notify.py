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

from kazma_core.runs.claims import WORK_FAILURES, settle_on_error
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
    if with_commands:
        # The ops card is escaped for Telegram's MarkdownV2: ids stay as they are.
        lines.append(f"Run {row['run_id']} · incident {row['id']}")
    else:
        # A chat card is Markdown: ``_`` would turn into italics.
        lines.append(f"Run `{row['run_id']}` · incident `{row['id']}`")
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
        async def _interrupted(exc: BaseException) -> None:
            logger.warning("[runs] incident %s: the card send raised %s", row["id"], type(exc).__name__,
                           exc_info=exc)
            await asyncio.to_thread(store.finish_notification, row["id"], outcome="uncertain",
                                    detail=f"the send raised {type(exc).__name__}")

        # The row is "sending" now: it always ends recorded (audit 2026-10-26
        # AUD-018 -- an httpx or sqlite3 error escaped the three types caught
        # here and left it "sending" until the next start). A send that
        # raised a known failure falls through to the ops channels; anything
        # else may or may not have reached the chat: uncertain, never resent.
        async with settle_on_error(_interrupted):
            try:
                result = await _send(target, _card_text(row), backend=platform, run_card=card)
            except WORK_FAILURES as exc:
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


# A follow-up's body on the ops channels: under Telegram's 4,096 with the card around it.
_FOLLOW_UP_MAX = 3500

def _ids(row: dict[str, Any], *, code: bool) -> tuple[str, str]:
    """The run and incident ids: code spans in a chat (Markdown reads ``_`` as
    italics, live 2026-10-25), plain on the ops channels (escaped there)."""
    if code:
        return f"`{row['run_id']}`", f"`{row['id']}`"
    return str(row["run_id"]), str(row["id"])


async def _send_text(row: dict[str, Any], text: Any, key: str) -> None:
    """A follow-up (recovery, diagnosis) to where the incident went; best effort.

    *text* is called with ``code=True`` for a chat and ``code=False`` for the
    ops channels."""
    origin = row.get("origin") or {}
    platform = str(origin.get("platform") or "")
    target = str(origin.get("target") or "")
    if platform in _CHAT_PLATFORMS and target:
        from kazma_core.tools.send_message import send_failed

        try:
            result = await _send(target, text(code=True), backend=platform)
        except WORK_FAILURES as exc:  # a send that raises is a refused send (AUD-018)
            result = f"Error: {type(exc).__name__}: {exc}"
        if not send_failed(result):
            return
        logger.warning("[runs] %s for %s not taken by %s: %s", key, row["id"], platform, str(result)[:200])
    from kazma_core.observability.ops_alerts import alert

    # The first line is the title; the rest is the body, whole (a diagnosis
    # runs past the ordinary alert's 600 characters).
    title, _, body = text(code=False).partition("\n")
    alert(f"runs.{key}.{row['id']}", title, body.strip(), severity="info", cooldown_s=0,
          max_detail=_FOLLOW_UP_MAX)


async def send_recovery(row: dict[str, Any], *, finished: bool) -> None:
    """One note when a stalled or stuck run moves again (or finishes)."""
    what = "finished" if finished else "is making progress again"

    def text(*, code: bool) -> str:
        _run, incident = _ids(row, code=code)
        return (f"✅ {_label(row['kind'])} {what}: “{row.get('title', '')}” "
                f"(incident {incident} closed by itself).")
    await _send_text(row, text, "recovered")


async def send_diagnosis(row: dict[str, Any], text: str) -> None:
    """The diagnosis, to where the incident went."""
    def message(*, code: bool) -> str:
        run, incident = _ids(row, code=code)
        return f"🔎 Diagnosis of {run} (incident {incident}):\n{text}"
    await _send_text(row, message, "diagnosis")


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
