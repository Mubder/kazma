"""The one list of config keys the agent may not change about itself.

Two lists guarded this before (audit 2026-09-30, AUD-007): ``config_save``
(``agent/tool_builtins/system.py``) blocked ``security.``, ``kazma_secret``,
``vault.``, ``yolo.``; the commitment resolver
(``safety/commitment/authorize.py``) blocked ``safety.``,
``agent.commitment.``, ``notifications.lifecycle.``. Neither covered
``agent.hooks.*`` (shell commands run on every tool call,
``agent/tool_hooks.py``) or ``mcp.servers`` (server commands the MCP manager
spawns), so the agent could write its own persistent command execution
through a config change. One list now, used by both.

A key is protected when changing it could turn a safety control off, hand
the agent a new way to run code or reach the network, or expose a secret:

- ``safety.`` / ``agent.commitment.`` / ``yolo.`` — the approval and
  autonomy gates.
- ``agent.hooks.`` — pre/post-tool hook COMMANDS, run for every tool call.
- ``mcp.`` — MCP server definitions (``command`` / ``args`` are spawned) and
  their OAuth/env.
- ``security.`` / ``kazma_secret`` / ``vault.`` — auth, the shared secret,
  the secret vault.
- ``notifications.lifecycle.`` — the operator's own status channel.

And every key that is not a setting at all (2026-10-01): Kazma's own state,
credentials and what it learned, as ``kazma_core.settings_restore.classify``
names them -- approval grants (``task_grant.``, ``hitl_grant.``,
``path_grant.``), signed-in browsers, the Soul. ``config_save`` could write
those: an approval card stood between the agent and a grant it gave itself,
but a card is not a reason to offer the write.

Membership is by prefix (``key`` equals a prefix without its trailing dot,
or starts with it), on the key without its tenant prefix: ``tenant.<id>.
mcp.servers`` is a tenant's MCP servers and was not protected.
``is_sensitive_config_key`` (secret-CLASS keys, e.g. provider API keys) is a
SEPARATE, additional gate that ``config_save`` already applies; this list is
the self-protection gate.
"""

from __future__ import annotations

from kazma_core.settings_validation import base_key

__all__ = ["PROTECTED_CONFIG_PREFIXES", "is_protected_config_key"]

#: Prefixes the agent may not write through a config tool. Keep this the only
#: definition; ``tests/test_protected_config.py`` checks both call sites use it.
PROTECTED_CONFIG_PREFIXES: tuple[str, ...] = (
    "safety.",
    "agent.commitment.",
    "agent.hooks.",
    "mcp.",
    "notifications.lifecycle.",
    "security.",
    "vault.",
    "yolo.",
    "kazma_secret",
)


def is_protected_config_key(key: str) -> bool:
    """Whether *key* is one the agent may not change about itself."""
    k = base_key(str(key or "").strip())
    if not k:
        return False
    if any(k == p.rstrip(".") or k.startswith(p) for p in PROTECTED_CONFIG_PREFIXES):
        return True
    # Imported here: settings_restore reads the settings store's module, which
    # must not depend on the safety package at import time.
    from kazma_core.settings_restore import SETTING, classify

    return classify(k) != SETTING
