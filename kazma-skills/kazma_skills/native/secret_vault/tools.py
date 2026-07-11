"""Secret Vault tools — LLM-callable functions for encrypted secret management.

These tools are auto-registered by ``NativeSkillLoader`` when the
``secret-vault`` native skill is loaded.  They wrap the ``SecretVault``
singleton (``kazma_core.security.vault``).

Security model:
  - ``vault_store`` — unrestricted (anyone can store a secret).
  - ``vault_list`` — unrestricted (names + categories only, no values).
  - ``vault_retrieve`` — HITL-gated (user must approve before a secret is
    released).  See ``safety/hitl.py`` ``DEFAULT_DANGER_TOOLS``.
  - ``vault_delete`` — HITL-gated (user must approve deletion).

The vault is disabled if ``KAZMA_VAULT_KEY`` is not set; all tools return
a graceful error string in that case.
"""

from __future__ import annotations

import json
from typing import Any

from kazma_core.security.vault import get_vault


async def vault_store(
    name: str,
    value: str,
    category: str = "general",
    metadata: str = "{}",
) -> str:
    """Store an encrypted secret in the vault.

    Args:
        name: A unique identifier (e.g. "openai_key", "database_password").
        value: The secret value to encrypt and store.
        category: Grouping tag — "llm", "database", "payment", "personal", etc.
        metadata: Optional JSON metadata (non-sensitive, e.g. {"url": "https://api.openai.com"}).

    Returns:
        Success confirmation or error message.
    """
    vault = get_vault()
    if vault is None:
        return "Error: Secret vault is disabled. Set KAZMA_VAULT_KEY to enable."
    try:
        meta = json.loads(metadata) if isinstance(metadata, str) else metadata
    except (json.JSONDecodeError, TypeError):
        meta = {}
    try:
        sid = vault.store(name, value, category=category, metadata=meta)
        return f"Secret '{name}' stored securely (id={sid}, category={category})."
    except Exception as exc:
        return f"Error storing secret: {exc}"


async def vault_retrieve(name: str) -> str:
    """Retrieve a secret from the vault by name.

    This action requires HITL approval — the user will be asked to approve
    before the secret is released.

    Args:
        name: The name of the secret to retrieve (e.g. "openai_key").

    Returns:
        The decrypted secret value, or an error/not-found message.
    """
    vault = get_vault()
    if vault is None:
        return "Error: Secret vault is disabled. Set KAZMA_VAULT_KEY to enable."
    value = vault.retrieve(name)
    if value is None:
        return f"No secret found with name '{name}'."
    return value


async def vault_list() -> str:
    """List all stored secrets (names and categories, NOT values).

    Returns:
        JSON array of secret metadata, or an error message.
    """
    vault = get_vault()
    if vault is None:
        return "Error: Secret vault is disabled. Set KAZMA_VAULT_KEY to enable."
    items = vault.list_secrets()
    if not items:
        return "The vault is empty."
    return json.dumps(items, ensure_ascii=False, indent=2)


async def vault_delete(name: str) -> str:
    """Delete a secret from the vault by name.

    This action requires HITL approval.

    Args:
        name: The name of the secret to delete.

    Returns:
        Success or error message.
    """
    vault = get_vault()
    if vault is None:
        return "Error: Secret vault is disabled. Set KAZMA_VAULT_KEY to enable."
    deleted = vault.delete(name)
    if deleted:
        return f"Secret '{name}' deleted."
    return f"No secret found with name '{name}'."
