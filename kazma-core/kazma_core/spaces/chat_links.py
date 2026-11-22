"""Linking a person's chat-app account to their Kazma account (Spaces S3-01).

A signed-in person asks My account for a short code for one chat app
(:func:`issue_code`) and sends it to the bot in a direct chat. The adapter
that receives it hands it here (``sender_rule.SenderGate.link_attempted``,
asked by every message accept path before the sender rule), and
:func:`redeem` links the sender's platform account to the account that asked
for the code. From then on that chat-app account is the person's own Kazma
account (S3-02 makes the sender rule ask the link).

The rules:

* **One use, ten minutes.** A code is kept only as its hash, under the
  runtime settings key :data:`CODES_KEY`, beside its account, its chat app
  and when it expires. The first redemption marks it used in the same atomic
  step that reads it; a second one links nothing. A new code for the same
  chat app replaces the person's earlier one.
* **In a direct chat only.** A live code typed in a group, a server channel
  or a Slack channel has been seen by others: it is burned there and then,
  and the group is told once that codes work only in a direct message.
* **For the chat app it was made for.** A Telegram code sent on Discord is
  burned too, and links nothing.
* **One Kazma account per chat-app account.** A chat-app account linked to
  another Kazma account is not moved by a code: it is unlinked there first.
* **A guess costs.** A sender's codes are counted as they arrive
  (:func:`take_attempt`, before anything is read): past
  :data:`MAX_ATTEMPTS` within :data:`ATTEMPT_WINDOW_S` none is looked at or
  answered until the window passes (in memory: a restart forgets it). A
  flood of guesses at once is counted as it comes, never after.

Only where Spaces is enforced (:func:`linking_on`): an install with one
account issues no code and every message, code-shaped or not, goes where it
always went.

Links live in the identity store's ``links`` table (``identity`` registry):
``{"platform", "user_id", "account", "linked_at"}``, unique per chat-app
account; every link is an event of the People record (``chat_linked``).

Every function here that reads or writes the settings store blocks: call it
off the event loop. :func:`code_in` and :func:`take_attempt` touch memory
only.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from kazma_core.spaces import identity
from kazma_core.spaces.scope import spaces_enforced

__all__ = [
    "CODES_KEY",
    "ATTEMPT_WINDOW_S",
    "CODE_TTL_S",
    "LINK_OUTCOMES",
    "LinkRefused",
    "MAX_ATTEMPTS",
    "PLATFORMS",
    "PLATFORM_LABELS",
    "Redemption",
    "code_in",
    "issue_code",
    "linking_on",
    "links_of",
    "platform_user_id",
    "redeem",
    "reply_text",
    "send_text",
    "take_attempt",
]

logger = logging.getLogger(__name__)

#: The chat apps a person links.
PLATFORMS: tuple[str, ...] = ("telegram", "discord", "slack", "whatsapp")
PLATFORM_LABELS: dict[str, str] = {
    "telegram": "Telegram", "discord": "Discord", "slack": "Slack", "whatsapp": "WhatsApp",
}

#: The runtime settings key of the live codes: ``{hash: entry}``. Never the
#: code itself.
CODES_KEY = "identity.link_codes"
#: How long a code works.
CODE_TTL_S = 600
#: How long an expired or used code is still known (so "used" and "expired"
#: are told apart from a code that never was), after its expiry.
_KEEP_AFTER_EXPIRY_S = 600
#: Live codes kept at most; the oldest go first.
_MAX_CODES = 500

#: A sender's codes looked at, at most ...
MAX_ATTEMPTS = 5
#: ... within this window; past it, none is read or answered until it passes.
ATTEMPT_WINDOW_S = 600

#: The letters a code is made of: no I, L, O, 0 or 1 (each reads as another).
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LEN = 8

#: What :func:`redeem` answers.
LINK_OUTCOMES = (
    "linked",            # the chat-app account is now the code's account
    "already_linked",    # it already was
    "linked_elsewhere",  # it is another Kazma account's: nothing moved
    "wrong_code",        # no such code
    "used",              # the code was used
    "expired",           # the code ran out
    "wrong_platform",    # the code was made for another chat app: burned
    "not_direct",        # a live code typed in a group: burned
    "account_gone",      # the code's account was disabled or deleted
    "unavailable",       # the store could not answer: nothing changed
)
#: Outcomes a group is told (the rest are said in a direct chat only).
_TOLD_IN_A_GROUP = frozenset({"not_direct"})

# A code as the person sends it: ``/link ABCD-2345`` (Telegram, with or
# without the bot's name), ``link ABCD-2345``, ``ربط ABCD-2345``, or the code
# alone with its dash. The text is stripped and at most _MAX_TEXT long, so
# both patterns are anchored and bounded (tests/test_regex_sweep.py).
_PREFIXED = re.compile(
    r"(?:/link(?:@[A-Za-z0-9_]{1,64})?|link|ربط)\s+([A-Za-z0-9]{4}-?[A-Za-z0-9]{4})",
    re.IGNORECASE,
)
_BARE = re.compile(r"([A-Za-z0-9]{4}-[A-Za-z0-9]{4})")
_MAX_TEXT = 96


class LinkRefused(ValueError):
    """A code that is not issued; ``code`` and ``status`` are for the page."""

    def __init__(self, code: str, status: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class Redemption:
    """What became of a code a sender sent."""

    outcome: str
    platform: str
    account: str = ""
    #: The chat app the code was made for (``wrong_platform``).
    made_for: str = ""
    #: The language the code was asked for in ("" when the code is unknown).
    language: str = ""
    #: The name of the account linked (``linked``, ``already_linked``): the
    #: sender is told whose account they are now.
    account_name: str = ""

    def reply_in_group(self) -> bool:
        """Whether a group is told: only that a live code was burned there
        (a direct chat is always answered)."""
        return self.outcome in _TOLD_IN_A_GROUP


def linking_on() -> bool:
    """Whether codes are issued and redeemed: only where Spaces is enforced
    (read from memory)."""
    return spaces_enforced()


def _now() -> float:
    return time.time()


def _hash(code: str) -> str:
    return hashlib.sha256(f"kazma-chat-link:{code}".encode()).hexdigest()


def _normal(code: str) -> str:
    return re.sub(r"[\s-]", "", str(code or "")).upper()


def _display_code(code: str) -> str:
    """A code as the page shows it: ``ABCD-2345``."""
    plain = _normal(code)
    return f"{plain[:4]}-{plain[4:]}" if len(plain) == _CODE_LEN else plain


def send_text(platform: str, code: str) -> str:
    """What the person sends the bot: Telegram's ``/link CODE`` (a command
    there), the code alone elsewhere (Slack and Discord take ``/`` as their
    own commands)."""
    shown = _display_code(code)
    return f"/link {shown}" if platform == "telegram" else shown


def code_in(text: Any) -> str | None:
    """The code a message carries, normalized, or None when it is no link
    attempt. Pure: no store read (asked on the adapter's loop).

    A message that names ``/link`` (or ``link``, ``ربط``) is an attempt even
    when its code holds a letter no code has; a code alone counts only in
    its own letters and with its dash, so a date or a word is never taken
    for one."""
    if not isinstance(text, str):
        return None
    said = text.strip()
    if not said or len(said) > _MAX_TEXT:
        return None
    prefixed = _PREFIXED.fullmatch(said)
    if prefixed:
        return _normal(prefixed.group(1))
    bare = _BARE.fullmatch(said)
    if bare:
        plain = _normal(bare.group(1))
        if len(plain) == _CODE_LEN and all(ch in _ALPHABET for ch in plain):
            return plain
    return None


def platform_user_id(platform: str, raw: Any) -> str:
    """A chat-app user id as a link keeps it: a WhatsApp number in digits
    (the ``wa_id`` WhatsApp sends), a Discord sender's user part, every other
    platform's as written."""
    text = str(raw if raw is not None else "").strip()
    plat = (platform or "").strip().lower()
    if plat == "whatsapp":
        return re.sub(r"\D", "", text)
    if plat == "discord" and ":" in text:
        return text.split(":", 1)[0]
    return text


# ── Guesses ─────────────────────────────────────────────────────────────

_attempts: dict[str, list[float]] = {}
_attempts_lock = threading.Lock()


def take_attempt(platform: str, user_id: str) -> bool:
    """Count a code from this sender as it arrives, and say whether it is
    looked at: no more than :data:`MAX_ATTEMPTS` in the last
    :data:`ATTEMPT_WINDOW_S`. Memory only (asked on the adapter's loop)."""
    key = f"{platform}:{user_id}"
    now = _now()
    with _attempts_lock:
        kept = [at for at in _attempts.get(key, ()) if now - at < ATTEMPT_WINDOW_S]
        if len(kept) >= MAX_ATTEMPTS:
            _attempts[key] = kept
            return False
        kept.append(now)
        _attempts[key] = kept
        if len(_attempts) > 10_000:  # a flood of senders: forget the oldest half
            for old in list(_attempts)[: len(_attempts) // 2]:
                _attempts.pop(old, None)
        return True


def _forget_attempts_for_tests() -> None:
    with _attempts_lock:
        _attempts.clear()


# ── Codes ───────────────────────────────────────────────────────────────


def _kept(table: Any, now: float) -> dict[str, dict[str, Any]]:
    """The code table without what is past being known, newest last."""
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(table, dict):
        return out
    entries = []
    for digest, entry in table.items():
        if not isinstance(entry, dict):
            continue
        try:
            expires = float(entry.get("expires_at") or 0)
        except (TypeError, ValueError):
            continue
        if now - expires < _KEEP_AFTER_EXPIRY_S:
            entries.append((float(entry.get("issued_at") or 0), str(digest), entry))
    entries.sort()
    for _issued, digest, entry in entries[-_MAX_CODES:]:
        out[digest] = dict(entry)
    return out


def _store() -> Any:
    from kazma_core.config_store import get_config_store

    return get_config_store()


def _store_errors() -> tuple[type[BaseException], ...]:
    from kazma_core.db.pg_helpers import store_errors

    return store_errors()


def issue_code(account_id: str, platform: str, *, language: str = "") -> dict[str, Any]:
    """A new code for *account_id* to link their *platform* account with.

    Returns ``{"code", "platform", "expires_at" (epoch), "ttl_s", "send"}``;
    the code is never stored, only its hash. Replaces the account's earlier
    code for that chat app. Refused (:class:`LinkRefused`): Spaces off, an
    unknown chat app, an account that is not there or not enabled. Blocking.
    """
    if not linking_on():
        raise LinkRefused("spaces_off", 409, "Chat apps are linked once Spaces is on for this install.")
    plat = (platform or "").strip().lower()
    if plat not in PLATFORMS:
        raise LinkRefused("bad_platform", 400, f"Not a chat app Kazma links: {platform!r}")
    reg = identity.read_registry()
    account = (reg.get("accounts") or {}).get(account_id)
    if not account or not account.get("enabled", True):
        raise LinkRefused("no_account", 403, "This account cannot link a chat app.")
    code = "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LEN))
    now = _now()
    entry = {
        "account": account_id,
        "platform": plat,
        "issued_at": now,
        "expires_at": now + CODE_TTL_S,
        "lang": language if language in ("en", "ar") else "",
    }

    def update(current: Any) -> dict[str, dict[str, Any]]:
        table = _kept(current, now)
        for digest in [d for d, e in table.items()
                       if e.get("account") == account_id and e.get("platform") == plat
                       and not e.get("used_at")]:
            del table[digest]  # one live code per person and chat app
        table[_hash(code)] = entry
        return table

    _store().atomic_update(CODES_KEY, update, category="auth")
    logger.info("[spaces] a code to link %s was issued to account %s", PLATFORM_LABELS[plat], account_id)
    return {
        "code": _display_code(code),
        "platform": plat,
        "expires_at": entry["expires_at"],
        "ttl_s": CODE_TTL_S,
        "send": send_text(plat, code),
    }


def _take(code: str, platform: str, user_id: str, *, direct: bool) -> Redemption:
    """Read the code and mark it used, in one atomic step: what it was for,
    or why it links nothing."""
    digest = _hash(code)
    now = _now()
    found: list[Redemption] = []

    def update(current: Any) -> dict[str, dict[str, Any]]:
        found.clear()
        table = _kept(current, now)
        entry = table.get(digest)
        if entry is None:
            found.append(Redemption("wrong_code", platform))
            return table
        lang = str(entry.get("lang") or "")
        made_for = str(entry.get("platform") or "")
        if entry.get("used_at"):
            found.append(Redemption("used", platform, language=lang))
            return table
        if float(entry.get("expires_at") or 0) <= now:
            found.append(Redemption("expired", platform, language=lang))
            return table
        # From here the code is seen: whatever becomes of it, it is used.
        entry["used_at"] = now
        entry["used_by"] = f"{platform}:{user_id}"
        if made_for != platform:
            entry["burned"] = "wrong_platform"
            found.append(Redemption("wrong_platform", platform, made_for=made_for, language=lang))
        elif not direct:
            entry["burned"] = "not_direct"
            found.append(Redemption("not_direct", platform, language=lang))
        else:
            found.append(Redemption("linked", platform, account=str(entry.get("account") or ""),
                                    language=lang))
        table[digest] = entry
        return table

    _store().atomic_update(CODES_KEY, update, category="auth")
    return found[-1] if found else Redemption("unavailable", platform)


def _link(platform: str, user_id: str, account_id: str) -> tuple[str, str]:
    """Write the link. Returns the outcome -- ``linked``, ``already_linked``,
    ``linked_elsewhere`` or ``account_gone`` -- and the account's name."""
    from kazma_core.spaces import people

    said: list[str] = []
    name: list[str] = []

    def change(reg: dict[str, Any]) -> None:
        account = (reg.get("accounts") or {}).get(account_id)
        if not account or not account.get("enabled", True):
            raise identity.IdentityError("account_gone", "the code's account is not enabled")
        name[:] = [str(account.get("name") or "")]
        for link in reg["links"]:
            if link.get("platform") == platform and str(link.get("user_id")) == user_id:
                if link.get("account") == account_id:
                    raise identity.IdentityError("already_linked", "already linked")
                raise identity.IdentityError("linked_elsewhere", "linked to another account")
        reg["links"].append({
            "platform": platform,
            "user_id": user_id,
            "account": account_id,
            "linked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        })
        people.record_event(reg, "chat_linked", account_id, account_id, detail=platform)
        said.append("linked")

    try:
        identity.update_registry(change)
    except identity.IdentityError as exc:
        if exc.code in ("already_linked", "linked_elsewhere", "account_gone"):
            return exc.code, (name[-1] if name else "")
        raise
    return (said[-1] if said else "linked"), (name[-1] if name else "")


def redeem(platform: str, user_id: Any, code: str, *, direct: bool) -> Redemption:
    """Link the sender's *platform* account with the account that asked for
    *code*, or say why not. One use: the code is spent by the first message
    that reaches it whatever happens after. Blocking (the settings store)."""
    plat = (platform or "").strip().lower()
    uid = platform_user_id(plat, user_id)
    if plat not in PLATFORMS or not uid or not linking_on():
        return Redemption("wrong_code", plat)
    try:
        taken = _take(_normal(code), plat, uid, direct=direct)
    except _store_errors() as exc:
        logger.warning("[spaces] a link code could not be read (%s); nothing was linked",
                       type(exc).__name__)
        return Redemption("unavailable", plat)
    if taken.outcome != "linked":
        logger.info("[spaces] a code to link %s from %s did not link: %s",
                    PLATFORM_LABELS.get(plat, plat), uid, taken.outcome)
        return taken
    try:
        outcome, name = _link(plat, uid, taken.account)
    except (identity.IdentityUnavailable, identity.IdentityError, *_store_errors()) as exc:
        logger.warning("[spaces] linking %s %s failed (%s); the code is spent",
                       PLATFORM_LABELS[plat], uid, type(exc).__name__)
        return Redemption("unavailable", plat, language=taken.language)
    logger.warning("[spaces] %s %s: %s to account %s",
                   PLATFORM_LABELS[plat], uid, outcome.replace("_", " "), taken.account)
    return Redemption(outcome, plat, account=taken.account, language=taken.language,
                      account_name=name)


# ── Reading links ───────────────────────────────────────────────────────


def links_of(account_id: str) -> list[dict[str, Any]]:
    """The chat-app accounts linked to *account_id*, oldest first. Blocking."""
    return [dict(link) for link in identity.read_registry()["links"]
            if isinstance(link, dict) and link.get("account") == account_id]


def _link_of(platform: str, user_id: Any) -> str | None:
    """The Kazma account a chat-app account is linked to, or None. Blocking."""
    plat = (platform or "").strip().lower()
    uid = platform_user_id(plat, user_id)
    for link in identity.read_registry()["links"]:
        if isinstance(link, dict) and link.get("platform") == plat and str(link.get("user_id")) == uid:
            return str(link.get("account") or "") or None
    return None


# ── What the sender is told ─────────────────────────────────────────────

_WHERE_EN = "Kazma: Settings → My account → Chat apps"
_WHERE_AR = "Kazma: الإعدادات ← حسابي ← تطبيقات الدردشة"


def reply_text(redemption: Redemption, user_text: str | None = None) -> str:
    """What the sender is told, in the language the code was asked for in
    (else the language they wrote in)."""
    label = PLATFORM_LABELS.get(redemption.platform, redemption.platform)
    other = PLATFORM_LABELS.get(redemption.made_for, redemption.made_for)
    outcome = redemption.outcome
    who = redemption.account_name or "the owner"
    who_ar = redemption.account_name or "المالك"
    if outcome == "linked":
        en = (f"Linked: this {label} account is now the Kazma account of {who}, and what you "
              f"send here runs as {who}. If that is not you, ask whoever runs this Kazma to "
              "remove the link.")
        ar = (f"تمّ الربط: حساب {label} هذا صار حساب {who_ar} في Kazma، وما ترسله هنا يُنفَّذ "
              f"باسم {who_ar}. إن لم يكن هذا أنت فاطلب ممّن يدير Kazma هذا إزالة الربط.")
    elif outcome == "already_linked":
        en = f"This {label} account is already the Kazma account of {who}."
        ar = f"حساب {label} هذا هو حساب {who_ar} في Kazma من قبل."
    elif outcome == "linked_elsewhere":
        en = (f"This {label} account is linked to another Kazma account. Unlink it there first "
              f"({_WHERE_EN}).")
        ar = f"حساب {label} هذا مربوط بحساب آخر في Kazma. فُكّ ربطه هناك أولًا ({_WHERE_AR})."
    elif outcome == "wrong_platform":
        en = (f"That code was made for {other}, and it can no longer be used. Get a code for "
              f"{label} in {_WHERE_EN}.")
        ar = f"ذلك الرمز أُنشئ لـ{other}، ولم يعد صالحًا. احصل على رمز لـ{label} من {_WHERE_AR}."
    elif outcome == "not_direct":
        en = ("Link codes work only in a direct message with me. That code can no longer be "
              f"used: get a new one in {_WHERE_EN}, and send it to me directly.")
        ar = ("رموز الربط لا تعمل إلا في رسالة مباشرة إليّ. لم يعد ذلك الرمز صالحًا: احصل على رمز "
              f"جديد من {_WHERE_AR}، وأرسله إليّ مباشرة.")
    elif outcome == "account_gone":
        en = "That code's Kazma account is disabled or no longer exists. Nothing was linked."
        ar = "حساب Kazma صاحب ذلك الرمز معطّل أو لم يعد موجودًا. لم يُربط شيء."
    elif outcome == "unavailable":
        en = "Kazma could not link right now. Get a new code and try again in a minute."
        ar = "تعذّر على Kazma الربط الآن. احصل على رمز جديد وحاول بعد دقيقة."
    else:  # wrong_code, used, expired
        en = ("That code does not work. A code works once, for ten minutes. Get a new one in "
              f"{_WHERE_EN}.")
        ar = f"ذلك الرمز لا يعمل. الرمز يعمل مرة واحدة، لمدة عشر دقائق. احصل على رمز جديد من {_WHERE_AR}."
    if redemption.language == "ar":
        return ar
    if redemption.language == "en":
        return en
    from kazma_core.language_lock import in_their_language

    return in_their_language(user_text, en, ar)
