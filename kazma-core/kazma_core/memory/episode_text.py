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

__all__ = ["display_text", "embed_text", "is_small_talk", "is_small_talk_words", "match_text"]

#: Characters of each side recall shows the model (five turns fit its budget).
_SHOW_QUESTION = 200
_SHOW_ANSWER = 300
_SHOW_ONE = 500


#: The whole message is small talk (plan W5) -- the words themselves, never a
#: length: "Pixel's age?" is short and a question. Arabic is matched folded
#: (documents.arabic.fold_for_search). Two kinds, told apart by what follows:
#: a greeting, thanks or goodbye is answered in kind -- on the live install
#: such replies ran to 531 characters (median 141, 2026-09-27) -- while "ok",
#: "yes" or "تمام" is often the go-ahead for a report.
_TAIL = r"(?:\W+(?:kazma|there|you|all|a lot|so much|very much|again|جزيلا))*\W*$"
_GREETING_RE = re.compile(
    r"^\W*(?:hi|hello|hey|hiya|yo|thanks|thank you|thx|bye|goodbye"
    r"|good (?:morning|afternoon|evening|night)"
    r"|مرحبا|اهلا|هلا|هلا والله|السلام عليكم|وعليكم السلام|صباح الخير|مساء الخير"
    r"|شكرا|مشكور|يعطيك العافيه|مع السلامه)" + _TAIL,
    re.IGNORECASE,
)
_ACKNOWLEDGEMENT_RE = re.compile(
    r"^\W*(?:ok|okay|got it|noted|sure|yes|yep|no|nope|cool|nice|great|perfect|lol|haha"
    r"|تمام|طيب|اوك|اوكي|ممتاز|حلو|زين)" + _TAIL,
    re.IGNORECASE,
)
#: A reply at least this long says something, whatever the user said.
_GREETING_REPLY = 1000
_ACKNOWLEDGEMENT_REPLY = 200


#: The words small talk is made of, folded: greetings, thanks, goodbyes,
#: acknowledgements, laughter, and the words that come with them ("thank you
#: so much", "صباح الخير", "يعطيك العافية").
_SMALL_TALK_WORDS = frozenset({
    "hi", "hello", "hey", "hiya", "yo", "thanks", "thank", "thx", "ty", "bye", "goodbye", "good", "morning",
    "afternoon", "evening", "night", "ok", "okay", "k", "got", "it", "noted", "sure", "yes", "yep", "yeah",
    "no", "nope", "cool", "nice", "great", "perfect", "awesome", "lol", "kazma", "there", "you", "all", "a",
    "lot", "so", "much", "very", "again", "mrhba", "مرحبا", "اهلا", "هلا", "والله", "السلام", "عليكم",
    "وعليكم", "صباح", "مساء", "الخير", "النور", "شكرا", "مشكور", "مشكوره", "يعطيك", "العافيه", "مع",
    "السلامه", "تمام", "طيب", "اوك", "اوكي", "ممتاز", "حلو", "زين", "جزيلا", "يسلمو", "تسلم", "حبيبي",
})
_LAUGH_OR_PAUSE_RE = re.compile(r"(?:ha){2,}+|(?:he){2,}+|lo+l|hm+|uh+|um+|ه{2,}+")
_WORD_RE = re.compile(r"\w+")


def is_small_talk_words(user_text: str | None) -> bool:
    """Whether every word of *user_text* is small talk ("ok thanks",
    "شكراً", "hmm"), or it has no word at all ("👍"): such a message teaches
    nothing, however long. Decided by its words, never by its length: "I'm
    vegan" and "عندي سكري" are short and are statements."""
    from kazma_core.documents.arabic import fold_for_search

    words = _WORD_RE.findall(fold_for_search(user_text or ""))
    return all(w in _SMALL_TALK_WORDS or _LAUGH_OR_PAUSE_RE.fullmatch(w) for w in words)


def is_small_talk(user_text: str | None, assistant_text: str | None) -> bool:
    """A turn with nothing to recall: small talk and a reply in kind.

    Kept in memory (plan W5 -- nothing is deleted); recall leaves it out of
    the history it shows. "ok" followed by a report is not small talk.
    """
    from kazma_core.documents.arabic import fold_for_search

    said = fold_for_search(user_text or "")
    reply = len(_flat(assistant_text))
    if _GREETING_RE.match(said):
        return reply < _GREETING_REPLY
    return bool(_ACKNOWLEDGEMENT_RE.match(said)) and reply < _ACKNOWLEDGEMENT_REPLY


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
