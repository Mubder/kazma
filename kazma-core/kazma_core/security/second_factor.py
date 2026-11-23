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

A code is taken once: the step a code matched is kept per account
(:data:`STEPS_KEY`, runtime state), and a code of that step or an earlier
one is refused. Checking a code is :func:`check`, which ``POST
/api/auth/login`` asks after a right password (or, for the owner, the
install's secret): a password alone does not sign in to an account with a
second factor.

The admin resets a person's second factor from People when they lost the
phone and the codes (:func:`reset`, recorded on the People record and told
to the person); deleting a person forgets it (:func:`forget`). A vault that
cannot be read is :class:`SecondFactorUnavailable`: sign-in then refuses,
never "no second factor".
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
    "SecondFactorRefused",
    "SecondFactorUnavailable",
    "account_for_sign_in",
    "begin",
    "check",
    "confirm",
    "forget",
    "reset",
    "state",
    "turn_off",
]

logger = logging.getLogger(__name__)

#: The vault names: ``account.2fa.<account id>`` (on) and ``....pending``.
#: Install-scoped (``vault.INSTALL_SCOPED_SECRETS``).
VAULT_PREFIX = "account.2fa."
#: The last step a code was taken from, by account: runtime state.
STEPS_KEY = "system.sign_in.second_factor_steps"
#: How many recovery codes a person gets.
RECOVERY_CODES = 10
#: How long a secret begun waits for its first code.
PENDING_S = 15 * 60
#: The user id the install's shared secret signs in as (the owner).
_SECRET_USER_ID = "shared-secret"
_RECOVERY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_lock = threading.Lock()


class SecondFactorRefused(ValueError):
    """A change that is refused; nothing was changed."""

    def __init__(self, code: str, status: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class SecondFactorUnavailable(RuntimeError):
    """The vault, or the store of used steps, cannot be read now."""


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


def state(account_id: str) -> dict[str, Any]:
    """Whether *account_id* has a second factor (``on``), since when, and how
    many recovery codes are left. Never the secret."""
    held = _read(_name(account_id)) if account_id else None
    if not held or not held.get("secret"):
        return {"on": False, "since": "", "recovery_left": 0}
    return {"on": True, "since": str(held.get("since") or ""),
            "recovery_left": len(held.get("recovery") or [])}


# ── Turning it on and off ────────────────────────────────────────────────


def begin(account_id: str, label: str) -> dict[str, str]:
    """A new secret for *account_id*, kept pending until :func:`confirm`:
    ``{"secret", "uri"}`` (shown once). Refused while one is on."""
    if not account_id:
        raise SecondFactorRefused("no_account", 403, "Only an account has a second factor.")
    with _lock:
        if state(account_id)["on"]:
            raise SecondFactorRefused("already_on", 409, "A second factor is on already. Turn it off first.")
        secret = totp.new_secret()
        _write(_pending_name(account_id), {"secret": secret, "at": time.time()})
    return {"secret": secret, "uri": totp.provisioning_uri(secret, account=label or account_id)}


def confirm(account_id: str, code: str) -> list[str]:
    """Turn the pending secret on once *code* is right; answers the recovery
    codes (shown once). Refused: none pending (``nothing_begun``), one begun
    more than :data:`PENDING_S` ago (``begun_too_long_ago``), a wrong code
    (``wrong_code``)."""
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
        _write(_name(account_id), {
            "secret": str(pending["secret"]),
            "recovery": [_digest(c) for c in codes],
            "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        _delete(_pending_name(account_id))
        _keep_step(account_id, step)
    _record("second_factor_on", account_id, account_id)
    logger.info("[auth] a second factor was turned on for account %s", account_id)
    return codes


def turn_off(account_id: str, code: str) -> None:
    """Turn *account_id*'s second factor off, with a code from it or a
    recovery code. Refused: none on (``not_on``), a wrong code."""
    if not state(account_id)["on"]:
        raise SecondFactorRefused("not_on", 409, "No second factor is on.")
    if not check(account_id, code):
        raise SecondFactorRefused("wrong_code", 400, "That code is not right.")
    forget(account_id)
    _record("second_factor_off", account_id, account_id)
    logger.info("[auth] a second factor was turned off for account %s", account_id)


def reset(account_id: str, *, by: str) -> None:
    """The admin takes a person's second factor off (they lost the phone and
    the recovery codes): recorded, and told to the person."""
    if not state(account_id)["on"]:
        raise SecondFactorRefused("not_on", 409, "This person has no second factor.")
    forget(account_id)
    _record("second_factor_reset", account_id, by)
    logger.warning("[auth] the second factor of account %s was reset by %s", account_id, by)


def forget(account_id: str) -> None:
    """Delete *account_id*'s second factor and any begun one."""
    with _lock:
        _delete(_name(account_id))
        _delete(_pending_name(account_id))
        _forget_step(account_id)


# ── Checking a code ──────────────────────────────────────────────────────


def check(account_id: str, code: str) -> bool:
    """Whether *code* is a right code of *account_id*'s second factor: a code
    from the app, taken once, or a recovery code, used once. False with no
    second factor on. :class:`SecondFactorUnavailable` when it cannot say."""
    typed = _normal(code)
    if not typed:
        return False
    with _lock:
        held = _read(_name(account_id))
        if not held or not held.get("secret"):
            return False
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


def _steps() -> Any:
    from kazma_core.config_store import get_config_store

    return get_config_store()


def _last_step(account_id: str) -> int:
    try:
        held = _steps().get(STEPS_KEY) or {}
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

    _steps().atomic_update(STEPS_KEY, _update, category="system")


def _forget_step(account_id: str) -> None:
    def _update(current: Any) -> dict[str, int]:
        held = dict(current) if isinstance(current, dict) else {}
        held.pop(account_id, None)
        return held

    _steps().atomic_update(STEPS_KEY, _update, category="system")
