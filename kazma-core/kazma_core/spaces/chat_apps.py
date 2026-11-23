"""The space a chat app's message works in (Spaces).

The chat apps -- Telegram, Discord, Slack, WhatsApp -- are the install's
bots. :func:`chat_app_scope` is the thread-record check the gateway's
handler asks before it reads or builds anything of the message's chat:

* an install that does not enforce Spaces binds nothing, as before;
* once it does, a chat recorded in another space than the one the message
  works in is refused (:class:`ChatInAnotherSpace`), and a record that cannot
  be read is refused for what it is (:class:`ChatRecordUnreadable`).

**Where a message works (S3-03).** Once Spaces is enforced each sender is a
linked account (S3-02) and :func:`chat_place` says where their message
works, and as whom:

* a **direct chat** works in the space the person chose with ``/space``
  (:func:`space_command`), their personal space at first;
* a **group** (a Telegram group, a Discord server channel, a Slack channel)
  works in the one shared space a member bound it to with ``/space``; a
  sender who is not a member of that space is ignored there, and an unbound
  group answers only ``/space`` (and says so, at most every ten minutes);
* the turn runs as the sender's account in that space; each space keeps its
  own chats: the chat's sender key is the platform sender for the owner's
  space (the owner's chats stay where they always were) and carries the
  space for any other (:attr:`ChatPlace.sender_key`), so its thread is that
  space's alone.

The bindings are the settings key :data:`BINDINGS_KEY` (a credential: a
restore never moves a chat into another space): a person's
choice for each of their direct chats, and each group's space with who bound
it. A binding to a space the person or the binder is no longer a member of,
or that is gone, is as if none were set.

:func:`chat_app_work` is the rule for a chat-app command that reads the
connected accounts (``/x``): before Spaces the owner's tenant, as it always
was; once enforced the scope the handler bound, and nothing when none is
bound.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from kazma_core.spaces.scope import (
    OWNER_ACCOUNT_ID,
    OWNER_SPACE_ID,
    Scope,
    require_scope,
    spaces_enforced,
)
from kazma_core.spaces.threads import ThreadInAnotherSpace

__all__ = [
    "BINDINGS_KEY",
    "ChatInAnotherSpace",
    "ChatPlace",
    "ChatRecordUnreadable",
    "chat_app_scope",
    "chat_app_work",
    "chat_is_the_askers",
    "chat_place",
    "is_space_command",
    "space_command",
]

logger = logging.getLogger(__name__)


class ChatInAnotherSpace(ThreadInAnotherSpace):
    """A chat app's message reached a chat that belongs to another space."""


class ChatRecordUnreadable(ChatInAnotherSpace):
    """Whose a chat app's chat is cannot be read right now: refused like a
    chat of another space, and said for what it is (a retry may work)."""


def chat_app_scope(thread_id: str, place: ChatPlace | None = None) -> Scope | None:
    """The scope a chat app's message on *thread_id* works in, or None when
    nothing is bound (an install that does not enforce Spaces): the place's
    scope (:func:`chat_place`), else the owner's in the owner's space.

    Raises :class:`ChatInAnotherSpace` for a chat recorded in another space
    than that, and when the chat's record cannot be read (never guessed).
    Blocking: the thread record store, on a first asking."""
    if not spaces_enforced():
        return None
    from kazma_core.spaces import threads

    bound = place.scope if place is not None and place.scope is not None else Scope(
        space_id=OWNER_SPACE_ID, asker=OWNER_ACCOUNT_ID)
    try:
        record = threads.record_of(str(thread_id or ""))
    except (sqlite3.Error, OSError) as exc:
        logger.warning("[spaces] the chat %s's record cannot be read (%s); the message is refused",
                       str(thread_id)[:12], type(exc).__name__)
        raise ChatRecordUnreadable("The chat's record cannot be read right now.") from None
    if record is not None and record.space_id != bound.space_id:
        raise ChatInAnotherSpace(
            "This chat belongs to another space than the one this chat app works in here."
        )
    return bound


# ── Where a chat-app message works (S3-03) ──────────────────────────────

#: The settings key of the chats' spaces: ``{"direct": {"<platform>:
#: <user id>": {"space", "at"}}, "group": {"<platform>:<chat>": {"space",
#: "by", "at"}}}``.
BINDINGS_KEY = "identity.chat_spaces"
#: An unbound group is told how to bind it at most this often.
_UNBOUND_SAID_EVERY_S = 600.0
_unbound_said: dict[str, float] = {}
_unbound_lock = threading.Lock()


@dataclass(frozen=True)
class ChatPlace:
    """Where one chat-app message works, or why it does not."""

    #: The space and the account the message works as; None when refused.
    scope: Scope | None
    #: The sender key the chat's thread is found by: the platform sender in
    #: the owner's space, the sender and the space anywhere else.
    sender_key: str = ""
    #: Why it does not work: ``not_linked``, ``group_unbound``, ``not_a_member``.
    refusal: str = ""
    #: What the sender is told ("" for nothing).
    reply: str = ""
    #: A group chat (else a direct chat).
    group: bool = False


def _is_direct(platform: str, meta: dict[str, Any]) -> bool:
    """Whether a message came in a direct chat with the bot: a Telegram
    private chat, a Discord direct message (no server), a Slack direct
    message (a ``D`` channel), WhatsApp (always one person)."""
    plat = (platform or "").strip().lower()
    if plat == "telegram":
        return str(meta.get("chat_type") or "private") == "private"
    if plat == "discord":
        return not meta.get("guild_id")
    if plat == "slack":
        return str(meta.get("channel_id") or "").startswith("D") or meta.get("channel_type") == "im"
    return True


def _group_of(platform: str, meta: dict[str, Any]) -> str:
    """The group a message came in: its chat (Telegram), channel (Discord,
    Slack)."""
    plat = (platform or "").strip().lower()
    if plat == "telegram":
        return str(meta.get("chat_id") or "")
    return str(meta.get("channel_id") or "")


def _user_of(platform: str, meta: dict[str, Any], sender_id: str) -> str:
    from kazma_core.spaces.chat_links import platform_user_id

    raw = meta.get("user_id")
    if raw in (None, "") and ":" in sender_id:
        raw = sender_id.split(":", 1)[1]
    return platform_user_id(platform, raw)


def _store() -> Any:
    from kazma_core.config_store import get_config_store

    return get_config_store()


def _bindings() -> dict[str, dict[str, dict[str, Any]]]:
    raw = _store().get(BINDINGS_KEY, None)
    out: dict[str, dict[str, dict[str, Any]]] = {"direct": {}, "group": {}}
    if isinstance(raw, dict):
        for part in ("direct", "group"):
            table = raw.get(part)
            if isinstance(table, dict):
                out[part] = {str(k): dict(v) for k, v in table.items() if isinstance(v, dict)}
    return out


def _bind(part: str, key: str, entry: dict[str, Any] | None) -> None:
    def update(current: Any) -> dict[str, Any]:
        table: dict[str, Any] = dict(current) if isinstance(current, dict) else {}
        section = dict(table.get(part) or {}) if isinstance(table.get(part), dict) else {}
        if entry is None:
            section.pop(key, None)
        else:
            section[key] = entry
        table[part] = section
        return table

    _store().atomic_update(BINDINGS_KEY, update, category="identity")


def _personal_space(reg: dict[str, Any], account_id: str) -> str:
    for space_id, space in (reg.get("spaces") or {}).items():
        if space.get("personal_of") == account_id and not space.get("account_deleted_at"):
            return str(space_id)
    return ""


def _member(reg: dict[str, Any], account_id: str, space_id: str) -> bool:
    space = (reg.get("spaces") or {}).get(space_id) or {}
    if not space or space.get("erasing_at"):
        return False
    return any(m.get("account") == account_id and m.get("space") == space_id
               for m in reg.get("memberships") or ())


def _space_name(reg: dict[str, Any], space_id: str, lang_ar: bool) -> str:
    space = (reg.get("spaces") or {}).get(space_id) or {}
    if space.get("personal_of"):
        return "مساحتك الشخصية" if lang_ar else "your personal space"
    return str(space.get("name") or space_id)


def _say(user_text: str | None, english: str, arabic: str) -> str:
    from kazma_core.language_lock import in_their_language

    return in_their_language(user_text, english, arabic)


def _key_for(sender_id: str, space_id: str) -> str:
    return sender_id if space_id == OWNER_SPACE_ID else f"{sender_id}--in-{space_id}"


def _unbound_reply(platform: str, group: str, user_text: str | None) -> str:
    now = time.time()
    key = f"{platform}:{group}"
    with _unbound_lock:
        if now - _unbound_said.get(key, 0.0) < _UNBOUND_SAID_EVERY_S:
            return ""
        _unbound_said[key] = now
        if len(_unbound_said) > 5000:
            _unbound_said.clear()
    return _say(
        user_text,
        "This group works in no space yet. A member of a shared space binds it: send /space and "
        "the space's name. Then everyone's messages here run in that space; people who are not "
        "its members are not answered.",
        "هذه المجموعة لا تعمل في أي مساحة بعد. يربطها عضو في مساحة مشتركة: أرسل /space واسم المساحة. "
        "بعدها تُنفَّذ رسائل الجميع هنا في تلك المساحة، ولا يُرد على من ليس عضوًا فيها.",
    )


def chat_place(platform: str, meta: dict[str, Any], sender_id: str,
               user_text: str | None = None) -> ChatPlace:
    """Where a chat-app message works once Spaces is enforced (S3-03): its
    sender's linked account, in the space their direct chat chose or the
    shared space the group is bound to. Blocking (the identity store and the
    bindings): call it off the loop. Raises ``IdentityUnavailable`` when the
    identity store cannot answer (never guessed)."""
    from kazma_core.spaces import chat_links, identity

    plat = (platform or "").strip().lower()
    uid = _user_of(plat, meta, sender_id)
    reg = identity.read_registry()
    account = ""
    for link in reg.get("links") or ():
        if isinstance(link, dict) and link.get("platform") == plat and str(link.get("user_id")) == uid:
            account = str(link.get("account") or "")
    person = (reg.get("accounts") or {}).get(account) or {}
    if not account or not person or not person.get("enabled", True):
        return ChatPlace(None, refusal=chat_links.NOT_LINKED)
    bindings = _bindings()
    if _is_direct(plat, meta):
        chosen = str((bindings["direct"].get(f"{plat}:{uid}") or {}).get("space") or "")
        space = chosen if chosen and _member(reg, account, chosen) else _personal_space(reg, account)
        if not space:
            return ChatPlace(None, refusal="not_a_member")
        return ChatPlace(Scope(space, account), sender_key=_key_for(sender_id, space))
    group = _group_of(plat, meta)
    bound = str((bindings["group"].get(f"{plat}:{group}") or {}).get("space") or "")
    shared = bound and not ((reg.get("spaces") or {}).get(bound) or {}).get("personal_of")
    if not group or not bound or not shared or (reg.get("spaces") or {}).get(bound, {}).get("erasing_at"):
        return ChatPlace(None, refusal="group_unbound", group=True,
                         reply=_unbound_reply(plat, group, user_text))
    if not _member(reg, account, bound):
        return ChatPlace(None, refusal="not_a_member", group=True)
    return ChatPlace(Scope(bound, account), sender_key=f"{plat}:grp{group}--in-{bound}", group=True)


def chat_is_the_askers(space_id: str, asker: str, platform: str, target: str,
                       source_thread: str = "") -> bool:
    """Whether the chat-app chat *target* (``<platform>:<chat>``) is one
    work of *asker* in *space_id* may be delivered to (S3-04): a group bound
    to that space while the asker is a member of it, or the asker's own chat
    in that space (the source thread's record: that space, opened by them).
    Never another person's chat. Blocking (the identity store, the bindings,
    the thread record); raises ``IdentityUnavailable`` when who is a member
    cannot be read."""
    from kazma_core.spaces import identity, threads

    if not space_id or not asker or ":" not in str(target or ""):
        return False
    reg = identity.read_registry()
    if not _member(reg, asker, space_id):
        return False
    plat = (platform or "").strip().lower()
    chat = str(target).split(":", 1)[1]
    bound = str((_bindings()["group"].get(f"{plat}:{chat}") or {}).get("space") or "")
    if bound:
        return bound == space_id
    record = threads.record_of(str(source_thread or "")) if source_thread else None
    return record is not None and record.space_id == space_id and record.opener == asker


# ── /space ──────────────────────────────────────────────────────────────

_SPACE_COMMAND = re.compile(r"/space(?:@[A-Za-z0-9_]{1,64})?(?:\s+(.{1,80}))?", re.S)


#: The words ``/space`` reads beside a space's name or number, in English
#: and Arabic alike.
_SPACE_WORDS = {"personal": "personal", "me": "personal", "mine": "personal",
                "شخصي": "personal", "شخصية": "personal", "مساحتي": "personal",
                "none": "none", "off": "none", "لا شيء": "none", "بدون": "none"}


def _space_word(word: str) -> str:
    """What a ``/space`` argument names beside a space: ``personal`` (the
    person's own space), ``none`` (a group bound to nothing), or "". Pure."""
    return _SPACE_WORDS.get(" ".join((word or "").split()).casefold(), "")


def is_space_command(text: Any) -> bool:
    """Whether *text* is ``/space`` (with or without an argument). Pure."""
    return isinstance(text, str) and _SPACE_COMMAND.fullmatch(text.strip()) is not None


def space_command(platform: str, meta: dict[str, Any], sender_id: str, text: str) -> str:
    """Answer ``/space``: show where this chat works and the spaces the
    person may choose (a direct chat), or bind a group to one shared space
    (a member of it; to move a bound group, a member of both). Returns the
    reply. Blocking (the identity store and the bindings)."""
    from kazma_core.spaces import identity

    match = _SPACE_COMMAND.fullmatch((text or "").strip())
    wanted = (match.group(1) if match else "") or ""
    wanted = wanted.strip()
    arabic = bool(re.search("[\u0600-\u06ff]", wanted))
    plat = (platform or "").strip().lower()
    uid = _user_of(plat, meta, sender_id)
    reg = identity.read_registry()
    account = next((str(link.get("account")) for link in reg.get("links") or ()
                    if isinstance(link, dict) and link.get("platform") == plat
                    and str(link.get("user_id")) == uid), "")
    if not account or not ((reg.get("accounts") or {}).get(account) or {}).get("enabled", True):
        return _say(text, "Link this account to your Kazma account first (My account → Chat apps).",
                    "اربط هذا الحساب بحسابك في Kazma أولًا (حسابي ← تطبيقات الدردشة).")
    mine = [s for s in identity.spaces_of(account) if not s.get("erasing_at")]
    direct = _is_direct(plat, meta)
    bindings = _bindings()

    def pick(word: str) -> dict[str, Any] | None:
        if word.isdigit() and 1 <= int(word) <= len(mine):
            return mine[int(word) - 1]
        low = word.casefold()
        for space in mine:
            if low in (str(space.get("id")).casefold(), str(space.get("name") or "").casefold()):
                return space
            if space.get("personal_of") == account and _space_word(word) == "personal":
                return space
        return None

    if direct:
        key = f"{plat}:{uid}"
        chosen = str((bindings["direct"].get(key) or {}).get("space") or "")
        current = chosen if chosen and _member(reg, account, chosen) else _personal_space(reg, account)
        if not wanted:
            lines_en = [f"This chat works in {_space_name(reg, current, False)}.", "Your spaces:"]
            lines_ar = [f"تعمل هذه المحادثة في {_space_name(reg, current, True)}.", "مساحاتك:"]
            for number, space in enumerate(mine, start=1):
                here = " ←" if space["id"] == current else ""
                lines_en.append(f"{number}. {_space_name(reg, space['id'], False)}{here}")
                lines_ar.append(f"{number}. {_space_name(reg, space['id'], True)}{here}")
            lines_en.append("Send /space and a number to switch. Each space keeps its own chats and memory.")
            lines_ar.append("أرسل /space ورقمًا للتبديل. لكل مساحة محادثاتها وذاكرتها.")
            return _say(text, "\n".join(lines_en), "\n".join(lines_ar))
        target = pick(wanted)
        if target is None:
            return _say(text, "You are not a member of a space by that name. Send /space to see yours.",
                        "لست عضوًا في مساحة بهذا الاسم. أرسل /space لترى مساحاتك.")
        _bind("direct", key, {"space": target["id"], "at": _now_iso()})
        logger.info("[spaces] a %s direct chat of account %s works in space %s now", plat, account,
                    target["id"])
        return _say(
            text if not arabic else wanted,
            f"This chat works in {_space_name(reg, target['id'], False)} now: what you send here runs "
            "there. Send /space to see your spaces.",
            f"تعمل هذه المحادثة في {_space_name(reg, target['id'], True)} الآن: ما ترسله هنا يُنفَّذ هناك. "
            "أرسل /space لترى مساحاتك.",
        )

    group = _group_of(plat, meta)
    gkey = f"{plat}:{group}"
    bound = str((bindings["group"].get(gkey) or {}).get("space") or "")
    bound_ok = bool(bound) and bound in (reg.get("spaces") or {})
    if not wanted:
        if bound_ok:
            return _say(text, f"This group works in {_space_name(reg, bound, False)}.",
                        f"تعمل هذه المجموعة في {_space_name(reg, bound, True)}.")
        return _unbound_reply(plat, group, text) or _say(
            text, "This group works in no space yet: a member of a shared space sends /space and its name.",
            "هذه المجموعة لا تعمل في أي مساحة بعد: يرسل عضو في مساحة مشتركة /space واسمها.")
    if bound_ok and not _member(reg, account, bound):
        return _say(text, "Only a member of the space this group works in may move it.",
                    "لا ينقل هذه المجموعة إلا عضو في المساحة التي تعمل فيها.")
    if _space_word(wanted) == "none":
        _bind("group", gkey, None)
        logger.info("[spaces] a %s group was unbound by account %s", plat, account)
        return _say(text, "This group works in no space now.", "لا تعمل هذه المجموعة في أي مساحة الآن.")
    target = pick(wanted)
    if target is None:
        return _say(text, "You are not a member of a space by that name.",
                    "لست عضوًا في مساحة بهذا الاسم.")
    if target.get("personal_of"):
        return _say(text, "A group works in a shared space only: a personal space is one person's.",
                    "لا تعمل المجموعة إلا في مساحة مشتركة: المساحة الشخصية لشخص واحد.")
    _bind("group", gkey, {"space": target["id"], "by": account, "at": _now_iso()})
    logger.info("[spaces] a %s group was bound to space %s by account %s", plat, target["id"], account)
    return _say(
        text if not arabic else wanted,
        f"This group works in {_space_name(reg, target['id'], False)} now: its members can talk to "
        "me here, and nobody else is answered.",
        f"تعمل هذه المجموعة في {_space_name(reg, target['id'], True)} الآن: يمكن لأعضائها التحدث إليّ "
        "هنا، ولا يُرد على غيرهم.",
    )


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


@contextmanager
def chat_app_work() -> Iterator[None]:
    """Run a chat-app command that reads the owner's connected accounts.

    Before Spaces: in the owner's tenant, as the gateway always did (the
    gateway installs no tenant of its own). Once enforced: in the scope the
    handler bound (:func:`chat_app_scope`); with none bound it raises
    ``NoSpaceBound`` -- never the owner's by default."""
    if spaces_enforced():
        require_scope()
        yield
        return
    from kazma_core.tenant_context import tenant_scope

    with tenant_scope(OWNER_SPACE_ID):
        yield
