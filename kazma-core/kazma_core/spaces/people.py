"""The people of an install: create, reset and delete (Spaces S1-25).

What the admin can and cannot do (plan 4.10): the admin creates an account,
resets its password and deletes it, and never opens a space they are not a
member of. A reset is the one way an admin could reach a member's space (set
a password, sign in as them), so it is never silent:

* every change is an event in the identity store's record
  (``identity`` ``events``), written in the same atomic step as the change.
  The record is hash-chained (:func:`broken_at` finds an edit made outside
  Kazma) and append-only (``identity.update_registry`` refuses a change that
  edits or drops an earlier event); no route, tool or restore writes it, and
  Settings' own writers refuse the identity keys once Spaces is enforced
  (``settings_scope.install_writer_refusal``);
* the member is told at their next sign-in (:func:`notices_for`): the page
  shows each reset of their password that someone else made, until they
  have read it.

A new person gets a personal space (sandboxed, one member forever). Deleting
a person ends their sessions and takes them out of every space; their
personal space stays, with no member, marked ``account_deleted_at``, until
its data is erased (S1-27).

Only once Spaces is enforced: before that, a second account changes how the
whole install behaves, and the switch that turns Spaces on checks its
preconditions first (``spaces.preconditions``, S1-33). A new account asks
them again, and a missing one refuses it by name. The older multi-user mode keeps its own user
list (``security.platform_rbac``) until then.

Every function here reads or writes the settings store: call it off the
event loop.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import UTC, datetime
from typing import Any

from kazma_core.spaces import identity
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, spaces_enforced

__all__ = [
    "PeopleRefused",
    "broken_at",
    "close_space",
    "closed_spaces",
    "create_person",
    "forget_space",
    "delete_person",
    "list_people",
    "mark_seen",
    "notices_for",
    "reset_password",
]

logger = logging.getLogger(__name__)

#: What the record holds.
_KINDS = ("account_created", "password_reset", "account_deleted", "space_erased")
#: The events a person is told about, when someone else made them.
_TOLD = ("password_reset",)
_NAME_MAX = 64
_PASSWORD_MIN = 8


class PeopleRefused(ValueError):
    """A change to the people that is refused; nothing was changed."""

    def __init__(self, code: str, status: int, message: str, *,
                 missing: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        #: For ``preconditions_missing``: each precondition that does not
        #: hold (id, title, detail, fix).
        self.missing = list(missing or [])


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _require_enforced() -> None:
    if not spaces_enforced():
        raise PeopleRefused(
            "spaces_off", 409,
            "People are added once Spaces is on for this install.",
        )


def _require_preconditions() -> None:
    """A new account is made only while every precondition of a second
    account holds (S1-33): asked again each time, since a setting changed
    after the switch (a plain copy of the backup, a secret held twice) is
    somebody's data the day a person is added."""
    from kazma_core.spaces import preconditions

    left = preconditions.missing()
    if left:
        raise PeopleRefused(
            "preconditions_missing", 409,
            "Not yet: " + "; ".join(f"{f.title}: {f.detail}" for f in left),
            missing=[f.as_dict() for f in left],
        )


# ── The record ──────────────────────────────────────────────────────────


def _event_hash(event: dict[str, Any]) -> str:
    fields = {key: value for key, value in event.items() if key != "hash"}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _record(reg: dict[str, Any], kind: str, account: str, by: str, *, space: str = "") -> None:
    """Append one event to *reg*'s record, chained to the one before."""
    if kind not in _KINDS:
        raise ValueError(f"not an event of the record: {kind!r}")
    events = reg["events"]
    event = {
        "seq": len(events) + 1,
        "at": _now(),
        "kind": kind,
        "account": account,
        "by": by,
        "prev": events[-1].get("hash", "") if events else "",
    }
    if space:
        event["space"] = space
    event["hash"] = _event_hash(event)
    events.append(event)


def broken_at(events: list[dict[str, Any]] | None = None) -> int | None:
    """The sequence number of the first event of the record that does not
    chain (it, or one before it, was edited outside Kazma), or None when the
    record is whole."""
    if events is None:
        events = identity.read_registry()["events"]
    previous = ""
    for index, event in enumerate(events, start=1):
        if (not isinstance(event, dict) or event.get("seq") != index
                or event.get("prev") != previous or event.get("hash") != _event_hash(event)):
            return index
        previous = str(event["hash"])
    return None


# ── Reading ─────────────────────────────────────────────────────────────


def list_people() -> list[dict[str, Any]]:
    """Every account, the owner first: id, name, install role, enabled,
    when made, its personal space. Never a password hash, never a word of a
    space's data."""
    reg = identity.read_registry()
    personal = {space.get("personal_of"): space_id for space_id, space in reg["spaces"].items()
                if space.get("personal_of") and not space.get("account_deleted_at")}
    people = [
        {
            "id": account_id,
            "name": str(account.get("name") or ""),
            "role": str(account.get("role") or "member"),
            "enabled": bool(account.get("enabled", True)),
            "created_at": str(account.get("created_at") or ""),
            "personal_space": personal.get(account_id, ""),
            "owner": account_id == OWNER_ACCOUNT_ID,
        }
        for account_id, account in reg["accounts"].items()
    ]
    return sorted(people, key=lambda p: (not p["owner"], p["name"].lower()))


def closed_spaces() -> list[dict[str, Any]]:
    """The personal spaces of deleted people, waiting to be erased (S1-27):
    id, the name it had, when its person was deleted. Never what it holds."""
    reg = identity.read_registry()
    closed = [
        {"id": space_id, "name": str(space.get("name") or ""),
         "deleted_at": str(space.get("account_deleted_at") or "")}
        for space_id, space in reg["spaces"].items()
        if space.get("personal_of") and space.get("account_deleted_at")
    ]
    return sorted(closed, key=lambda space: space["deleted_at"])


def notices_for(account_id: str) -> list[dict[str, Any]]:
    """The changes someone else made to *account_id* that its person has not
    read yet, oldest first: ``seq``, ``kind``, ``at``."""
    reg = identity.read_registry()
    seen = int(reg["events_seen"].get(account_id) or 0)
    return [
        {"seq": int(event["seq"]), "kind": event["kind"], "at": event["at"]}
        for event in reg["events"]
        if isinstance(event, dict) and event.get("account") == account_id
        and event.get("kind") in _TOLD and event.get("by") != account_id
        and int(event.get("seq") or 0) > seen
    ]


def mark_seen(account_id: str, through: int) -> None:
    """*account_id*'s person has read the notices up to *through*."""
    def _change(reg: dict[str, Any]) -> None:
        if account_id not in reg["accounts"]:
            raise identity.IdentityError("no_such_account", f"no account {account_id!r}")
        last = len(reg["events"])
        reg["events_seen"][account_id] = max(int(reg["events_seen"].get(account_id) or 0),
                                             min(int(through), last))

    identity.update_registry(_change)


# ── Changing ────────────────────────────────────────────────────────────


def _checked_name(name: str) -> str:
    cleaned = " ".join(str(name or "").split())
    if not cleaned or len(cleaned) > _NAME_MAX or any(ord(ch) < 32 for ch in cleaned):
        raise PeopleRefused("bad_name", 400, f"A name is 1 to {_NAME_MAX} characters.")
    return cleaned


def _checked_password(password: str) -> str:
    if len(str(password or "")) < _PASSWORD_MIN:
        raise PeopleRefused("weak_password", 400, f"A password is at least {_PASSWORD_MIN} characters.")
    return str(password)


def _password_hash(password: str) -> str:
    from kazma_core.security.platform_rbac import _hash_password

    return _hash_password(password)


def _changed(change: Any) -> dict[str, Any]:
    """Apply *change* as one registry step; a refusal of the store's rules is
    a :class:`PeopleRefused`."""
    try:
        return identity.update_registry(change)
    except identity.IdentityError as exc:
        status = 404 if exc.code == "no_such_account" else 409
        raise PeopleRefused(exc.code, status, str(exc)) from exc


def _person(reg: dict[str, Any], account_id: str) -> dict[str, Any]:
    account = reg["accounts"].get(account_id)
    if account is None:
        raise identity.IdentityError("no_such_account", "There is no such person.")
    if account_id == OWNER_ACCOUNT_ID:
        raise identity.IdentityError(
            "is_the_owner", "The owner's own sign-in is changed in My account, not here.")
    return account


def create_person(name: str, password: str, *, by: str) -> dict[str, Any]:
    """A new person: an account that signs in with *name* and *password*,
    and its personal space. Recorded. Returns the person as
    :func:`list_people` lists them."""
    _require_enforced()
    _require_preconditions()
    name = _checked_name(name)
    password_hash = _password_hash(_checked_password(password))
    account_id = f"p{secrets.token_hex(6)}"
    space_id = f"s{secrets.token_hex(6)}"

    def _change(reg: dict[str, Any]) -> None:
        now = _now()
        reg["accounts"][account_id] = {
            "id": account_id,
            "name": name,
            "role": "member",
            "enabled": True,
            "language": "",
            # A local sign-in: platform_rbac's sign-in reads these accounts
            # (``identity.local_users``), as an operator of the older mode.
            "sign_in": {"kind": "local", "password_hash": password_hash},
            "legacy_role": "operator",
            "legacy_user_id": account_id,
            "meta": {},
            "created_at": now,
        }
        reg["spaces"][space_id] = {
            "id": space_id, "name": name, "kind": "sandboxed", "personal_of": account_id,
            "created_by": by, "created_at": now,
        }
        reg["memberships"].append({"account": account_id, "space": space_id, "role": "owner"})
        _record(reg, "account_created", account_id, by)

    _changed(_change)
    logger.info("[people] account created id=%s by=%s", account_id, by)
    return next(p for p in list_people() if p["id"] == account_id)


def reset_password(account_id: str, password: str, *, by: str) -> None:
    """Set a person's password, end their sessions, and record it: they are
    told at their next sign-in (:func:`notices_for`)."""
    _require_enforced()
    password_hash = _password_hash(_checked_password(password))

    def _change(reg: dict[str, Any]) -> None:
        account = _person(reg, account_id)
        sign_in = dict(account.get("sign_in") or {})
        if sign_in.get("kind") != "local":
            raise identity.IdentityError("no_password", "This person does not sign in with a password.")
        sign_in["password_hash"] = password_hash
        account["sign_in"] = sign_in
        _record(reg, "password_reset", account_id, by)

    reg = _changed(_change)
    _end_sessions(reg, account_id)
    logger.warning("[people] password reset id=%s by=%s", account_id, by)


def delete_person(account_id: str, *, by: str) -> None:
    """Delete a person: their account and every membership go, their sessions
    end, and their personal space stays with no member until it is erased
    (S1-27). Recorded."""
    _require_enforced()
    legacy: list[str] = []

    def _change(reg: dict[str, Any]) -> None:
        account = _person(reg, account_id)
        legacy[:] = [str(account.get("legacy_user_id") or account_id)]
        del reg["accounts"][account_id]
        reg["memberships"] = [m for m in reg["memberships"] if m.get("account") != account_id]
        now = _now()
        for space in reg["spaces"].values():
            if space.get("personal_of") == account_id:
                space["account_deleted_at"] = now
        reg["events_seen"].pop(account_id, None)
        _record(reg, "account_deleted", account_id, by)

    _changed(_change)
    _end_sessions_of(legacy[0] if legacy else account_id)
    logger.warning("[people] account deleted id=%s by=%s", account_id, by)


def _end_sessions(reg: dict[str, Any], account_id: str) -> None:
    account = reg["accounts"].get(account_id) or {}
    _end_sessions_of(str(account.get("legacy_user_id") or account_id))


def _end_sessions_of(user_id: str) -> None:
    from kazma_core.security.web_sessions import revoke_user_sessions

    revoke_user_sessions(user_id=user_id)


# ── A space's end (S1-27) ───────────────────────────────────────────────


def close_space(space_id: str, *, by: str) -> None:
    """Mark *space_id* as being erased: from now on nobody is a member of it
    (``identity.is_member``), so no request or job binds it, while its
    memberships stay for an erase that stopped half way to be run again."""
    def _change(reg: dict[str, Any]) -> None:
        space = reg["spaces"].get(space_id)
        if space is None:
            raise identity.IdentityError("no_such_space", f"no space {space_id!r}")
        space.setdefault("erasing_at", _now())
        space.setdefault("erasing_by", by)

    _changed(_change)


def forget_space(space_id: str, *, by: str) -> None:
    """Take an erased space and its memberships out of the identity store,
    recorded (``space_erased``)."""
    def _change(reg: dict[str, Any]) -> None:
        space = reg["spaces"].pop(space_id, None)
        if space is None:
            return
        reg["memberships"] = [m for m in reg["memberships"] if m.get("space") != space_id]
        _record(reg, "space_erased", str(space.get("personal_of") or ""), by, space=space_id)

    _changed(_change)

