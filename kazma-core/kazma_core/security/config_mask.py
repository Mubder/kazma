"""Masking of configuration values before they leave the process.

One masker for every surface that echoes configuration: the Settings API
(``kazma_ui.settings``) and the agent's ``config_read`` tool. It moved here
from ``kazma_ui/settings.py`` (audit 2026-10-26 AUD-005) because
``config_read`` -- in kazma_core, which cannot import the web UI -- kept its
own check of the key's NAME only: ``config_read("providers.list")`` returned
every provider's API key and ``memory.backends.state.url`` the Postgres
password, to the model, with no approval card (AGENTS.md §26D).
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["MASK", "key_is_secret", "mask_deep"]


# Keys whose values are secrets and must be masked in API responses.
_SENSITIVE_KEY_FRAGMENTS = (
    "api_key", "apikey", "token", "secret", "password", "passphrase",
    "passwd", "credential", "private_key", "authorization", "webhook",
    "pat", "bearer",
)


#: Constant replacement — no last-4, no length hint.
MASK = "***"

#: Depth ceiling for the recursive walk. Config values are shallow; anything
#: deeper is a cycle or a pathological payload and is replaced wholesale rather
#: than returned unmasked.
_MAX_MASK_DEPTH = 12


#: Fragment match on ``.``/``_`` token boundaries, not raw substring. A plain
#: ``in`` test made ``pat`` match ``workspace.selected_path`` and ``token``
#: match ``tokenizer`` — masking real config as if it were a credential.
_SENSITIVE_KEY_RE = re.compile(
    r"(?:^|[._])(?:" + "|".join(re.escape(f) for f in _SENSITIVE_KEY_FRAGMENTS) + r")(?:$|[._])"
)


def key_is_secret(key: str) -> bool:
    """True when *key* names a credential, at any nesting depth.

    Combines the local fragment list with ``is_sensitive_config_key`` (which
    also drives vault routing) so both layers agree on what a secret is.
    """
    normalised = str(key).lower().replace("-", "_")
    if _SENSITIVE_KEY_RE.search(normalised):
        return True
    try:
        from kazma_core.config_store import is_sensitive_config_key

        return bool(is_sensitive_config_key(key))
    except Exception:
        return False


def mask_deep(node: Any, _depth: int = 0) -> Any:
    """Return *node* with every secret-named member masked, at any depth.

    Audit F-02: the previous implementation was documented as recursive but
    descended exactly two levels and handled only ``str`` and flat ``dict``
    values. A **list** fell through every branch, so ``providers.list`` — a
    list of provider objects each holding an ``api_key`` — was returned
    entirely unmasked, leaking live provider credentials over
    ``GET /api/settings``.

    Handles dicts, lists/tuples, and JSON-encoded strings (config values are
    frequently stored as serialized JSON).
    """
    import json

    if _depth > _MAX_MASK_DEPTH:
        return MASK

    if isinstance(node, dict):
        out: dict[Any, Any] = {}
        for k, v in node.items():
            if key_is_secret(k):
                out[k] = _mask_leaf(v, _depth)
            else:
                out[k] = mask_deep(v, _depth + 1)
        return out

    if isinstance(node, (list, tuple)):
        masked = [mask_deep(v, _depth + 1) for v in node]
        return type(node)(masked) if isinstance(node, tuple) else masked

    if isinstance(node, str) and node.strip():
        # Config values are often JSON-encoded; mask inside, then re-encode so
        # the wire shape is unchanged.
        # A password inside a URL is a secret whatever the key is called:
        # memory.backends.state.url held the live Postgres DSN (2026-09-25).
        from kazma_core.security.url_credentials import (
            mask_url_credentials,
            mask_urls_in_text,
        )

        stripped = node.strip()
        if stripped[0] in "{[":
            try:
                decoded = json.loads(stripped)
            except (json.JSONDecodeError, TypeError, ValueError):
                return mask_urls_in_text(node)
            return json.dumps(mask_deep(decoded, _depth + 1))
        return mask_url_credentials(node)

    return node


def _mask_leaf(value: Any, depth: int) -> Any:
    """Mask a value reached through a secret-named key.

    A non-empty scalar becomes ``***``. Containers are still walked, so a
    secret-named subtree keeps its shape (and any nested non-secret metadata)
    while every scalar inside it is masked.
    """
    import json

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return value
        if stripped[0] in "{[":
            try:
                return json.dumps(_mask_all_scalars(json.loads(stripped), depth + 1))
            except (json.JSONDecodeError, TypeError, ValueError):
                return MASK
        return MASK
    if isinstance(value, (dict, list, tuple)):
        return _mask_all_scalars(value, depth + 1)
    if value is None or isinstance(value, bool):
        return value
    return MASK


def _mask_all_scalars(node: Any, depth: int) -> Any:
    """Mask every non-empty scalar in *node*, regardless of key name."""
    if depth > _MAX_MASK_DEPTH:
        return MASK
    if isinstance(node, dict):
        return {k: _mask_all_scalars(v, depth + 1) for k, v in node.items()}
    if isinstance(node, (list, tuple)):
        masked = [_mask_all_scalars(v, depth + 1) for v in node]
        return type(node)(masked) if isinstance(node, tuple) else masked
    if node is None or isinstance(node, bool):
        return node
    if isinstance(node, str) and not node.strip():
        return node
    return MASK
