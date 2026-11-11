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

The approval record keeps the same view (2026-11-11): the gate registry
(``safety.hitl_gates``) stores a call's arguments as a card shows them, its
payload with every call's arguments masked (:func:`redacted_gate_payload`),
its free text through :func:`redact_text`, and :func:`arguments_digest` of
the REAL arguments. It stored them raw, credentials included.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

__all__ = [
    "HIDDEN",
    "SECRET_ARGUMENTS",
    "arguments_digest",
    "redact_arguments",
    "redact_text",
    "redacted_arguments_json",
    "redacted_gate_payload",
]

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
        return redact_text(tool_args)
    return json.dumps(redact_arguments(tool, parsed), ensure_ascii=False, default=str)


def arguments_digest(tool_args: str | None) -> str:
    """The hash a card shows of a call's REAL arguments (the JSON text).

    The swarm bus card's footer, the Swarm page's panel and the gate record
    (``args_digest``) carry the same one, so what is approved can be matched
    to what runs, and two calls that differ only in a hidden secret are two
    calls.
    """
    return hashlib.sha256((tool_args or "").encode("utf-8")).hexdigest()[:16]


def redact_text(text: Any) -> str:
    """Free text that may quote a call (a card's message, a refusal, an
    error): every secret-shaped fragment and URL password masked."""
    from kazma_core.errors import redact_secrets
    from kazma_core.security.url_credentials import mask_urls_in_text

    return mask_urls_in_text(redact_secrets(str(text or "")))


#: Payload members that hold a call's arguments, at the top and in each entry
#: of ``tools`` (a batch card) and ``items`` (a semantic card).
_ARGUMENT_MEMBERS = ("args", "arguments")
_CALL_LISTS = ("tools", "items")


def _redact_call(entry: Any, default_tool: str) -> Any:
    if not isinstance(entry, dict):
        from kazma_core.security.url_credentials import mask_url_credentials_deep

        return mask_url_credentials_deep(entry)
    from kazma_core.security.url_credentials import mask_url_credentials_deep

    tool = str(entry.get("tool") or entry.get("name") or entry.get("tool_name") or default_tool)
    out: dict[str, Any] = {}
    for key, value in entry.items():
        if key in _ARGUMENT_MEMBERS:
            out[key] = redact_arguments(tool, value)
        elif key in _CALL_LISTS and isinstance(value, list):
            out[key] = [_redact_call(item, tool) for item in value]
        elif key == "message" and isinstance(value, str):
            out[key] = redact_text(value)
        else:
            out[key] = mask_url_credentials_deep(value)
    return out


def redacted_gate_payload(payload_json: str | None) -> str:
    """A card's payload (JSON text) as the approval record may keep it: the
    arguments of the call, and of every call in a batch, as a card shows
    them; the message through :func:`redact_text`; URL passwords masked
    everywhere. Text that is not a JSON object is kept as free text."""
    raw = payload_json or ""
    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return redact_text(raw)
    if not isinstance(parsed, dict):
        from kazma_core.security.url_credentials import mask_url_credentials_deep

        return json.dumps(mask_url_credentials_deep(parsed), ensure_ascii=False, default=str)
    return json.dumps(_redact_call(parsed, str(parsed.get("tool") or "")), ensure_ascii=False, default=str)
