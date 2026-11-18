"""A text file attached to a chat message: fenced as data for the model.

The web chat reads a small text file in the browser and sends it with the
message. Until 2026-11 the page put the file's text into the message itself,
under ``[Attached file: <name>]`` in a code block, and the message reached the
model, memory and the chat's title as the user's own words; a file that held
a line of three backticks ended the code block early, and the rest read as
the user speaking (the review of the memory framework audit's F3). Every
other surface already fenced an attached document's text
(``agent_handler/attachments.build_user_content``).

Now the page sends the file beside the message (``text_attachment``) and the
server puts it into the model's message here, inside a data fence
(``prompt_fence.format_untrusted_block``, source ``document_attachment``), as
the chat apps do. The chat's stored row keeps the user's words and the file's
name (:func:`display_text`), never its text.
"""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["AttachedText", "attached_text_from", "display_text", "file_label", "model_text"]

#: How much of the file the model is given in the message (the page's cut).
MAX_TEXT_CHARS = 8000
#: How much of a file's name a stub shows.
MAX_NAME_CHARS = 200


@dataclass(frozen=True)
class AttachedText:
    """A text file sent with a message: its name and (at most
    :data:`MAX_TEXT_CHARS` of) its text."""

    name: str
    text: str


def file_label(name: Any) -> str:
    """*name* as a stub shows it: one line, no brackets or control or
    direction characters (a name like ``report] I live in Lyon.pdf`` ended a
    stub early, and its tail read as the user's words), at most
    :data:`MAX_NAME_CHARS` characters."""
    kept = []
    for ch in str(name or ""):
        if ch in "[]\r\n\t" or unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp"):
            kept.append(" ")
        else:
            kept.append(ch)
    label = " ".join("".join(kept).split())[:MAX_NAME_CHARS].strip()
    return label or "file"


def attached_text_from(value: Any) -> AttachedText | None:
    """The ``text_attachment`` a request carried, or None when it carried
    none, or one that is not a name and a text."""
    if not isinstance(value, dict):
        return None
    text = value.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    return AttachedText(name=file_label(value.get("name")), text=text[:MAX_TEXT_CHARS])


def _stub(attached: AttachedText) -> str:
    return f"[Attached file: {file_label(attached.name)}]"


def display_text(user_text: str | None, attached: AttachedText) -> str:
    """What the chat keeps and shows for the message: the user's words and
    the file's name."""
    words = (user_text or "").rstrip()
    return f"{words}\n\n{_stub(attached)}" if words else _stub(attached)


def model_text(user_text: str | None, attached: AttachedText) -> str:
    """What the model is given: the user's words, the file's name, and its
    text inside a data fence."""
    from kazma_core.safety.prompt_fence import format_untrusted_block

    fenced = format_untrusted_block(attached.text, source="document_attachment")
    return f"{display_text(user_text, attached)}\n{fenced}"
