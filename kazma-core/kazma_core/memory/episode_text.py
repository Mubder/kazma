"""What an episode says: the text its meaning vector is made from, and what recall shows.

One home for both, used by every episode writer (``dual_write.episode_row``,
which the golden eval and the retrieval benchmark build their rows with, and
``swarm_bridge``), the vector repair and rebuild (``reembed``), and recall's
display.

**Shown: the question AND the answer.** Recall showed "the summary, else the
question, else the answer" -- with a question present, the model saw what the
user had asked and never what it had answered, so a recalled "what did you
suggest?" came back without the suggestion (plan R3, 2026-09-27).

**Embedded: the question** (the summary, else the question, else the
answer), as before. Embedding the answer too was measured on the retrieval
benchmark before it was adopted, and not adopted: with every threshold
re-swept, precision fell in four categories (fact 0.71 -> 0.65, multi 0.89 ->
0.78, noise 0.61 -> 0.57, paraphrase 0.66 -> 0.65) and the questions answered
only in the assistant text gained nothing -- their words are in the answer,
which keyword search and word coverage already read. Live answers run to
thousands of characters and would pull a vector further from what the turn
was about than the benchmark's short ones. Word coverage and the keyword
index read all three fields.
"""

from __future__ import annotations

import re

__all__ = ["display_text", "embed_text", "is_small_talk", "match_text"]

#: Characters of each side recall shows the model (five turns fit its budget).
_SHOW_QUESTION = 200
_SHOW_ANSWER = 300
_SHOW_ONE = 500


#: A greeting, thanks or acknowledgement -- the whole message (plan W5). The
#: words themselves, not a length: "Pixel's age?" is short and a question.
#: Arabic is matched folded (documents.arabic.fold_for_search).
_SMALL_TALK_RE = re.compile(
    r"^\W*(?:(?:hi|hello|hey|hiya|yo|thanks|thank you|thx|ok|okay|got it|noted|sure|yes|yep|no|nope"
    r"|cool|nice|great|perfect|lol|haha|bye|good (?:morning|afternoon|evening|night)"
    r"|مرحبا|اهلا|هلا|هلا والله|السلام عليكم|وعليكم السلام|شكرا|مشكور|يعطيك العافيه|تمام"
    r"|طيب|اوك|اوكي|ممتاز|حلو|زين)(?:\W+(?:kazma|there|you|a lot|so much|very much|جزيلا))*)\W*$",
    re.IGNORECASE,
)
#: A reply longer than this says something, whatever the user said.
_SMALL_TALK_REPLY = 200


def is_small_talk(user_text: str | None, assistant_text: str | None) -> bool:
    """A turn with nothing to recall: small talk and a short reply.

    Kept in memory (plan W5 -- nothing is deleted); recall leaves it out of
    the history it shows. "ok" followed by a long report is not small talk.
    """
    from kazma_core.documents.arabic import fold_for_search

    if len(_flat(assistant_text)) >= _SMALL_TALK_REPLY:
        return False
    return bool(_SMALL_TALK_RE.match(fold_for_search(user_text or "")))


def _flat(text: str | None) -> str:
    return " ".join((text or "").split())


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def embed_text(user_text: str | None, assistant_text: str | None,
               summary_text: str | None = "") -> str:
    """The text an episode's meaning vector is made from: the first of the
    summary, the question and the answer that is not blank."""
    return next((t.strip() for t in (summary_text, user_text, assistant_text)
                 if t and t.strip()), "")


def match_text(user_text: str | None, assistant_text: str | None,
               summary_text: str | None = "") -> str:
    """What recall compares an episode by -- de-duplication, graph-walk seeds:
    the summary, else the question, else the answer, as before R3.

    The two-sided :func:`display_text` is only shown. Used for these too, it
    changed which memories recall kept -- one question asked twice with two
    answers stopped counting as one -- and precision fell on the benchmark.
    """
    return str(summary_text or user_text or assistant_text or "")[:400]


def display_text(user_text: str | None, assistant_text: str | None,
                 summary_text: str | None = "") -> str:
    """What recall shows of an episode: who said what, bounded.

    Labelled only when both sides are there -- a swarm result keeps its
    whole text in one field and must not read as something the user said.
    """
    question, answer = _flat(user_text), _flat(assistant_text)
    if question and answer:
        return f"User: {_clip(question, _SHOW_QUESTION)} / Assistant: {_clip(answer, _SHOW_ANSWER)}"
    return _clip(question or answer or _flat(summary_text), _SHOW_ONE)
