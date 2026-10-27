"""The environment an MCP server process starts with: one rule, both clients.

Audit H-4 found the manager starting every MCP server with
``{**os.environ, **cfg env}`` -- the vault key, the database password, every
API key the server process holds -- and gave it this allowlist. The Settings
and /mcp "Test" client (:mod:`kazma_core.mcp_client`) kept the old line until
2026-09-30, so testing a server, often a package the operator is only trying
out, handed it all of them. Both clients call :func:`mcp_child_env`, and
``tests/test_child_env.py`` covers the MCP code.
"""

from __future__ import annotations

import logging
import os
from typing import Any

__all__ = ["MCP_CHILD_ENV_ALLOWLIST", "mcp_child_env"]

logger = logging.getLogger(__name__)

# What an MCP server inherits from Kazma's own environment: the basics a
# program needs to run, find its tools and reach the network, and nothing
# that authenticates anything. ``KAZMA_MCP_INHERIT_ENV=1`` restores full
# inheritance for a server that genuinely needs an exotic parent variable.
MCP_CHILD_ENV_ALLOWLIST: tuple[str, ...] = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "COMSPEC",
    "WINDIR",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "LANG",
    "LC_ALL",
    "TERM",
    "SHELL",
    "TZ",
    "XDG_DATA_HOME",
    "XDG_CONFIG_HOME",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
)


def _carries_credentials(key: str, value: str) -> bool:
    """Whether an inherited variable holds a credential in its value: a URL
    password anywhere, or userinfo in a proxy given without a scheme
    (``user:pass@proxy:3128``, which curl and httpx accept)."""
    from kazma_core.security.url_credentials import url_has_credentials

    if url_has_credentials(value):
        return True
    if key.upper().endswith("_PROXY") and "://" not in value:
        return url_has_credentials("http://" + value.strip())
    return False


def mcp_child_env(name: str, cfg: dict[str, Any]) -> dict[str, str]:
    """The environment for MCP server *name* with config *cfg*.

    The allowlisted part of Kazma's environment, then the server's own
    ``env`` (operator intent wins), then an ``auth`` of type ``env``. *cfg*
    holds real values here, never ``vault://`` pointers: callers resolve the
    server's secrets first (:func:`kazma_core.mcp.secrets.resolve`).
    """
    if (os.environ.get("KAZMA_MCP_INHERIT_ENV") or "").strip().lower() in (
        "1", "true", "on", "yes",
    ):
        env = dict(os.environ)
    else:
        env = {k: v for k, v in os.environ.items() if k.upper() in MCP_CHILD_ENV_ALLOWLIST}
        # A proxy URL can carry the proxy account's password
        # (``http://user:pass@proxy:3128``), and the allowlist handed it to
        # every MCP server, a package the operator may only be trying out
        # (audit 2026-10-26 AUD-097). Such a value stays behind; a server that
        # needs the proxy gets it in its own ``env``, which is applied below.
        for key in [k for k, v in env.items() if _carries_credentials(k, v)]:
            del env[key]
            logger.info(
                "[MCP] stdio server '%s': not passing %s (it carries credentials); "
                "set it in the server's own env if the server needs it",
                name, key,
            )
    for key, value in (cfg.get("env") or {}).items():
        if isinstance(key, str) and isinstance(value, (str, int, float, bool)):
            env[key] = str(value)
    auth = cfg.get("auth") or {}
    if auth.get("type") == "env" and auth.get("name") and auth.get("value"):
        env[str(auth["name"])] = str(auth["value"])
    dropped = len(os.environ) - len(env)
    if dropped > 0:
        logger.debug(
            "[MCP] stdio server '%s': scrubbed %d inherited env vars (allowlist mode)",
            name, dropped,
        )
    return env
