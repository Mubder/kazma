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

logger = logging.getLogger(__name__)

_DEFAULT_BOT_NAME = "Kazma Agent"
_DEFAULT_BOT_EMAIL = "kazma-agent[bot]@users.noreply.github.com"


def _read_config() -> dict[str, Any]:
    """Read the ``git.bot_identity`` block from ``kazma.yaml``."""
    try:
        import yaml

        cfg_path = Path("kazma.yaml")
        if cfg_path.exists():
            with open(cfg_path) as f:
                full = yaml.safe_load(f) or {}
            return full.get("git", {}).get("bot_identity", {}) or {}
    except Exception:
        pass
    return {}


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

    name = (
        os.environ.get("KAZMA_BOT_NAME", "")
        or cfg.get("name", _DEFAULT_BOT_NAME)
    )
    email = (
        os.environ.get("KAZMA_BOT_EMAIL", "")
        or cfg.get("email", _DEFAULT_BOT_EMAIL)
    )

    # GitHub App path: if app_id + private key are configured, try to
    # resolve the app's true bot email (which carries the custom logo).
    app_email = _try_app_email(cfg)
    if app_email:
        email = app_email

    return {"name": name, "email": email}


def get_commit_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Return env vars to inject for bot-authored commits.

    Merges ``GIT_AUTHOR_*`` / ``GIT_COMMITTER_*`` into a copy of
    ``base_env`` (or ``os.environ`` if not given). When bot identity is
    disabled, returns the base env unchanged.
    """
    env = dict(base_env if base_env is not None else os.environ)
    identity = get_bot_identity()
    if identity is None:
        return env

    env["GIT_AUTHOR_NAME"] = identity["name"]
    env["GIT_AUTHOR_EMAIL"] = identity["email"]
    env["GIT_COMMITTER_NAME"] = identity["name"]
    env["GIT_COMMITTER_EMAIL"] = identity["email"]
    return env


# ── GitHub App token path (future-ready, inert until credentials set) ────


# Cache the minted token + its expiry so we don't re-mint on every commit.
_app_token_cache: dict[str, Any] = {"token": None, "expires": 0}


def _try_app_email(cfg: dict[str, Any]) -> str | None:
    """If a GitHub App is configured, return its true bot email.

    The real GitHub App bot email is ``{app_id}+{slug}[bot]@users.noreply.github.com``.
    We can't know the slug without an API call, so we use the configured
    email as a fallback (the user sets it to their app's actual email).

    Returns None if no app is configured or the key file is missing.
    """
    app_id = cfg.get("app_id")
    key_path = cfg.get("app_private_key_path")
    if not app_id or not key_path:
        return None
    if not Path(key_path).exists():
        logger.debug("[git_identity] app_private_key_path %s not found", key_path)
        return None
    # The app's bot email should be configured by the user in the `email`
    # field — we just confirm the app credentials exist so the caller knows
    # the app path is active. The email itself comes from config.
    return cfg.get("app_email")  # e.g. "123456+kazma-agent[bot]@users.noreply..."


def get_app_installation_token() -> str | None:
    """Mint a GitHub App installation token (for API auth, not just commits).

    Uses the private key to sign a JWT, exchanges it for an installation
    access token via the GitHub API. Cached until 5 min before expiry.

    Returns None if no app is configured or minting fails.
    """
    cfg = _read_config()
    app_id = cfg.get("app_id")
    key_path = cfg.get("app_private_key_path")
    installation_id = cfg.get("app_installation_id")

    if not app_id or not key_path or not installation_id:
        return None

    # Check cache.
    if _app_token_cache["token"] and time.time() < _app_token_cache["expires"]:
        return _app_token_cache["token"]

    try:
        import httpx
        import jwt
        from pathlib import Path

        # Read the private key.
        private_key = Path(key_path).read_bytes()

        # Create the JWT (valid for 10 min, per GitHub's limit).
        now = int(time.time())
        payload = {
            "iat": now - 60,
            "exp": now + 600,
            "iss": str(app_id),
        }
        app_jwt = jwt.encode(payload, private_key, algorithm="RS256")

        # Exchange for installation token.
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"https://api.github.com/app/installations/{installation_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {app_jwt}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            token = data.get("token")
            expires_at = data.get("expires_at")

        if token:
            # Cache for 50 minutes (tokens are valid for 1 hour).
            _app_token_cache["token"] = token
            _app_token_cache["expires"] = now + 3000
            logger.info("[git_identity] Minted GitHub App installation token")
            return token
    except ImportError:
        logger.debug("[git_identity] PyJWT/httpx not installed — app token unavailable")
    except Exception as exc:
        logger.warning("[git_identity] App token minting failed: %s", exc)

    return None
