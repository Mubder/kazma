"""Platform multi-user RBAC (admin / operator / viewer) — Phase 4.4.

Distinct from division RBAC (``kazma_core.rbac`` enterprise divisions).
This module gates *product* capabilities for the web UI / API.

Roles (least → most privilege):
  * viewer   — read-only APIs, no approve/write/exec
  * operator — day-to-day chat, HITL approve, IDE (default operator)
  * admin    — settings, users, secrets, system

Enable with users in ConfigStore key ``platform.users`` or Postgres
``kazma_platform_users``. Optional OIDC: see ``kazma_core.security.oidc``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
from dataclasses import dataclass
from typing import Any

from kazma_core.security.secret_compare import same_secret

__all__ = [
    "PlatformRole",
    "PlatformUser",
    "PlatformUsersUnavailable",
    "ROLE_RANK",
    "authenticate_local_user",
    "create_local_user",
    "get_user",
    "list_users",
    "require_role",
    "role_allows",
    "set_local_password",
    "valid_tenant_id",
]

logger = logging.getLogger(__name__)

PlatformRole = str  # "viewer" | "operator" | "admin"

ROLE_RANK: dict[str, int] = {
    "viewer": 10,
    "operator": 50,
    "admin": 100,
}

# Path prefixes that require at least admin
_ADMIN_PREFIXES = (
    "/api/settings",
    # The People routes (Spaces S1-25): create, reset and delete people.
    "/api/people",
    # A problem report carries the install's diagnostics out of it (3.1).
    "/api/support",
    "/api/import",
    "/api/system",
    "/api/mcp",
    "/api/connectors",
    "/api/providers",
    "/api/provider",
    "/api/config",
    "/api/chaos",
    # Every tenant's checkpointed threads: list, delete one, clear all. Was on
    # the viewer list (audit 2026-09-22).
    "/api/sessions",
    "/settings",
    # The memory engine's own maintenance: the durable task queue (list,
    # retry, clear), reconsolidation of every tenant's facts, and the golden
    # benchmark. The rest of /api/memory is per tenant, and any role reaches
    # it (tests/test_memory_routes_tenant_scope.py, 2026-09-27).
    "/api/memory/v2/queue",
    "/api/memory/v2/reconsolidate",
    "/api/memory/v2/eval",
    # AUD-104: the Soul confirm queue. A confirmed delta is guidance in every
    # future system prompt, for every user of the install -- an operator
    # could confirm what the admin had held back.
    "/api/commitment",
    # The install's mail and calendar connections: the OAuth client, the
    # accounts, sign-in and disconnect. One set for the whole install, used
    # by every chat; only Settings (an admin page) calls these routes, and
    # they carry no check of their own. An operator could reconnect or
    # disconnect the install's mailbox until 2026-11-10. The X credential
    # routes were already admin-only (above).
    "/api/email",
    "/api/calendar",
    # The setup guide saves a provider's key and switches the model Kazma
    # uses, as Settings does: its page and its writes are the admin's.
    "/setup",
    "/api/setup/local",
    "/api/setup/models",
    "/api/setup/bootstrap",
    "/api/setup/check",
)

# Writes that change the whole install: a credential every chat uses, the
# active workspace (process-wide, and the file scope of every agent tool) or
# a process-wide switch. Reading them stays with every signed-in role; any
# other method needs admin. Until 2026-11-15 an operator session or token
# could replace the install's GitHub token or App key, switch or add the
# workspace, add folders outside it, stop the chat apps, decide a division
# request and save a model profile's key. ``tests/test_install_wide_writes.py``
# holds every such route of the built app to this rule.
_ADMIN_WRITE_PREFIXES = (
    "/api/github/token",
    "/api/github/app",
    "/api/github/oauth/start",
    "/api/github/oauth/revoke",
    # A clone becomes the active workspace.
    "/api/github/repos/clone",
    "/api/workspaces/create",
    "/api/workspaces/switch",
    "/api/workspaces/delete",
    "/api/workspace/select",
    # Folders outside the workspace every file tool may read and write.
    "/api/workspace/extra-roots",
    # The chat apps' start, stop and rebuild: every chat platform at once.
    "/api/gateway",
    "/api/divisions",
    "/api/models/profiles",
    "/api/models/saved",
)

# Once Spaces is enforced, a space's own connected accounts are its members'
# to connect and disconnect (decision N26: each person connects their own; the
# review of Spaces S1-17c): GitHub's token, sign-in and disconnect, and the
# mail and calendar routes. They were the admin's in every mode, and a member
# is an operator: no member could connect a mailbox, a calendar or GitHub of
# their own space. Membership is the tenant middleware's to check (every one
# is a ``space`` route). The install's own pieces stay the admin's: the mail
# sign-ins' app registrations and the mail presets (``install`` routes, held
# equal to ``route_scopes`` by ``tests/test_github_account_spaces.py``), and,
# being outside this list, the GitHub App and a clone. Not enforced, every
# one is the admin's, as before.
#
# X too (the owner, 2026-11-18: "Yes, per space"): a member connects their
# own X account in their own space -- the credentials (the OAuth 1.0a keys,
# the app's Bearer token and OAuth 2.0 client), Sign in with X and
# Disconnect. What publishes is unchanged: the always-ask card, only the
# asker approves their own posts (N4), nothing of another space reaches the
# owner's chats (O1), a draft is seen before it is published. The reply
# bot's policy and resuming X after a stop stay the admin's (continuing
# authority, below).
_SPACE_ACCOUNT_PREFIXES = (
    "/api/github/token",
    "/api/github/oauth/start",
    "/api/github/oauth/revoke",
    "/api/email",
    "/api/calendar",
    "/api/x/credentials",
    "/api/x/api-access",
    "/api/x/oauth2/start",
    "/api/x/oauth2/sign-out",
    "/api/x/disconnect",
)
_INSTALL_ACCOUNT_PIECES = (
    "/api/email/presets",
    "/api/email/oauth/gmail/client",
    "/api/email/oauth/microsoft/client",
)


# Once Spaces is enforced, Settings is everyone's page, split in three
# (Spaces S1-23): My account and This space are each person's, Install the
# admin's. A member opens the page and changes their own password and sees
# their own browsers' sessions; the API tokens (the owner's account), the
# platform users and every install setting stay the admin's. Not enforced,
# Settings stays the admin's page, as before.
_OWN_SETTINGS = (
    ("/settings", "GET"),
    ("/api/settings/account/password", "PUT"),
    ("/api/settings/account/sessions", "GET"),
)


def _reaches_own_settings(path: str, method: str) -> bool:
    """Whether *path* is a person's own part of Settings, which any
    signed-in account reaches once Spaces is enforced (:data:`_OWN_SETTINGS`).
    Read from memory (``spaces_enforced``): no settings read."""
    if (path.rstrip("/") or "/", method.upper()) not in _OWN_SETTINGS:
        return False
    from kazma_core.spaces.scope import spaces_enforced

    return spaces_enforced()


def _under(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == p or path.startswith(p + "/") for p in prefixes)


def _connects_a_spaces_own_account(path: str) -> bool:
    """Whether *path* connects or disconnects a space's own account, which a
    member may do once Spaces is enforced (:data:`_SPACE_ACCOUNT_PREFIXES`).
    Read from memory (``spaces_enforced``): no settings read."""
    if not _under(path, _SPACE_ACCOUNT_PREFIXES) or _under(path, _INSTALL_ACCOUNT_PIECES):
        return False
    from kazma_core.spaces.scope import spaces_enforced

    return spaces_enforced()


#: The requests in which a person gives the account they connect (a key, a
#: password, a device sign-in's answer). Named, never a prefix: a disconnect
#: or a test that refreshes a token writes too, and what it writes is not
#: theirs because they pressed it. A sign-in through the provider finishes in
#: its callback (``oauth_sign_in.attempt_scope``).
CONNECT_ROUTES = frozenset({
    ("POST", "/api/email/accounts"),
    ("POST", "/api/email/gmail/connect"),
    ("POST", "/api/email/oauth/microsoft/device/poll"),
    ("POST", "/api/email/protocol/connect"),
    ("POST", "/api/github/token"),
    ("POST", "/api/x/api-access"),
    ("POST", "/api/x/credentials"),
})


def connects_an_account(path: str, method: str) -> bool:
    """Whether the request is a person connecting an account of their space
    (:data:`CONNECT_ROUTES`): what it keeps is recorded as theirs
    (``connected_accounts.connecting``). Only once Spaces is enforced."""
    return (method.upper(), path.rstrip("/")) in CONNECT_ROUTES and _connects_a_spaces_own_account(path)


# Single routes (never their children) only an administrator may call.
# Model discovery asks the address a request names: with an operator's
# credential, or a link the signed-in owner opened (a GET carries the
# cookie, and CSRF checks writes only), it asked any host until 2026-11-16.
# The address is checked too (security.ssrf.check_model_server_url); only
# Settings, an admin page, calls it. /api/models/options and the rest stay.
_ADMIN_EXACT = (
    "/api/models",
    "/v1/models",
)

# Paths viewer may access (read-ish); everything else needs operator+
_VIEWER_OK_PREFIXES = (
    "/api/status",
    "/api/telemetry",
    "/api/chat",
    "/api/memory",
    "/api/dashboard",
    "/dashboard",
    "/chat",
    "/health",
    "/",
)


@dataclass
class PlatformUser:
    user_id: str
    username: str
    role: str = "operator"
    enabled: bool = True
    meta: dict[str, Any] | None = None

    def has_at_least(self, role: str) -> bool:
        return ROLE_RANK.get(self.role, 0) >= ROLE_RANK.get(role, 999)

    @property
    def tenant_id(self) -> str | None:
        """The tenant this user's requests run under, if bound (``meta.tenant_id``)."""
        value = (self.meta or {}).get("tenant_id")
        return str(value) if value else None


_TENANT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def valid_tenant_id(value: str) -> bool:
    """A tenant id is a short slug: it keys secrets, memory and sessions."""
    return bool(_TENANT_ID_RE.match(value or ""))


def role_allows(role: str, path: str, method: str = "GET") -> bool:
    """Return True if *role* may access *path* with *method*."""
    rank = ROLE_RANK.get(role, 0)
    if rank >= ROLE_RANK["admin"]:
        return True
    m = method.upper()
    # A space's own connected accounts are its members' once Spaces is
    # enforced (GitHub, mail, calendar, X's credentials and sign-in): before
    # the X rule below, which keeps them the admin's otherwise.
    if _connects_a_spaces_own_account(path):
        return rank >= ROLE_RANK["operator"]
    if _reaches_own_settings(path, m):
        return rank >= ROLE_RANK["viewer"]
    # Credentials and publication policy grant continuing authority. Operators
    # can review individual drafts, but only administrators can change that grant.
    if ((path.rstrip("/") in ("/api/x/reply", "/api/x/reply/qualification") and m == "PUT")
            or path.rstrip("/") in ("/api/x/credentials", "/api/x/disconnect", "/api/x/resume")
            # The whole X API's credentials: the app's Bearer token, the OAuth
            # 2.0 client and Sign in with X (2026-11-17).
            or path.rstrip("/") in ("/api/x/oauth2/start", "/api/x/oauth2/sign-out")
            or (path.rstrip("/") == "/api/x/api-access" and m not in ("GET", "HEAD", "OPTIONS"))):
        return False
    # "About me" is shown to the model on every call, for every user of the
    # tenant: changing it is guidance for all of them, like the personality,
    # so only an administrator may (Memory audit M11).
    if path.rstrip("/") == "/api/memory/v2/profile" and m in ("PUT", "POST", "PATCH", "DELETE"):
        return False
    if path.rstrip("/") in _ADMIN_EXACT:
        return False
    # Admin-only surfaces
    for p in _ADMIN_PREFIXES:
        if path == p or path.startswith(p + "/"):
            return rank >= ROLE_RANK["admin"]
    if m not in ("GET", "HEAD", "OPTIONS"):
        for p in _ADMIN_WRITE_PREFIXES:
            if path == p or path.startswith(p + "/"):
                return False
    # Mutations need operator+
    if m in ("POST", "PUT", "PATCH", "DELETE"):
        # Approve / chat / ide mutations
        return rank >= ROLE_RANK["operator"]
    # GET: viewer ok for read surfaces; operator for rest
    if rank >= ROLE_RANK["operator"]:
        return True
    # viewer
    for p in _VIEWER_OK_PREFIXES:
        if path == p or path.startswith(p + "/"):
            return True
    return False


def require_role(user: PlatformUser | None, minimum: str) -> bool:
    if user is None or not user.enabled:
        return False
    return user.has_at_least(minimum)


# Aligned with security/vault.py (OWASP PBKDF2-SHA256 guidance). Login
# passwords previously used 200k — legacy 3-field hashes (which carry no
# iteration field) still verify at that count and upgrade on next login.
_PBKDF2_ITERATIONS = 600_000
_PBKDF2_LEGACY_ITERATIONS = 200_000


def _pbkdf2_hex(password: str, salt: str, iterations: int) -> str:
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    )
    return dk.hex()


def _hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    return (
        f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}"
        f"${_pbkdf2_hex(password, salt, _PBKDF2_ITERATIONS)}"
    )


def _is_legacy_password_hash(stored: str) -> bool:
    """Pre-alignment hash: 3 fields, iteration count implicit (200k)."""
    parts = stored.split("$")
    return len(parts) == 3 and parts[0] == "pbkdf2_sha256"


def _verify_password(password: str, stored: str) -> bool:
    return _verify_password_spent(password, stored)[0]


def _verify_password_spent(password: str, stored: str) -> tuple[bool, int]:
    """Whether *password* matches *stored*, and the PBKDF2 iterations spent
    finding out: 0 for a hash that is empty or malformed, 200,000 for a
    legacy one. :func:`authenticate_local_user` tops a refusal up to one
    full verification with it."""
    try:
        parts = stored.split("$")
        if len(parts) == 4 and parts[0] == "pbkdf2_sha256":
            iterations = int(parts[1])
            salt, hexdigest = parts[2], parts[3]
        elif _is_legacy_password_hash(stored):
            iterations = _PBKDF2_LEGACY_ITERATIONS
            salt, hexdigest = parts[1], parts[2]
        else:
            return False, 0
        if not 0 < iterations <= 10_000_000:
            return False, 0
        check = _pbkdf2_hex(password, salt, iterations)
        return same_secret(check, hexdigest), iterations
    except Exception:
        return False, 0


def _spend_the_rest(password: str, spent: int) -> None:
    """Spend what one full verification costs, less *spent* iterations.

    Every refusal of a local sign-in costs the same work (2026-11-15): an
    unknown name costs one verification of :data:`_DUMMY_PASSWORD_HASH`
    (600,000 iterations), while a name whose stored hash is legacy cost
    200,000 and an empty or malformed one cost nothing -- so the time of a
    refusal said which names exist, and what their hash looked like. Kazma
    writes no hash above 600,000 iterations; one that costs more is not
    evened out.
    """
    rest = _PBKDF2_ITERATIONS - max(0, int(spent))
    if rest > 0:
        _pbkdf2_hex(password, "kazma-no-such-user", rest)


class PlatformUsersUnavailable(RuntimeError):
    """The platform user list could not be read (audit 2026-10-26 AUD-123)."""


def _load_users_from_store() -> list[dict[str, Any]]:
    """The platform users, or :class:`PlatformUsersUnavailable` when the store
    cannot answer -- never an empty list standing for "unknown".

    It swallowed the error and answered ``[]``, which is single-operator
    mode: the multi-user check said "no" during a store outage, so
    ``tenant_isolation.multi_user_or_production`` -- written to fail closed on
    an RBAC store error -- never saw one, and a read-modify-write of the list
    saved an empty read back over the real one (audit 2026-10-26 AUD-123).
    Only a sign-in or a listing, where an empty list fails closed, takes
    :func:`_users_for_sign_in` instead.
    """
    # The users are accounts of the identity store since Spaces S1-01
    # (kazma_core.spaces.identity); this is their old shape.
    from kazma_core.spaces import identity

    try:
        return identity.local_users()
    except identity.IdentityUnavailable as exc:
        raise PlatformUsersUnavailable(f"the platform users are unreadable: {exc}") from exc


def _users_for_sign_in() -> list[dict[str, Any]]:
    """The platform users for a sign-in or a listing; empty when the store
    cannot answer (no local user authenticates: fail-closed there). A
    decision that "no users" would open reads :func:`_load_users_from_store`.
    """
    try:
        return _load_users_from_store()
    except PlatformUsersUnavailable:
        # Safe to swallow the value (audit O3): an empty user list means no
        # local user authenticates, which is fail-closed. Logged because a
        # store error here silently locks every local user out.
        logger.warning(
            "[rbac] could not read platform.users — no local user will "
            "authenticate until the store recovers",
            exc_info=True,
        )
    return []


def _save_users_to_store(users: list[dict[str, Any]]) -> None:
    """Replace the user list with *users*. A caller that read the list first
    and saves a changed copy can lose another writer's change between the
    two: use :func:`_update_users` for that."""
    from kazma_core.spaces import identity

    identity.update_local_users(lambda _current: list(users))


def _update_users(change: Any) -> list[dict[str, Any]]:
    """Read, change and save the user list as ONE atomic step (Spaces S1-01).

    *change* edits the list it is given in place, or returns a new one. The
    list was read, changed and saved back whole until 2026-11: two saves at
    once and one of them was lost -- a user created while another's password
    was being set was gone.
    """
    from kazma_core.spaces import identity

    try:
        return identity.update_local_users(change)
    except identity.IdentityUnavailable as exc:
        raise PlatformUsersUnavailable(f"the platform users are unreadable: {exc}") from exc


def list_users() -> list[PlatformUser]:
    out: list[PlatformUser] = []
    for u in _users_for_sign_in():
        out.append(
            PlatformUser(
                user_id=str(u.get("user_id") or u.get("username") or ""),
                username=str(u.get("username") or ""),
                role=str(u.get("role") or "operator"),
                enabled=bool(u.get("enabled", True)),
                meta=dict(u.get("meta") or {}),
            )
        )
    return out


def get_user(username: str) -> PlatformUser | None:
    for u in list_users():
        if u.username.lower() == username.lower():
            return u
    return None


def create_local_user(
    username: str,
    password: str,
    *,
    role: str = "operator",
    user_id: str | None = None,
    tenant_id: str | None = None,
) -> PlatformUser:
    """Create or update a local username/password user.

    ``tenant_id`` binds the user's requests to that tenant (their sessions
    carry it and the tenant middleware scopes every request by it). Omitted
    on an update, an existing binding is kept.
    """
    if role not in ROLE_RANK:
        raise ValueError(f"Invalid role {role!r}; use viewer|operator|admin")
    if tenant_id is not None and not valid_tenant_id(tenant_id):
        raise ValueError(f"Invalid tenant_id {tenant_id!r}")
    # One atomic step: an unreadable list raises (read as empty, it was saved
    # back as just this user, every other user gone: audit 2026-10-26
    # AUD-123), and a user saved by someone else meanwhile is kept.
    new_uid = user_id or secrets.token_hex(8)
    ph = _hash_password(password)
    outcome: dict[str, Any] = {"uid": new_uid, "meta": {}}

    def _upsert(users: list[dict[str, Any]]) -> None:
        meta: dict[str, Any] = {}
        for u in users:
            if str(u.get("username", "")).lower() == username.lower():
                u["password_hash"] = ph
                u["role"] = role
                u["enabled"] = True
                meta = dict(u.get("meta") or {})
                if tenant_id is not None:
                    meta["tenant_id"] = tenant_id
                u["meta"] = meta
                outcome.update(uid=str(u.get("user_id") or new_uid), meta=meta)
                return
        if tenant_id is not None:
            meta["tenant_id"] = tenant_id
        users.append(
            {
                "user_id": new_uid,
                "username": username,
                "password_hash": ph,
                "role": role,
                "enabled": True,
                "meta": meta,
            }
        )
        outcome.update(uid=new_uid, meta=meta)

    _update_users(_upsert)
    uid = str(outcome["uid"])
    meta = dict(outcome["meta"])
    logger.warning(
        "[platform_rbac] user upsert username=%s role=%s tenant=%s",
        username, role, meta.get("tenant_id") or "-",
    )
    return PlatformUser(user_id=uid, username=username, role=role, enabled=True, meta=meta)


#: Verified against when no stored hash is: an unknown or disabled user costs
#: the same PBKDF2 as a known one. Its digest matches no password (a fixed
#: value, not a hash of one); only the cost is the point.
_DUMMY_PASSWORD_HASH = (
    f"pbkdf2_sha256${_PBKDF2_ITERATIONS}$kazma-no-such-user${'0' * 64}"
)


def authenticate_local_user(username: str, password: str) -> PlatformUser | None:
    """Validate local credentials; return PlatformUser or None.

    Every refusal costs one full PBKDF2 verification (600,000 iterations):
    an unknown or a disabled user returned at once while a known one cost
    600,000 iterations, so the answer's time said which usernames exist
    (security audit 2026-11-10); a known user with a legacy hash (200,000)
    or an empty one (none) still differed until 2026-11-15
    (:func:`_spend_the_rest`).
    """
    for u in _users_for_sign_in():
        if str(u.get("username", "")).lower() != username.lower():
            continue
        if not u.get("enabled", True):
            _verify_password(password, _DUMMY_PASSWORD_HASH)
            return None
        stored = str(u.get("password_hash") or "")
        matched, spent = _verify_password_spent(password, stored)
        if matched:
            if _is_legacy_password_hash(stored):
                # Silent migration: re-hash at the aligned 600k cost.
                # Best-effort — a failed write leaves the legacy hash in
                # place (still verifiable) rather than locking the user out.
                try:
                    upgraded = _hash_password(password)

                    def _upgrade(users: list[dict[str, Any]]) -> None:
                        for candidate in users:
                            if (
                                str(candidate.get("user_id") or "")
                                == str(u.get("user_id") or "")
                                and str(candidate.get("username", "")).lower()
                                == username.lower()
                                # Still the hash that was just verified: a
                                # password changed meanwhile is kept.
                                and str(candidate.get("password_hash") or "") == stored
                            ):
                                candidate["password_hash"] = upgraded

                    _update_users(_upgrade)
                    logger.info(
                        "[platform_rbac] upgraded password hash to "
                        "%d iterations for username=%s",
                        _PBKDF2_ITERATIONS,
                        username,
                    )
                except Exception:
                    logger.debug(
                        "[platform_rbac] password hash upgrade skipped",
                        exc_info=True,
                    )
            return PlatformUser(
                user_id=str(u.get("user_id") or username),
                username=str(u.get("username")),
                role=str(u.get("role") or "operator"),
                enabled=True,
                meta=dict(u.get("meta") or {}),
            )
        # The name matched and the password did not: what is left of one
        # full verification is spent, so this refusal costs what any other does.
        _spend_the_rest(password, spent)
        return None
    _verify_password(password, _DUMMY_PASSWORD_HASH)
    return None


def set_local_password(username: str, password: str) -> bool:
    """Set a local user's password, nothing else about them. False when there
    is no such user.

    Settings -> Account -> Change password wrote ``account.password_hash``,
    which sign-in never reads (sign-in checks this store): the old password
    kept working (2026-11 functional audit). The caller checks the current
    password first (:func:`authenticate_local_user`)."""
    if len(password or "") < 8:
        raise ValueError("Password must be at least 8 characters")
    new_hash = _hash_password(password)
    changed: list[bool] = []

    def _set(users: list[dict[str, Any]]) -> None:
        for u in users:
            if str(u.get("username", "")).lower() == (username or "").lower():
                u["password_hash"] = new_hash
                changed.append(True)
                return

    _update_users(_set)
    if changed:
        logger.warning("[platform_rbac] password changed username=%s", username)
        return True
    return False


def multi_user_enabled(*, store: bool = True) -> bool:
    """True when ``KAZMA_MULTI_USER`` forces it, or platform users exist.

    ``store=False`` answers from the environment only and does not open the
    settings store, so a reader of a fresh install does not create
    ``settings.db``. An unreadable store still raises when ``store`` is
    true: "no users" is single-operator mode (audit 2026-10-26 AUD-123).
    """
    if (os.environ.get("KAZMA_MULTI_USER") or "").strip().lower() in (
        "1", "true", "on", "yes",
    ):
        return True
    if not store:
        return False
    return bool(_load_users_from_store())
