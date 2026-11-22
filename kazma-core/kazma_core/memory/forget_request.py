"""Is the user asking Kazma to forget something? In English or in Arabic.

A memory tool that forgets keeps its own turn out of memory
(``forget.keep_turn_out``): written after the forget, "Forget every memory
that mentions BLUE-HERON-42" became a memory holding the codeword (live
2026-10-19). That is right only when the USER asked to forget. Until
2026-11-22 the turn was kept out whenever the model forgot something, and a
model that retired the old home on its own in an update turn ("I moved to
Porto last month", the Lisbon memory invalidated) kept the update itself out
of memory: no new home was written (the memory plan's finding 16).

Only the user's own words count (``memory.own_words``): never a "forget"
inside an attached document, a quote or a text to translate. English is read
lower-cased; Arabic raw for the imperative "انسى" (folded, it is also "أنسى",
I forget) and folded for the rest, as whole words with an optional "and"/"so"
prefix. Fixed words and bounded gaps: linear.
"""

from __future__ import annotations

import re

from kazma_core.memory.own_words import own_words

__all__ = ["asks_to_forget", "is_forget_request"]

#: "forget" as a request: opening a clause, or after a polite opener ("please
#: forget", "can you forget"). Never "I forget", "did I forget", "don't
#: forget" (a request to remember) or "forget to" ("I tend to forget to").
_FORGET_EN = re.compile(
    r"(?:^|[.!?;:,]\s*|\b(?:please|pls|kazma|can you|could you|would you|will you|i want you to"
    r"|i'd like you to|i need you to|just|and|so|now)\s+)forget\b(?!\s+to\b)"
)
#: Taking something out of Kazma's memory, named as such: "delete that from
#: your memory", "erase what you know about my address", "stop remembering",
#: "don't save this".
_ERASE_EN = re.compile(
    r"\b(?:delete|erase|remove|wipe|clear|drop)\b[^.!?\n]{0,60}?"
    r"\b(?:from (?:your |the )?memory|your memor(?:y|ies)|that memory|this memory|the memory"
    r"|what you (?:know|remember|saved|stored|noted))\b"
    r"|\b(?:stop|quit) remembering\b"
    r"|(?<!\bi )\b(?:don't|do not|never) (?:save|store|keep|record) (?:this|that|it)\b"
)
#: The Arabic imperative "forget", as written: "انسى" / "انسَ" / "إنسى" with no
#: hamza on its alef (folded, "أنسى" -- I forget -- reads the same), never
#: after "أنا" / "انا".
_FORGET_AR_RAW = re.compile(
    r"(?<![\w؀-ۿ])(?<!انا )(?<!أنا )[وف]?(?:انسى|انسَ|انسي|إنسى|إنسَ)(?![\w؀-ۿ])"
)
#: Taking something out of Kazma's memory, folded: "امسح من ذاكرتك", "احذف
#: هالمعلومه", "لا تحفظ هذا", "شيلها من بالك"; and "forget" asked politely,
#: "you" forgetting after a modal: "تقدر تنسى", "ممكن تنساه", "أبيك تنسى"
#: (never "لا تنسى", don't forget).
_ERASE_AR = re.compile(
    r"(?<!\w)[وف]?(?:امسح|احذف|شيل|شيلها|شيله)(?!\w)[^.!?؟\n]{0,40}?"
    r"(?:ذاكرتك|الذاكره|ذاكره|بالك|المعلومه|هالمعلومه|الذكري|هالذكري)"
    r"|(?<!\w)(?:لا|ما) (?:تحفظ|تسجل|تتذكر)(?!\w)"
    r"|(?<!\w)(?:تقدر|ممكن|ابيك|ابغاك|اريدك|ودي|تكفي)(?: ان)? تنس(?:ي|اه|اها)(?!\w)"
)


def is_forget_request(text: str | None) -> bool:
    """True when the user's own words in *text* ask Kazma to forget
    something: never a "forget" inside an attached document, a quote or a
    text the user asked Kazma to translate (``memory.own_words``). A request
    asked as a question counts ("Can you forget what I said about Dana?")."""
    return asks_to_forget(own_words(text, questions=True))


def asks_to_forget(text: str | None) -> bool:
    """Whether *text*, as it stands, asks Kazma to forget something."""
    if not text:
        return False
    lowered = " ".join(text.strip().lower().replace("’", "'").split())
    if _FORGET_EN.search(lowered) or _ERASE_EN.search(lowered):
        return True
    if _FORGET_AR_RAW.search(" ".join(text.split())):
        return True
    from kazma_core.documents.arabic import fold_for_search

    return bool(_ERASE_AR.search(fold_for_search(text)))
