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


#: The phrases small talk is made of, folded, each WHOLE: greetings, thanks,
#: goodbyes, acknowledgements, the go-aheads and answers a person replies
#: with ("thank you", "صباح الخير", "يعطيك العافية", "go ahead", "ان شاء
#: الله", "I'm good"). A message is small talk only when it is nothing but
#: these, the words that may follow one (``_SMALL_TALK_TAILS``), laughter and
#: pauses. Judged by whole phrases, never by single words: made of small-talk
#: WORDS, "I am a man", "I am Sure", "I'm Kazma" and "no it will do" were
#: filler, and their facts never reached the deep pass (the security review
#: of F1's second half, 2026-11-18).
_SMALL_TALK_PHRASES = frozenset({
    "hi", "hello", "hey", "hiya", "yo", "thanks", "thank you", "thx", "ty", "bye", "goodbye", "bye bye",
    "good morning", "good afternoon", "good evening", "good night", "gm", "gn", "see you", "see ya",
    "take care", "ok", "okay", "k", "kk", "ok then", "got it", "noted", "sure", "sure thing", "of course",
    "for sure", "yes", "yes please", "yep", "yup", "yeah", "no", "no thanks", "no thank you", "nope", "nah",
    "np", "no problem", "no worries", "cool", "nice", "great", "perfect", "awesome", "fine", "all good",
    "good", "very good", "very nice", "good job", "well done", "lol", "lmao", "brb", "nvm", "please",
    "go ahead", "go on", "go for it", "proceed", "continue", "done", "do it", "will do", "sounds good",
    "sounds great", "that s it", "thats it", "that s all", "thats all", "i m good", "im good", "i am good",
    "i m fine", "im fine", "i am fine", "i m ok", "im ok", "i am ok", "i m okay", "i am okay",
    "mrhba", "مرحبا", "اهلا", "اهلا وسهلا", "هلا", "هلا والله", "السلام عليكم", "وعليكم السلام",
    "صباح الخير", "مساء الخير", "صباح النور", "مساء النور", "شكرا", "شكرا جزيلا", "مشكور", "مشكوره",
    "يعطيك العافيه", "الله يعطيك العافيه", "الله يعافيك", "يعافيك", "مع السلامه", "تمام", "طيب", "اوك",
    "اوكي", "ممتاز", "حلو", "زين", "يسلمو", "تسلم", "تسلم ايدك", "نعم", "لا", "لا شكرا", "اي", "ايوه",
    "ايه", "كمل", "كملي", "يلا", "خلاص", "تم", "ماشي", "صح", "الحمدلله", "الحمد لله", "ان شاء الله",
    "حياك", "حياك الله", "ابشر", "انا زين", "انا بخير", "بخير", "اي والله", "والله",
})
#: Words that may follow a phrase, never stand alone: "thanks man", "thank
#: you so much", "شكرا حبيبي". Alone, "man" or "you" says something.
_SMALL_TALK_TAILS = frozenset({
    "kazma", "there", "you", "all", "a lot", "so much", "very much", "again", "bro", "man", "buddy",
    "dude", "so", "very", "too", "then", "now", "حبيبي", "جزيلا", "والله", "كثير", "مره", "يالغالي",
})
_PHRASE_WORDS = 4  # the longest phrase or tail, in words
_LAUGH_OR_PAUSE_RE = re.compile(r"(?:ha){2,}+|(?:he){2,}+|lo+l|hm+|uh+|um+|ه{2,}+")
_WORD_RE = re.compile(r"\w+")


def is_small_talk_words(user_text: str | None) -> bool:
    """Whether *user_text* is nothing but small talk ("ok thanks", "thank you
    so much", "شكراً", "hmm"), or has no word at all ("👍"): such a message
    teaches nothing. Read phrase by phrase, the longest first, a tail only
    after a phrase; never by its length ("I'm vegan" and "عندي سكري" are short
    and are statements) and never word by word ("I am a man")."""
    from kazma_core.documents.arabic import fold_for_search

    words = _WORD_RE.findall(fold_for_search(user_text or "").lower())
    i, after_phrase = 0, False
    while i < len(words):
        if _LAUGH_OR_PAUSE_RE.fullmatch(words[i]):
            i += 1
            continue
        for size in range(min(_PHRASE_WORDS, len(words) - i), 0, -1):
            piece = " ".join(words[i:i + size])
            if piece in _SMALL_TALK_PHRASES or (after_phrase and piece in _SMALL_TALK_TAILS):
                i += size
                after_phrase = True
                break
        else:
            return False
    return True


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
