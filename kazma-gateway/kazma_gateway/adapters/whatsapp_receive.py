"""Why a WhatsApp message was left unanswered, in the words the Test and the log show.

The reasons every chat app shares are ``receive_log.COMMON_REASONS``; these
are WhatsApp's own (2026-11-14).
"""

from __future__ import annotations

__all__ = ["WHATSAPP_REASONS"]

WHATSAPP_REASONS: dict[str, str] = {
    # The shared refusals, in the WhatsApp card's words (its allowlist is numbers).
    "no_allowlist": "was refused: Allowed numbers is empty and its author is not the owner's number Kazma knows",
    "user_not_allowed": "was refused: its author's number is not in Allowed numbers",
    "duplicate": "was a resend of a message Kazma had already received (Meta resends until it gets an answer)",
    "unsupported_type": (
        "is a kind of message Kazma does not read yet (a sticker, location, contact or reaction); "
        "text, photos, documents and voice notes are read"
    ),
    "media_failed": "had a photo, document or voice note Kazma could not download from WhatsApp",
    "not_this_number": "was sent to another phone number of the WhatsApp Business Account, not the one Kazma uses",
}
