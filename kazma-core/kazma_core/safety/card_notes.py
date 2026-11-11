"""What an approval card says about a call beyond its arguments, in English and Arabic.

A note is data the gate attaches to the card's payload (``card_notes``):
``{"code": <one of NOTE_TEXT>, ...its values}``. Every surface words it from
this table: the chat apps' cards (``kazma_gateway.agent_handler.hitl``) and
the swarm bus card (``swarm.safety``) through :func:`note_lines`; the web chat
card from the catalog (``chat.hitl_note_<code>``), which
``tests/test_outside_text_fetch.py`` holds equal to this table.

Notes (2026-11-11):

* ``outside_fetch`` -- the call fetches an address that came from text Kazma
  read (a page, a mail, a document), not from the user's message or a search
  result (``safety.outside_text_fetch``).
* ``git_programs`` -- the repository configures programs git runs for this
  command: hooks, filter and diff drivers, an ssh command, a pager
  (``safety.git_repo_programs``).
"""

from __future__ import annotations

from typing import Any

__all__ = ["NOTE_TEXT", "card_language", "note_lines"]

#: code -> {"en": ..., "ar": ...}. ``{host}`` / ``{items}`` are filled from the note.
NOTE_TEXT: dict[str, dict[str, str]] = {
    "outside_fetch": {
        "en": "This address came from text Kazma read, not from you or a search result: {host}",
        "ar": "هذا العنوان جاء من نص قرأه Kazma، لا منك ولا من نتيجة بحث: {host}",
    },
    "git_programs": {
        "en": "This repository sets programs git runs for this command: {items}",
        "ar": "هذا المستودع يحدد برامج يشغّلها git لهذا الأمر: {items}",
    },
}


def card_language(user_text: str | None) -> str:
    """``"ar"``, ``"en"``, or ``""`` (both) for the message a card answers."""
    from kazma_core.language_lock import detect_user_language

    lang = detect_user_language(user_text or "")
    return lang if lang in ("ar", "en") else ""


def _note_text(note: dict[str, Any], lang: str) -> str:
    """One note in *lang*: ``"ar"``, ``"en"``, or both (Arabic first) for ``""``."""
    text = NOTE_TEXT.get(str(note.get("code") or ""))
    if text is None:
        return ""
    items = note.get("items") or []
    values = {
        "host": str(note.get("host") or ""),
        "items": "; ".join(str(i) for i in items) if isinstance(items, list) else str(items),
    }
    if lang in ("ar", "en"):
        return text[lang].format(**values)
    return f"{text['ar'].format(**values)}\n{text['en'].format(**values)}"


def note_lines(notes: Any, lang: str = "") -> list[str]:
    """Every note of a card's ``card_notes``, worded, in order."""
    if not isinstance(notes, list):
        return []
    out = []
    for note in notes:
        if isinstance(note, dict):
            line = _note_text(note, lang)
            if line:
                out.append(line)
    return out
