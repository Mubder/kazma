"""One masker for a model provider's row, for every answer that shows one.

A provider row holds its API key, any extra keys, a base URL that may carry a
password, secret headers and AWS credentials. The Providers API masked them
(``kazma_ui.providers``), but the unified model options -- ``GET
/api/settings/models/options`` and ``GET /api/swarm/models``, which any
signed-in user may read -- returned ``ModelRegistry.list_providers()`` as it
is: every provider's key in the clear (found by the defensive check before the
2026-11 security review). Both now pass through this one function.

A masked key keeps its last four characters (``****abcd``) so a form can show
which key is stored; a save that sends the mask back keeps the stored key
(``provider_check.merge_typed_secrets``).
"""

from __future__ import annotations

from typing import Any

from kazma_core.security.url_credentials import mask_url_credentials

__all__ = ["mask_provider_entry", "mask_secret_tail"]

#: The fields of a provider row that hold a credential, besides the URL,
#: headers and extra keys handled on their own.
_SECRET_FIELDS = ("api_key", "aws_secret_access_key", "aws_session_token")


def mask_secret_tail(value: str) -> str:
    """``****`` and the last four characters, ``***`` for a short value."""
    if not value:
        return ""
    if len(value) < 4:
        return "***"
    return f"****{value[-4:]}"


def mask_provider_entry(provider: dict[str, Any]) -> dict[str, Any]:
    """A copy of *provider* with every secret masked.

    The API key, the AWS secret and token, each extra key's value, each
    header's value (shown as ``headers``) and any password in the base URL.
    """
    safe = dict(provider)
    for field in _SECRET_FIELDS:
        if safe.get(field):
            safe[field] = mask_secret_tail(str(safe[field]))
    if safe.get("base_url"):
        safe["base_url"] = mask_url_credentials(safe["base_url"])
    headers = safe.pop("secret_headers", None)
    if isinstance(headers, dict):
        safe["headers"] = {k: mask_secret_tail(str(v)) for k, v in headers.items()}
    if isinstance(safe.get("api_keys"), list):
        safe["api_keys"] = [
            {"id": str(k.get("id") or ""), "label": str(k.get("label") or ""),
             "api_key": mask_secret_tail(str(k.get("api_key") or ""))}
            for k in safe["api_keys"] if isinstance(k, dict)
        ]
    return safe
