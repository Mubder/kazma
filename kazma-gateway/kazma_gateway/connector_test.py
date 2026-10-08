"""What every chat app's connector Test shares (2026-09-29).

Discord's Test came first: it asks Discord about the bot and joins the
answers with what Kazma's own connection received (``receive_log``). The
owner asked for the same on every adapter; Telegram and Slack build their
checks from the same pieces, so a message's fate and the connection's state
are judged -- and worded -- one way everywhere.

A check is ``{"key", "ok", "detail"}`` (+ ``"link"``): ``ok`` is True, False
(something to fix) or None (worth knowing, not wrong).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

__all__ = ["Checks", "command_registration", "judge_message", "listening", "show", "when"]


class Checks:
    """The Test's rows, and its result."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(self, key: str, ok: bool | None, detail: str, link: str | None = None) -> None:
        item: dict[str, Any] = {"key": key, "ok": ok, "detail": detail}
        if link:
            item["link"] = link
        self.items.append(item)

    def ok(self, key: str) -> bool:
        return any(c["key"] == key and c["ok"] is True for c in self.items)

    def result(self, bot_name: str | None) -> dict[str, Any]:
        failed = next((c for c in self.items if c["ok"] is False), None)
        return {
            "success": failed is None,
            "bot_name": bot_name,
            "error": failed["detail"] if failed else None,
            "checks": self.items,
        }


def when(value: Any) -> datetime | None:
    """A time from an ISO string or epoch seconds (None when neither)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    try:
        stamp = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def show(stamp: datetime | None) -> str:
    return stamp.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if stamp else "an unknown time"


def judge_message(
    *,
    message_id: Any,
    author_id: Any,
    who: str,
    stamp: datetime | None,
    live: dict[str, Any] | None,
    allowed: list[str],
    place: str,
    platform: str,
    never_hint: str,
    reasons: dict[str, str] | None = None,
) -> tuple[bool | None, str]:
    """What became of one message a person wrote -- from the adapter's
    record, by the message's id. *reasons* words a drop the record names
    (the record's own words win)."""
    author = str(author_id or "")
    lead = f"{place} ({show(stamp)}, from {who})"
    if allowed and author and author not in allowed:
        return False, (
            f"{lead} is from someone not in Allowed User IDs ({', '.join(allowed)}), so Kazma "
            f"does not answer it. If that is you, put {author} in Allowed User IDs and Save."
        )
    if live is None:
        return None, f"{lead}: Kazma's {platform} connection is not running, so it cannot have received it."
    outcome = (live.get("recent") or {}).get(str(message_id or ""))
    if outcome == "passed_on":
        return True, f"{lead} reached Kazma and was handed on to be answered."
    if outcome == "accepted":
        return True, f"{lead} reached Kazma and is being prepared (a voice note is transcribed first)."
    if outcome:
        why = {**(reasons or {}), **(live.get("reasons") or {})}.get(outcome, outcome)
        return False, f"{lead} reached Kazma but was not answered: it {why}."
    session_since = when(live.get("session_since"))
    if session_since and stamp and stamp < session_since:
        return None, (
            f"{lead} is older than Kazma's current {platform} session ({show(session_since)}, "
            "a restart or reconnect), so this connection cannot say what became of it. "
            "Send a new one, then Test again."
        )
    return False, (
        f"{lead} never reached Kazma's connection, though the connection was up "
        f"(its last event: {show(when(live.get('last_event_at')))}). {never_hint}"
    )


def command_registration(live: dict[str, Any] | None, platform: str) -> tuple[bool | None, str]:
    """Expose registration readback without credentials or private scope IDs."""
    if platform == "Slack":
        return None, (
            "Native /kazma commands require a slash_commands entry in the Slack app manifest. "
            "Kazma's bot token cannot verify or edit that manifest. See the native slash commands guide."
        )
    key = "command_menus" if platform == "Telegram" else "native_commands"
    states = (live or {}).get(key)
    if not isinstance(states, dict) or not states:
        return None, "Command registration has not completed in this connection. Test again after startup."
    failed = [str(state) for state in states.values() if state != "verified"]
    if failed:
        return False, (
            f"{len(failed)} of {len(states)} command scope(s) failed registration readback "
            f"({', '.join(sorted(set(failed)))}). Check bot permissions and reconnect, then Test again."
        )
    return True, f"All {len(states)} command scope(s) match Kazma's current command catalog."


def listening(live: dict[str, Any] | None, platform: str, *, not_running: str) -> tuple[bool, str]:
    """Is Kazma's connection up, and what has it received?"""
    if live is None:
        return False, not_running
    problem = live.get("last_problem") or {}
    if not live.get("connected"):
        said = f" Its last problem ({problem.get('at')}): {problem.get('what')}" if problem else ""
        return False, (
            f"Kazma's {platform} connection is down right now.{said} "
            f"If this stays, the log's [{platform.lower()}] lines say why."
        )
    dropped = sum((live.get("dropped") or {}).values())
    return True, (
        f"Connected since {show(when(live.get('connected_since')))}: "
        f"{live.get('messages', 0)} message(s) received, {live.get('passed_on', 0)} handed on "
        f"to be answered, {dropped} not answered; {sum((live.get('events') or {}).values())} "
        f"event(s) in all since Kazma started."
    )
