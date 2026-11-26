"""A message one vendor's content filter refuses, answered by another vendor.

Qwen's filter refused a harmless Arabic question (HTTP 400
``data_inspection_failed``, the real-use runs of 2026-11-22) and the turn
ended in an error: a 4xx is permanent and never failed over, since the same
request fails the same way everywhere. A content filter is one vendor's, so
its refusal goes to the first backup of ANOTHER vendor
(``agent.nonstop.failover``, the owner's own choice of who may be asked), and
the answer says why it came from there. A vendor is the preset's, in every
region (``provider_endpoints.vendor_of``). The final synthesis of a turn that
ended with no answer text (``graph_respond``) is asked of the turn's own
client only.
"""

from __future__ import annotations

from typing import Any

from kazma_core.provider_endpoints import vendor_of

__all__ = [
    "already_told", "is_content_refused", "other_vendor", "person_text", "refusal_failed_text", "refusal_line",
]

_TOLD_EN = "> The content filter of the provider of"
_TOLD_AR = "> رفض مرشّح المحتوى لدى مزوّد"


def is_content_refused(exc: BaseException) -> bool:
    """True for a provider's content-filter refusal (``LLMError.kind``)."""
    return getattr(exc, "kind", "") == "content_refused"


def _client_vendor(client: Any) -> str:
    """A client's vendor: its row's preset, else its own address -- never the
    egress proxy's, which every client shares. No I/O."""
    config = getattr(client, "config", None)
    own = getattr(client, "_direct_base_url", None) or getattr(config, "base_url", None)
    return vendor_of(
        getattr(client, "provider_preset", ""), getattr(client, "provider_name", ""), str(own or ""))


def other_vendor(primary: Any, backup: Any) -> bool:
    """True when *backup* is another vendor's than *primary*; one whose vendor
    is not known is not."""
    mine, theirs = _client_vendor(primary), _client_vendor(backup)
    return bool(theirs) and theirs != mine


def person_text(messages: Any) -> str:
    """The text of the person's own last message: never Kazma's prompt to go
    on, and of a message with a picture its words alone."""
    from kazma_core.agent.plan_fence import is_graph_nudge
    from kazma_core.reasoning_text import text_of_parts

    for message in reversed(list(messages or [])):
        if not isinstance(message, dict) or (message.get("role") or message.get("type")) not in ("user", "human"):
            continue
        text = text_of_parts(message.get("content"))
        if not is_graph_nudge(text):
            return text
    return ""


def already_told(messages: Any) -> bool:
    """True when this turn's answer so far already says who refused it (a turn
    that goes on by itself asks the model again). Judged by how an answer
    opens -- the line is put at its head (:func:`refusal_line`) -- never by
    the words anywhere in it: an answer that quotes them was told nothing
    (``tests/test_messages_judged_by_how_they_open.py``)."""
    from kazma_core.agent.plan_fence import running_turn
    from kazma_core.reasoning_text import text_of_parts

    return any(
        text_of_parts(m.get("content")).lstrip().startswith((_TOLD_EN, _TOLD_AR))
        for m in running_turn(messages) if isinstance(m, dict) and m.get("role") == "assistant"
    )


def refusal_line(user_text: str | None, refused_model: str, used_model: str) -> str:
    """The line an answer from the backup begins with, in the message's language."""
    from kazma_core.language_lock import in_their_language

    refused, used = refused_model or "the model", used_model
    return in_their_language(
        user_text,
        f"{_TOLD_EN} `{refused}` refused this message, so `{used}` answered it.",
        f"{_TOLD_AR} `{refused}` هذه الرسالة، فأجاب عنها `{used}`.",
    )


def refusal_failed_text(user_text: str | None, model: str, *, backup_tried: bool) -> str:
    """What a turn no one answered says, in the message's language (both when
    it is not known), and what can be done about it as things are."""
    from kazma_core.language_lock import in_their_language

    where = f" ({model})" if model else ""
    if backup_tried:
        then_en = "A backup model from another vendor was asked and did not answer either. " \
                  "Put it another way, or choose another model."
        then_ar = "سُئل نموذج احتياطي من مزوّد آخر فلم يُجب هو أيضًا. أعد صياغتها أو اختر نموذجًا آخر."
    else:
        then_en = "Put it another way, choose another model, or set a backup model from another vendor " \
                  "(Settings > System): Kazma asks it when this happens."
        then_ar = "أعد صياغتها، أو اختر نموذجًا آخر، أو حدّد نموذجًا احتياطيًا من مزوّد آخر " \
                  "(الإعدادات > النظام): يسأله كاظمة حين يحدث هذا."
    return "⚠️ " + in_their_language(
        user_text,
        f"The content filter of the model's provider{where} refused this message, so it was not answered. {then_en}",
        f"رفض مرشّح المحتوى لدى مزوّد النموذج{where} هذه الرسالة، فلم يُجَب عنها. {then_ar}",
    )
