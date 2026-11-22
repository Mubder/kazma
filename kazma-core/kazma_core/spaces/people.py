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

The admin also sets each person's daily limit and the limit for people who
have none of their own (S2-02, ``spaces.daily_limits``), and switches each
person's features (S2-11, ``spaces.features``): recorded like the rest.

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
from pathlib import Path
from typing import Any

from kazma_core.spaces import identity
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID, spaces_enforced

__all__ = [
    "PeopleRefused",
    "broken_at",
    "close_space",
    "closed_spaces",
    "create_person",
    "create_shared_space",
    "forget_space",
    "delete_person",
    "limits_for_people",
    "list_people",
    "list_shared_spaces",
    "mark_seen",
    "notices_for",
    "reset_password",
    "set_daily_limit",
    "set_default_daily_limit",
    "set_features",
]

logger = logging.getLogger(__name__)

#: What the record holds.
_KINDS = ("account_created", "password_reset", "account_deleted", "space_erased",
          "daily_limit_set", "default_daily_limit_set", "features_set", "space_created")
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
    when made, its personal space, its daily limit (its own, ``None`` for
    none, and the one it has: ``limit``, ``limit_source``). Never a password
    hash, never a word of a space's data."""
    from kazma_core.spaces.daily_limits import limit_of

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
            **_limits_of(reg, account_id, limit_of),
        }
        for account_id, account in reg["accounts"].items()
    ]
    return sorted(people, key=lambda p: (not p["owner"], p["name"].lower()))


def _limits_of(reg: dict[str, Any], account_id: str, limit_of: Any) -> dict[str, Any]:
    from kazma_core.spaces import features

    policy = reg["policies"].get(account_id) or {}
    own = policy.get("daily_limit") if isinstance(policy, dict) else None
    limit, source = limit_of(reg, account_id)
    return {"daily_limit": own if isinstance(own, dict) else None,
            "limit": limit.as_dict() if limit else None, "limit_source": source,
            "features": features.listed(features.grants_of(reg, account_id))}


def limits_for_people() -> dict[str, Any]:
    """The limit for people who have none of their own: ``limit``, whether
    the admin set it (``set``) and Kazma's own (``shipped``)."""
    from kazma_core.spaces.daily_limits import SHIPPED_DEFAULT, default_limit

    reg = identity.read_registry()
    return {"limit": default_limit(reg).as_dict(),
            "set": isinstance(reg["defaults"].get("daily_limit"), dict),
            "shipped": SHIPPED_DEFAULT.as_dict()}


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


#: The two kinds of shared space (plan 4.11, N29): a plain one, sandboxed,
#: the default; one with a project folder, the host kind, for development.
SHARED_KINDS = ("sandboxed", "host")


def list_shared_spaces() -> list[dict[str, Any]]:
    """Every shared space, oldest first: id, name, kind, when made and by
    whom, its members (id, name, role) and, for one with a project folder,
    the folder it works in. Never a word of what it holds."""
    reg = identity.read_registry()
    roots = _project_roots()
    names = {account_id: str(account.get("name") or "") for account_id, account in reg["accounts"].items()}
    spaces = []
    for space_id, space in reg["spaces"].items():
        if space.get("personal_of") or space_id == OWNER_SPACE_ID or space.get("erasing_at"):
            continue
        members = [
            {"id": str(m.get("account")), "name": names.get(str(m.get("account")), ""),
             "role": str(m.get("role") or "member")}
            for m in reg["memberships"] if m.get("space") == space_id
        ]
        spaces.append({
            "id": space_id, "name": str(space.get("name") or ""), "kind": str(space.get("kind") or ""),
            "created_at": str(space.get("created_at") or ""), "created_by": str(space.get("created_by") or ""),
            "members": sorted(members, key=lambda m: (m["role"] != "owner", m["name"].lower())),
            "project_folder": roots.get(space_id, "") if space.get("kind") == "host" else "",
        })
    return sorted(spaces, key=lambda space: space["created_at"])


def _project_roots() -> dict[str, str]:
    """Each space's first workspace root, by space (the project folder it
    was made with, unless its people switched it)."""
    from kazma_core.stores import get_workspace_store

    roots: dict[str, str] = {}
    for space_id, root in get_workspace_store().roots_by_space():
        roots.setdefault(space_id, root)
    return roots


def create_shared_space(name: str, *, kind: str = "sandboxed", project_folder: str = "",
                        by: str) -> dict[str, Any]:
    """A new shared space, made by the admin (plan 4.11, N29), with the admin
    as its owner. *kind*: ``sandboxed`` (a plain shared space: its own space
    folder, no Development, no GitHub; the default) or ``host`` (one with a
    project folder, for development: *project_folder* must pass
    ``spaces.project_folder``'s rules). Recorded (``space_created``).
    Returns the space as :func:`list_shared_spaces` lists it."""
    from kazma_core.spaces import project_folder as folder_rules

    _require_enforced()
    if not identity.is_admin_account(by):
        raise PeopleRefused("admin_only", 403, "Only the admin makes a shared space.")
    name = _checked_name(name)
    if kind not in SHARED_KINDS:
        raise PeopleRefused("bad_kind", 400, f"A shared space is one of {', '.join(SHARED_KINDS)}.")
    space_id = f"s{secrets.token_hex(6)}"
    workspace_id = ""
    if kind == "host":
        found = folder_rules.problem(project_folder)
        if found:
            raise PeopleRefused(f"folder_{found}", 400, "That folder cannot be this space's project folder.")
        workspace_id = _make_project_workspace(space_id, name, project_folder, by)

    def _change(reg: dict[str, Any]) -> None:
        if by not in reg["accounts"]:
            raise identity.IdentityError("no_such_account", f"no account {by!r}")
        reg["spaces"][space_id] = {
            "id": space_id, "name": name, "kind": kind, "personal_of": None,
            "created_by": by, "created_at": _now(),
        }
        reg["memberships"].append({"account": by, "space": space_id, "role": "owner"})
        _record(reg, "space_created", "", by, space=space_id)

    recorded = False
    try:
        _changed(_change)
        recorded = True
    finally:
        if workspace_id and not recorded:
            _drop_project_workspace(space_id, workspace_id)
    logger.info("[people] shared space created id=%s kind=%s by=%s", space_id, kind, by)
    return next(space for space in list_shared_spaces() if space["id"] == space_id)


def _make_project_workspace(space_id: str, name: str, folder: str, by: str) -> str:
    """The new space's first workspace: its project folder, active. Written
    before the space itself, so a space is never there without its folder."""
    from kazma_core.spaces.scope import bind_scope
    from kazma_core.stores import get_workspace_store

    store = get_workspace_store()
    with bind_scope(space_id, by):
        made = store.create_workspace(name, str(Path(folder).expanduser()))
        if not store.set_active_workspace(made["id"]):
            store.delete_workspace(made["id"])
            raise PeopleRefused("folder_unreachable", 400, "That folder could not be made the space's folder.")
    return str(made["id"])


def _drop_project_workspace(space_id: str, workspace_id: str) -> None:
    from kazma_core.spaces.scope import bind_scope
    from kazma_core.stores import get_workspace_store

    with bind_scope(space_id, OWNER_ACCOUNT_ID):
        get_workspace_store().delete_workspace(workspace_id)


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
        reg["policies"].pop(account_id, None)
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


def _checked_limit(raw: Any) -> Any:
    from kazma_core.spaces.daily_limits import LimitRefused, parse_limit

    try:
        return parse_limit(raw)
    except LimitRefused as exc:
        raise PeopleRefused(exc.code, 400, str(exc)) from exc


def set_daily_limit(account_id: str, raw: dict[str, Any] | None, *, by: str) -> None:
    """Set a person's own daily limit (``{"tokens", "usd"}``), or take it
    away (``None``, or no tokens): they then have the limit for people, and
    the admin none. Recorded."""
    _require_enforced()
    limit = _checked_limit(raw)

    def _change(reg: dict[str, Any]) -> None:
        if account_id not in reg["accounts"]:
            raise identity.IdentityError("no_such_account", "There is no such person.")
        policy = dict(reg["policies"].get(account_id) or {})
        if limit is None:
            policy.pop("daily_limit", None)
        else:
            policy["daily_limit"] = limit.as_dict()
        reg["policies"][account_id] = policy
        _record(reg, "daily_limit_set", account_id, by)

    _changed(_change)
    logger.info("[people] daily limit set id=%s by=%s", account_id, by)


def set_default_daily_limit(raw: dict[str, Any] | None, *, by: str) -> None:
    """Set the limit for people who have none of their own, or go back to
    Kazma's own (``None``). Recorded."""
    _require_enforced()
    limit = _checked_limit(raw)

    def _change(reg: dict[str, Any]) -> None:
        if limit is None:
            reg["defaults"].pop("daily_limit", None)
        else:
            reg["defaults"]["daily_limit"] = limit.as_dict()
        _record(reg, "default_daily_limit_set", "", by)

    _changed(_change)
    logger.info("[people] the daily limit for people was set by=%s", by)


def set_features(account_id: str, changes: dict[str, Any], *, by: str) -> None:
    """Switch a person's features (``{feature: on}``, S2-11): one step,
    recorded. The admin has every feature; only Development is theirs to
    switch (N28). Refused: an unknown feature, a value that is not a switch."""
    _require_enforced()
    from kazma_core.spaces import features

    if not isinstance(changes, dict) or not changes:
        raise PeopleRefused("bad_features", 400, "Name the features to switch.")

    def _change(reg: dict[str, Any]) -> None:
        if account_id not in reg["accounts"]:
            raise identity.IdentityError("no_such_account", "There is no such person.")
        try:
            checked = features.checked_change(account_id, changes, reg)
        except features.FeatureRefused as exc:
            raise identity.IdentityError(exc.code, str(exc)) from exc
        features.apply_change(reg, account_id, checked)
        _record(reg, "features_set", account_id, by)

    try:
        _changed(_change)
    except PeopleRefused as exc:
        if exc.code in ("unknown_feature", "bad_switch"):
            raise PeopleRefused(exc.code, 400, str(exc)) from exc
        raise
    logger.info("[people] features set id=%s by=%s", account_id, by)


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

