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

Shared spaces (S2-12): the admin makes one, plain or with a project folder,
and adds people to it; the person is told at their next page load. A member
leaves when they choose; the space's owner or the admin removes one. When a
member goes -- leaving, removed, or deleted -- every fact filed under their
own memory entry in that space is forgotten (decision N36,
``memory.forget.forget_entry``): what they said about themself and what
others said about them. Their messages stay, as the conversation's history.
The share of the space's documents they were given is taken back.

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
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kazma_core.spaces import identity
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID, spaces_enforced

__all__ = [
    "PeopleRefused",
    "add_member",
    "broken_at",
    "close_space",
    "closed_spaces",
    "create_person",
    "create_shared_space",
    "forget_space",
    "delete_person",
    "leave_space",
    "limits_for_people",
    "list_people",
    "list_shared_spaces",
    "mark_seen",
    "notices_for",
    "remove_member",
    "reset_password",
    "set_daily_limit",
    "set_default_daily_limit",
    "set_features",
    "set_enabled",
    "set_models",
    "set_default_space_limits",
    "set_space_limits",
    "set_tools",
    "spaces_usage",
    "end_sessions",
    "note_sign_in",
    "record_event",
]

logger = logging.getLogger(__name__)

#: What the record holds.
_KINDS = ("account_created", "password_reset", "account_deleted", "space_erased",
          "daily_limit_set", "default_daily_limit_set", "features_set", "tools_set",
          "models_set", "account_disabled", "account_enabled", "space_created", "member_added", "member_left", "member_removed",
          "space_limits_set", "default_space_limits_set", "sessions_ended")
#: A person's chat-app accounts (Spaces stage 3, ``spaces.chat_links``).
_KINDS += ("chat_linked", "chat_unlinked")
#: The events a person is told about, when someone else made them.
_TOLD = ("password_reset", "member_added", "member_removed", "sessions_ended")
_NAME_MAX = 64
#: One shared-space create at a time in this process (the review, finding 10).
_CREATE_LOCK = threading.Lock()
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


def record_event(reg: dict[str, Any], kind: str, account: str, by: str, *, detail: str = "") -> None:
    """Append one event to *reg*'s record, for a change another module makes
    inside its own ``identity.update_registry`` step (a chat-app link,
    ``spaces.chat_links``). *detail* is a short word (the chat app), never a
    person's data."""
    _record(reg, kind, account, by, detail=detail)


def _record(reg: dict[str, Any], kind: str, account: str, by: str, *, space: str = "",
            detail: str = "") -> None:
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
    if detail:
        event["detail"] = detail
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
    none, and the one it has: ``limit``, ``limit_source``), the spaces it is
    a member of (``spaces``), when it last signed in (``last_sign_in``) and
    how many sessions it has open (``sessions``). Never a password hash, a
    session id, or a word of a space's data."""
    from kazma_core.spaces.daily_limits import limit_of

    reg = identity.read_registry()
    personal = {space.get("personal_of"): space_id for space_id, space in reg["spaces"].items()
                if space.get("personal_of") and not space.get("account_deleted_at")}
    signed_in = _sign_ins(reg)
    people = [
        {
            "id": account_id,
            "name": str(account.get("name") or ""),
            "role": str(account.get("role") or "member"),
            "enabled": bool(account.get("enabled", True)),
            "created_at": str(account.get("created_at") or ""),
            "personal_space": personal.get(account_id, ""),
            "owner": account_id == OWNER_ACCOUNT_ID,
            "spaces": _spaces_of(reg, account_id),
            # Their linked chat-app accounts (stage 3): a chat app and an id.
            "links": [{"platform": str(link.get("platform") or ""),
                       "user_id": str(link.get("user_id") or "")}
                      for link in reg.get("links") or () if isinstance(link, dict)
                      and link.get("account") == account_id],
            **signed_in.get(account_id, {"sessions": 0, "last_sign_in": ""}),
            **_limits_of(reg, account_id, limit_of),
        }
        for account_id, account in reg["accounts"].items()
    ]
    return sorted(people, key=lambda p: (not p["owner"], p["name"].lower()))


def _spaces_of(reg: dict[str, Any], account_id: str) -> list[dict[str, Any]]:
    """The spaces *account_id* is a member of, its personal one first: id,
    name (a personal space's is its person's), personal or shared, and its
    role there. A space being erased is left out."""
    out = []
    for membership in reg["memberships"]:
        if membership.get("account") != account_id:
            continue
        space_id = str(membership.get("space") or "")
        space = reg["spaces"].get(space_id) or {}
        if not space or space.get("erasing_at"):
            continue
        personal = bool(space.get("personal_of")) or space_id == OWNER_SPACE_ID
        out.append({"id": space_id, "name": "" if personal else str(space.get("name") or ""),
                    "kind": "personal" if personal else "shared",
                    "role": str(membership.get("role") or "member")})
    return sorted(out, key=lambda s: (s["kind"] != "personal", s["name"].lower()))


# ── Sign-ins and sessions (S2-10) ───────────────────────────────────────

#: When each person last signed in, by account: runtime state (``system.``,
#: never carried by a backup or a restore), written at a sign-in with a
#: name and password. A session's own start is read beside it.
LAST_SIGN_IN_KEY = "system.people.last_sign_in"
_LAST_SIGN_IN_MAX = 2000
#: The user id a sign-in with the install's shared secret is given, and the
#: actor of the loopback auto-login: both are the owner's sessions.
_OWNER_SESSION_USER = "shared-secret"
_OWNER_SESSION_ACTOR = "auto-cookie"


def _account_of_session(reg: dict[str, Any], user_id: str, actor: str) -> str:
    """The account a session or a sign-in is, or ``""`` for none (an OIDC
    session, one of an account deleted since)."""
    if actor == _OWNER_SESSION_ACTOR or user_id == _OWNER_SESSION_USER:
        return OWNER_ACCOUNT_ID
    if not user_id or actor == "oidc":
        return ""
    for account_id, account in reg["accounts"].items():
        if str(account.get("legacy_user_id") or "") == user_id:
            return account_id
    return ""


def note_sign_in(user_id: str, actor: str = "login") -> None:
    """Record that the person behind *user_id* signed in now, for the People
    page. Only where Spaces is enforced; nothing else depends on it, so a
    store that cannot answer is said and the sign-in goes on. Blocking."""
    if not spaces_enforced():
        return
    from kazma_core.config_store import get_config_store

    try:
        account = _account_of_session(identity.read_registry(), str(user_id or ""), str(actor or ""))
        if not account:
            return
        now = _now()

        def _update(current: Any) -> dict[str, str]:
            held = {str(k): str(v) for k, v in current.items()} if isinstance(current, dict) else {}
            held[account] = now
            if len(held) > _LAST_SIGN_IN_MAX:
                held = dict(sorted(held.items(), key=lambda kv: kv[1])[-_LAST_SIGN_IN_MAX:])
            return held

        get_config_store().atomic_update(LAST_SIGN_IN_KEY, _update, category="system")
    except (sqlite3.Error, OSError, RuntimeError, ValueError, TypeError, LookupError):
        # The store away, locked or refusing (RuntimeError covers an
        # unreadable identity store and a store that is unavailable): the
        # sign-in is never refused for it.
        logger.warning("[people] a sign-in was not recorded on People", exc_info=True)


def _sign_ins(reg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """By account: how many sessions it has open and when it last signed in
    (the newer of the recorded sign-in and its newest session's start)."""
    from kazma_core.config_store import get_config_store
    from kazma_core.security.web_sessions import session_starts

    recorded = get_config_store().get(LAST_SIGN_IN_KEY) or {}
    out: dict[str, dict[str, Any]] = {}
    for session in session_starts():
        account = _account_of_session(reg, session["user_id"], session["actor"])
        if not account:
            continue
        held = out.setdefault(account, {"sessions": 0, "last_sign_in": ""})
        held["sessions"] += 1
        began = datetime.fromtimestamp(session["started_at"], UTC).isoformat(timespec="seconds") if session["started_at"] else ""
        held["last_sign_in"] = max(held["last_sign_in"], began)
    if isinstance(recorded, dict):
        for account, at in recorded.items():
            held = out.setdefault(str(account), {"sessions": 0, "last_sign_in": ""})
            held["last_sign_in"] = max(held["last_sign_in"], str(at or ""))
    return out


def end_sessions(account_id: str, *, by: str) -> int:
    """End every open session of a person: they sign in again on each
    device. Recorded, and the person is told at their next sign-in. Refused:
    the owner (their own sessions are ended in My account). Returns how
    many were ended."""
    _require_enforced()
    if account_id == OWNER_ACCOUNT_ID:
        raise PeopleRefused("owner_in_my_account", 409, "Your own sessions are ended in My account.")

    def _change(reg: dict[str, Any]) -> None:
        _person(reg, account_id)
        _record(reg, "sessions_ended", account_id, by)

    reg = _changed(_change)
    account = reg["accounts"].get(account_id) or {}
    from kazma_core.security.web_sessions import revoke_user_sessions

    ended = revoke_user_sessions(user_id=str(account.get("legacy_user_id") or account_id))
    logger.warning("[people] sessions ended id=%s count=%d by=%s", account_id, ended, by)
    return ended


def _limits_of(reg: dict[str, Any], account_id: str, limit_of: Any) -> dict[str, Any]:
    from kazma_core.spaces import features, person_models

    policy = reg["policies"].get(account_id) or {}
    own = policy.get("daily_limit") if isinstance(policy, dict) else None
    limit, source = limit_of(reg, account_id)
    return {"daily_limit": own if isinstance(own, dict) else None,
            "limit": limit.as_dict() if limit else None, "limit_source": source,
            "features": features.listed(features.grants_of(reg, account_id)),
            # The single tools of what they hold, each on or off (S2-01).
            "tools": features.listed_tools(features.grants_of(reg, account_id)),
            # The models they may use and their default (S2-03).
            "models": person_models.listed_models(reg, account_id)}


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
    # One create at a time from the folder check to the record (the S2-12
    # review, finding 10): two creates never take one folder.
    with _CREATE_LOCK:
        if kind == "host":
            _drop_orphan_rows()
            found = folder_rules.problem(project_folder)
            if found:
                raise PeopleRefused(f"folder_{found}", 400, "That folder cannot be this space's project folder.")
            workspace_id = _make_project_workspace(space_id, name, project_folder, by)
        _record_shared_space(space_id, name, kind, by, workspace_id)
    logger.info("[people] shared space created id=%s kind=%s by=%s", space_id, kind, by)
    return next(space for space in list_shared_spaces() if space["id"] == space_id)


def _record_shared_space(space_id: str, name: str, kind: str, by: str, workspace_id: str) -> None:
    """Record the new shared space; its project workspace goes if it is not
    recorded."""

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


def _drop_orphan_rows() -> None:
    """A workspace row of a space the identity store does not hold -- a
    create a stop cut off between its row and its record -- is dropped
    before the next create: no orphan root is ever left to block a folder."""
    from kazma_core.stores import get_workspace_store

    dropped = get_workspace_store().drop_rows_of_unknown_spaces(set(identity.read_registry()["spaces"]))
    if dropped:
        logger.warning("[people] %d workspace row(s) of no space were dropped (a create cut off)", dropped)


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


def _shared(reg: dict[str, Any], space_id: str) -> dict[str, Any]:
    """The shared space *space_id*, or the store's refusal: a personal space,
    one being erased and one that does not exist are no shared space."""
    space = reg["spaces"].get(space_id)
    if (space is None or space.get("personal_of") or space_id == OWNER_SPACE_ID
            or space.get("erasing_at") or space.get("account_deleted_at")):
        raise identity.IdentityError("not_shared", "That is not a shared space.")
    return space


def _role_in(reg: dict[str, Any], space_id: str, account_id: str) -> str | None:
    for membership in reg["memberships"]:
        if membership.get("account") == account_id and membership.get("space") == space_id:
            return str(membership.get("role") or "member")
    return None


def _take_out(reg: dict[str, Any], space_id: str, account_id: str) -> None:
    """Take *account_id* out of *space_id*; never its last owner."""
    role = _role_in(reg, space_id, account_id)
    if role is None:
        raise identity.IdentityError("not_a_member", "That person is not in this space.")
    owners = [m for m in reg["memberships"] if m.get("space") == space_id and m.get("role") == "owner"]
    if role == "owner" and len(owners) <= 1:
        raise identity.IdentityError("last_owner", "A shared space keeps its owner.")
    reg["memberships"] = [
        m for m in reg["memberships"]
        if not (m.get("account") == account_id and m.get("space") == space_id)
    ]


def add_member(space_id: str, account_id: str, *, by: str) -> None:
    """Add a person to a shared space (plan 4.11): the admin adds them
    directly; they are told at their next page load (``member_added``)."""
    _require_enforced()
    if not identity.is_admin_account(by):
        raise PeopleRefused("admin_only", 403, "Only the admin adds people to a shared space.")

    def _change(reg: dict[str, Any]) -> None:
        _shared(reg, space_id)
        if account_id not in reg["accounts"]:
            raise identity.IdentityError("no_such_account", "There is no such person.")
        if _role_in(reg, space_id, account_id) is not None:
            raise identity.IdentityError("already_member", "That person is already in this space.")
        reg["memberships"].append({"account": account_id, "space": space_id, "role": "member"})
        _record(reg, "member_added", account_id, by, space=space_id)

    _changed(_change)
    logger.info("[people] member added space=%s id=%s by=%s", space_id, account_id, by)


def leave_space(space_id: str, *, by: str) -> dict[str, Any]:
    """*by* leaves the shared space *space_id* (``member_left``). Every fact
    under their own memory entry there is forgotten, their messages stay
    (N36), and their share of its documents is taken back. A space's last
    owner does not leave it. Returns what went (:func:`_after_leaving`)."""
    _require_enforced()

    def _change(reg: dict[str, Any]) -> None:
        _shared(reg, space_id)
        _take_out(reg, space_id, by)
        _record(reg, "member_left", by, by, space=space_id)

    seq = _journal_then(_change, space_id, by)
    logger.info("[people] member left space=%s id=%s", space_id, by)
    return _after_leaving(space_id, by, seq)


def remove_member(space_id: str, account_id: str, *, by: str) -> dict[str, Any]:
    """Take a member out of a shared space: its owner or the admin does
    (``member_removed``; the person is told at their next page load). The
    same as leaving for what goes (N36): every fact under their own memory
    entry there, and their share of its documents; their messages stay."""
    _require_enforced()
    if account_id == by:
        return leave_space(space_id, by=by)
    admin = identity.is_admin_account(by)

    def _change(reg: dict[str, Any]) -> None:
        _shared(reg, space_id)
        if not admin and _role_in(reg, space_id, by) != "owner":
            raise identity.IdentityError(
                "not_yours", "Only the space's owner or the admin takes a member out.")
        _take_out(reg, space_id, account_id)
        _record(reg, "member_removed", account_id, by, space=space_id)

    try:
        seq = _journal_then(_change, space_id, account_id)
    except PeopleRefused as exc:
        if exc.code == "not_yours":
            raise PeopleRefused(exc.code, 403, str(exc)) from exc
        raise
    logger.info("[people] member removed space=%s id=%s by=%s", space_id, account_id, by)
    return _after_leaving(space_id, account_id, seq)


def _journal_then(change: Any, space_id: str, account_id: str) -> int:
    """Journal the forget of *account_id*'s entry in *space_id*, then make
    the membership *change* (the S2-12 review, finding 4): a stop between
    the two leaves an entry the forget convergence applies, so the facts go
    whatever happens next. The change is tried on a copy first, so a
    refused one journals nothing; a replay of an entry whose person is
    still a member forgets nothing (``forget_journal._still_a_member``).
    Returns the entry's sequence number."""
    import copy

    _changed_on(change, copy.deepcopy(identity.read_registry()))
    seq = _journal_entry_forget(space_id, account_id)
    _changed(change)
    return seq


def _changed_on(change: Any, reg: dict[str, Any]) -> None:
    """*change*'s refusal, on a copy of the registry (nothing written)."""
    try:
        change(reg)
    except identity.IdentityError as exc:
        status = 404 if exc.code in ("no_such_account", "no_such_space") else 409
        raise PeopleRefused(exc.code, status, str(exc)) from exc


def _journal_entry_forget(space_id: str, account_id: str) -> int:
    from kazma_core.memory import forget_journal
    from kazma_core.memory.speakers import entry_id

    try:
        return forget_journal.append("entry", tenant_id=space_id, session_key=entry_id(account_id))
    except (sqlite3.Error, OSError) as exc:
        logger.warning("[people] the forget of %s in %s could not be journaled: %s",
                       account_id, space_id, type(exc).__name__)
        raise PeopleRefused(
            "memory_unavailable", 503,
            "Memory cannot record this change right now, so nothing changed. Try again.",
        ) from exc


def _after_leaving(space_id: str, account_id: str, seq: int | None = None) -> dict[str, Any]:
    """What goes with a member who is no longer in *space_id*: every fact
    filed under their own memory entry there (N36), through memory's own
    forget step (journaled first, so the ledger, the mirror and the graph
    copy follow), and their share of its documents. Their messages stay.

    Run after the membership is gone, so no turn of theirs files a fact
    there meanwhile. A forget that fails is ``forget_failed`` (503): the
    membership has changed, and the facts are still there."""
    from kazma_core.documents.principal import take_back_access
    from kazma_core.memory import forget
    from kazma_core.memory.speakers import entry_id

    try:
        forgotten = forget.forget_entry(entry_id(account_id), tenant_id=space_id, journal_seq=seq)
    except (sqlite3.Error, OSError) as exc:
        logger.warning("[people] the facts of %s in %s could not be forgotten: %s",
                       account_id, space_id, type(exc).__name__, exc_info=True)
        raise PeopleRefused(
            "forget_failed", 503,
            "The person is out of the space, but the facts under their entry there "
            "could not be forgotten yet.",
        ) from exc
    if not forgotten.get("ok"):
        raise PeopleRefused("forget_failed", 503, "The facts under their entry could not be forgotten.")
    documents = take_back_access(space_id, account_id)
    return {"facts": len(forgotten.get("facts") or []), "documents": documents}


def notices_for(account_id: str) -> list[dict[str, Any]]:
    """The changes someone else made to *account_id* that its person has not
    read yet, oldest first: ``seq``, ``kind``, ``at``."""
    reg = identity.read_registry()
    seen = int(reg["events_seen"].get(account_id) or 0)
    told = []
    for event in reg["events"]:
        if not (isinstance(event, dict) and event.get("account") == account_id
                and event.get("kind") in _TOLD and event.get("by") != account_id
                and int(event.get("seq") or 0) > seen):
            continue
        notice = {"seq": int(event["seq"]), "kind": event["kind"], "at": event["at"]}
        if event.get("space"):
            # The space they were added to or taken out of: its name only.
            space = reg["spaces"].get(str(event["space"])) or {}
            notice["space_name"] = str(space.get("name") or "")
        if event["kind"] == "member_removed":
            # Said only once it is true (the S2-12 review, finding 4).
            notice["forgotten"] = _entry_forgotten(account_id, str(event.get("space") or ""))
        told.append(notice)
    return told


def _entry_forgotten(account_id: str, space_id: str) -> bool:
    from kazma_core.memory import forget
    from kazma_core.memory.speakers import entry_id

    try:
        return forget.entry_forgotten(entry_id(account_id), tenant_id=space_id)
    except (sqlite3.Error, OSError):
        logger.warning("[people] whether %s's facts in %s were forgotten could not be read",
                       account_id, space_id, exc_info=True)
        return False


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
        status = 404 if exc.code in ("no_such_account", "no_such_space") else 409
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
    (S1-27). Recorded. Every shared space they were in forgets the facts
    under their entry, as if they had left it (N36)."""
    _require_enforced()
    legacy: list[str] = []
    shared: list[str] = []

    def _change(reg: dict[str, Any]) -> None:
        account = _person(reg, account_id)
        legacy[:] = [str(account.get("legacy_user_id") or account_id)]
        shared[:] = [
            str(m.get("space")) for m in reg["memberships"]
            if m.get("account") == account_id
            and not (reg["spaces"].get(str(m.get("space"))) or {}).get("personal_of")
        ]
        del reg["accounts"][account_id]
        reg["policies"].pop(account_id, None)
        reg["memberships"] = [m for m in reg["memberships"] if m.get("account") != account_id]
        # Their chat-app accounts are nobody's now (stage 3): refused at once.
        reg["links"] = [link for link in reg["links"] if link.get("account") != account_id]
        now = _now()
        for space in reg["spaces"].values():
            if space.get("personal_of") == account_id:
                space["account_deleted_at"] = now
        reg["events_seen"].pop(account_id, None)
        _record(reg, "account_deleted", account_id, by)

    import copy

    _changed_on(_change, copy.deepcopy(identity.read_registry()))
    # Journaled before the account goes (the S2-12 review, finding 4); the
    # forget convergence also forgets any entry of an account that is gone.
    seqs = {space_id: _journal_entry_forget(space_id, account_id) for space_id in shared}
    _changed(_change)
    _end_sessions_of(legacy[0] if legacy else account_id)
    logger.warning("[people] account deleted id=%s by=%s", account_id, by)
    # Every shared space they were in forgets the facts under their entry,
    # as if they had left it (N36); all are tried before a failure is said.
    failed = None
    for space_id in shared:
        try:
            _after_leaving(space_id, account_id, seqs.get(space_id))
        except PeopleRefused as exc:
            failed = exc
    if failed is not None:
        raise failed


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


# ── Limits per space (S2-08) ────────────────────────────────────────────


def _checked_space_limits(raw: Any) -> dict[str, int]:
    from kazma_core.spaces.space_limits import LimitsRefused, parse_limits

    try:
        return parse_limits(raw)
    except LimitsRefused as exc:
        raise PeopleRefused(exc.code, 400, str(exc)) from exc


def set_space_limits(space_id: str, raw: dict[str, Any] | None, *, by: str) -> None:
    """Set a space's own limits (``{name: value}``, ``spaces.space_limits``;
    a limit left out or empty takes the limits for spaces), or take them
    all away (``None``). Recorded."""
    _require_enforced()
    limits = _checked_space_limits(raw)

    def _change(reg: dict[str, Any]) -> None:
        space = reg["spaces"].get(space_id)
        if not isinstance(space, dict) or space.get("erasing_at"):
            raise identity.IdentityError("no_such_space", "There is no such space.")
        if limits:
            space["limits"] = dict(limits)
        else:
            space.pop("limits", None)
        _record(reg, "space_limits_set", "", by, space=space_id)

    _changed(_change)
    logger.info("[people] limits set space=%s by=%s", space_id, by)


def set_default_space_limits(raw: dict[str, Any] | None, *, by: str) -> None:
    """Set the limits for spaces that have none of their own, or go back to
    Kazma's own (``None``). Recorded."""
    _require_enforced()
    limits = _checked_space_limits(raw)

    def _change(reg: dict[str, Any]) -> None:
        if limits:
            reg["defaults"]["space_limits"] = dict(limits)
        else:
            reg["defaults"].pop("space_limits", None)
        _record(reg, "default_space_limits_set", "", by)

    _changed(_change)
    logger.info("[people] the limits for spaces were set by=%s", by)


def spaces_usage() -> dict[str, Any]:
    """Every space that is open, the owner's first: its name and kind
    (``personal`` or ``shared``), its own limits (``None`` each not set)
    and what it holds and runs beside the limits it has
    (``space_limits.usage_of``); and the limits for spaces. Counts and
    sizes only, never a word of what a space holds. Blocking."""
    from kazma_core.spaces.space_limits import SHIPPED, limits_for_spaces, usage_of

    reg = identity.read_registry()
    names = {account_id: str(account.get("name") or "") for account_id, account in reg["accounts"].items()}
    out = []
    for space_id, space in reg["spaces"].items():
        if space.get("erasing_at") or (space.get("personal_of") and space.get("account_deleted_at")):
            continue
        personal = space.get("personal_of")
        out.append({
            "id": space_id,
            "name": str(space.get("name") or "") or names.get(str(personal or ""), ""),
            "kind": "personal" if personal or space_id == OWNER_SPACE_ID else "shared",
            "own_limits": dict(space.get("limits") or {}),
            **usage_of(space_id),
        })
    out.sort(key=lambda s: (s["id"] != OWNER_SPACE_ID, s["kind"] != "personal", s["name"].lower()))
    return {"spaces": out, "limits_for_spaces": limits_for_spaces(reg).as_dict(),
            "set": isinstance(reg["defaults"].get("space_limits"), dict), "shipped": SHIPPED.as_dict()}


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


def set_tools(account_id: str, off: Any, *, by: str) -> list[str]:
    """Switch single tools off for a person (S2-01, plan 4.8: "X Studio on,
    but posting off"): *off* is the whole list of their tools that are off.
    One step, recorded. Refused: the admin (who has every tool), a tool no
    person can have, a value that is not a list of names. Returns the list
    kept."""
    _require_enforced()
    from kazma_core.spaces import features

    kept: list[str] = []

    def _change(reg: dict[str, Any]) -> None:
        if account_id not in reg["accounts"]:
            raise identity.IdentityError("no_such_account", "There is no such person.")
        try:
            checked = features.checked_tools(account_id, off, reg)
        except features.FeatureRefused as exc:
            raise identity.IdentityError(exc.code, str(exc)) from exc
        features.apply_tools(reg, account_id, checked)
        kept[:] = checked
        _record(reg, "tools_set", account_id, by)

    try:
        _changed(_change)
    except PeopleRefused as exc:
        if exc.code in ("unknown_tool", "bad_tools"):
            raise PeopleRefused(exc.code, 400, str(exc)) from exc
        raise
    logger.info("[people] tools set id=%s off=%d by=%s", account_id, len(kept), by)
    return kept


def set_enabled(account_id: str, enabled: Any, *, by: str) -> None:
    """Disable or enable a person (S2-07). Disabled, they cannot sign in,
    their sessions end, and their work waits -- a scheduled job is held at
    its occurrence and says why, any other background work is refused as
    work of a feature that is off (``features.Grants.disabled``) -- until
    they are enabled again. Their spaces, data and switches stay. One step,
    recorded. Refused: the owner, a value that is not true or false."""
    _require_enforced()
    if not isinstance(enabled, bool):
        raise PeopleRefused("bad_enabled", 400, "A person is enabled or disabled.")

    def _change(reg: dict[str, Any]) -> None:
        account = _person(reg, account_id)
        account["enabled"] = enabled
        _record(reg, "account_enabled" if enabled else "account_disabled", account_id, by)

    reg = _changed(_change)
    if not enabled:
        _end_sessions(reg, account_id)
    logger.warning("[people] account %s id=%s by=%s", "enabled" if enabled else "disabled", account_id, by)


def set_models(account_id: str, allowed: Any, default: Any, *, by: str) -> dict[str, Any]:
    """Choose the models a person may use and their default (S2-03):
    *allowed* is the whole list of ``provider:model`` references (None:
    every model the install offers), *default* one of them (empty: the
    install's when they may use it, else their first). One step, recorded.
    Refused: the admin (who has every model), a value that is not a list of
    references, a default they may not use, no model at all. Returns what
    was kept: ``{"allowed", "default"}``."""
    _require_enforced()
    from kazma_core.spaces import person_models

    kept: dict[str, Any] = {}

    def _change(reg: dict[str, Any]) -> None:
        if account_id not in reg["accounts"]:
            raise identity.IdentityError("no_such_account", "There is no such person.")
        try:
            clean, chosen = person_models.checked_models(account_id, allowed, default, reg)
        except person_models.ModelsRefused as exc:
            raise identity.IdentityError(exc.code, str(exc)) from exc
        person_models.apply_models(reg, account_id, clean, chosen)
        kept.update({"allowed": clean, "default": chosen})
        _record(reg, "models_set", account_id, by)

    try:
        _changed(_change)
    except PeopleRefused as exc:
        if exc.code in ("bad_models", "default_not_allowed", "no_models"):
            raise PeopleRefused(exc.code, 400, str(exc)) from exc
        raise
    logger.info("[people] models set id=%s allowed=%s by=%s", account_id,
                "every" if kept.get("allowed") is None else len(kept["allowed"]), by)
    return kept


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

