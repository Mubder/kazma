"""Login, logout, OIDC, and auth-status endpoints.

Extracted from the former ``kazma_ui/routes_direct.py`` god module
(3,862 lines) — audit O5. Handler bodies are unchanged; only their
module changed. Registration order within this group is preserved.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse as _JSONResponse
from kazma_core.errors import safe_error
from kazma_ui.json_body import InvalidJsonBody, invalid_json_response, read_json

logger = logging.getLogger(__name__)

__all__ = ["LOGIN_TABLE_MAX", "register_auth_routes"]

#: The most addresses (or username@address pairs) a login failure table
#: keeps. Past it the entries whose last failure is oldest go first: the
#: tables were pruned of stale keys only, so fresh keys -- an IPv6 /64 holds
#: 2**64 addresses -- grew them without bound inside the window.
LOGIN_TABLE_MAX = 4096


def _login_throttle_key(address: str) -> str:
    """The key a client's login failures are counted under: its IPv4
    address, or the /64 network of an IPv6 one (2026-11-15).

    One subscriber is handed a whole /64, so keyed by the full address every
    attempt from one network could come from an address of its own and none
    reached the limit. An IPv4-mapped IPv6 address counts as its IPv4
    address; anything that is not an address is its own key.
    """
    import ipaddress

    text = str(address or "").strip()
    try:
        ip = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return text or "unknown"
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


#: Spaces S4-02: failures against one account from every address together
#: (only where Spaces is enforced). Above it, sign-ins to that account are
#: refused for the window, but from an address it signed in from before
#: (:data:`KNOWN_ADDRESSES_KEY`), so a guess spread over many addresses
#: cannot lock its person out.
ACCOUNT_MAX_FAILS = 30
ACCOUNT_WINDOW_S = 900.0
#: The addresses each account signed in from (hashed, the newest ten),
#: runtime state.
KNOWN_ADDRESSES_KEY = "system.sign_in.known_addresses"
_KNOWN_PER_ACCOUNT = 10
_KNOWN_ACCOUNTS_MAX = 2000
_KNOWN_FOR_S = 30 * 24 * 3600


def _address_mark(address: str) -> str:
    import hashlib

    return hashlib.sha256(f"kazma-sign-in:{address}".encode()).hexdigest()[:20]


def _known_address(username: str, address: str) -> bool:
    """Whether *username* signed in from *address* in the last 30 days.
    Blocking (a settings read); a store that cannot answer says no."""
    import time as _time

    from kazma_core.config_store import get_config_store

    try:
        held = get_config_store().get(KNOWN_ADDRESSES_KEY) or {}
    except (sqlite3.Error, OSError, RuntimeError):  # unknown is "not known": the limit holds
        logger.warning("[auth] the addresses accounts signed in from cannot be read", exc_info=True)
        return False
    seen = (held.get(username) or {}) if isinstance(held, dict) else {}
    try:
        at = float(seen.get(_address_mark(address)) or 0) if isinstance(seen, dict) else 0.0
    except (TypeError, ValueError):
        return False
    return _time.time() - at < _KNOWN_FOR_S


def _remember_address(username: str, address: str) -> None:
    """Keep *address* as one *username* signs in from. Blocking; a store that
    cannot answer is said and the sign-in goes on."""
    import time as _time

    from kazma_core.config_store import get_config_store

    mark, now = _address_mark(address), _time.time()

    def _update(current: Any) -> dict[str, Any]:
        held = dict(current) if isinstance(current, dict) else {}
        seen = dict(held.get(username) or {}) if isinstance(held.get(username), dict) else {}
        seen[mark] = now
        held[username] = dict(sorted(seen.items(), key=lambda kv: float(kv[1] or 0))[-_KNOWN_PER_ACCOUNT:])
        if len(held) > _KNOWN_ACCOUNTS_MAX:
            newest = sorted(held.items(), key=lambda kv: max((float(v or 0) for v in kv[1].values()),
                                                             default=0.0) if isinstance(kv[1], dict) else 0.0)
            held = dict(newest[-_KNOWN_ACCOUNTS_MAX:])
        return held

    try:
        get_config_store().atomic_update(KNOWN_ADDRESSES_KEY, _update, category="system")
    except (sqlite3.Error, OSError, RuntimeError):  # never refuses a sign-in that succeeded
        logger.warning("[auth] the address a sign-in came from was not kept", exc_info=True)


def _note_paused(username: str) -> None:
    """Tell the person behind *username*, at their next sign-in, that
    sign-ins to their account were paused (the People record). Blocking."""
    from kazma_core.security.platform_rbac import _users_for_sign_in
    from kazma_core.security.second_factor import account_for_sign_in
    from kazma_core.spaces import identity
    from kazma_core.spaces.people import record_event

    try:
        user_id = next((str(u.get("user_id") or "") for u in _users_for_sign_in()
                        if str(u.get("username", "")).lower() == username), "")
        account = account_for_sign_in(user_id) if user_id else ""
        if not account:
            return

        def _change(reg: dict[str, Any]) -> None:
            if account in reg["accounts"]:
                record_event(reg, "sign_in_paused", account, "kazma")

        identity.update_registry(_change)
    except (sqlite3.Error, OSError, RuntimeError, ValueError, LookupError):
        # The pause holds whether or not it is told.
        logger.warning("[auth] a paused sign-in was not recorded for its person", exc_info=True)


def _second_factor_verdict(user_id: str, code: str) -> str | None:
    """None when the sign-in of *user_id* may go on; ``second_factor_required``
    when its account has a second factor and no code came;
    ``wrong_second_factor`` for a wrong one. Blocking (the identity store,
    the vault); raises when it cannot say."""
    from kazma_core.security import second_factor

    account = second_factor.account_for_sign_in(user_id)
    if not account or not second_factor.state(account)["on"]:
        return None
    if not str(code or "").strip():
        return "second_factor_required"
    return None if second_factor.check(account, code) else "wrong_second_factor"


def register_auth_routes(self: Any) -> None:
    """Register the auth routes onto ``self.app``."""
    @self.app.get("/api/auth/status")
    def _auth_status(request: Request) -> dict[str, Any]:
        """Whether auth is enabled and whether this request is authenticated."""
        from kazma_ui.auth import (
            _is_loopback_client,
            get_kazma_secret,
            get_request_principal,
            is_authenticated,
            proxy_health,
        )

        expected = get_kazma_secret()
        oidc = False
        multi_user = False
        try:
            from kazma_core.security.oidc import oidc_configured
            from kazma_core.security.platform_rbac import multi_user_enabled

            oidc = oidc_configured()
            multi_user = multi_user_enabled()
        except Exception:
            pass
        if not expected:
            return {
                "auth_enabled": False,
                "authenticated": True,
                "mode": "open",
                "oidc": oidc,
                "multi_user": multi_user,
            }
        # Public demo mode: report as open/authenticated so the client skips
        # the login redirect. Matches the middleware bypass in auth.py.
        from kazma_core.security.boot_guard import env_flag

        if env_flag("KAZMA_DEMO_MODE"):
            return {
                "auth_enabled": False,
                "authenticated": True,
                "mode": "demo",
                "oidc": oidc,
                "multi_user": multi_user,
            }
        ok = is_authenticated(request, expected)
        if not ok:
            # Before sign-in only what the sign-in page needs (FA-219): the
            # proxy check named the trusted proxies and the peer seen, and the
            # loopback flag and the user mode said more about the install.
            return {
                "auth_enabled": True,
                "authenticated": False,
                "mode": "secret",
                "oidc": oidc,
            }
        principal = get_request_principal(request)
        return {
            "auth_enabled": True,
            "authenticated": ok,
            "loopback": _is_loopback_client(request),
            "mode": "secret",
            "oidc": oidc,
            "multi_user": multi_user,
            "principal": principal,
            # Makes a mis-set KAZMA_TRUSTED_PROXIES visible without reading
            # logs — the F-01 class otherwise fails silently.
            "proxy": proxy_health(),
        }
    # Login brute-force throttle (audit M3) — in-process sliding windows, one
    # per client address and one per username. The address is the
    # proxy-aware client (audit F-12: the raw TCP peer collapsed every client
    # behind a reverse proxy into one bucket). There is no global lockout
    # (audit 2026-09-30, AUD-020): 200 failures from ANY addresses refused
    # every login for five minutes, the owner's included, and behind a proxy
    # twenty addresses (one IPv6 /64) could renew that forever. Failures
    # against one username never lock another, and a distributed guess at the
    # shared secret gains nothing: a generated secret is 128 bits, and a short
    # one set by hand is flagged at boot (kazma_ui.auth.warn_if_weak_secret).
    _login_failures: dict[str, list[float]] = {}
    _login_failures_user: dict[str, list[float]] = {}
    # Every address together, per account (Spaces S4-02).
    _login_failures_account: dict[str, list[float]] = {}
    _paused_told: dict[str, float] = {}
    _LOGIN_WINDOW_S = 300.0
    _LOGIN_MAX_FAILS = 10

    def _recent_failures(table: dict[str, list[float]], key: str, now: float,
                         window: float = _LOGIN_WINDOW_S) -> list[float]:
        """The failures of *key* inside the window. The table stays bounded:
        stale keys go first, then -- at :data:`LOGIN_TABLE_MAX` -- the keys
        whose last failure is oldest (never *key* itself)."""
        if len(table) > 1000:
            for k in list(table):
                if not any(now - t < window for t in table[k]):
                    del table[k]
        if key not in table and len(table) >= LOGIN_TABLE_MAX:
            by_age = sorted(table, key=lambda k: max(table[k], default=0.0))
            for k in by_age[: len(table) - LOGIN_TABLE_MAX + 1]:
                del table[k]
        kept = [t for t in table.get(key, []) if now - t < window]
        table[key] = kept
        return kept

    def _release(table: dict[str, list[float]], key: str, stamp: float) -> None:
        """Take back one reserved attempt (it did not fail)."""
        kept = table.get(key)
        if kept and stamp in kept:
            kept.remove(stamp)

    def _too_many() -> Response:
        return _JSONResponse(
            {"detail": "Too many failed login attempts — try again later"},
            status_code=429,
        )
    @self.app.post("/api/auth/login")
    async def _auth_login(request: Request) -> Response:
        """Exchange KAZMA_SECRET for an HttpOnly session cookie."""
        import time as _time

        from kazma_ui.auth import (
            SECRET_COOKIE,
            _is_https,
            aget_kazma_secret,
            client_address,
            verify_secret,
        )

        # Counted per address, an IPv6 address by its /64 (_login_throttle_key).
        client_ip = _login_throttle_key(client_address(request) or "unknown")
        now = _time.time()
        recent = _recent_failures(_login_failures, client_ip, now)
        if len(recent) >= _LOGIN_MAX_FAILS:
            logger.warning("[auth] login rate limit hit for %s", client_ip)
            return _too_many()
        # AUD-101: reserve this attempt's slot BEFORE the first await, and
        # count it as a failure unless it proves otherwise. The check ran
        # before the body read and the password hash (both awaited) and the
        # failure was appended after them, so a burst of concurrent attempts
        # all passed the check -- far more than ten guesses per window. The
        # list is appended in place (``_recent_failures`` may replace it on
        # another request's call; appending to a stale list lost the entry).
        recent.append(now)

        expected = await aget_kazma_secret()
        if not expected:
            _release(_login_failures, client_ip, now)
            return _JSONResponse(
                {"status": "ok", "message": "Auth disabled (no KAZMA_SECRET)"},
                status_code=200,
            )
        try:
            body = await read_json(request, allow_empty=True)
        except InvalidJsonBody:
            return invalid_json_response()
        if not isinstance(body, dict):
            body = {}
        secret = str(body.get("secret") or "").strip()
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "").strip()
        session_user = None
        session_role = "admin"
        session_uid = None
        session_tenant = None
        authenticated = False
        # The username's window is per address too: keyed by the name alone,
        # ten wrong passwords from anywhere locked the real user out for five
        # minutes, from their own address too (security audit 2026-11-10).
        user_key = f"{username.lower()[:200]}@{client_ip}" if username else ""
        user_recent = _recent_failures(_login_failures_user, user_key, now) if user_key else []
        if user_key and len(user_recent) >= _LOGIN_MAX_FAILS:
            logger.warning("[auth] login rate limit hit for a username (from %s)", client_ip)
            _release(_login_failures, client_ip, now)
            return _too_many()
        if user_key:
            # AUD-101: the username's slot too, before the hash is awaited.
            user_recent.append(now)
        # Spaces S4-02: one account, every address together. Only where
        # Spaces is enforced; an address the account signed in from before
        # passes, so a spread guess cannot lock its person out.
        from kazma_core.spaces.scope import spaces_enforced

        account_key = username.lower()[:200] if username and spaces_enforced() else ""
        if account_key:
            account_recent = _recent_failures(_login_failures_account, account_key, now,
                                              ACCOUNT_WINDOW_S)
            if len(account_recent) >= ACCOUNT_MAX_FAILS and not await asyncio.to_thread(
                    _known_address, account_key, client_ip):
                logger.warning("[auth] sign-ins to one account paused after %d failures (from %s)",
                               len(account_recent), client_ip)
                _release(_login_failures, client_ip, now)
                if user_key:
                    _release(_login_failures_user, user_key, now)
                if now - _paused_told.get(account_key, 0.0) > ACCOUNT_WINDOW_S:
                    _paused_told[account_key] = now
                    await asyncio.to_thread(_note_paused, account_key)
                return _too_many()
            account_recent.append(now)

        # Path A: multi-user local username + password (Phase 4.4). Off the
        # event loop: a user-store read and PBKDF2 at 600,000 iterations
        # (0.2 s) froze every chat stream per attempt (audit AUD-002).
        if username and password:
            try:
                from kazma_core.security.platform_rbac import authenticate_local_user

                pu = await asyncio.to_thread(authenticate_local_user, username, password)
                if pu is not None:
                    authenticated = True
                    session_user = pu.username
                    session_role = pu.role
                    session_uid = pu.user_id
                    session_tenant = pu.tenant_id
            except Exception:
                logger.debug("[auth] local user auth failed", exc_info=True)

        # Path B: shared operator secret
        if not authenticated:
            check = secret or password
            if check and verify_secret(check, expected):
                authenticated = True
                session_user = "operator"
                session_role = "admin"
                session_uid = "shared-secret"

        if not authenticated:
            # The slots reserved above stay: this attempt failed.
            # One message for every failure mode (bad secret, unknown user,
            # wrong password) so the response cannot confirm which usernames
            # exist (audit F-12).
            return _JSONResponse(
                {"detail": "Invalid credentials"},
                status_code=401,
            )

        # A second factor (Spaces S4-02): a right password -- or, for the
        # owner, the install's secret -- does not sign in to an account that
        # has one. The slots reserved above stay until a code is right.
        try:
            verdict = await asyncio.to_thread(_second_factor_verdict, str(session_uid or ""),
                                              str(body.get("code") or ""))
        except (RuntimeError, sqlite3.Error, OSError):
            # Cannot say whether one is on (SecondFactorUnavailable and
            # IdentityUnavailable are RuntimeErrors): refused.
            logger.warning("[auth] whether an account has a second factor cannot be read", exc_info=True)
            return _JSONResponse(
                {"detail": "Sign-in cannot be checked right now. Try again.",
                 "error": "second_factor_unavailable"},
                status_code=503, headers={"Retry-After": "5"})
        if verdict is not None:
            return _JSONResponse(
                {"detail": "Enter the code from your authenticator app."
                 if verdict == "second_factor_required" else "That code is not right.",
                 "error": verdict, "second_factor_required": True},
                status_code=401)

        # Success — clear failures for this IP and this username
        _login_failures.pop(client_ip, None)
        if user_key:
            _login_failures_user.pop(user_key, None)
        if account_key:
            # This attempt did not fail; an address it signs in from passes
            # the account's pause (S4-02).
            _release(_login_failures_account, account_key, now)
            await asyncio.to_thread(_remember_address, account_key, client_ip)

        resp = _JSONResponse({
            "status": "ok",
            "authenticated": True,
            "username": session_user,
            "role": session_role,
        })
        # Always mint an opaque session — never put KAZMA_SECRET in a cookie.
        try:
            import asyncio as _asyncio

            from kazma_core.security.web_sessions import SESSION_COOKIE, create_session

            sid = await _asyncio.to_thread(
                create_session,
                actor="login",
                username=session_user,
                role=session_role,
                user_id=session_uid,
                tenant_id=session_tenant,
            )
            # When the person last signed in, for People (Spaces S2-10).
            from kazma_core.spaces.people import note_sign_in

            await _asyncio.to_thread(note_sign_in, session_uid or "", "login")
            resp.set_cookie(
                key=SESSION_COOKIE,
                value=sid,
                httponly=True,
                samesite="lax",
                path="/",
                secure=_is_https(request),
                max_age=60 * 60 * 24 * 14,
            )
            resp.delete_cookie(SECRET_COOKIE, path="/")
            return resp
        except Exception:
            logger.warning("[auth] opaque session create failed — refusing raw secret cookie", exc_info=True)
            return _JSONResponse(
                {"error": "session_create_failed", "detail": "Could not mint a login session."},
                status_code=503,
            )
    from fastapi import Depends

    from kazma_ui.rate_limit import rate_limit

    # Reached before sign-in, by anyone: each call fetches the IdP's discovery
    # document and writes a pending sign-in, of which 32 are kept -- a loop
    # evicted real ones (security audit 2026-11-10). Per client address.
    @self.app.get(
        "/api/auth/oidc/start",
        dependencies=[Depends(rate_limit("oidc_start", 10, per_address=True))],
    )
    async def _oidc_start(request: Request) -> Response:
        """Redirect browser to configured OIDC IdP (Phase 4.4)."""
        from fastapi.responses import RedirectResponse

        from kazma_ui.auth import _is_https

        try:
            from kazma_core.security.oidc import (
                BINDING_COOKIE_PATH,
                build_authorize_url,
                oidc_configured,
            )

            if not oidc_configured():
                return _JSONResponse(
                    {"error": "OIDC not configured (KAZMA_OIDC_ISSUER + CLIENT_ID)"},
                    status_code=503,
                )
            info = await build_authorize_url()
            resp = RedirectResponse(url=info["url"], status_code=302)
            # Binds the sign-in to this browser (FA-133): the callback takes
            # the state only from the browser holding it. Lax: the identity
            # provider's redirect back is a top-level GET, which carries it.
            resp.set_cookie(
                key=info["binding_cookie"],
                value=info["state"],
                httponly=True,
                samesite="lax",
                path=BINDING_COOKIE_PATH,
                secure=_is_https(request),
                max_age=600,
            )
            return resp
        except Exception as exc:
            logger.exception("[oidc] start failed")
            return _JSONResponse({"error": safe_error(exc)}, status_code=500)
    @self.app.get("/api/auth/oidc/callback")
    async def _oidc_callback(request: Request) -> Response:
        """OIDC callback — mint opaque session from IdP claims."""
        from fastapi.responses import RedirectResponse

        from kazma_ui.auth import SESSION_COOKIE, _is_https

        code = request.query_params.get("code") or ""
        state = request.query_params.get("state") or ""
        if not code or not state:
            return _JSONResponse({"error": "Missing code/state"}, status_code=400)
        from kazma_core.security.oidc import BINDING_COOKIE_PATH, binding_cookie_name

        binding = binding_cookie_name(state)
        try:
            from kazma_core.security.oidc import exchange_code
            from kazma_core.security.web_sessions import create_session, use_opaque_sessions

            result = await exchange_code(
                code, state, bound_state=request.cookies.get(binding, "")
            )
            import asyncio as _asyncio

            if not await _asyncio.to_thread(use_opaque_sessions):
                return _JSONResponse(
                    {"error": "Opaque sessions required for OIDC"},
                    status_code=500,
                )
            sid = await _asyncio.to_thread(
                create_session,
                actor="oidc",
                username=result.get("username"),
                role=result.get("role") or "operator",
                user_id=result.get("user_id"),
                tenant_id=result.get("tenant_id"),
            )
            resp = RedirectResponse(url="/", status_code=302)
            resp.set_cookie(
                key=SESSION_COOKIE,
                value=sid,
                httponly=True,
                samesite="lax",
                path="/",
                secure=_is_https(request),
                max_age=60 * 60 * 24 * 14,
            )
            resp.delete_cookie(binding, path=BINDING_COOKIE_PATH)
            return resp
        except Exception as exc:
            logger.exception("[oidc] callback failed")
            failed = _JSONResponse({"error": safe_error(exc)}, status_code=400)
            failed.delete_cookie(binding, path=BINDING_COOKIE_PATH)
            return failed
    @self.app.get("/api/auth/me")
    def _auth_me(request: Request) -> Response:
        """Return current principal (role/username) for UI chrome."""
        from kazma_core.security.boot_guard import env_flag

        from kazma_ui.auth import (
            DEMO_VISITOR_ROLE,
            get_kazma_secret,
            get_request_principal,
            is_authenticated,
        )

        # Public demo mode: report as an authenticated demo visitor, with the
        # role the auth gate gives one (not admin: security audit 2026-11-10).
        if env_flag("KAZMA_DEMO_MODE"):
            return _JSONResponse(
                {"authenticated": True, "source": "demo", "role": DEMO_VISITOR_ROLE}
            )
        secret = get_kazma_secret()
        if secret and not is_authenticated(request, secret):
            return _JSONResponse({"authenticated": False}, status_code=401)
        principal = get_request_principal(request) or {}
        return _JSONResponse({"authenticated": True, **principal})
    @self.app.post("/api/auth/logout")
    async def _auth_logout(request: Request) -> Response:
        """Clear auth cookies and revoke opaque session."""
        from kazma_ui.auth import SECRET_COOKIE, SESSION_COOKIE

        try:
            from kazma_core.security.web_sessions import revoke_session

            sid = request.cookies.get(SESSION_COOKIE) or ""
            if sid:
                # A settings-store delete: off the event loop (audit AUD-003).
                await asyncio.to_thread(revoke_session, sid)
        except Exception:
            pass
        resp = _JSONResponse({"status": "ok", "authenticated": False})
        resp.delete_cookie(SECRET_COOKIE, path="/")
        resp.delete_cookie(SESSION_COOKIE, path="/")
        return resp
