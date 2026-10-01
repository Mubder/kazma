"""What a chat app's connection received, and what became of each message.

Found on Discord first (2026-09-29): no message from the owner had reached
Kazma for eight days, and nothing said so -- a message could be left without
a line in the log, and the connector Test only checked the token. Telegram
and Slack had the same blind spots: an update they could not read, a channel
outside the list, a user not on it (Telegram logged that at DEBUG), and on
Slack every message RECEIVED was logged at DEBUG only. Each adapter keeps one
of these; its connector Test shows it beside what the platform's own API says.

Lives on the event loop with its adapter; ``snapshot`` is plain data.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any

__all__ = ["COMMON_REASONS", "ReceiveLog", "iso"]

#: Reasons every adapter shares, in words the Test and the log show.
COMMON_REASONS: dict[str, str] = {
    "no_allowlist": "was refused: Allowed User IDs is empty",
    "user_not_allowed": "was refused: its author is not in Allowed User IDs",
    "queue_full": "was dropped: Kazma's message queue was full",
    "processing_failed": "failed while being prepared (a voice note or file that could not be read?)",
    # Left by the gateway after the adapter handed it on (its flood guard).
    "rate_limited": (
        "was left unanswered: its author sent more messages in a minute than "
        "gateway.rate_limits allows (Kazma answered \"Slow down\")"
    ),
}


def iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds")


class ReceiveLog:
    """Counts and the last outcomes of one adapter, since it started."""

    #: How many message ids ``recent`` keeps.
    RECENT = 50
    #: A drop that points at a setting to fix is a WARNING at most this often
    #: per reason (a busy channel must not flood the log).
    WARN_EVERY_S = 600.0

    def __init__(self, reasons: dict[str, str] | None = None) -> None:
        self.reasons: dict[str, str] = {**COMMON_REASONS, **(reasons or {})}
        self.started_at = time.time()
        self.connected = False
        self.connected_since: float | None = None
        #: When the current session began. Discord replays a resumed
        #: session's gap; a new session -- and a restart -- does not.
        self.session_since: float | None = None
        self.new_sessions = 0
        self.resumes = 0
        self.last_alive_at: float | None = None
        self.last_problem: dict[str, Any] | None = None
        self.events: Counter = Counter()
        self.last_event_at: float | None = None
        self.messages = 0
        self.passed_on = 0
        self.last_passed_on_at: float | None = None
        self.dropped: Counter = Counter()
        self.last_drop: dict[str, Any] | None = None
        self.last_human_drop: dict[str, Any] | None = None
        #: The last message a person (not a bot) sent, as received.
        self.last_person: dict[str, Any] | None = None
        #: What became of the last messages, by id: "passed_on", "accepted"
        #: or a reason key -- oldest first.
        self.recent: dict[str, str] = {}
        #: Platform facts the Test may show (the bot's id, servers at READY).
        self.extra: dict[str, Any] = {}
        self._warned_at: dict[str, float] = {}

    # ── connection ──────────────────────────────────────────────────────

    def connected_now(self, *, new_session: bool) -> None:
        now = time.time()
        self.connected, self.connected_since, self.last_alive_at = True, now, now
        if new_session:
            self.session_since = now
            self.new_sessions += 1

    def resumed(self) -> None:
        self.connected_now(new_session=False)
        self.resumes += 1

    def disconnected(self, problem: str | None = None) -> None:
        self.connected = False
        if problem:
            self.problem(problem)

    def problem(self, what: str) -> None:
        """Something went wrong with the connection (its words, for the Test)."""
        self.last_problem = {"at": iso(time.time()), "what": what}

    def alive(self) -> None:
        """The connection answered (a poll came back, a ping was answered)."""
        self.last_alive_at = time.time()

    def event(self, kind: str) -> None:
        self.events[kind] += 1
        self.last_event_at = time.time()

    # ── messages ────────────────────────────────────────────────────────

    def message(
        self, message_id: Any = None, *, author_id: Any = None, person: bool = True, where: str | None = None
    ) -> None:
        self.messages += 1
        self.outcome(message_id, "accepted")
        if person:
            self.last_person = {
                "id": str(message_id or "") or None,
                "author_id": str(author_id or "") or None,
                "at": iso(time.time()),
                "where": where,
            }

    def outcome(self, message_id: Any, what: str) -> None:
        """What became of message *message_id* (the newest word wins)."""
        mid = str(message_id or "")
        if not mid:
            return
        self.recent.pop(mid, None)
        self.recent[mid] = what
        while len(self.recent) > self.RECENT:
            self.recent.pop(next(iter(self.recent)))

    def note_passed_on(self, message_id: Any = None) -> None:
        """A message handed to Kazma to answer."""
        self.passed_on += 1
        self.last_passed_on_at = time.time()
        self.outcome(message_id, "passed_on")

    def why(self, reason: str) -> str:
        return self.reasons.get(reason, reason)

    def drop(
        self,
        reason: str,
        *,
        message_id: Any = None,
        author_id: Any = None,
        channel_id: Any = None,
        where: str | None = None,
        person: bool = True,
    ) -> dict[str, Any]:
        """Record a message that was not answered; returns the record."""
        self.dropped[reason] += 1
        self.outcome(message_id, reason)
        record = {
            "reason": reason,
            "why": self.why(reason),
            "at": iso(time.time()),
            "author_id": str(author_id or "") or None,
            "channel_id": str(channel_id or "") or None,
            "where": where,
        }
        self.last_drop = record
        if person:
            self.last_human_drop = record
        return record

    def log_drop(
        self, log: logging.Logger, platform: str, record: dict[str, Any], *, warn: frozenset[str]
    ) -> None:
        """Say why a message was left: a bot's at DEBUG, one of *warn* (a
        setting to fix) as a WARNING at most every WARN_EVERY_S per reason,
        the rest at INFO. One rule for every adapter."""
        reason = record["reason"]
        if reason == "from_a_bot":
            log.debug("[%s] Ignored a message from a bot in channel %s", platform, record["channel_id"])
            return
        level = logging.INFO
        if reason in warn:
            now = time.monotonic()
            if now - self._warned_at.get(reason, -self.WARN_EVERY_S) >= self.WARN_EVERY_S:
                self._warned_at[reason] = now
                level = logging.WARNING
        place = f" in {record['where']}" if record["where"] else ""
        log.log(
            level,
            "[%s] A message from user %s%s (channel %s) was not answered: it %s",
            platform, record["author_id"], place, record["channel_id"], record["why"],
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "started_at": iso(self.started_at),
            "connected": self.connected,
            "connected_since": iso(self.connected_since),
            "session_since": iso(self.session_since),
            "new_sessions": self.new_sessions,
            "resumes": self.resumes,
            "last_alive_at": iso(self.last_alive_at),
            "last_problem": self.last_problem,
            "events": dict(self.events),
            "last_event_at": iso(self.last_event_at),
            "messages": self.messages,
            "passed_on": self.passed_on,
            "last_passed_on_at": iso(self.last_passed_on_at),
            "dropped": dict(self.dropped),
            "reasons": dict(self.reasons),
            "last_drop": self.last_drop,
            "last_human_drop": self.last_human_drop,
            "last_person": self.last_person,
            "recent": dict(self.recent),
            **self.extra,
        }
