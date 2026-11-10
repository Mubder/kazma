"""What an approval card may show of a tool call's arguments.

One redactor for every approval card: the chat graph's card on Telegram,
Discord and Slack (``kazma_gateway.agent_handler.hitl``), the swarm bus's card
(``kazma_core.swarm.approval_card``) and the Swarm page's view of a bus request
(``swarm.shared_approvals.describe``). Audit 2026-11-10: the bus card posted
every argument verbatim to every chat platform, and the chat card's own list
of names (``token``, ``password``...) missed compound names --
``access_token``, ``client_secret``, ``bot_token``,
``aws_secret_access_key`` -- and ``vault_store``'s ``value``.

Built on the product's maskers: a member is hidden when its NAME is a secret by
the Settings masker's rule (``config_mask.key_is_secret``), is one of the few
names a card always hid (headers, cookies, a child's environment), or is a
secret argument of its tool (:data:`SECRET_ARGUMENTS`); every other string has
its URL passwords masked (``url_credentials.mask_url_credentials_deep``).
Every argument NAME stays on the card (SW-S03), and a card's sha256 footer
still hashes the REAL arguments, so what is approved can be checked against
what runs.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["HIDDEN", "SECRET_ARGUMENTS", "redact_arguments", "redacted_arguments_json"]

#: What a hidden value reads as.
HIDDEN = "[hidden]"

#: Tool -> the arguments that are secrets whatever they are called.
SECRET_ARGUMENTS: dict[str, frozenset[str]] = {
    "vault_store": frozenset({"value"}),
}

#: Names a card always hid that are not credentials by the Settings rule: a
#: request's headers and cookies, a child process's environment.
_ALWAYS_HIDDEN = frozenset({"header", "headers", "cookie", "cookies", "env"})

_MAX_DEPTH = 12


def _is_secret_key(name: Any) -> bool:
    from kazma_core.security.config_mask import key_is_secret

    lowered = str(name).strip().lower()
    return lowered in _ALWAYS_HIDDEN or key_is_secret(lowered)


def _walk(node: Any, depth: int) -> Any:
    if depth > _MAX_DEPTH:
        return HIDDEN
    if isinstance(node, dict):
        return {k: (HIDDEN if _is_secret_key(k) else _walk(v, depth + 1)) for k, v in node.items()}
    if isinstance(node, (list, tuple)):
        return [_walk(v, depth + 1) for v in node]
    from kazma_core.security.url_credentials import mask_url_credentials_deep

    return mask_url_credentials_deep(node)


def redact_arguments(tool: str, args: Any) -> Any:
    """*args* as an approval card may show them: names kept, secrets hidden."""
    secret = SECRET_ARGUMENTS.get(str(tool or "").strip(), frozenset())
    if isinstance(args, dict):
        return {
            k: (HIDDEN if (str(k) in secret or _is_secret_key(k)) else _walk(v, 1))
            for k, v in args.items()
        }
    return _walk(args, 0)


def redacted_arguments_json(tool: str, tool_args: str | None) -> str | None:
    """The call's JSON arguments as a card may show them (None stays None).

    Text that is not JSON is shown with its URL passwords masked.
    """
    if tool_args is None:
        return None
    try:
        parsed = json.loads(tool_args) if tool_args else {}
    except ValueError:
        from kazma_core.security.url_credentials import mask_urls_in_text

        return mask_urls_in_text(tool_args)
    return json.dumps(redact_arguments(tool, parsed), ensure_ascii=False, default=str)
