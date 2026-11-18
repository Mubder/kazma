"""Is the user asking Kazma to remember something? In English or in Arabic.

A turn that says "remember this" goes straight to the recall tier
(``dual_write.episode_row``) and counts as a durable cue for fact extraction
(``belief_extractor.is_filler_turn``). Both read only English phrases until
2026-09-27 (plan W4): "تذكر أن اجتماعي يوم الثلاثاء" was treated like small
talk.

Arabic is compared folded (``documents.arabic.fold_for_search``: harakat,
hamza forms, taa marbuta and alef maqsura do not matter) and as whole words,
with an optional "and"/"so" prefix: "تذكرة" (a ticket) is not "تذكر".
"""

from __future__ import annotations

import re

from kazma_core.memory.own_words import own_words

__all__ = ["is_remember_request"]

#: English, as whole words (Memory audit M26: as substrings "Can you denote
#: that" matched "note that"). "Do you remember that film?" asks Kazma to
#: recall, not to remember: a phrase right after "do/did you" is a question.
_ENGLISH = (
    "remember that",
    "remember my",
    "remember this",
    "remember:",
    "don't forget",
    "do not forget",
    "note that",
)
_ENGLISH_RE = re.compile(
    r"(?<![\w'])(?<!do you )(?<!did you )(?:" + "|".join(re.escape(p) for p in _ENGLISH) + r")(?![\w'])"
)

#: Arabic (MSA and Gulf), written in folded form.
_ARABIC = (
    "تذكر",  # remember
    "لا تنسي",  # don't forget (تنسى)
    "لا تنس",
    "احفظ",  # keep / save this
    "سجل عندك",  # note it down
    "خلك فاكر",  # keep in mind
    "خليك فاكر",
    "خل في بالك",
    "خله في بالك",
    "خلها في بالك",
    "حط في بالك",
    "خذ في بالك",
)
_ARABIC_RE = re.compile(
    r"(?<!\w)[وف]?(?:" + "|".join(re.escape(p) for p in _ARABIC) + r")(?!\w)"
)


def is_remember_request(text: str | None) -> bool:
    """True when the user's own words in *text* ask Kazma to remember
    something: never a "remember" inside an attached document, a quote or a
    text the user asked Kazma to translate (``memory.own_words``)."""
    return asks_to_remember(own_words(text))


def asks_to_remember(text: str | None) -> bool:
    """Whether *text*, as it stands, asks Kazma to remember something.
    ``own_words`` asks it of the words before a quote ("remember this:
    "..."": the quote is then the user's own statement); everything else
    asks :func:`is_remember_request`."""
    if not text:
        return False
    lowered = " ".join(text.strip().lower().replace("\u2019", "'").split())
    if _ENGLISH_RE.search(lowered):
        return True
    from kazma_core.documents.arabic import fold_for_search

    return bool(_ARABIC_RE.search(fold_for_search(text)))
