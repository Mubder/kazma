"""Per-turn language lock — force reply language to match the user.

Cultural context and the default agent language (``language: ar``) bias
models toward Arabic. This module detects the user's message script and
emits a short system message that must be the *last* language instruction
before the user turn is processed.
"""

from __future__ import annotations

import re

__all__ = ["detect_user_language", "language_lock_message"]

_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def detect_user_language(text: str) -> str:
    """Return ``'ar'``, ``'en'``, or ``'mixed'`` / ``'unknown'``.

    Uses script counts (Arabic vs Latin letters). Short pure-symbol messages
    are ``unknown``.
    """
    t = (text or "").strip()
    if not t:
        return "unknown"
    ar = len(_ARABIC_RE.findall(t))
    la = len(_LATIN_RE.findall(t))
    if ar == 0 and la == 0:
        return "unknown"
    if ar > 0 and la > 0:
        # Code-switch: whichever dominates; tie → mixed
        if ar >= la * 1.2:
            return "ar"
        if la >= ar * 1.2:
            return "en"
        return "mixed"
    if ar > 0:
        return "ar"
    return "en"


def language_lock_message(user_text: str) -> str:
    """System message enforcing reply language for this turn only."""
    lang = detect_user_language(user_text)
    # Branding: كاظمه/كاظمة only — never the wrong phonetic كازما.
    _name_ar = (
        "NAME: In Arabic refer to yourself as كاظمه (or كاظمة). "
        "Never write كازما."
    )
    if lang == "en":
        return (
            "LANGUAGE LOCK (this turn): The user wrote in ENGLISH. "
            "You MUST reply in English only. Do NOT use Arabic script. "
            "Do NOT greet in Arabic. Cultural context does NOT override this. "
            "Code, paths, and tool names stay as-is. "
            "Your English product name is Kazma."
        )
    if lang == "ar":
        return (
            "LANGUAGE LOCK (this turn): The user wrote in ARABIC. "
            "You MUST reply in Arabic. English only for code/paths/identifiers. "
            f"{_name_ar}"
        )
    if lang == "mixed":
        return (
            "LANGUAGE LOCK (this turn): The user mixed Arabic and English. "
            "Mirror their mix; default the bulk of the reply to the language "
            "they used more. Do not force pure Arabic or pure English. "
            f"{_name_ar}"
        )
    return (
        "LANGUAGE LOCK (this turn): User language unclear — reply in English "
        "unless they previously established another language in this thread."
    )
