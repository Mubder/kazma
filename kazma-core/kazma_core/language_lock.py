"""Per-turn language lock — force reply language to match the **current** user message.

Cultural context, default ``agent.language``, and long Arabic (or English) history
bias models toward the first language of the session. This module detects the
*latest* user message script and emits a short system instruction that must
override history for that turn only.
"""

from __future__ import annotations

import re

__all__ = ["detect_user_language", "language_lock_message"]

_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")
_LATIN_RE = re.compile(r"[A-Za-z]")

# Shared override so history cannot pin the session to the first language.
_HISTORY_OVERRIDE = (
    "HISTORY OVERRIDE: Earlier messages in this thread may be in another language. "
    "Ignore their language for *this* reply. Only the user's latest message "
    "determines the reply language. Switching when the user switches is required, "
    "not optional."
)

_name_ar = (
    "NAME: In Arabic refer to yourself as كاظمه (or كاظمة). "
    "Never write كازما."
)


def detect_user_language(text: str) -> str:
    """Return ``'ar'``, ``'en'``, ``'mixed'``, or ``'unknown'``.

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
    if lang == "en":
        return (
            "LANGUAGE LOCK (this turn ONLY): The user's LATEST message is ENGLISH. "
            "You MUST reply in English only. Do NOT use Arabic script. "
            "Do NOT greet in Arabic. Cultural context and agent.language do NOT override this. "
            f"{_HISTORY_OVERRIDE} "
            "Code, paths, and tool names stay as-is. "
            "Your English product name is Kazma."
        )
    if lang == "ar":
        return (
            "LANGUAGE LOCK (this turn ONLY): The user's LATEST message is ARABIC. "
            "You MUST reply in Arabic. English only for code/paths/identifiers. "
            f"{_HISTORY_OVERRIDE} "
            f"{_name_ar}"
        )
    if lang == "mixed":
        return (
            "LANGUAGE LOCK (this turn ONLY): The user mixed Arabic and English "
            "in their LATEST message. Mirror their mix; default the bulk of the "
            "reply to the language they used more. Do not force pure Arabic or pure English. "
            f"{_HISTORY_OVERRIDE} "
            f"{_name_ar}"
        )
    # Unclear script (emoji, numbers only): do NOT inherit session language —
    # that was the main cause of "stuck on first language". Prefer English
    # and wait for a clear next message.
    return (
        "LANGUAGE LOCK (this turn ONLY): User language is unclear from this message. "
        "Reply in English (short). Do not continue a previous Arabic (or other) "
        "session language just because history was Arabic. "
        f"{_HISTORY_OVERRIDE}"
    )
