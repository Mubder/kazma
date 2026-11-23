"""Invitation links that expire (Spaces S4-02).

The admin invites a person from People: :func:`create` makes a link
(``/invite/<token>``) that works once, for :data:`HOURS` hours (or fewer, as
the admin chose); Kazma keeps only the token's SHA-256 digest
(:data:`INVITATIONS_KEY`, keyed by the digest, never the token). The person
opens the link, chooses a name and a password, and :func:`accept` makes
their account and personal space through ``people.create_person`` -- the
same path the admin's "Add a person" takes, so S1-33's preconditions are
asked there too -- and spends the link in the same step. A link that ran
out, was used or revoked does nothing. Every change is an event of the
People record (``invitation_made``, ``invitation_used``,
``invitation_revoked``).

Only where Spaces is enforced (the People routes refuse otherwise).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time
from typing import Any

from kazma_core.security.secret_compare import same_secret
from kazma_core.spaces import identity
from kazma_core.spaces.people import PeopleRefused, create_person, record_event

__all__ = ["HOURS", "INVITATIONS_KEY", "accept", "create", "list_invitations", "lookup", "revoke"]

logger = logging.getLogger(__name__)

#: The live invitations: ``{digest: entry}``. Credential state: a restore
#: never brings back a used or revoked one.
INVITATIONS_KEY = "identity.invitations"
#: How long a link works, at most, and by default.
HOURS = 72
#: How many invitations are kept (used, revoked and run-out ones go first).
_MAX_KEPT = 200
#: How long an acceptance holds its link (a process that died half way
#: leaves it open again after this).
_CLAIM_S = 300.0
_lock = threading.Lock()


def _store() -> Any:
    from kazma_core.config_store import get_config_store

    return get_config_store()


def _digest(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _record(kind: str, account: str, by: str, *, detail: str = "") -> None:
    def _change(reg: dict[str, Any]) -> None:
        record_event(reg, kind, account, by, detail=detail)

    identity.update_registry(_change)


def _public(digest: str, entry: dict[str, Any], now: float) -> dict[str, Any]:
    """What People shows of an invitation: never its digest or token."""
    if entry.get("used_at"):
        status = "used"
    elif entry.get("revoked_at"):
        status = "revoked"
    elif float(entry.get("expires_at") or 0) <= now:
        status = "expired"
    elif now - float(entry.get("claimed_at") or 0) < _CLAIM_S:
        # Being used right now (:func:`accept`): not open to a second use.
        status = "claimed"
    else:
        status = "open"
    return {
        "id": str(entry.get("id") or ""),
        "name": str(entry.get("name") or ""),
        "by": str(entry.get("by") or ""),
        "created_at": float(entry.get("created_at") or 0),
        "expires_at": float(entry.get("expires_at") or 0),
        "status": status,
        "used_by": str(entry.get("used_by") or ""),
    }


def _pruned(held: dict[str, Any], now: float) -> dict[str, Any]:
    """At most :data:`_MAX_KEPT`, the open ones kept first."""
    if len(held) <= _MAX_KEPT:
        return held

    def _order(item: tuple[str, Any]) -> tuple[int, float]:
        entry = item[1] if isinstance(item[1], dict) else {}
        is_open = not entry.get("used_at") and not entry.get("revoked_at") \
            and float(entry.get("expires_at") or 0) > now
        return (1 if is_open else 0, float(entry.get("created_at") or 0))

    return dict(sorted(held.items(), key=_order)[-_MAX_KEPT:])


def create(*, by: str, name: str = "", hours: int = HOURS) -> dict[str, Any]:
    """A new invitation: ``{"token", ...}``. The token is answered once and
    never kept. *name* is a suggestion the invited person may change."""
    try:
        hours = int(hours)
    except (TypeError, ValueError):
        raise PeopleRefused("bad_hours", 400, f"An invitation lasts 1 to {HOURS} hours.") from None
    if not 1 <= hours <= HOURS:
        raise PeopleRefused("bad_hours", 400, f"An invitation lasts 1 to {HOURS} hours.")
    suggested = " ".join(str(name or "").split())[:64]
    token = secrets.token_urlsafe(32)
    now = time.time()
    entry = {"id": secrets.token_hex(6), "name": suggested, "by": by,
             "created_at": now, "expires_at": now + hours * 3600}

    def _update(current: Any) -> dict[str, Any]:
        held = dict(current) if isinstance(current, dict) else {}
        held[_digest(token)] = entry
        return _pruned(held, now)

    with _lock:
        _store().atomic_update(INVITATIONS_KEY, _update, category="auth")
    _record("invitation_made", "", by, detail=entry["id"])
    logger.info("[people] an invitation was made by %s, for %d hours", by, hours)
    return {"token": token, **_public(_digest(token), entry, now)}


def list_invitations() -> list[dict[str, Any]]:
    """Every invitation kept, newest first: never a token or a digest."""
    now = time.time()
    held = _store().get(INVITATIONS_KEY) or {}
    rows = [_public(d, e, now) for d, e in held.items() if isinstance(e, dict)] if isinstance(held, dict) else []
    return sorted(rows, key=lambda r: r["created_at"], reverse=True)


def _live(token: str, now: float) -> tuple[str, dict[str, Any]] | None:
    held = _store().get(INVITATIONS_KEY) or {}
    if not isinstance(held, dict):
        return None
    digest = _digest(token)
    for key, entry in held.items():
        if same_secret(str(key), digest) and isinstance(entry, dict):
            if _public(key, entry, now)["status"] == "open":
                return key, entry
    return None


def lookup(token: str) -> dict[str, Any] | None:
    """The invitation *token* opens while it is open (its suggested name and
    when it runs out), else None."""
    found = _live(token, time.time())
    if found is None:
        return None
    entry = found[1]
    return {"name": str(entry.get("name") or ""), "expires_at": float(entry.get("expires_at") or 0)}


def accept(token: str, name: str, password: str) -> dict[str, Any]:
    """Make the invited person's account and spend the link. The link is
    claimed first (one compare-and-set: a second use at the same moment
    finds it taken), the account made, then the link marked used; an account
    that could not be made gives the claim back. Refused: a link that is not
    open (``invitation_closed``, one answer for one that never was, ran out,
    was used, revoked or is being used), and every refusal of
    ``people.create_person`` (a name taken, a short password, the
    preconditions)."""
    now = time.time()
    digest = _digest(token)
    claimed: dict[str, Any] = {}

    def _claim(current: Any) -> dict[str, Any]:
        held = dict(current) if isinstance(current, dict) else {}
        claimed.clear()
        for key, entry in held.items():
            if same_secret(str(key), digest) and isinstance(entry, dict)                     and _public(key, entry, now)["status"] == "open":
                held[key] = {**entry, "claimed_at": now}
                claimed.update(held[key])
        return held

    def _settle(made_id: str) -> Any:
        def _update(current: Any) -> dict[str, Any]:
            held = dict(current) if isinstance(current, dict) else {}
            kept = dict(held.get(digest) or claimed)
            kept.pop("claimed_at", None)
            if made_id:
                kept["used_at"] = now
                kept["used_by"] = made_id
            held[digest] = kept
            return held

        return _update

    with _lock:
        _store().atomic_update(INVITATIONS_KEY, _claim, category="auth")
        if not claimed:
            raise PeopleRefused("invitation_closed", 410, "This invitation does not work any more.")
        made: dict[str, Any] = {}
        try:
            made = create_person(name, password, by=str(claimed.get("by") or ""))
        finally:
            # Spent once the account is made; else the claim is given back
            # (a refused name or password, a store away, a stop).
            _store().atomic_update(INVITATIONS_KEY, _settle(str(made.get("id") or "")), category="auth")
        made_id = str(made.get("id") or "")
    _record("invitation_used", made_id, made_id, detail=str(claimed.get("id") or ""))
    logger.info("[people] an invitation was used: account %s made", made_id)
    return made


def revoke(invitation_id: str, *, by: str) -> None:
    """Close an open invitation now. Refused: none open by that id."""
    now = time.time()
    closed = {"done": False}

    def _update(current: Any) -> dict[str, Any]:
        held = dict(current) if isinstance(current, dict) else {}
        for key, entry in held.items():
            if isinstance(entry, dict) and str(entry.get("id") or "") == invitation_id \
                    and _public(key, entry, now)["status"] == "open":
                held[key] = {**entry, "revoked_at": now}
                closed["done"] = True
        return held

    with _lock:
        _store().atomic_update(INVITATIONS_KEY, _update, category="auth")
    if not closed["done"]:
        raise PeopleRefused("no_such_invitation", 404, "No open invitation has that id.")
    _record("invitation_revoked", "", by, detail=invitation_id)
