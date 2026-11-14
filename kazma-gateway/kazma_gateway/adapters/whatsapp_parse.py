"""Reading what Meta's WhatsApp Cloud API sends to Kazma's webhook.

Two jobs, both pure (no network, no state):

* :func:`signature_ok` -- every call carries ``X-Hub-Signature-256``, an
  HMAC-SHA256 of the raw body under the app's secret. Kazma answers nothing
  that does not match: the webhook is reachable from the internet, and the
  signature is the only proof a call came from Meta.
* :func:`inbound_messages` -- the messages in a webhook payload, flattened
  into :class:`WaInbound`. Status updates (sent, delivered, read) are counted,
  never answered.

A WhatsApp user's id (``wa_id``) is their phone number in digits, with the
country code and no ``+``: :func:`wa_digits` turns what a person types into
that form, so "+965 9999 1234" in Allowed numbers matches ``96599991234``.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Any

__all__ = ["WaInbound", "count_statuses", "inbound_messages", "signature_ok", "wa_digits"]

#: The kinds Kazma reads; anything else is recorded as ``unsupported_type``.
READ_KINDS = frozenset({"text", "image", "document", "audio", "interactive", "button"})


def wa_digits(value: Any) -> str:
    """A phone number as WhatsApp ids it: digits only (``+965 9999-1234`` -> ``96599991234``)."""
    return re.sub(r"\D", "", str(value if value is not None else ""))


def signature_ok(app_secret: str, body: bytes, header: str | None) -> bool:
    """Whether *header* (``sha256=<hex>``) is the HMAC-SHA256 of *body* under *app_secret*.

    An empty secret or header is never a match: a webhook with no secret set
    answers nothing (the caller says so)."""
    if not app_secret or not header:
        return False
    scheme, _, given = header.strip().partition("=")
    if scheme.lower() != "sha256" or not given:
        return False
    expected = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, given.strip().lower())


@dataclass(frozen=True)
class WaInbound:
    """One message a person sent to the business number."""

    message_id: str
    from_id: str
    name: str
    phone_number_id: str
    timestamp: float
    kind: str
    text: str = ""
    media_id: str = ""
    mime: str = ""
    filename: str = ""
    voice: bool = False
    #: A button the person pressed: its id (Kazma's callback data) and title.
    button_id: str = ""
    button_title: str = ""

    @property
    def readable(self) -> bool:
        return self.kind in READ_KINDS


def _value_blocks(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        return []
    blocks: list[dict[str, Any]] = []
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            if isinstance(change, dict) and change.get("field") == "messages" and isinstance(change.get("value"), dict):
                blocks.append(change["value"])
    return blocks


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _one(raw: dict[str, Any], value: dict[str, Any], names: dict[str, str]) -> WaInbound | None:
    message_id = str(raw.get("id") or "")
    from_id = wa_digits(raw.get("from"))
    if not message_id or not from_id:
        return None
    kind = str(raw.get("type") or "unknown")
    phone_number_id = str(((value.get("metadata") or {}).get("phone_number_id")) or "")
    base = {
        "message_id": message_id, "from_id": from_id, "name": names.get(from_id, ""),
        "phone_number_id": phone_number_id, "timestamp": _float(raw.get("timestamp")), "kind": kind,
    }
    if kind == "text":
        return WaInbound(**base, text=str((raw.get("text") or {}).get("body") or ""))
    if kind in ("image", "document", "audio"):
        media = raw.get(kind) or {}
        return WaInbound(
            **base,
            text=str(media.get("caption") or ""),
            media_id=str(media.get("id") or ""),
            mime=str(media.get("mime_type") or ""),
            filename=str(media.get("filename") or ""),
            voice=bool(media.get("voice")) if kind == "audio" else False,
        )
    if kind == "interactive":
        inter = raw.get("interactive") or {}
        reply = inter.get("button_reply") or inter.get("list_reply") or {}
        return WaInbound(**base, button_id=str(reply.get("id") or ""), button_title=str(reply.get("title") or ""))
    if kind == "button":
        # A quick-reply button on a template message.
        button = raw.get("button") or {}
        return WaInbound(**base, button_id=str(button.get("payload") or ""), button_title=str(button.get("text") or ""))
    return WaInbound(**base)


def inbound_messages(payload: Any) -> list[WaInbound]:
    """Every message in a webhook *payload*, in the order Meta sent them."""
    found: list[WaInbound] = []
    for value in _value_blocks(payload):
        names = {
            wa_digits(c.get("wa_id")): str(((c.get("profile") or {}).get("name")) or "")
            for c in value.get("contacts") or [] if isinstance(c, dict)
        }
        for raw in value.get("messages") or []:
            if isinstance(raw, dict):
                one = _one(raw, value, names)
                if one is not None:
                    found.append(one)
    return found


def count_statuses(payload: Any) -> int:
    """How many delivery statuses (sent, delivered, read, failed) the payload holds."""
    return sum(len(v.get("statuses") or []) for v in _value_blocks(payload))
