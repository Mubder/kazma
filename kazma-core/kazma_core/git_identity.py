"""Git bot-identity — configurable commit author for Kazma's agent.

When enabled, Kazma's agent commits as a bot identity (e.g.
``Kazma Agent <kazma-agent[bot]@users.noreply.github.com>``) instead of
the local git user. This makes the agent appear in the GitHub Contributors
list with a ``[bot]`` label — like Dependabot or Copilot.

Two tiers:
  * **Email pattern** (default when enabled): uses the ``[bot]@users.noreply``
    email trick. Shows the bot name + label, but the avatar is GitHub's
    auto-generated identicon (no custom logo).
  * **GitHub App** (when ``app_id`` + private key provided): mints a real
    installation token and derives the app's true bot email, which gives
    a custom logo/avatar on commits. The user creates the app on GitHub's
    site, uploads their logo, and adds credentials here.

Resolution precedence:
  1. GitHub App credentials (if ``app_id`` + key file present)
  2. Config ``git.bot_identity.name`` / ``.email`` in ``kazma.yaml``
  3. Env vars ``KAZMA_BOT_NAME`` / ``KAZMA_BOT_EMAIL``
  4. None — disabled, commits use local git config unchanged

The identity is injected via ``GIT_AUTHOR_*`` / ``GIT_COMMITTER_*``
environment variables on the ``subprocess.run`` call — it never mutates
the repo's ``.git/config``, so the user's real git identity is preserved.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

__all__ = [
    "get_app_installation_token",
    "mint_app_installation_token",
    "invalidate_app_token_cache",
    "get_bot_identity",
    "get_commit_env",
]

logger = logging.getLogger(__name__)

_DEFAULT_BOT_NAME = "Kazma Agent"
_DEFAULT_BOT_EMAIL = "kazma-agent[bot]@users.noreply.github.com"


def _read_config() -> dict[str, Any]:
    """Read the ``git.bot_identity`` and GitHub App credentials from ConfigStore, kazma.yaml, and env vars."""
    merged: dict[str, Any] = {}

    # 1. Start with kazma.yaml defaults
    try:
        import yaml

        cfg_path = Path("kazma.yaml")
        if cfg_path.exists():
            with open(cfg_path) as f:
                full = yaml.safe_load(f) or {}
            merged.update(full.get("git", {}).get("bot_identity", {}) or {})
    except Exception:
        pass

    # 2. Merge from ConfigStore singleton
    try:
        from kazma_core.config_store import get_config_store

        store = get_config_store()
        app_id = store.get("connectors.github.app_id")
        if app_id:
            merged["app_id"] = app_id
        app_inst = store.get("connectors.github.app_installation_id")
        if app_inst:
            merged["app_installation_id"] = app_inst
        app_slug = store.get("connectors.github.app_slug")
        if app_slug:
            merged["app_slug"] = app_slug
        app_key = store.get("connectors.github.app_private_key")
        if app_key:
            merged["app_private_key"] = app_key
        app_key_path = store.get("connectors.github.app_private_key_path")
        if app_key_path:
            merged["app_private_key_path"] = app_key_path

        bot_enabled = store.get("git.bot_identity.enabled")
        if bot_enabled is not None:
            merged["enabled"] = bool(bot_enabled)
        bot_name = store.get("git.bot_identity.name")
        if bot_name:
            merged["name"] = bot_name
        bot_email = store.get("git.bot_identity.email")
        if bot_email:
            merged["email"] = bot_email
    except Exception:
        pass

    # 3. Environment variable overrides
    if os.environ.get("KAZMA_GITHUB_APP_ID"):
        merged["app_id"] = os.environ["KAZMA_GITHUB_APP_ID"]
    if os.environ.get("KAZMA_GITHUB_APP_INSTALLATION_ID"):
        merged["app_installation_id"] = os.environ["KAZMA_GITHUB_APP_INSTALLATION_ID"]
    if os.environ.get("KAZMA_GITHUB_APP_SLUG"):
        merged["app_slug"] = os.environ["KAZMA_GITHUB_APP_SLUG"]
    if os.environ.get("KAZMA_GITHUB_APP_PRIVATE_KEY"):
        merged["app_private_key"] = os.environ["KAZMA_GITHUB_APP_PRIVATE_KEY"]
    if os.environ.get("KAZMA_GITHUB_APP_PRIVATE_KEY_PATH"):
        merged["app_private_key_path"] = os.environ["KAZMA_GITHUB_APP_PRIVATE_KEY_PATH"]

    if merged.get("app_id") and merged.get("app_installation_id"):
        merged["enabled"] = True

    return merged


def get_bot_identity() -> dict[str, str] | None:
    """Return the bot commit identity, or None when disabled.

    Returns ``{"name": ..., "email": ...}`` when bot identity is enabled,
    ``None`` when disabled (commits use the local git config).
    """
    cfg = _read_config()

    # Check if enabled (config or env).
    enabled = cfg.get("enabled", False)
    if os.environ.get("KAZMA_BOT_NAME") or os.environ.get("KAZMA_BOT_EMAIL"):
        enabled = True  # env vars implicitly enable

    if not enabled:
        return None

    name = os.environ.get("KAZMA_BOT_NAME", "") or cfg.get("name", _DEFAULT_BOT_NAME)
    email = os.environ.get("KAZMA_BOT_EMAIL", "") or cfg.get("email", _DEFAULT_BOT_EMAIL)

    # GitHub App path: resolve the app's true bot email (which carries the custom logo/avatar).
    # Only use auto-derived email when NO explicit email is configured —
    # otherwise a mismatched app_slug would silently overwrite a correct
    # explicit override with a wrong-cased auto-derived email, causing 404s.
    app_email = _try_app_email(cfg)
    if app_email:
        explicit = os.environ.get("KAZMA_BOT_EMAIL", "") or cfg.get("email", "")
        if not explicit:
            email = app_email

    return {"name": name, "email": email}


def get_commit_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Return env vars to inject for bot-authored commits.

    Merges ``GIT_AUTHOR_*`` / ``GIT_COMMITTER_*`` into a copy of
    ``base_env`` (or the server's environment without its secrets, if not
    given -- a commit runs the repository's hooks). When bot identity is
    disabled, returns the base env unchanged.
    """
    if base_env is None:
        from kazma_core.security.child_env import tool_child_env

        base_env = tool_child_env()
    env = dict(base_env)
    identity = get_bot_identity()
    if identity is None:
        return env

    env["GIT_AUTHOR_NAME"] = identity["name"]
    env["GIT_AUTHOR_EMAIL"] = identity["email"]
    env["GIT_COMMITTER_NAME"] = identity["name"]
    env["GIT_COMMITTER_EMAIL"] = identity["email"]
    return env


# ── GitHub App token path ──────────────────────────────────────────────────


# Cache the minted token + its expiry so we don't re-mint on every commit/call.
_app_token_cache: dict[str, Any] = {"token": None, "expires": 0}


def invalidate_app_token_cache() -> None:
    """Clear the cached GitHub App installation token.

    Call this when a token is known to be dead (e.g. a push rejected with a
    401 / "invalid_grant") so the next call re-mints a fresh one instead of
    reusing the revoked ``ghs_*`` token. Safe to call unconditionally — it is
    a no-op when the cache is already empty.
    """
    if _app_token_cache["token"]:
        logger.info("[git_identity] Invalidating stale cached App installation token")
    _app_token_cache["token"] = None
    _app_token_cache["expires"] = 0


def _try_app_email(cfg: dict[str, Any]) -> str | None:
    """If a GitHub App is configured, return its true bot email.

    The real GitHub App bot email format on GitHub is:
    ``{bot_user_id}+{slug}[bot]@users.noreply.github.com``.

    Resolution order:
      1. Explicit ``app_email`` override in config (manual escape hatch).
      2. Auto-derived from ``app_slug`` by looking up the App bot's numeric
         user id via the GitHub ``/users/{slug}[bot]`` API (cached 24h).
         This produces the avatar-linked email without the user having to
         hand-craft it. Note: the bot *user id* is NOT the App id — using
         the App id does not link the avatar.
    """
    if cfg.get("app_email"):
        return str(cfg["app_email"])
    app_slug = cfg.get("app_slug")
    if app_slug:
        bot_user_id = _fetch_bot_user_id(str(app_slug))
        if bot_user_id:
            return f"{bot_user_id}+{app_slug}[bot]@users.noreply.github.com"
    return None


# Cache the App bot's numeric user id (stable for an App's lifetime).
# {"slug": {"id": int, "expires": float}}
_bot_user_id_cache: dict[str, dict[str, Any]] = {}


def _fetch_bot_user_id(app_slug: str) -> int | None:
    """Look up a GitHub App bot's numeric user id from its slug.

    Queries ``GET https://api.github.com/users/{slug}[bot]`` and returns the
    numeric ``id`` field. Cached per-slug for 24 hours (the bot user id is
    stable for the App's lifetime, so re-querying adds latency for nothing).

    Returns ``None`` if the lookup fails (network error, unknown slug, or
    ``httpx`` not installed). The caller treats ``None`` as "fall back to a
    non-avatar email".
    """
    app_slug = (app_slug or "").strip()
    if not app_slug:
        return None

    cached = _bot_user_id_cache.get(app_slug)
    if cached and time.time() < cached.get("expires", 0):
        return cached.get("id")

    try:
        import httpx

        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                f"https://api.github.com/users/{app_slug}[bot]",
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "kazma-agent",
                },
            )
            if resp.status_code != 200:
                logger.debug(
                    "[git_identity] Bot user id lookup for %r failed HTTP %s",
                    app_slug,
                    resp.status_code,
                )
                return None
            data = resp.json()
            bot_id = data.get("id")
            if isinstance(bot_id, int):
                _bot_user_id_cache[app_slug] = {
                    "id": bot_id,
                    "expires": time.time() + 86400,  # 24h
                }
                logger.info(
                    "[git_identity] Resolved GitHub App bot user id for %r -> %s",
                    app_slug,
                    bot_id,
                )
                return bot_id
    except ImportError:
        logger.debug("[git_identity] httpx not installed — bot user id lookup unavailable")
    except Exception as exc:
        logger.debug("[git_identity] Bot user id lookup failed: %s", exc)

    return None


# GitHub judges an App JWT by ITS clock: ``iat`` must be in the past and
# ``exp`` no more than 600 s in the future. ``exp = now + 600`` -- the value
# the docs show -- is therefore refused whenever this machine's clock runs
# even a second fast, which is what happened on the live install from
# 2026-09-24 22:32 to 2026-09-25 01:55 (386 refusals: "'Expiration time'
# claim ('exp') is too far in the future"). A 540 s lifetime and a 60 s
# backdate tolerate a clock up to 60 s fast or 540 s slow; beyond that the
# refusal's ``Date`` header gives GitHub's time and the mint retries once
# on it (the same correction Octokit's auth-app makes).
_JWT_BACKDATE_S = 60
_JWT_LIFETIME_S = 540
# GitHub's clock minus this machine's, learned from a clock refusal.
_clock_offset: dict[str, float] = {"seconds": 0.0}
# A failed mint is not retried on every call: the next unforced attempt
# waits 30 s, doubling to 15 min while GitHub keeps refusing.
_MINT_BACKOFF_FIRST_S = 30.0
_MINT_BACKOFF_MAX_S = 900.0
_mint_backoff: dict[str, float] = {"until": 0.0, "delay": 0.0}


def _now() -> float:
    """This machine's clock (tests substitute it to simulate a skewed clock)."""
    return time.time()


def _mint_github_jwt(app_id: int | str, private_key_bytes: bytes) -> str:
    """Create a signed RS256 JWT for GitHub App authentication.

    GitHub's JWT spec for Apps
    (https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app)
    requires:
      * ``iss`` — the App's ID or client ID, as a **string**
      * ``iat`` — issued-at, in the past by GitHub's clock
      * ``exp`` — expiry, no more than 10 minutes ahead of GitHub's clock
      * algorithm RS256, signed with the App's private key

    The claims are stamped on GitHub's time as far as it is known
    (``_clock_offset``), with the margins above for the drift that is not.
    Uses standard :func:`jwt.encode` (PyJWT ≥ 2.0 returns ``str`` for RS256).
    """
    import jwt

    now = int(_now() + _clock_offset["seconds"])
    payload = {
        "iat": now - _JWT_BACKDATE_S,
        "exp": now + _JWT_LIFETIME_S,
        "iss": str(app_id),  # App ID / client ID as a string (per GitHub docs)
    }
    return jwt.encode(payload, private_key_bytes, algorithm="RS256")


def _is_clock_refusal(status_code: int, body: str) -> bool:
    """True for GitHub's 401s about the JWT's time claims (not a bad key)."""
    return status_code == 401 and ("claim ('exp')" in body or "claim ('iat')" in body)


def _learn_clock_offset(date_header: str) -> bool:
    """Adopt GitHub's clock from a response ``Date`` header.

    Returns False when the header is missing or unparseable (the caller then
    has nothing to retry with). Says so once per new offset: a system clock
    this far off breaks more than GitHub (TLS, one-time codes), so the
    operator should fix it even though the mint no longer depends on it.
    """
    from datetime import timezone
    from email.utils import parsedate_to_datetime

    try:
        stamp = parsedate_to_datetime(date_header)
        if stamp.tzinfo is None:  # "-0000": HTTP dates are GMT
            stamp = stamp.replace(tzinfo=timezone.utc)
        server = stamp.timestamp()
    except (TypeError, ValueError, IndexError, OverflowError):
        return False
    offset = server - _now()
    if abs(offset - _clock_offset["seconds"]) >= 2:
        logger.warning(
            "[git_identity] This machine's clock is %+.0f s off GitHub's; GitHub App "
            "tokens are now minted on GitHub's time. Resynchronise the system clock "
            "(Windows: w32tm /resync).",
            -offset,
        )
    _clock_offset["seconds"] = offset
    return True


def _note_mint_failure() -> None:
    delay = min(
        max(_mint_backoff["delay"] * 2, _MINT_BACKOFF_FIRST_S), _MINT_BACKOFF_MAX_S
    )
    _mint_backoff["delay"] = delay
    _mint_backoff["until"] = time.monotonic() + delay


def _post_app_jwt(client: Any, url: str, app_id: str, private_key_bytes: bytes) -> Any:
    return client.post(
        url,
        headers={
            "Authorization": f"Bearer {_mint_github_jwt(app_id, private_key_bytes)}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def _exchange_app_jwt(client: Any, url: str, app_id: str, private_key_bytes: bytes) -> Any:
    """POST a fresh App JWT; on a clock refusal, retry once on GitHub's time."""
    resp = _post_app_jwt(client, url, app_id, private_key_bytes)
    if _is_clock_refusal(resp.status_code, resp.text or "") and _learn_clock_offset(
        resp.headers.get("Date", "")
    ):
        resp = _post_app_jwt(client, url, app_id, private_key_bytes)
    return resp


def get_app_installation_token() -> str | None:
    """Mint a GitHub App installation token (for API auth and Git operations).

    Uses the private key (file path or direct PEM string) to sign a JWT, then
    exchanges it for an installation access token via GitHub API.
    Cached for 50 minutes (tokens live 1 hour).
    """
    return mint_app_installation_token(force=False)


def mint_app_installation_token(force: bool = False) -> str | None:
    """Mint a GitHub App installation token, optionally bypassing the cache.

    When ``force=True`` the cached token is ignored (and overwritten) so a
    freshly minted token is always returned — use this after
    :func:`invalidate_app_token_cache` or directly when a previous token is
    known to be revoked/expired. Otherwise behaves like
    :func:`get_app_installation_token` (cached for ~50 min).
    """
    cfg = _read_config()
    app_id = str(cfg.get("app_id") or "").strip()
    installation_id = str(cfg.get("app_installation_id") or "").strip()
    key_path = str(cfg.get("app_private_key_path") or "").strip()
    key_str = str(cfg.get("app_private_key") or "").strip()

    if not app_id or not installation_id:
        return None
    if not key_str and not key_path:
        return None

    # Check cache (unless caller forced a fresh mint).
    if not force and _app_token_cache["token"] and time.time() < _app_token_cache["expires"]:
        return _app_token_cache["token"]
    # A recent refusal: the callers fall back to their next credential
    # instead of re-signing and re-posting on every call. A forced mint is
    # the caller saying it needs a new token now, so it still goes out.
    if not force and time.monotonic() < _mint_backoff["until"]:
        return None

    try:
        from pathlib import Path

        import httpx

        # Read and normalize the private key bytes
        private_key_bytes: bytes
        if key_str:
            k = key_str if isinstance(key_str, str) else key_str.decode("utf-8", errors="replace")
            if "\\n" in k and "\n" not in k:
                k = k.replace("\\n", "\n")
            private_key_bytes = k.strip().encode("utf-8")
        elif key_path:
            kp = Path(key_path).expanduser()
            if kp.exists():
                private_key_bytes = kp.read_bytes()
            else:
                logger.debug("[git_identity] App private key not found at path %s (expanded: %s)", key_path, kp)
                return None

        clean_inst_id = str(installation_id).strip()

        # Exchange a fresh JWT for an installation token.
        with httpx.Client(timeout=15.0) as client:
            resp = _exchange_app_jwt(
                client,
                f"https://api.github.com/app/installations/{clean_inst_id}/access_tokens",
                app_id,
                private_key_bytes,
            )
            if resp.status_code >= 400:
                logger.warning(
                    "[git_identity] GitHub App token exchange failed HTTP %s: %s",
                    resp.status_code,
                    resp.text[:500],
                )
                _note_mint_failure()
                return None
            data = resp.json()
            token = data.get("token")

        if token:
            # Cache for 50 minutes (tokens are valid for 1 hour).
            _app_token_cache["token"] = token
            _app_token_cache["expires"] = time.time() + 3000
            _mint_backoff["until"] = _mint_backoff["delay"] = 0.0
            logger.info("[git_identity] Minted GitHub App installation token for App ID %s", app_id)
            return token
        logger.warning("[git_identity] GitHub App token exchange returned no token")
    except ImportError:
        logger.debug("[git_identity] PyJWT/httpx not installed — app token unavailable")
        return None
    except Exception as exc:
        logger.warning("[git_identity] App token minting failed: %s", exc)

    _note_mint_failure()
    return None
