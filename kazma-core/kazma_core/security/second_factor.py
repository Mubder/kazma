"""A person's second factor: a code from their authenticator app (Spaces S4-02).

Turned on in My account, per account. The secret is kept in the vault as an
install-scoped secret (``account.2fa.<account id>``: read by sign-in before
any space is bound), never in plaintext settings, together with the digests
of the person's recovery codes. Turning it on is two steps: :func:`begin`
makes a secret and keeps it pending (shown once, as text and as an
``otpauth://`` link an authenticator app reads), and :func:`confirm` turns it
on once a code from it is right, answering ten recovery codes shown once
(each signs in once when the phone is lost; only their SHA-256 digests are
kept).

Whether an account has one is also a plain marker in settings
(:data:`ON_KEY`: the account and since when, never the secret). Sign-in reads
the marker first and the vault only when it is on: a vault that failed to
open does not stop the sign-in of an account with no second factor (the
review of S4-02, item 7). A marker that is on with a vault that cannot be
read is :class:`SecondFactorUnavailable`: sign-in refuses, never "none".

A code is taken once: the step a code matched is kept per account
(:data:`STEPS_KEY`, runtime state), and a code of that step or an earlier
one is refused. Checking a code is :func:`check`, which ``POST
/api/auth/login`` asks after a right password (or, for the owner, the
install's secret): a password alone does not sign in to an account with a
second factor.

Wrong codes pause the account's code checks, in every mode and from every
address, a known one too (the review of S4-02, item 3): after
:data:`WRONG_CODES` wrong codes in a row no code is checked for
:data:`PAUSE_S`, doubling each pause up to :data:`PAUSE_MAX_S`
(:data:`PAUSES_KEY`, runtime state). A right code ends the count. The person
sees the pause in My account, and, where Spaces is enforced, is told at
their next page load (the People record).

The admin resets a person's second factor from People when they lost the
phone and the codes (:func:`reset`, recorded on the People record and told
to the person); deleting a person forgets it (:func:`forget`).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import sqlite3
import threading
import time
from typing import Any

from kazma_core.security import totp

__all__ = [
    "ON_KEY",
    "PAUSES_KEY",
    "SecondFactorPaused",
    "SecondFactorRefused",
    "SecondFactorUnavailable",
    "account_for_sign_in",
    "begin",
    "check",
    "confirm",
    "forget",
    "break_glass",
    "factor_on",
    "reset",
    "state",
    "turn_off",
]

logger = logging.getLogger(__name__)

#: The vault names: ``account.2fa.<account id>`` (on) and ``....pending``.
#: Install-scoped (``vault.INSTALL_SCOPED_SECRETS``).
VAULT_PREFIX = "account.2fa."
#: Which accounts have a second factor, and since when: no secret, so a plain
#: setting (a credential to ``settings_restore``: a restore never moves it).
ON_KEY = "security.second_factor_on"
#: The last step a code was taken from, by account: runtime state.
STEPS_KEY = "system.sign_in.second_factor_steps"
#: Wrong codes and pauses, by account: runtime state.
PAUSES_KEY = "system.sign_in.second_factor_pauses"
#: How many recovery codes a person gets.
RECOVERY_CODES = 10
#: How long a secret begun waits for its first code.
PENDING_S = 15 * 60
#: Wrong codes in a row before the account's code checks pause.
WRONG_CODES = 5
#: The first pause; each pause after it is twice as long, up to the last.
PAUSE_S = 15 * 60
PAUSE_MAX_S = 24 * 3600
#: A pause is shown in My account this long after it began.
_PAUSE_SHOWN_S = 7 * 24 * 3600
#: The user id the install's shared secret signs in as (the owner).
_SECRET_USER_ID = "shared-secret"
_RECOVERY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_lock = threading.Lock()

#: Accounts whose unreadable vault was said (:func:`_held`), once each.
_SAID_UNREADABLE: set[str] = set()


class SecondFactorRefused(ValueError):
    """A change that is refused; nothing was changed."""

    def __init__(self, code: str, status: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class SecondFactorPaused(SecondFactorRefused):
    """Too many wrong codes: no code is checked until *until* (epoch)."""

    def __init__(self, until: float) -> None:
        super().__init__("second_factor_paused", 429,
                         "Too many wrong codes. Codes are not checked for a while; try again later.")
        self.until = until


class SecondFactorUnavailable(RuntimeError):
    """The vault, or the store of used steps, cannot be read now."""


def _settings() -> Any:
    from kazma_core.config_store import get_config_store

    return get_config_store()


def _vault() -> Any:
    """The vault, or None on an install with no vault at all (no second
    factor can be kept there, so none is on). One that is not ready yet is
    :class:`SecondFactorUnavailable`: sign-in refuses rather than guess."""
    from kazma_core.security.vault import get_vault, vault_disabled

    got = get_vault()
    if got is None and not vault_disabled():
        raise SecondFactorUnavailable("the vault is not ready")
    return got


def _name(account_id: str) -> str:
    return f"{VAULT_PREFIX}{account_id}"


def _pending_name(account_id: str) -> str:
    return f"{VAULT_PREFIX}{account_id}.pending"


def _read(name: str) -> dict[str, Any] | None:
    vault = _vault()
    if vault is None:
        return None
    try:
        raw = vault.retrieve(name)
    except (sqlite3.Error, OSError, ValueError, RuntimeError, LookupError) as exc:
        raise SecondFactorUnavailable(f"the vault could not be read ({type(exc).__name__})") from exc
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise SecondFactorUnavailable("a second factor in the vault is not readable") from exc
    return value if isinstance(value, dict) else None


def _write(name: str, value: dict[str, Any]) -> None:
    vault = _vault()
    if vault is None:
        raise SecondFactorRefused("no_vault", 409, "A second factor is kept in the vault, and this "
                                  "install has none (KAZMA_VAULT_KEY is not set).")
    vault.store(name, json.dumps(value, sort_keys=True), category="auth")


def _delete(name: str) -> None:
    vault = _vault()
    if vault is not None:
        vault.delete(name, every_scope=True)


def _digest(code: str) -> str:
    return hashlib.sha256(_normal(code).encode("utf-8")).hexdigest()


def _normal(code: str) -> str:
    return "".join(ch for ch in str(code or "").upper() if ch.isalnum())


def _new_recovery_code() -> str:
    raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}"


def _record(kind: str, account_id: str, by: str) -> None:
    """One event of the People record, where Spaces is enforced (an install
    that does not enforce it stores no record)."""
    from kazma_core.spaces import identity
    from kazma_core.spaces.scope import spaces_enforced

    if not spaces_enforced():
        return
    from kazma_core.spaces.people import record_event

    def _change(reg: dict[str, Any]) -> None:
        if account_id in reg["accounts"]:
            record_event(reg, kind, account_id, by)

    identity.update_registry(_change)


# ── The marker ───────────────────────────────────────────────────────────


def _markers() -> dict[str, Any]:
    try:
        held = _settings().get(ON_KEY) or {}
    except (sqlite3.Error, OSError, RuntimeError) as exc:
        # The store away (``ConfigStoreUnavailableError`` is a RuntimeError).
        raise SecondFactorUnavailable(f"whether a second factor is on cannot be read ({type(exc).__name__})") from exc
    return held if isinstance(held, dict) else {}


def _is_on(account_id: str) -> bool:
    """Whether *account_id* has a second factor, from the marker alone: no
    vault read. Blocking (a settings read)."""
    return bool(account_id) and account_id in _markers()


def _mark(account_id: str, since: str) -> None:
    def _update(current: Any) -> dict[str, Any]:
        held = dict(current) if isinstance(current, dict) else {}
        held[account_id] = since
        return held

    _settings().atomic_update(ON_KEY, _update, category="security")


def _unmark(account_id: str) -> None:
    def _update(current: Any) -> dict[str, Any]:
        held = dict(current) if isinstance(current, dict) else {}
        held.pop(account_id, None)
        return held

    _settings().atomic_update(ON_KEY, _update, category="security")


# ── Reading ─────────────────────────────────────────────────────────────


def account_for_sign_in(user_id: str) -> str:
    """The account a sign-in is: the owner's for the install's secret, a
    local user's account by its user id, or ``""`` (no account: the older
    multi-user mode's user that has none, an OIDC sign-in)."""
    from kazma_core.spaces import identity
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

    uid = str(user_id or "")
    if uid == _SECRET_USER_ID:
        return OWNER_ACCOUNT_ID
    if not uid:
        return ""
    for account in identity.list_accounts():
        if str(account.get("legacy_user_id") or "") == uid:
            return str(account["id"])
    return ""


def _held(account_id: str) -> dict[str, Any] | None:
    """The second factor kept for *account_id*, or None when it has none.

    The marker says it is on: the vault must hold it, or it cannot be
    checked (:class:`SecondFactorUnavailable`). The marker is off: the vault
    is asked too -- a settings restore from before the factor existed took
    the marker away while the vault kept it (the S4-02 re-check), and a
    second factor never goes off silently. One found there is on, and its
    marker is put back. A vault that cannot be read while the marker is off
    answers no: a vault that failed to open never stops an account with no
    second factor (the review of S4-02, item 7)."""
    marked = _is_on(account_id)
    try:
        held = _read(_name(account_id))
    except SecondFactorUnavailable:
        if marked:
            raise
        if account_id not in _SAID_UNREADABLE:
            # Once per account and process: this runs on every request that
            # sends the install's secret.
            _SAID_UNREADABLE.add(account_id)
            logger.warning("[auth] the vault cannot be read; account %s has no second factor marked",
                           account_id, exc_info=True)
        return None
    if not held or not held.get("secret"):
        if marked:
            raise SecondFactorUnavailable("a second factor is marked on and its secret is not in the vault")
        return None
    if not marked:
        logger.warning("[auth] the second factor of account %s was kept with no marker; marked on again",
                       account_id)
        try:
            _mark(account_id, str(held.get("since") or ""))
        except (sqlite3.Error, OSError, RuntimeError):
            # It is on either way: the vault holds it.
            logger.warning("[auth] the marker of account %s could not be put back", account_id, exc_info=True)
    return held


def factor_on(account_id: str) -> bool:
    """Whether *account_id* has a second factor: its marker, or the vault's
    copy when the marker is off (:func:`_held`). Raises
    :class:`SecondFactorUnavailable` when it is marked on and cannot be
    read. Blocking (settings, the vault)."""
    return bool(account_id) and _held(account_id) is not None


def state(account_id: str) -> dict[str, Any]:
    """Whether *account_id* has a second factor (``on``), since when, how
    many recovery codes are left, and the last pause wrong codes made (when
    it began and until when; shown in My account for a week). Never the
    secret."""
    pause = _pause_of(account_id) if account_id else {}
    last = float(pause.get("last") or 0)
    paused = {"paused_until": float(pause.get("until") or 0) if last else 0.0,
              "last_pause": last if time.time() - last < _PAUSE_SHOWN_S else 0.0}
    held = _held(account_id) if account_id else None
    if not held:
        return {"on": False, "since": "", "recovery_left": 0, **paused}
    return {"on": True, "since": str(held.get("since") or ""),
            "recovery_left": len(held.get("recovery") or []), **paused}


# ── Turning it on and off ────────────────────────────────────────────────


def begin(account_id: str, label: str) -> dict[str, str]:
    """A new secret for *account_id*, kept pending until :func:`confirm`:
    ``{"secret", "uri"}`` (shown once). Refused while one is on."""
    if not account_id:
        raise SecondFactorRefused("no_account", 403, "Only an account has a second factor.")
    with _lock:
        if factor_on(account_id):
            raise SecondFactorRefused("already_on", 409, "A second factor is on already. Turn it off first.")
        secret = totp.new_secret()
        _write(_pending_name(account_id), {"secret": secret, "at": time.time()})
    return {"secret": secret, "uri": totp.provisioning_uri(secret, account=label or account_id)}


def confirm(account_id: str, code: str) -> list[str]:
    """Turn the pending secret on once *code* is right; answers the recovery
    codes (shown once). Refused: none pending (``nothing_begun``), one begun
    more than :data:`PENDING_S` ago (``begun_too_long_ago``), a wrong code
    (``wrong_code``). The secret is in the vault before the marker says on:
    a marker never names a second factor the vault lacks."""
    with _lock:
        pending = _read(_pending_name(account_id))
        if not pending or not pending.get("secret"):
            raise SecondFactorRefused("nothing_begun", 409, "Start again: no second factor is waiting.")
        if time.time() - float(pending.get("at") or 0) > PENDING_S:
            _delete(_pending_name(account_id))
            raise SecondFactorRefused("begun_too_long_ago", 409, "That was too long ago. Start again.")
        step = totp.verify(str(pending["secret"]), code)
        if step is None:
            raise SecondFactorRefused("wrong_code", 400, "That code is not right.")
        codes = [_new_recovery_code() for _ in range(RECOVERY_CODES)]
        since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _write(_name(account_id), {
            "secret": str(pending["secret"]),
            "recovery": [_digest(c) for c in codes],
            "since": since,
        })
        marked = False
        try:
            _mark(account_id, since)
            marked = True
        finally:
            if not marked:
                # Not on: the vault's copy goes, so the two never disagree.
                _delete(_name(account_id))
        _delete(_pending_name(account_id))
        _keep_step(account_id, step)
    _record("second_factor_on", account_id, account_id)
    logger.info("[auth] a second factor was turned on for account %s", account_id)
    return codes


def turn_off(account_id: str, code: str) -> None:
    """Turn *account_id*'s second factor off, with a code from it or a
    recovery code. Refused: none on (``not_on``), a wrong code, a pause."""
    if not factor_on(account_id):
        raise SecondFactorRefused("not_on", 409, "No second factor is on.")
    if not check(account_id, code):
        raise SecondFactorRefused("wrong_code", 400, "That code is not right.")
    forget(account_id)
    _record("second_factor_off", account_id, account_id)
    logger.info("[auth] a second factor was turned off for account %s", account_id)


def reset(account_id: str, *, by: str) -> None:
    """The admin takes a person's second factor off (they lost the phone and
    the recovery codes): recorded, and told to the person."""
    if not factor_on(account_id):
        raise SecondFactorRefused("not_on", 409, "This person has no second factor.")
    forget(account_id)
    _record("second_factor_reset", account_id, by)
    logger.warning("[auth] the second factor of account %s was reset by %s", account_id, by)


def forget(account_id: str) -> None:
    """Delete *account_id*'s second factor and any begun one. The vault's
    copy goes first, then the marker: a copy left behind would be found
    again (:func:`_held`), and a marker left on with no copy refuses until
    this is done again."""
    with _lock:
        _delete(_name(account_id))
        _unmark(account_id)
        _delete(_pending_name(account_id))
        _forget_step(account_id)
        _forget_pauses(account_id)


def break_glass(account_id: str, *, by: str) -> dict[str, bool]:
    """Take *account_id*'s second factor off from this machine, with no code
    and no server (``kazma account second-factor off``, the S4-02 re-check):
    the way back for an owner who lost the phone and the recovery codes, or
    whose vault was reset (the marker on, nothing in the vault). The caller
    holds the runtime lock, so no server is running. The marker always
    goes; the vault's copies go when the vault can be opened (a copy left
    in a vault that cannot be read is never asked for: :func:`_held`).
    Recorded. Returns what it removed."""
    if not account_id:
        raise SecondFactorRefused("no_account", 403, "Only an account has a second factor.")
    with _lock:
        marked = _is_on(account_id)
        vault_cleared = True
        try:
            _delete(_name(account_id))
            _delete(_pending_name(account_id))
        except (SecondFactorUnavailable, sqlite3.Error, OSError, RuntimeError, ValueError):
            vault_cleared = False
            logger.warning("[auth] break-glass: the vault's copy of account %s could not be removed",
                           account_id, exc_info=True)
        _unmark(account_id)
        _forget_step(account_id)
        _forget_pauses(account_id)
    _record("second_factor_reset", account_id, by)
    logger.warning("[auth] the second factor of account %s was taken off on this machine by %s", account_id, by)
    return {"was_marked": marked, "vault_cleared": vault_cleared}


# ── Checking a code ──────────────────────────────────────────────────────


def check(account_id: str, code: str) -> bool:
    """Whether *code* is a right code of *account_id*'s second factor: a code
    from the app, taken once, or a recovery code, used once. False with no
    second factor on. :class:`SecondFactorPaused` while wrong codes paused
    the account's checks; :class:`SecondFactorUnavailable` when it cannot
    say. A wrong code counts toward the pause; a right one ends the count."""
    typed = _normal(code)
    if not typed:
        return False
    with _lock:
        held = _held(account_id)
        if not held:
            return False
        until = float(_pause_of(account_id).get("until") or 0)
        if until > time.time():
            raise SecondFactorPaused(until)
        right = _matches(account_id, held, typed)
        # Counted under the lock (the S4-02 re-check): counted after it, 40
        # wrong codes at once were all checked and the pause ran to a day.
        paused = False if right else _count_wrong(account_id)
    if right:
        _clear_wrong(account_id)
        return True
    if paused:
        logger.warning("[auth] code checks of account %s paused after %d wrong codes", account_id, WRONG_CODES)
        try:
            _record("second_factor_paused", account_id, "kazma")
        except (RuntimeError, sqlite3.Error, OSError, ValueError):
            # The pause holds whether or not it is told.
            logger.warning("[auth] a paused second factor was not recorded for its person", exc_info=True)
    return False


def _matches(account_id: str, held: dict[str, Any], typed: str) -> bool:
    if typed.isdigit():
        step = totp.verify(str(held["secret"]), typed, after_step=_last_step(account_id))
        if step is None:
            return False
        _keep_step(account_id, step)
        return True
    digest = _digest(typed)
    left = [str(d) for d in held.get("recovery") or []]
    matched = [d for d in left if hmac.compare_digest(d, digest)]
    if not matched:
        return False
    held["recovery"] = [d for d in left if d != matched[0]]
    _write(_name(account_id), held)
    logger.warning("[auth] a recovery code of account %s was used; %d left",
                   account_id, len(held["recovery"]))
    return True


def _last_step(account_id: str) -> int:
    try:
        held = _settings().get(STEPS_KEY) or {}
    except (sqlite3.Error, OSError, RuntimeError) as exc:
        # The store away (``ConfigStoreUnavailableError`` is a RuntimeError):
        # which codes were used cannot be said.
        raise SecondFactorUnavailable(f"the used codes cannot be read ({type(exc).__name__})") from exc
    try:
        return int(held.get(account_id, -1)) if isinstance(held, dict) else -1
    except (TypeError, ValueError):
        return -1


def _keep_step(account_id: str, step: int) -> None:
    def _update(current: Any) -> dict[str, int]:
        held = dict(current) if isinstance(current, dict) else {}
        held[account_id] = max(int(held.get(account_id, -1) or -1), int(step))
        return held

    _settings().atomic_update(STEPS_KEY, _update, category="system")


def _forget_step(account_id: str) -> None:
    def _update(current: Any) -> dict[str, int]:
        held = dict(current) if isinstance(current, dict) else {}
        held.pop(account_id, None)
        return held

    _settings().atomic_update(STEPS_KEY, _update, category="system")


# ── Pauses after wrong codes ─────────────────────────────────────────────


def _pause_of(account_id: str) -> dict[str, Any]:
    try:
        held = _settings().get(PAUSES_KEY) or {}
    except (sqlite3.Error, OSError, RuntimeError) as exc:
        raise SecondFactorUnavailable(f"the pauses cannot be read ({type(exc).__name__})") from exc
    entry = held.get(account_id) if isinstance(held, dict) else None
    return dict(entry) if isinstance(entry, dict) else {}


def _count_wrong(account_id: str) -> bool:
    """Count one wrong code; True when it began a pause."""
    began: list[bool] = [False]
    now = time.time()

    def _update(current: Any) -> dict[str, Any]:
        held = dict(current) if isinstance(current, dict) else {}
        entry = dict(held.get(account_id) or {}) if isinstance(held.get(account_id), dict) else {}
        wrong = int(entry.get("wrong") or 0) + 1
        if wrong >= WRONG_CODES:
            pauses = int(entry.get("pauses") or 0)
            entry.update(wrong=0, pauses=pauses + 1, last=now,
                         until=now + min(PAUSE_MAX_S, PAUSE_S * (2 ** min(pauses, 16))))
            began[0] = True
        else:
            entry["wrong"] = wrong
        held[account_id] = entry
        return held

    _settings().atomic_update(PAUSES_KEY, _update, category="system")
    return began[0]


def _clear_wrong(account_id: str) -> None:
    """A right code: the count of wrong codes, and the growth of the next
    pause, start again. When the last pause was is kept (My account)."""
    def _update(current: Any) -> dict[str, Any]:
        held = dict(current) if isinstance(current, dict) else {}
        entry = held.get(account_id)
        if isinstance(entry, dict) and (entry.get("wrong") or entry.get("pauses")):
            held[account_id] = {k: v for k, v in entry.items() if k in ("last", "until")}
        return held

    _settings().atomic_update(PAUSES_KEY, _update, category="system")


def _forget_pauses(account_id: str) -> None:
    def _update(current: Any) -> dict[str, Any]:
        held = dict(current) if isinstance(current, dict) else {}
        held.pop(account_id, None)
        return held

    _settings().atomic_update(PAUSES_KEY, _update, category="system")
