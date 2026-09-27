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

__all__ = ["is_remember_request"]

#: English, as they always were (substring, lower case).
_ENGLISH = (
    "remember that",
    "remember my",
    "remember this",
    "don't forget",
    "do not forget",
    "note that",
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
    """True when *text* asks Kazma to remember something."""
    if not text:
        return False
    lowered = text.strip().lower()
    if any(phrase in lowered for phrase in _ENGLISH):
        return True
    from kazma_core.documents.arabic import fold_for_search

    return bool(_ARABIC_RE.search(fold_for_search(text)))
