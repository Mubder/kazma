"""What shape a question has, read with no model (framework audit F9, part F).

Two shapes change what recall must do:

* **A follow-up** (:func:`is_follow_up`): "And what does she teach?", "Where
  is her practice?", "ووين يشتغل؟", "Yes, book it for Saturday at 10." What
  it is about is in the message before it. Recall searched it as typed, so
  "And her sister?" found nothing (``graph_supervisor`` passed the raw
  text). :func:`standalone_query` reads it with the user's message before
  it (decision 14: the rewrite with no model first; a model rewrite and a
  reranker are measured later, as options). The same test finds a stored
  turn that is a follow-up, so recall can show it beside the turn it
  follows (``recall._with_neighbours``): stored as typed, its vector is made
  from "Yes, book it for Saturday at 10." and no question reaches it.
* **A request for every memory** (:func:`asks_for_all`): "How many long runs
  did I do? List them all", "List every note", "كم مرة", "عطني كل". Recall
  showed at most five facts and five turns, so a count or a list could not be
  answered from it (F9); such a question gets a longer, compact list, and
  ``memory_list`` lists the rest.

Linear patterns only: bounded alternations of fixed words.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from kazma_core.memory.limits import FOLLOW_UP_EARLIER_CHARS

__all__ = ["asks_for_all", "earlier_user_texts", "is_follow_up", "standalone_query", "turns_in_context"]

#: A message that opens on one of these goes on from the one before it.
_OPENERS_EN = re.compile(
    r"^\W*(?:and|but|so|also|then|or|what about|how about|and what|what else|"
    r"yes|yeah|yep|yup|no|nope|ok|okay|sure|fine|alright|right|great|perfect|"
    r"go ahead|do it|please do)\b",
    re.IGNORECASE,
)
#: Arabic openers, folded: "و" written onto the first word ("ووين", "وشنو",
#: "وأي", "ومتى", "وكم"), or a word that goes on from what was said.
_OPENERS_AR = re.compile(
    r"^\W*(?:و(?:وين|شنو|ش|اي|متى|كم|منو|ليش|شلون|كيف|هل|بعدين|بعد)|طيب|زين|اوكي|اوك|خلاص|ايوه|"
    r"اي نعم|نعم|لا|يعني|بعدين|عيل|اجل|تمام|ماشي|يلا)(?=\s|$|[؟?.,،!])"
)
#: A word that points back at something said before.
_ANAPHORS_EN = re.compile(
    r"\b(?:she|her|hers|he|him|his|it|its|they|them|their|that|those|this one|that one|"
    r"the first one|the second one|the other one|there)\b",
    re.IGNORECASE,
)
_ANAPHORS_AR = re.compile(
    r"(?:^|\s)(?:هو|هي|هم|هذا|هذي|هذه|ذاك|ذيك|هناك|هناك|الاول|الثاني|الثانيه)(?=\s|$|[؟?.,،!])"
)
#: A follow-up is short: past this many content words a message says what
#: it is about.
_OPENER_MAX_TERMS = 5
_ANAPHOR_MAX_TERMS = 3
_FOLLOW_UP_MAX_CHARS = 160


def is_follow_up(text: str | None) -> bool:
    """Whether *text* goes on from the message before it: it opens on a
    connective or an answer ("and", "yes", "و", "خلاص") with few content
    words, or points back ("her", "it", "هي") with fewer. Small talk alone
    ("ok", "thanks") is not: it is about nothing."""
    from kazma_core.documents.arabic import fold_for_search
    from kazma_core.memory.episode_text import is_small_talk_words
    from kazma_core.memory.query_terms import content_terms

    said = " ".join(str(text or "").split())
    if not said or len(said) > _FOLLOW_UP_MAX_CHARS or is_small_talk_words(said):
        return False
    folded = fold_for_search(said).lower()
    terms = len(content_terms(said))
    if (_OPENERS_EN.search(said) or _OPENERS_AR.search(folded)) and terms <= _OPENER_MAX_TERMS:
        return True
    return bool(_ANAPHORS_EN.search(said) or _ANAPHORS_AR.search(folded)) and terms <= _ANAPHOR_MAX_TERMS


def _bounded(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= FOLLOW_UP_EARLIER_CHARS else flat[-FOLLOW_UP_EARLIER_CHARS:]


def standalone_query(question: str, earlier: Sequence[str] | None) -> str:
    """*question* as recall should search it: unchanged unless it is a
    follow-up and the user said something before it (*earlier*, oldest
    first); then the user's message before it (and the one before that,
    while those are follow-ups too), then the question. Bounded by
    ``limits.FOLLOW_UP_EARLIER_CHARS`` each."""
    q = str(question or "")
    if not earlier or not is_follow_up(q):
        return q
    from kazma_core.memory.episode_text import is_small_talk_words

    kept: list[str] = []
    for text in reversed([str(t or "") for t in earlier]):
        if not text.strip() or is_small_talk_words(text):
            continue
        kept.append(_bounded(text))
        if not is_follow_up(text) or len(kept) >= 2:
            break
    if not kept:
        return q
    return " ".join([*reversed(kept), q])


_ALL_EN = re.compile(
    r"\b(?:how many|list\b|lists\b|all (?:the|my|of|your|our|his|her|their|those|these)\b|"
    r"every\s+(?:time|one|single|\w+)|everything|each (?:time|one|of)|all of them|all the times)",
    re.IGNORECASE,
)
_ALL_AR = re.compile(
    r"(?:^|\s)(?:كم مره|كم مرة|كل\s+(?:ال|ا)\S+|كلها|كلهم|جميع|اذكر لي|اذكرلي|عدد|قائمه|قائمة|لستة|ليست)"
    r"(?=\s|$|[؟?.,،!])"
)


def asks_for_all(question: str | None) -> bool:
    """Whether *question* asks for every memory of a kind, or how many there
    are: "how many", "list", "all my", "every", "كم مرة", "عطني كل". Not
    "how often": a frequency is one fact ("How often do I exercise?")."""
    from kazma_core.documents.arabic import fold_for_search

    said = " ".join(str(question or "").split())
    if not said:
        return False
    return bool(_ALL_EN.search(said) or _ALL_AR.search(fold_for_search(said).lower()))


def _role_and_text(message: Any) -> tuple[str, str]:
    from kazma_core.memory.consolidator import message_text

    if isinstance(message, dict):
        return str(message.get("role") or ""), message_text(message.get("content"))
    role = str(getattr(message, "type", "") or getattr(message, "role", "") or "")
    return ("user" if role == "human" else role), message_text(getattr(message, "content", ""))


def earlier_user_texts(messages: Sequence[Any] | None, *, limit: int = 3) -> list[str]:
    """The user's messages before the latest one, oldest first, at most
    *limit*: what a follow-up goes on from (:func:`standalone_query`)."""
    said = [text for role, text in map(_role_and_text, messages or []) if role == "user" and text]
    return said[:-1][-limit:] if limit > 0 else said[:-1]


def turns_in_context(messages: Sequence[Any] | None, *, max_tokens: int) -> frozenset[str]:
    """``limits.turn_key`` of each user message the model reads in the
    conversation itself: the newest messages within *max_tokens* (four
    characters a token, the trim's own estimate), the latest message left
    out (it is not a memory yet). Recall leaves the chat's own memories of
    them out of its block (part F: they were boosted into it a second time)."""
    from kazma_core.memory.limits import turn_key

    budget = max(0, int(max_tokens)) * 4
    kept: set[str] = set()
    seen_latest = False
    for role, text in map(_role_and_text, reversed(list(messages or []))):
        budget -= len(text)
        if budget < 0:
            break
        if role == "user" and text:
            if not seen_latest:
                seen_latest = True
                continue
            kept.add(turn_key(text))
    return frozenset(kept)
