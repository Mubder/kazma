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
- ``backups.offsite.`` / ``backups.extra_paths`` — where the universal
  backup is sent and what it holds (audit 2026-10-26 AUD-094). That zip
  carries ``.env`` and ``vault.db``: one approved ``config_save`` redirected
  it to a WebDAV, S3 or FTP host of the agent's choosing, or added a folder
  of the agent's choosing to it.
- Where secrets and memory go, and who may talk to Kazma (2026-11, FA-126):
  ``embedding.`` (the embedder sends every memory text, with the key named by
  ``api_key_env``, to ``base_url``: one approved card pointed it at the vault
  key's variable and an outside host), ``memory.backends.`` (the memory
  mirror's database), ``providers.`` and ``registry.active_`` (model
  endpoints, the keys sent to them, which one is used), ``proxy.`` (the
  scraping route), ``documents.security.`` / ``documents.storage_root``
  (remote parsing of documents, where they are kept), and each chat app's
  ``allowed_*`` lists (who may give Kazma orders).

``tests/test_protected_config.py`` holds the class: every settings key whose
value is a URL, a host, an environment variable's name or an allowlist is
protected, or declared there as safe for the agent with the reason.

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

import re

from kazma_core.settings_validation import base_key

__all__ = ["PROTECTED_CONFIG_PATTERNS", "PROTECTED_CONFIG_PREFIXES", "is_protected_config_key"]

#: Prefixes the agent may not write through a config tool. Keep this the only
#: definition; ``tests/test_protected_config.py`` checks both call sites use it.
PROTECTED_CONFIG_PREFIXES: tuple[str, ...] = (
    "safety.",
    "agent.commitment.",
    "agent.hooks.",
    "mcp.",
    "notifications.lifecycle.",
    # The offsite backup's destination and contents (AUD-094).
    "backups.offsite.",
    "backups.extra_paths",
    "security.",
    "vault.",
    "yolo.",
    "kazma_secret",
    # Where secrets and memory go (FA-126).
    "embedding.",
    "memory.backends.",
    "providers.",
    "registry.active_",
    "proxy.",
    "documents.security.",
    "documents.storage_root",
)

#: Keys protected by shape, where a prefix cannot say it: who may message
#: Kazma on each chat app (``connectors.<app>.allowed_users`` and siblings).
PROTECTED_CONFIG_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^connectors\.[^.]+\.allowed_"),
)

#: And every key whose LAST segment says its value is a destination or a
#: gate -- a URL, a host, an endpoint, the name of an environment variable,
#: an allowlist, a storage path -- whatever its section, so a key added later
#: is protected the day it exists (FA-126: a deny list with one entry per
#: incident left ``embedding.api_key_env``, ``ui.host`` and the voice and
#: LLM endpoints open).
_DESTINATION = re.compile(
    r"(^|_)(url|urls|uri|dsn|host|hosts|endpoint|env|allowlist|storage_root|db_path)$"
    r"|^allowed_"
)


def is_protected_config_key(key: str) -> bool:
    """Whether *key* is one the agent may not change about itself."""
    k = base_key(str(key or "").strip())
    if not k:
        return False
    if any(k == p.rstrip(".") or k.startswith(p) for p in PROTECTED_CONFIG_PREFIXES):
        return True
    if any(p.match(k) for p in PROTECTED_CONFIG_PATTERNS):
        return True
    if _DESTINATION.search(k.rsplit(".", 1)[-1]):
        return True
    # Imported here: settings_restore reads the settings store's module, which
    # must not depend on the safety package at import time.
    from kazma_core.settings_restore import SETTING, classify

    return classify(k) != SETTING
