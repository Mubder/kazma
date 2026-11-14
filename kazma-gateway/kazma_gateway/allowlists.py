"""Live connector allowlists — one apply path for boot, refresh, and Settings.

Adapters expose ``set_allowed_users`` (and Slack team/channel setters).
Settings writes ``connectors.<platform>.allowed_users`` (and Slack
``allowed_teams`` / ``allowed_channels``); this module pushes those values
onto the running adapter without requiring a process restart.

Also home of :func:`is_gateway_admin` — the admin gate for admin-grade
chat commands and alert-card buttons (audit H-8): "everyone may chat"
(the open switch, ``KAZMA_GATEWAY_OPEN_TO_EVERYONE``) must never imply
"everyone may install packages or change global config" -- and of
:func:`known_owner_ids`, the owner's own accounts Kazma already knows, which
are the only senders a chat app with an empty allowlist answers
(``sender_rule``, 2026-11-11).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "GATEWAY_ADMINS_ENV",
    "OWNER_ID_KEYS",
    "OwnerId",
    "apply_adapter_allowlists",
    "apply_gateway_allowlists",
    "is_gateway_admin",
    "known_owner_ids",
    "split_ids",
]

#: Comma-separated admin identities (platform user ids or full ``platform:id``
#: sender ids). When set, it is the AUTHORITATIVE admin set — the per-platform
#: user allowlist no longer grants admin on its own.
GATEWAY_ADMINS_ENV = "KAZMA_GATEWAY_ADMINS"

#: Settings that hold a Telegram chat Kazma already uses to reach its owner:
#: the guard's pager (Settings -> Alerts) and the chat-app delivery chat
#: (Settings -> Adapters & Routes -> Telegram, the reminders' and status
#: messages' address). Only a PRIVATE chat counts as the owner's account: on
#: Telegram its id is the user's id and is positive; a group or channel id is
#: negative, an ``@name`` is a public channel.
OWNER_ID_KEYS: dict[str, tuple[str, ...]] = {
    "telegram": ("guard.telegram.chat_id", "connectors.telegram.swarm_chat_id"),
    "discord": (),
    "slack": (),
    "whatsapp": (),
}


@dataclass(frozen=True)
class OwnerId:
    """One of the owner's own accounts on a platform, and where Kazma read it."""

    user_id: str
    source: str


def split_ids(raw: Any) -> list[str]:
    """Split a comma-separated ConfigStore value into stripped ids."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [s.strip() for s in str(raw).split(",") if s.strip()]


def _platform_user_id(platform: str, raw: Any) -> str:
    """A user id as the platform's adapter writes it: a WhatsApp number in
    digits ("+965 9999-1234" -> "96599991234", the ``wa_id`` WhatsApp sends);
    every other platform's as written."""
    text = str(raw if raw is not None else "").strip()
    if (platform or "").strip().lower() == "whatsapp":
        return re.sub(r"\D", "", text)
    return text


def _candidate_tokens(sender_id: str) -> set[str]:
    """Full actor plus platform user tokens, without treating channels as users."""
    tokens = {sender_id}
    if ":" in sender_id:
        tail = sender_id.split(":", 1)[1]
        if tail:
            tokens.add(tail)
    # Discord's verified inbound actor is discord:user:channel. Admin lists
    # contain users; thread ownership keeps the complete actor elsewhere.
    parts = sender_id.split(":")
    if len(parts) == 3 and parts[0] == "discord" and all(p.isdigit() for p in parts[1:]):
        tokens.add(parts[1])
    return tokens


def _private_telegram_chat(raw: Any) -> str:
    """The user id a Telegram chat id names, or "" for a group, a channel, an
    ``@name`` or anything else."""
    text = str(raw if raw is not None else "").strip()
    if not text.isdigit() or int(text) <= 0:
        return ""
    return str(int(text))


def known_owner_ids(config_store: Any, platform: str) -> list[OwnerId]:
    """The owner's own accounts on *platform* that Kazma already holds.

    A chat app whose allowlist is empty answers these and no one else
    (``sender_rule``); they grant admin there too (:func:`is_gateway_admin`).
    Two sources, both the operator's own words:

    * on Telegram, a private chat in :data:`OWNER_ID_KEYS` -- the chat the
      guard pages and the chat-app delivery chat;
    * on any platform, a ``platform:id`` entry of ``KAZMA_GATEWAY_ADMINS``
      (an unqualified entry does not say which platform it is on).

    Discord and Slack store no owner's user id anywhere else: with an empty
    allowlist and no admin entry, they answer nobody until the owner adds
    themselves. Blocking: settings reads.
    """
    plat = (platform or "").strip().lower()
    found: list[OwnerId] = []
    if config_store is not None:
        for key in OWNER_ID_KEYS.get(plat, ()):
            uid = _private_telegram_chat(_cs_get(config_store, key, ""))
            if uid:
                found.append(OwnerId(uid, key))
    for part in split_ids(os.environ.get(GATEWAY_ADMINS_ENV, "")):
        if ":" not in part:
            continue
        entry_plat, entry_id = (x.strip() for x in part.split(":", 1))
        if entry_plat.lower() != plat or not entry_id:
            continue
        # A full Discord sender id is discord:<user>:<channel>; the user is first.
        uid = entry_id.split(":", 1)[0] if plat == "discord" else _platform_user_id(plat, entry_id)
        if uid:
            found.append(OwnerId(uid, GATEWAY_ADMINS_ENV))
    seen: set[str] = set()
    unique: list[OwnerId] = []
    for owner in found:
        if owner.user_id not in seen:
            seen.add(owner.user_id)
            unique.append(owner)
    return unique


def is_gateway_admin(sender_id: str, platform: str = "") -> bool:
    """Whether this platform identity may use admin-grade gateway actions.

    Sources, in order:

    1. ``KAZMA_GATEWAY_ADMINS`` env (comma list of user ids or
       ``platform:id`` sender ids). When set it is authoritative.
    2. Membership in the platform's configured ``allowed_users`` list —
       the operator explicitly curated those users.
    3. With that list empty: the owner's own accounts Kazma knows
       (:func:`known_owner_ids`) -- the only senders a private chat app
       answers then.
    4. Otherwise: NOT admin. With the open switch on
       (``KAZMA_GATEWAY_OPEN_TO_EVERYONE``) every workspace member may chat,
       and none of them but the owner may install packages, flip global
       config, or switch the global model.

    Source 2 is a settings read: async callers (every button press on the
    chat apps) use ``asyncio.to_thread`` (audit 2026-10-26 AUD-111).
    """
    sender_id = (sender_id or "").strip()
    if not sender_id:
        return False
    candidates = _candidate_tokens(sender_id)

    env_raw = (os.environ.get(GATEWAY_ADMINS_ENV) or "").strip()
    if env_raw:
        sender_plat = (
            platform or (sender_id.split(":", 1)[0] if ":" in sender_id else "")
        ).strip().lower()
        for part in split_ids(env_raw):
            if ":" in part:
                # A qualified entry (`telegram:12345`) grants admin on THAT
                # platform only. Expanding it to its bare tail — which is what
                # _candidate_tokens does — meant `telegram:12345` also matched
                # a Discord user whose snowflake happened to be 12345. Both
                # platforms use numeric ids, so the collision is possible and
                # silently grants admin on a platform the operator never named
                # (audit 2026-09-16 F-8).
                entry_plat, entry_id = part.split(":", 1)
                entry_uid = _platform_user_id(entry_plat, entry_id)
                if (
                    entry_plat.strip().lower() == sender_plat
                    and entry_uid
                    and entry_uid in candidates
                ):
                    return True
            elif part in candidates:
                # An UNqualified entry (`12345`) is the operator saying "this
                # id, wherever it appears" — honour it as written.
                return True
        return False

    plat = (platform or "").strip().lower()
    if not plat and ":" in sender_id:
        plat = sender_id.split(":", 1)[0].strip().lower()
    if plat:
        try:
            from kazma_core.config_store import get_config_store

            store = get_config_store()
            raw = store.get(f"connectors.{plat}.allowed_users", "")
            listed = split_ids(raw)
            if not listed:
                listed = [owner.user_id for owner in known_owner_ids(store, plat)]
        except Exception:
            logger.debug("[allowlists] admin allowlist read failed", exc_info=True)
            return False
        allow_tokens: set[str] = set()
        for part in listed:
            allow_tokens |= _candidate_tokens(_platform_user_id(plat, part))
        return bool(candidates & allow_tokens)
    return False


def _cs_get(config_store: Any, key: str, default: str = "") -> Any:
    try:
        return config_store.get(key, default)
    except Exception:
        logger.debug("[allowlists] config get %s failed", key, exc_info=True)
        return default


def apply_adapter_allowlists(adapter: Any, config_store: Any) -> None:
    """Push ConfigStore allowlists, and the owner's own ids Kazma knows, onto
    one live adapter."""
    if adapter is None or config_store is None:
        return
    name = str(getattr(adapter, "name", "") or "").strip().lower()
    if hasattr(adapter, "set_owner_ids"):
        adapter.set_owner_ids(owner.user_id for owner in known_owner_ids(config_store, name))
    if name == "telegram":
        raw = _cs_get(config_store, "connectors.telegram.allowed_users", "")
        ids: list[int] = []
        for part in split_ids(raw):
            try:
                ids.append(int(part))
            except ValueError:
                logger.warning("[allowlists] Invalid Telegram user id: %s", part)
        if hasattr(adapter, "set_allowed_users"):
            adapter.set_allowed_users(ids)
        return
    if name == "discord":
        raw = _cs_get(config_store, "connectors.discord.allowed_users", "")
        if hasattr(adapter, "set_allowed_users"):
            adapter.set_allowed_users(split_ids(raw))
        # The card's Guild ID: server messages from those servers only (direct
        # messages always). It was saved and read by nothing until 2026-09-29.
        if hasattr(adapter, "set_allowed_guilds"):
            adapter.set_allowed_guilds(
                split_ids(_cs_get(config_store, "connectors.discord.guild_id", ""))
            )
        return
    if name == "whatsapp":
        # Numbers in any spelling: the adapter keeps them as WhatsApp ids.
        if hasattr(adapter, "set_allowed_users"):
            adapter.set_allowed_users(split_ids(_cs_get(config_store, "connectors.whatsapp.allowed_users", "")))
        return
    if name == "slack":
        users = split_ids(_cs_get(config_store, "connectors.slack.allowed_users", ""))
        teams = split_ids(_cs_get(config_store, "connectors.slack.allowed_teams", ""))
        channels = split_ids(
            _cs_get(config_store, "connectors.slack.allowed_channels", "")
        )
        if hasattr(adapter, "set_allowed_users"):
            adapter.set_allowed_users(users)
        if hasattr(adapter, "set_allowed_teams"):
            adapter.set_allowed_teams(teams)
        if hasattr(adapter, "set_allowed_channels"):
            adapter.set_allowed_channels(channels)


def apply_gateway_allowlists(gateway: Any, config_store: Any) -> int:
    """Apply allowlists to every adapter on *gateway*. Returns adapter count."""
    if gateway is None:
        return 0
    adapters = list(getattr(gateway, "adapters", None) or [])
    for adapter in adapters:
        try:
            apply_adapter_allowlists(adapter, config_store)
        except Exception:
            logger.warning(
                "[allowlists] apply failed for %s",
                getattr(adapter, "name", type(adapter).__name__),
                exc_info=True,
            )
    return len(adapters)
