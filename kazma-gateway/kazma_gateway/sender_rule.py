"""Who may message Kazma on a chat app, and what a refused sender is told.

One rule for Telegram, Discord and Slack (2026-11-11, the owner's decision).

Until then an adapter whose allowlist was empty took messages from anyone who
could reach its bot: the builder forced ``allow_all`` unless
``KAZMA_GATEWAY_STRICT_ALLOWLIST`` was set, so a Telegram bot whose name
leaked was an open line to the owner's agent -- its tools, its memory, its
files -- for anyone who wrote to it.

Now:

* a listed user passes, and only a listed user, when the allowlist is set;
* with the allowlist empty, Kazma is PRIVATE: it answers only the owner's own
  account on that platform that Kazma already knows
  (``allowlists.known_owner_ids``: on Telegram a private chat Kazma already
  pages the owner in; on any platform a ``platform:id`` entry of
  ``KAZMA_GATEWAY_ADMINS``), and refuses everyone else (``no_allowlist``);
* ``KAZMA_GATEWAY_OPEN_TO_EVERYONE`` (``boot_guard.env_flag``) brings back
  the old open posture, warned at every boot while it is on.
  ``KAZMA_GATEWAY_STRICT_ALLOWLIST`` -- the old way to ask for what is now
  the default -- changes nothing on its own and wins over the open switch.

A refused sender writing in a direct conversation is told so at most once a
day per sender per platform (:meth:`SenderGate.tell_refused_sender`): the bot
is private, here is your id, the owner adds it in Settings. Never in a group,
a server channel or a Slack channel, where one stranger's reply would be
everyone's noise. The refusal itself is recorded in the adapter's receive log
and logged by its rule (``ReceiveLog.log_drop``), as before.

Every adapter's accept path asks :meth:`SenderGate.sender_refusal`; nothing
reads the allowlist itself (``tests/test_chat_apps_private_by_default.py``).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "NOTICE_KEY",
    "NO_ALLOWLIST",
    "NOT_LISTED",
    "OPEN_SWITCH",
    "REFUSALS",
    "STRICT_SWITCH",
    "SenderGate",
    "open_to_everyone",
    "private_notice",
    "refusal",
]

#: The sender is refused: the allowlist is empty and the sender is not the
#: owner's own account Kazma knows.
NO_ALLOWLIST = "no_allowlist"
#: The sender is refused: the allowlist is set and does not name them.
NOT_LISTED = "user_not_allowed"
REFUSALS: frozenset[str] = frozenset({NO_ALLOWLIST, NOT_LISTED})

#: Lets everyone in when a chat app's allowlist is empty (the posture before
#: 2026-11-11). A security switch: read only through ``boot_guard.env_flag``.
OPEN_SWITCH = "KAZMA_GATEWAY_OPEN_TO_EVERYONE"
#: The old opt-in to refuse on an empty allowlist. Private is the default now;
#: set, it only wins over :data:`OPEN_SWITCH`.
STRICT_SWITCH = "KAZMA_GATEWAY_STRICT_ALLOWLIST"

#: The settings key that says which refused senders were told, and when
#: (``{"<platform>:<user id>": epoch}``): a restart does not tell them again.
NOTICE_KEY = "gateway.private_notices"
_DAY_S = 86_400.0
#: Senders told in one day, at most: a flood of strangers is told nothing
#: past it (a reply each would be an amplifier).
_MAX_TOLD = 500

_LABELS = {"telegram": "Telegram", "discord": "Discord", "slack": "Slack"}

_told: dict[str, float] = {}
_told_lock = threading.Lock()


def open_to_everyone() -> bool:
    """Whether an empty allowlist lets everyone in (the open switch is on and
    the old strict switch is not)."""
    from kazma_core.security.boot_guard import env_flag

    return env_flag(OPEN_SWITCH) and not env_flag(STRICT_SWITCH)


def _norm(value: Any) -> str:
    return str(value if value is not None else "").strip()


def refusal(
    user_id: Any,
    *,
    allowed: Iterable[Any],
    owners: Iterable[Any],
    open_to_everyone: bool,
) -> str:
    """Why *user_id* may not message Kazma, or ``""`` when it may.

    *allowed* is the platform's allowlist (Telegram keeps ints, the others
    strings: compared as text). *owners* counts only while it is empty.
    """
    uid = _norm(user_id)
    listed = {_norm(a) for a in allowed if _norm(a)}
    if listed:
        return "" if uid and uid in listed else NOT_LISTED
    if not uid:
        return NO_ALLOWLIST  # a sender with no id is no one's: never "everyone"
    if open_to_everyone or uid in {_norm(o) for o in owners}:
        return ""
    return NO_ALLOWLIST


def private_notice(platform: str, user_id: Any, user_text: str | None) -> str:
    """What a refused sender is told, in the language they wrote in."""
    from kazma_core.language_lock import in_their_language

    label = _LABELS.get(platform, platform)
    uid = _norm(user_id)
    english = (
        "Kazma here. This bot is private: it answers its owner only.\n"
        f"Your {label} user id is {uid}.\n"
        f"If you are the owner, add it in Kazma: Settings → Adapters & Routes → {label} → "
        "Allowed User IDs, then Save."
    )
    arabic = (
        "هنا Kazma. هذا البوت خاص: لا يرد إلا على مالكه.\n"
        f"معرّفك في {label} هو {uid}.\n"
        f"إن كنت المالك فأضفه في Kazma: الإعدادات ← المحوّلات والمسارات ← {label} ← "
        "معرّفات المستخدمين المسموح لهم، ثم احفظ."
    )
    return in_their_language(user_text, english, arabic)


def _claim_here(key: str, now: float) -> bool:
    """This process has not told *key* in the last day (and now has)."""
    with _told_lock:
        for old in [k for k, at in _told.items() if now - at >= _DAY_S]:
            del _told[old]
        if key in _told or len(_told) >= _MAX_TOLD:
            return False
        _told[key] = now
        return True


def _claim_durably(key: str, now: float) -> bool | None:
    """No run of Kazma told *key* in the last day (and now one has). None
    when the settings store cannot answer: the claim in memory stands.
    Blocking: a settings write."""
    claimed = False

    def update(current: Any) -> dict[str, float]:
        nonlocal claimed
        table: dict[str, float] = {}
        if isinstance(current, dict):
            for k, at in current.items():
                try:
                    stamp = float(at)
                except (TypeError, ValueError):
                    continue
                if now - stamp < _DAY_S:
                    table[str(k)] = stamp
        if key not in table and len(table) < _MAX_TOLD:
            table[key] = now
            claimed = True
        return table

    from kazma_core.config_store import get_config_store
    from kazma_core.db.pg_helpers import store_errors

    try:
        get_config_store().atomic_update(NOTICE_KEY, update, category="gateway")
    except store_errors() as exc:  # the store being away must not stop the notice
        logger.warning(
            "[gateway] Could not record that a refused sender was told (%s): "
            "a restart today may tell them again",
            type(exc).__name__,
        )
        return None
    return claimed


def _forget_told_for_tests() -> None:
    with _told_lock:
        _told.clear()


class SenderGate:
    """The one sender rule, for a chat adapter (mixed into each one).

    The adapter keeps ``_allowed_users`` (its allowlist) and ``_allow_all``
    (the open switch, set by the builder); ``_owner_ids`` comes from
    ``allowlists.apply_adapter_allowlists``.
    """

    name: str
    _allowed_users: Any
    _allow_all: bool = False
    _owner_ids: frozenset[str] = frozenset()
    _running: bool = False

    def set_owner_ids(self, user_ids: Iterable[Any]) -> None:
        """The owner's own ids on this platform that Kazma knows (they pass
        while the allowlist is empty)."""
        self._owner_ids = frozenset(_norm(u) for u in user_ids if _norm(u))

    @property
    def owner_ids(self) -> frozenset[str]:
        return self._owner_ids

    def sender_refusal(self, user_id: Any) -> str:
        """Why *user_id* may not message Kazma here, or ``""``."""
        return refusal(
            user_id,
            allowed=self._allowed_users or (),
            owners=self._owner_ids,
            open_to_everyone=self._allow_all,
        )

    def actor_allowed(self, user_id: Any) -> bool:
        """Whether *user_id* may message Kazma here (buttons, commands)."""
        return not self.sender_refusal(user_id)

    def admission(self) -> dict[str, Any]:
        """Who this adapter admits, for its diagnostics (plain data)."""
        return {
            "allowed_users": len(self._allowed_users or ()),
            "allow_all": self._allow_all,
            "owner_ids": sorted(self._owner_ids),
        }

    def tell_refused_sender(
        self,
        reason: str,
        *,
        user_id: Any,
        direct: bool,
        target_id: str,
        context_metadata: dict[str, Any] | None = None,
        user_text: str | None = None,
    ) -> bool:
        """After a refusal is recorded: tell the sender, at most once a day.

        Only for a refusal of this rule, in a direct conversation, on a
        running connection. Returns whether a notice was started. The send
        runs in the background; the claim is in memory now (two messages at
        once start one notice) and in the settings store before the send.
        """
        uid = _norm(user_id)
        if reason not in REFUSALS or not direct or not uid or not getattr(self, "_running", False):
            return False
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return False  # not on the adapter's loop: nothing to send it with
        key = f"{self.name}:{uid}"
        now = time.time()
        if not _claim_here(key, now):
            return False
        from kazma_core.background import spawn_background

        text = private_notice(self.name, uid, user_text)
        spawn_background(
            self._send_private_notice(key, now, uid, target_id, dict(context_metadata or {}), text),
            name=f"{self.name}-private-notice",
        )
        return True

    async def _send_private_notice(
        self, key: str, now: float, uid: str, target_id: str, meta: dict[str, Any], text: str
    ) -> None:
        if await asyncio.to_thread(_claim_durably, key, now) is False:
            return  # an earlier run of Kazma told them today
        import httpx

        from kazma_gateway.gateway import OutboundMessage

        try:
            sent = await self.send(  # type: ignore[attr-defined]
                OutboundMessage(target_id=target_id, text=text, context_metadata=meta, durable=False)
            )
        except (httpx.HTTPError, OSError, RuntimeError, ValueError, TypeError) as exc:
            sent = False
            logger.warning("[%s] Telling refused user %s that Kazma is private failed (%s)",
                           self.name, uid, type(exc).__name__)
        logger.info(
            "[%s] Told refused user %s that this bot is private (at most once a day): %s",
            self.name, uid, "sent" if sent else "not sent",
        )
