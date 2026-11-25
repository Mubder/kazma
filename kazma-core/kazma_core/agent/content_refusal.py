"""A message one vendor's content filter refuses, answered by another vendor.

Qwen's filter refused a harmless Arabic question (HTTP 400
``data_inspection_failed``, the real-use runs of 2026-11-22) and the turn
ended in an error: a 4xx is permanent and never failed over, since the same
request fails the same way everywhere. A content filter is one vendor's, so
its refusal goes to the first backup of ANOTHER vendor
(``agent.nonstop.failover``, the owner's own choice of who may be asked), and
the answer says why it came from there.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

__all__ = ["is_content_refused", "other_vendor", "refusal_line"]


def is_content_refused(exc: BaseException) -> bool:
    """True for a provider's content-filter refusal (``LLMError.kind``)."""
    return getattr(exc, "kind", "") == "content_refused"


def _vendor_of(client: Any) -> str:
    """A client's vendor: its address's host (what the backup suggestions
    use), else the provider it was built for. No I/O."""
    base = str(getattr(getattr(client, "config", None), "base_url", "") or "")
    try:
        host = urlsplit(base).hostname or ""
    except ValueError:
        host = ""
    return (host or str(getattr(client, "provider_name", "") or "")).lower()


def other_vendor(primary: Any, backup: Any) -> bool:
    """True when *backup* is another vendor's than *primary*; one whose vendor
    is not known is not."""
    mine, theirs = _vendor_of(primary), _vendor_of(backup)
    return bool(theirs) and theirs != mine


def refusal_line(user_text: str | None, refused_model: str, used_model: str) -> str:
    """The line an answer from the backup begins with, in the message's language."""
    from kazma_core.language_lock import in_their_language

    refused, used = refused_model or "the model", used_model
    return in_their_language(
        user_text,
        f"> The content filter of the provider of `{refused}` refused this message, so `{used}` answered it.",
        f"> رفض مرشّح المحتوى لدى مزوّد `{refused}` هذه الرسالة، فأجاب عنها `{used}`.",
    )
