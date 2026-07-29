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

__all__ = ["get_app_installation_token", "get_bot_identity", "get_commit_env"]

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

    name = (
        os.environ.get("KAZMA_BOT_NAME", "")
        or cfg.get("name", _DEFAULT_BOT_NAME)
    )
    email = (
        os.environ.get("KAZMA_BOT_EMAIL", "")
        or cfg.get("email", _DEFAULT_BOT_EMAIL)
    )

    # GitHub App path: resolve the app's true bot email (which carries the custom logo/avatar).
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


# ── GitHub App token path ──────────────────────────────────────────────────


# Cache the minted token + its expiry so we don't re-mint on every commit/call.
_app_token_cache: dict[str, Any] = {"token": None, "expires": 0}


def _try_app_email(cfg: dict[str, Any]) -> str | None:
    """If a GitHub App is configured, return its true bot email.

    The real GitHub App bot email format on GitHub is:
    ``{app_id}+{slug}[bot]@users.noreply.github.com``.
    """
    if cfg.get("app_email"):
        return str(cfg["app_email"])
    app_id = cfg.get("app_id")
    app_slug = cfg.get("app_slug")
    if app_id and app_slug:
        return f"{app_id}+{app_slug}[bot]@users.noreply.github.com"
    return None


def _mint_github_jwt(app_id: int | str, private_key_bytes: bytes) -> str:
    """Create a signed RS256 JWT with integer `iss` claim required by GitHub App API.

    PyJWT forces `iss` to be a `str`, causing GitHub API to reject the token with
    `'Issuer' claim ('iss') must be an Integer`. We construct the JWT with
    cryptography + json to preserve integer `iss`.
    """
    import base64
    import json
    import time
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    now = int(time.time())
    digits = "".join(c for c in str(app_id) if c.isdigit())
    if not digits:
        logger.warning("[git_identity] Cannot mint JWT: App ID contains no digits (%r)", app_id)
        return ""
    clean_app_id = int(digits)

    header = {"alg": "RS256", "typ": "JWT"}
    payload = {
        "iat": now - 120,
        "exp": now + 600,
        "iss": clean_app_id,  # Integer claim required by GitHub REST API
    }

    def _b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")

    sig_input = f"{_b64url(json.dumps(header).encode('utf-8'))}.{_b64url(json.dumps(payload).encode('utf-8'))}".encode("utf-8")
    key = serialization.load_pem_private_key(private_key_bytes, password=None)
    sig = key.sign(sig_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{sig_input.decode('utf-8')}.{_b64url(sig)}"


def get_app_installation_token() -> str | None:
    """Mint a GitHub App installation token (for API auth and Git operations).

    Uses the private key (file path or direct PEM string) to sign a JWT, then
    exchanges it for an installation access token via GitHub API.
    Cached until 5 min before expiry.
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

    # Check cache
    if _app_token_cache["token"] and time.time() < _app_token_cache["expires"]:
        return _app_token_cache["token"]

    try:
        import httpx
        import jwt
        from pathlib import Path

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

        # Create the JWT with integer `iss` claim required by GitHub App API
        app_jwt = _mint_github_jwt(app_id, private_key_bytes)
        if not app_jwt:
            return None

        clean_inst_id = str(installation_id).strip()

        # Exchange for installation token.
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"https://api.github.com/app/installations/{clean_inst_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {app_jwt}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            if resp.status_code >= 400:
                logger.warning(
                    "[git_identity] GitHub App token exchange failed HTTP %s: %s",
                    resp.status_code,
                    resp.text[:500],
                )
            resp.raise_for_status()
            data = resp.json()
            token = data.get("token")

        if token:
            # Cache for 50 minutes (tokens are valid for 1 hour).
            _app_token_cache["token"] = token
            _app_token_cache["expires"] = now + 3000
            logger.info("[git_identity] Minted GitHub App installation token for App ID %s", app_id)
            return token
    except ImportError:
        logger.debug("[git_identity] PyJWT/httpx not installed — app token unavailable")
    except Exception as exc:
        logger.warning("[git_identity] App token minting failed: %s", exc)

    return None

