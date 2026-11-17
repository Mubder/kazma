"""The identity store: accounts, spaces and who is in which (Spaces S1-01).

One document on the settings backend (SQLite or Postgres), key
:data:`REGISTRY_KEY`, changed only through :func:`update_registry` -- one
``atomic_update`` per change, so two writers never lose each other's work.
It replaces the ``platform.users`` list, which was read, changed and saved
back whole: two people saved at once and one user was gone.

What it holds:

* ``accounts`` -- one person's login: id, name, sign-in, enabled, the
  install role (``admin`` or ``member``), language.
* ``spaces`` -- a private world of data: id, name, kind (``host`` or
  ``sandboxed``), the account it is the personal space of (or none, for a
  shared space), who made it.
* ``memberships`` -- account, space, role in the space (``owner`` or
  ``member``).
* ``links``, ``space_keys``, ``policies`` -- kept empty here; stages 2 and 3
  and item S1-26 fill them.

**The owner is always there.** An install with no stored registry reads as
one account (:data:`OWNER_ACCOUNT_ID`, the admin) in one space
(:data:`OWNER_SPACE_ID`, a host space, the owner's personal space). Nothing
is written for that; :func:`move_platform_users` writes the registry once, at
start, and moves the ``platform.users`` list into it.

**Users of the older multi-user mode** (``security.platform_rbac``) are
accounts with ``sign_in.kind == "local"`` and their old role kept in
``legacy_role``. ``platform_rbac`` reads and writes them through
:func:`local_users` / :func:`update_local_users`; an install that has them
behaves as before until Spaces is enforced.

**Enforcement** is its own small key (:data:`ENFORCED_KEY`), never part of
the registry, so the scope module can keep it in memory with no read of a
document that holds password hashes. Nothing in the product writes it yet:
the switch that turns Spaces on arrives with the check of its preconditions
(plan S1-33), once every part of stage 1 keeps spaces apart.

Every function here reads or writes the settings store: call it off the
event loop (``asyncio.to_thread``). They are loop-stall helpers.
"""

from __future__ import annotations

import copy
import hashlib
import logging
import secrets
import threading
from datetime import UTC, datetime
from typing import Any, Callable

from kazma_core.spaces.scope import (
    OWNER_ACCOUNT_ID,
    OWNER_SPACE_ID,
    set_enforced,
    valid_account_id,
    valid_space_id,
)

__all__ = [
    "ENFORCED_KEY",
    "REGISTRY_KEY",
    "IdentityError",
    "IdentityUnavailable",
    "add_member",
    "create_space",
    "enforcement_enabled",
    "is_member",
    "list_accounts",
    "local_users",
    "move_platform_users",
    "prime_enforcement",
    "read_registry",
    "remove_member",
    "spaces_of",
    "update_local_users",
    "update_registry",
    "watch_enforcement",
]

logger = logging.getLogger(__name__)

#: The settings key of the registry document.
REGISTRY_KEY = "identity.registry"

#: The settings key of the enforcement switch (a plain boolean).
ENFORCED_KEY = "identity.enforced"

#: The old list this store replaces.
_PLATFORM_USERS_KEY = "platform.users"

_INSTALL_ROLES = ("admin", "member")
_SPACE_ROLES = ("owner", "member")
_SPACE_KINDS = ("host", "sandboxed")
_LEGACY_ROLES = ("viewer", "operator", "admin")


class IdentityError(ValueError):
    """A change the identity store refuses; ``code`` is stable for pages."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class IdentityUnavailable(RuntimeError):
    """The identity store could not be read. Never read as "no accounts"."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _store() -> Any:
    from kazma_core.config_store import get_config_store

    return get_config_store()


def _store_errors() -> tuple[type[BaseException], ...]:
    """What a settings round trip can legitimately raise (the one list,
    ``db.pg_helpers.store_errors``): a programming error still surfaces."""
    from kazma_core.db.pg_helpers import store_errors

    return store_errors()


def _is_admin_account(account_: dict[str, Any]) -> bool:
    """True for an account whose STORED install role is admin. This is the
    registry's own rule (one admin: the owner). Whether a request may do an
    admin's work is ``kazma_ui.auth.admin_decision``, never this."""
    return account_["role"] == "admin" and not account_.get("legacy_role")


# ── The document ────────────────────────────────────────────────────────


def _owner_account() -> dict[str, Any]:
    return {
        "id": OWNER_ACCOUNT_ID,
        "name": "",
        "role": "admin",
        "enabled": True,
        "language": "",
        "sign_in": {"kind": "owner"},
        "created_at": "",
    }


def _owner_space() -> dict[str, Any]:
    return {
        "id": OWNER_SPACE_ID,
        "name": "",
        "kind": "host",
        "personal_of": OWNER_ACCOUNT_ID,
        "created_by": OWNER_ACCOUNT_ID,
        "created_at": "",
    }


def _empty_registry() -> dict[str, Any]:
    """What an install with nothing stored is: the owner, alone."""
    return {
        "version": 0,
        "accounts": {OWNER_ACCOUNT_ID: _owner_account()},
        "spaces": {OWNER_SPACE_ID: _owner_space()},
        "memberships": [
            {"account": OWNER_ACCOUNT_ID, "space": OWNER_SPACE_ID, "role": "owner"}
        ],
        "links": [],
        "space_keys": {},
        "policies": {},
        "moved_platform_users": False,
    }


def _normalized(raw: Any) -> dict[str, Any]:
    """A stored value as a whole registry: the owner and the owner's space
    are always in it, and every table is of its type."""
    if raw is None:
        return _empty_registry()
    if not isinstance(raw, dict):
        raise IdentityUnavailable(
            f"{REGISTRY_KEY} holds {type(raw).__name__}, not a mapping"
        )
    reg = copy.deepcopy(raw)
    base = _empty_registry()
    for table in ("accounts", "spaces", "space_keys", "policies"):
        if not isinstance(reg.get(table), dict):
            reg[table] = {}
    for table in ("memberships", "links"):
        if not isinstance(reg.get(table), list):
            reg[table] = []
    reg["accounts"].setdefault(OWNER_ACCOUNT_ID, base["accounts"][OWNER_ACCOUNT_ID])
    reg["spaces"].setdefault(OWNER_SPACE_ID, base["spaces"][OWNER_SPACE_ID])
    if not any(
        m.get("account") == OWNER_ACCOUNT_ID and m.get("space") == OWNER_SPACE_ID
        for m in reg["memberships"]
        if isinstance(m, dict)
    ):
        reg["memberships"].append(base["memberships"][0])
    reg["version"] = int(reg.get("version") or 0)
    reg["moved_platform_users"] = bool(reg.get("moved_platform_users"))
    return reg


def _legacy_accounts(users: Any) -> dict[str, dict[str, Any]]:
    """The ``platform.users`` list as accounts, each user kept whole."""
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(users, list):
        return out
    for user in users:
        if not isinstance(user, dict):
            continue
        account_ = _account_from_local_user(user)
        if account_["id"] != OWNER_ACCOUNT_ID:
            out[account_["id"]] = account_
    return out


def read_registry() -> dict[str, Any]:
    """The registry, as a copy the caller may change freely.

    An install that has not moved its ``platform.users`` list yet reads with
    those users in it (nothing is written by a read). Raises
    :class:`IdentityUnavailable` when the store cannot answer.
    """
    try:
        store = _store()
        reg = _normalized(store.get(REGISTRY_KEY, None))
        if not reg["moved_platform_users"]:
            for account_id, account_ in _legacy_accounts(
                store.get(_PLATFORM_USERS_KEY, None)
            ).items():
                reg["accounts"].setdefault(account_id, account_)
    except IdentityUnavailable:
        raise
    except _store_errors() as exc:
        raise IdentityUnavailable(
            f"the identity store could not be read: {type(exc).__name__}"
        ) from exc
    return reg


def update_registry(change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Apply *change* to the registry as one atomic step and return the new
    registry. *change* edits the mapping it is given in place; raising
    :class:`IdentityError` in it writes nothing.

    The only writer of :data:`REGISTRY_KEY`.
    """
    store = _store()
    try:
        legacy = store.get(_PLATFORM_USERS_KEY, None)
    except _store_errors() as exc:
        # A store that cannot answer changes nothing: writing over what it
        # could not read is how a whole user list was lost (AUD-123).
        raise IdentityUnavailable(
            f"the identity store could not be read: {type(exc).__name__}"
        ) from exc
    refused: list[IdentityError] = []
    written: list[dict[str, Any]] = []

    def _updater(current: Any) -> Any:
        reg = _normalized(current)
        if not reg["moved_platform_users"]:
            for account_id, account_ in _legacy_accounts(legacy).items():
                reg["accounts"].setdefault(account_id, account_)
            reg["moved_platform_users"] = True
        try:
            change(reg)
            _check(reg)
        except IdentityError as exc:
            refused.append(exc)
            # Unchanged: what was stored is stored again.
            return current if isinstance(current, dict) else _normalized(None)
        reg["version"] = int(reg.get("version") or 0) + 1
        written.append(reg)
        return reg

    store.atomic_update(REGISTRY_KEY, _updater, category="auth")
    if refused:
        raise refused[0]
    if legacy:
        # The list's users are in the registry now; the old key goes. A crash
        # between the two leaves the key behind, read by nothing: the
        # registry says it was moved.
        try:
            store.delete(_PLATFORM_USERS_KEY)
        except _store_errors():
            logger.warning(
                "[identity] the old platform.users key could not be removed; "
                "it is no longer read",
                exc_info=True,
            )
    return copy.deepcopy(written[-1]) if written else read_registry()


def _check(reg: dict[str, Any]) -> None:
    """The rules every stored registry keeps."""
    names: dict[str, str] = {}
    admins = 0
    for account_id, account_ in reg["accounts"].items():
        if not valid_account_id(account_id) or account_.get("id") != account_id:
            raise IdentityError("bad_account_id", f"not an account id: {account_id!r}")
        if account_.get("role") not in _INSTALL_ROLES:
            raise IdentityError("bad_role", f"not an install role: {account_.get('role')!r}")
        if _is_admin_account(account_):
            admins += 1
        name = str(account_.get("name") or "").strip().lower()
        if not name and account_id == OWNER_ACCOUNT_ID:
            # The owner signs in with the install's secret until they choose
            # a name (plan S1-04); every other account has one.
            continue
        if not name:
            raise IdentityError("name_required", "an account needs a name")
        if name in names:
            raise IdentityError("name_taken", f"the name {account_.get('name')!r} is taken")
        names[name] = account_id
    owner = reg["accounts"].get(OWNER_ACCOUNT_ID) or {"role": ""}
    if not _is_admin_account(owner) or not owner.get("enabled", True):
        raise IdentityError("owner_required", "the owner's account stays an enabled admin")
    if admins != 1:
        raise IdentityError("one_admin", "an install has one admin: the owner")
    for space_id, space_ in reg["spaces"].items():
        if not valid_space_id(space_id) or space_.get("id") != space_id:
            raise IdentityError("bad_space_id", f"not a space id: {space_id!r}")
        if space_.get("kind") not in _SPACE_KINDS:
            raise IdentityError("bad_space_kind", f"not a space kind: {space_.get('kind')!r}")
        personal_of = space_.get("personal_of")
        if personal_of and personal_of not in reg["accounts"]:
            raise IdentityError("no_such_account", f"no account {personal_of!r}")
    seen: set[tuple[str, str]] = set()
    members: dict[str, list[str]] = {}
    for membership in reg["memberships"]:
        pair = (str(membership.get("account")), str(membership.get("space")))
        if pair[0] not in reg["accounts"]:
            raise IdentityError("no_such_account", f"no account {pair[0]!r}")
        if pair[1] not in reg["spaces"]:
            raise IdentityError("no_such_space", f"no space {pair[1]!r}")
        if membership.get("role") not in _SPACE_ROLES:
            raise IdentityError("bad_role", f"not a space role: {membership.get('role')!r}")
        if pair in seen:
            raise IdentityError("already_member", f"{pair[0]!r} is already in {pair[1]!r}")
        seen.add(pair)
        members.setdefault(pair[1], []).append(pair[0])
    for space_id, space_ in reg["spaces"].items():
        personal_of = space_.get("personal_of")
        if personal_of and members.get(space_id, []) != [personal_of]:
            # A personal space has one member forever (decision N5).
            raise IdentityError(
                "personal_space_is_private",
                f"the personal space {space_id!r} has one member: {personal_of!r}",
            )


# ── Reading ─────────────────────────────────────────────────────────────


def list_accounts() -> list[dict[str, Any]]:
    """Every account, the owner first."""
    reg = read_registry()
    return sorted(
        reg["accounts"].values(),
        key=lambda a: (a["id"] != OWNER_ACCOUNT_ID, str(a.get("name") or "").lower()),
    )


def spaces_of(account_id: str) -> list[dict[str, Any]]:
    """The spaces an account is a member of, its personal space first; each
    carries ``role``, the account's role in it."""
    reg = read_registry()
    out: list[dict[str, Any]] = []
    for membership in reg["memberships"]:
        if membership.get("account") != account_id:
            continue
        found = reg["spaces"].get(str(membership.get("space")))
        if found:
            out.append({**found, "role": membership.get("role")})
    return sorted(
        out, key=lambda s: (s.get("personal_of") != account_id, str(s.get("name") or s["id"]))
    )


def is_member(account_id: str, space_id: str) -> bool:
    """True when the account is a member of the space. An unreadable store
    raises: "not a member" is never guessed."""
    return any(
        m.get("account") == account_id and m.get("space") == space_id
        for m in read_registry()["memberships"]
    )


# ── Changing spaces ─────────────────────────────────────────────────────


def create_space(
    name: str,
    *,
    created_by: str,
    kind: str = "sandboxed",
    personal_of: str | None = None,
    space_id: str | None = None,
) -> dict[str, Any]:
    """Make a space. A personal space gets its one member; a shared space
    gets its maker as ``owner``. Returns the space."""
    new_id = space_id or f"s{secrets.token_hex(6)}"
    made: dict[str, Any] = {}

    def _change(reg: dict[str, Any]) -> None:
        if new_id in reg["spaces"]:
            raise IdentityError("space_exists", f"the space {new_id!r} exists")
        if created_by not in reg["accounts"]:
            raise IdentityError("no_such_account", f"no account {created_by!r}")
        made.update(
            id=new_id,
            name=str(name or "").strip(),
            kind=kind,
            personal_of=personal_of or None,
            created_by=created_by,
            created_at=_now(),
        )
        reg["spaces"][new_id] = dict(made)
        reg["memberships"].append(
            {"account": personal_of or created_by, "space": new_id, "role": "owner"}
        )

    update_registry(_change)
    return made


def add_member(space_id: str, account_id: str, *, role: str = "member") -> None:
    """Add an account to a shared space. A personal space refuses."""

    def _change(reg: dict[str, Any]) -> None:
        reg["memberships"].append({"account": account_id, "space": space_id, "role": role})

    update_registry(_change)


def remove_member(space_id: str, account_id: str) -> None:
    """Take an account out of a shared space. The data it put there stays."""

    def _change(reg: dict[str, Any]) -> None:
        before = len(reg["memberships"])
        reg["memberships"] = [
            m
            for m in reg["memberships"]
            if not (m.get("account") == account_id and m.get("space") == space_id)
        ]
        if len(reg["memberships"]) == before:
            raise IdentityError("not_a_member", f"{account_id!r} is not in {space_id!r}")

    update_registry(_change)


# ── The older multi-user mode's users ───────────────────────────────────


def _account_from_local_user(user: dict[str, Any]) -> dict[str, Any]:
    name = str(user.get("username") or "").strip()
    raw_id = str(user.get("user_id") or name).strip().lower()
    if valid_account_id(raw_id) and raw_id != OWNER_ACCOUNT_ID:
        account_id = raw_id
    else:
        # The same user reads as the same account every time, before and
        # after the move.
        account_id = "u" + hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:12]
    role = str(user.get("role") or "operator")
    if role not in _LEGACY_ROLES:
        role = "operator"
    return {
        "id": account_id,
        "name": name,
        "role": "member",
        "legacy_role": role,
        "legacy_user_id": str(user.get("user_id") or name),
        "enabled": bool(user.get("enabled", True)),
        "language": "",
        "sign_in": {"kind": "local", "password_hash": str(user.get("password_hash") or "")},
        "meta": dict(user.get("meta") or {}),
        "created_at": str(user.get("created_at") or ""),
    }


def _local_user_from_account(account_: dict[str, Any]) -> dict[str, Any]:
    sign_in = account_.get("sign_in") or {}
    return {
        "user_id": str(account_.get("legacy_user_id") or account_["id"]),
        "username": str(account_.get("name") or ""),
        "password_hash": str(sign_in.get("password_hash") or ""),
        "role": str(account_.get("legacy_role") or "operator"),
        "enabled": bool(account_.get("enabled", True)),
        "meta": dict(account_.get("meta") or {}),
    }


def _is_local_user(account_: dict[str, Any]) -> bool:
    return (account_.get("sign_in") or {}).get("kind") == "local" and bool(
        account_.get("legacy_role")
    )


def local_users() -> list[dict[str, Any]]:
    """The users of the older multi-user mode, in ``platform.users``'s shape
    (``user_id``, ``username``, ``password_hash``, ``role``, ``enabled``,
    ``meta``). The owner is not one of them."""
    return [
        _local_user_from_account(a) for a in read_registry()["accounts"].values() if _is_local_user(a)
    ]


def update_local_users(
    change: Callable[[list[dict[str, Any]]], list[dict[str, Any]] | None],
) -> list[dict[str, Any]]:
    """Change the older mode's users as one atomic step.

    *change* gets the list in ``platform.users``'s shape and edits it in
    place, or returns a new list. Users it adds become accounts, users it
    drops are removed with their memberships, and the rest keep their account
    id. Returns the new list.
    """
    result: list[dict[str, Any]] = []

    def _change(reg: dict[str, Any]) -> None:
        current = {
            account_id: a for account_id, a in reg["accounts"].items() if _is_local_user(a)
        }
        users = [_local_user_from_account(a) for a in current.values()]
        returned = change(users)
        users = users if returned is None else list(returned)
        by_legacy_id = {
            str(a.get("legacy_user_id") or account_id): account_id
            for account_id, a in current.items()
        }
        kept: set[str] = set()
        for user in users:
            if not isinstance(user, dict):
                continue
            fresh = _account_from_local_user(user)
            account_id = by_legacy_id.get(fresh["legacy_user_id"])
            if account_id is None:
                account_id = fresh["id"]
                while account_id in reg["accounts"]:
                    account_id = f"u{secrets.token_hex(6)}"
                fresh["created_at"] = _now()
            else:
                fresh["created_at"] = current[account_id].get("created_at", "")
            fresh["id"] = account_id
            reg["accounts"][account_id] = fresh
            kept.add(account_id)
        for account_id in set(current) - kept:
            del reg["accounts"][account_id]
            reg["memberships"] = [
                m for m in reg["memberships"] if m.get("account") != account_id
            ]
        result[:] = [
            _local_user_from_account(a) for a in reg["accounts"].values() if _is_local_user(a)
        ]

    update_registry(_change)
    return list(result)


def move_platform_users() -> int:
    """Write the registry once and move the ``platform.users`` list into it.

    Called at start. Idempotent: an install that already moved writes
    nothing. Returns how many users the registry holds from the old list.
    """
    store = _store()
    stored = store.get(REGISTRY_KEY, None)
    if isinstance(stored, dict) and stored.get("moved_platform_users"):
        return len(local_users())
    if not store.get(_PLATFORM_USERS_KEY, None) and stored is None:
        # Nothing to move and nothing stored: the owner alone, which is what
        # an empty store reads as. No write.
        return 0
    update_registry(lambda reg: None)
    moved = len(local_users())
    logger.info("[identity] the platform user list is in the identity store (%d users)", moved)
    return moved


# ── Enforcement ─────────────────────────────────────────────────────────


def enforcement_enabled() -> bool:
    """The stored switch. Raises :class:`IdentityUnavailable` when the store
    cannot answer: unknown is never read as "off"."""
    try:
        return bool(_store().get(ENFORCED_KEY, False))
    except _store_errors() as exc:
        raise IdentityUnavailable(
            f"the Spaces switch could not be read: {type(exc).__name__}"
        ) from exc


def prime_enforcement() -> bool:
    """Read the stored switch into this process (``scope.spaces_enforced``).
    A store that cannot answer leaves the process as it was, with a WARNING.
    """
    from kazma_core.spaces import scope

    try:
        set_enforced(enforcement_enabled())
    except IdentityUnavailable:
        logger.warning(
            "[identity] the Spaces switch could not be read; this process keeps "
            "its last known answer (%s)",
            "enforced" if scope.spaces_enforced() else "not enforced",
        )
    return scope.spaces_enforced()


_watch_lock = threading.Lock()
_watched: list[Any] = []


def watch_enforcement() -> None:
    """Follow the stored switch: prime now, and again whenever it is written
    in this process. Called once at start, off the loop."""
    store = _store()
    with _watch_lock:
        if any(seen is store for seen in _watched):
            return
        _watched.append(store)

    def _on_change(keys: Any) -> None:
        # The store calls this after its commit, outside its lock, in the
        # thread that wrote: the read below is one more statement there.
        if keys is None or ENFORCED_KEY in keys:
            prime_enforcement()

    add = getattr(store, "add_change_listener", None)
    if callable(add):
        add(_on_change)
    prime_enforcement()

