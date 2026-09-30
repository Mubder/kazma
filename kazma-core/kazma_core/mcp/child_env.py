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
