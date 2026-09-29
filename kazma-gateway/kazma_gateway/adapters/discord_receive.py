"""What Kazma's Discord connection received, and what became of each message.

Found 2026-09-29: no message from the owner had reached Kazma on Discord for
at least eight days, and nothing said so. The adapter dropped a message that
arrived without text, from a server outside its list, or from a bot, without
a line in the log, and the connector's Test only checked that the token signs
in. This record is what the Test shows beside the checks it makes with
Discord's API: whether the connection is up, what it received, and why each
message it did not answer was left.

Lives on the event loop with the adapter; ``snapshot`` is a plain dict.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

__all__ = ["DROP_REASONS", "DiscordReceiveLog"]

#: Why a message was not answered, in words the Test and the log show.
DROP_REASONS: dict[str, str] = {
    "from_a_bot": "sent by a bot (Kazma's own posts included)",
    "no_text": (
        "arrived without text or attachment -- in a server channel Discord "
        "sends a bot the text only while its Message Content Intent is on, or "
        "when the message mentions the bot"
    ),
    "no_channel": "named no channel",
    "empty_event": "was empty",
    "server_not_allowed": "came from a server that is not the Guild ID set for Kazma",
    "no_allowlist": "was refused: Allowed User IDs is empty",
    "user_not_allowed": "was refused: its author is not in Allowed User IDs",
    "queue_full": "was dropped: Kazma's message queue was full",
    "processing_failed": "failed while being prepared (a voice note that could not be transcribed?)",
}


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds")


@dataclass
class DiscordReceiveLog:
    """Counts and the last few outcomes, since the adapter started."""

    started_at: float = field(default_factory=time.time)
    connected: bool = False
    connected_since: float | None = None
    new_sessions: int = 0
    resumes: int = 0
    guilds_at_ready: int | None = None
    bot_user_id: str | None = None
    events: Counter = field(default_factory=Counter)
    last_event_at: float | None = None
    messages: int = 0
    passed_on: int = 0
    last_passed_on_at: float | None = None
    dropped: Counter = field(default_factory=Counter)
    last_drop: dict[str, Any] | None = None
    last_human_drop: dict[str, Any] | None = None
    #: When the current session began (READY). A resume replays what was
    #: said in its gap; a new session does not, so a message older than this
    #: may have been said while the bot was away.
    session_since: float | None = None
    #: The last messages seen, by Discord message id: what became of each
    #: ("passed_on", "accepted" or a DROP_REASONS key), oldest first.
    recent: dict[str, str] = field(default_factory=dict)

    #: How many message ids ``recent`` keeps.
    RECENT = 50

    # ── connection ──────────────────────────────────────────────────────

    def ready(self, data: Any) -> None:
        now = time.time()
        self.connected, self.connected_since = True, now
        self.session_since = now
        self.new_sessions += 1
        if isinstance(data, dict):
            guilds = data.get("guilds")
            self.guilds_at_ready = len(guilds) if isinstance(guilds, list) else None
            self.bot_user_id = str((data.get("user") or {}).get("id") or "") or None

    def resumed(self) -> None:
        self.connected, self.connected_since = True, time.time()
        self.resumes += 1

    def disconnected(self) -> None:
        self.connected = False

    def event(self, kind: str) -> None:
        self.events[kind] += 1
        self.last_event_at = time.time()

    # ── messages ────────────────────────────────────────────────────────

    def message(self, message_id: Any = None) -> None:
        self.messages += 1
        self.outcome(message_id, "accepted")

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

    def drop(self, reason: str, data: Any) -> dict[str, Any]:
        """Record a message that was not answered; returns the record."""
        self.dropped[reason] += 1
        d = data if isinstance(data, dict) else {}
        self.outcome(d.get("id"), reason)
        record = {
            "reason": reason,
            "why": DROP_REASONS.get(reason, reason),
            "at": _iso(time.time()),
            "author_id": str((d.get("author") or {}).get("id") or "") or None,
            "channel_id": str(d.get("channel_id") or "") or None,
            "guild_id": str(d.get("guild_id") or "") or None,
        }
        self.last_drop = record
        if reason != "from_a_bot":
            self.last_human_drop = record
        return record

    def snapshot(self) -> dict[str, Any]:
        return {
            "started_at": _iso(self.started_at),
            "connected": self.connected,
            "connected_since": _iso(self.connected_since),
            "session_since": _iso(self.session_since),
            "recent": dict(self.recent),
            "new_sessions": self.new_sessions,
            "resumes": self.resumes,
            "guilds_at_ready": self.guilds_at_ready,
            "bot_user_id": self.bot_user_id,
            "events": dict(self.events),
            "last_event_at": _iso(self.last_event_at),
            "messages": self.messages,
            "passed_on": self.passed_on,
            "last_passed_on_at": _iso(self.last_passed_on_at),
            "dropped": dict(self.dropped),
            "last_drop": self.last_drop,
            "last_human_drop": self.last_human_drop,
        }
