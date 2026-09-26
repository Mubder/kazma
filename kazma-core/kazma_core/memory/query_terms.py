"""The content words of a question, for memory's keyword search (Stage 2, R2).

Memory's keyword channel ORed every token of two or more characters,
stopwords included, and fell back to substring LIKE when that found nothing.
So "What is my dog's name?" matched every memory containing "what", "is" or
"my", and "run out" matched "about". Those hits took ranks in the fusion and
outvoted the memory whose meaning matched the question (measured on the
retrieval benchmark, ``memory/benchmark.py``: 25% of paraphrased questions
found their answer).

:func:`content_terms` keeps the words that carry the question: English and
Gulf/Modern-Standard Arabic stopwords dropped, Arabic folded with
``documents.arabic.fold_for_search`` (the one home of Arabic search policy),
possessive "'s", a light English plural and the Arabic definite article
stripped. :func:`coverage` is the share of a question's content words (by
rarity) that a memory's text contains.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping

from kazma_core.documents.arabic import fold_for_search

__all__ = ["content_terms", "coverage", "mentions", "search_terms"]

#: A word: letters and digits, hyphen-joined ("wi-fi", "zx4-91q") -- an
#: underscore separates, as it does in the full-text index, so a slug like
#: "platform_team" is the words "platform" and "team".
_TOKEN = re.compile(r"[^\W_]+(?:-[^\W_]+)*", re.UNICODE)

_EN_STOPWORDS = frozenset(
    """a an the and or but if then so of to in on at by for from with about as into over under
    is are was were be been being am do does did done doing have has had having i me my mine we us
    our ours you your yours he him his she her hers it its they them their theirs this that these
    those there here what which who whom whose when where why how can could should would will
    shall may might must not no yes any some all each every just also too very really much many
    more most less least than please tell know say said get got go going gone let now still yet
    ever again one ones thing things something anything nothing way it's i'm i've i'd isn't don't
    doesn't didn't won't can't what's where's when's who's""".split()
)
_AR_STOPWORDS_RAW = """في من على الى إلى عن مع هذا هذه ذلك تلك التي الذي اللي هو هي هم انا أنا انت أنت
نحن و او أو ثم شنو وش ايش إيش شلون كيف متى وين أين اين ليش لماذا هل ما ماذا كم حق حقي حقك يا لي
لك له لها عند عندي كان كانت يكون تكون بين قبل بعد كل بس شي شيء فيه فيها منه منها اي أي"""
_AR_STOPWORDS = frozenset(fold_for_search(w) for w in _AR_STOPWORDS_RAW.split())
#: Arabic writes the article onto the word, and a conjunction (و ف) and a
#: preposition (ب ك ل) onto that: "وبالقهوة" is "and with the coffee". The
#: article forms are stripped when what is left is still a word; a bare
#: one-letter prefix is not (many words begin with those letters), but
#: :func:`mentions` lets one precede a word of three letters or more.
_AR_ARTICLES = tuple(
    sorted(
        {c + p + "ال" for c in ("", "و", "ف") for p in ("", "ب", "ك")}
        | {c + "لل" for c in ("", "و", "ف")},
        key=len,
        reverse=True,
    )
)
_AR_PROCLITIC = "(?:[وف]?[بك]?ال|[وف]?لل|[وف]?[بكل])?"


def _normalise(token: str) -> str:
    t = token.strip("-_")
    if t.endswith("'s"):
        t = t[:-2]
    for article in _AR_ARTICLES:
        if t.startswith(article) and len(t) - len(article) >= 3:
            t = t[len(article):]  # "القهوة" and "والقهوة" are "قهوة"
            break
    if t.isascii() and len(t) > 3 and t.endswith("s") and not t.endswith(("ss", "us", "is")):
        t = t[:-1]  # a light plural: "vaccines" is "vaccine"
    return t


def content_terms(text: str) -> list[str]:
    """The question's content words, folded and de-duplicated, in order."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in _TOKEN.findall(fold_for_search(text or "")):
        if raw in _EN_STOPWORDS or raw in _AR_STOPWORDS:
            continue
        term = _normalise(raw)
        if len(term) < 2 or term in _EN_STOPWORDS or term in _AR_STOPWORDS or term in seen:
            continue
        seen.add(term)
        out.append(term)
    return out


def search_terms(text: str) -> list[str]:
    """The words a keyword index should be asked for, per content word.

    The question's own spelling (lower-cased), plus its folded and
    normalised form when that differs: memory's full-text index holds the
    text as written, so a folded form alone would miss an unfolded Arabic
    word, and a raw form alone would miss the plural or the article.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in _TOKEN.findall((text or "").lower()):
        folded = fold_for_search(raw)
        if folded in _EN_STOPWORDS or folded in _AR_STOPWORDS:
            continue
        term = _normalise(folded)
        if len(term) < 2 or term in _EN_STOPWORDS or term in _AR_STOPWORDS:
            continue
        for variant in (raw.strip("-_"), term):
            if len(variant) >= 2 and variant not in seen:
                seen.add(variant)
                out.append(variant)
    return out


def mentions(text: str, words: Iterable[str]) -> bool:
    """Whether *text* contains one of *words* at the start of a word.

    SQL ``LIKE '%w%'`` is a substring test ("out" is in "about"): keyword
    fallbacks prefilter with it and confirm here. Prefix-of-word, like the
    FTS query, so "vaccine" still finds "vaccines"; an Arabic word may carry
    the article or a one-letter conjunction or preposition ("اسماء" finds
    "الأسماء", and "وبالقهوة" holds "قهوة").
    """
    long_words = [re.escape(w) for w in words if len(w) >= 3]
    short_words = [re.escape(w) for w in words if 0 < len(w) < 3]
    branches = []
    if long_words:
        branches.append(_AR_PROCLITIC + "(?:" + "|".join(long_words) + ")")
    if short_words:
        branches.append("(?:" + "|".join(short_words) + ")")
    if not branches:
        return False
    pattern = re.compile(r"(?<!\w)(?:" + "|".join(branches) + ")", re.UNICODE)
    raw = (text or "").replace("_", " ")  # a slug's words are words (see _TOKEN)
    return bool(pattern.search(fold_for_search(raw)) or pattern.search(raw.lower()))


def coverage(
    question_terms: Iterable[str],
    text: str,
    *,
    doc_freq: Mapping[str, int] | None = None,
    docs: int = 0,
) -> float:
    """The share of *question_terms* that *text* contains, weighted by rarity.

    With no *doc_freq* every term weighs the same. A rare word ("TP1352",
    "passport") counts for more than a common one ("time").
    """
    terms = list(question_terms)
    if not terms:
        return 0.0
    present = set(content_terms(text))

    def weight(term: str) -> float:
        if not doc_freq or docs <= 0:
            return 1.0
        return math.log((docs + 1) / (doc_freq.get(term, 0) + 1)) + 1.0

    total = sum(weight(t) for t in terms)
    return sum(weight(t) for t in terms if t in present) / total if total else 0.0
