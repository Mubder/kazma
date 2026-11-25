"""What the user typed, found in memory's own text: one answer (memory plan W6).

The lists of memory -- the agent's ``memory_list``, ``memory_list_beliefs``
and ``memory_list_entities``, the Memory page's facts, memories and things --
each searched in its own way, and each compared the asked words with the text
as it was written: SQLite's ``LIKE`` on one hand, on the other the full-text
index, which folds no Arabic letter. ``memory_list`` folded the asked word and
looked for it in the unfolded text, so an Arabic word ending in a taa marbuta
did not find itself; the page found a word only in the spelling it was stored
in. Measured on 2026-11-25, eight memories in each language: English 8 of 8,
Arabic 0 (``memory_list``, the same spelling) and 0 (the page, the other usual
spelling).

Here both sides are folded with ``documents.arabic.fold_for_search`` and
compared word by word, as recall reads a question (``query_terms``): a word's
article, one-letter prefix, possessive or light plural is no difference, nor
is the Arabic "my" at its end ("سيارتي", "سيارة"). An Arabic dual ("سيارتين"),
a broken plural and "our" at a typed word's end ("سيارتنا") are other words.
Only a word that may be the one asked for is folded (a text of 50,000 turns
took 24 seconds folded whole, for "gpt5").
:func:`register` puts the tests on a SQLite connection as functions, so a
statement asks them over the rows it already narrowed to: no index, nothing
stored. A list whose full-text index answered asks them beside it (``OR``), so
what the index finds is still found.

Asked through :func:`holds`, which hands the columns over as bytes:

* ``kazma_asked_every(text, ...)``: the texts together hold every content word.
* ``kazma_asked_any(text, ...)``: they hold one of them.
* ``kazma_asked_phrase(text)``: the whole query is in the text, letter for
  letter once folded (a query of one letter or a sign has no words).
* ``kazma_folded(text)``: the folded text, to compare a name with.
* ``kazma_alias_names(json)``: an entity's aliases as text (the column is JSON,
  some of it with its Arabic escaped).
"""

from __future__ import annotations

import functools
import os
import re
import sqlite3
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = ["Asked", "fold", "holds", "register"]

#: The most words of a query that are looked for, as the memories panel asks
#: its index; a list that wants every word asks fewer (``of``'s *words*).
_WORDS_MAX = 12

#: The longest a word is looked for: no word is longer, and a pattern of a
#: thousand letters cost four milliseconds a row.
_WORD_CHARS = 80


def fold(text: object) -> str:
    """*text* as it is compared: ``fold_for_search``'s answer (an ASCII text
    has nothing to fold but its case and its spaces)."""
    raw = str(text or "")
    if raw.isascii():
        return " ".join(raw.split()).lower()
    from kazma_core.documents.arabic import fold_for_search

    return fold_for_search(raw)


def _span(*spans: tuple[int, int]) -> str:
    """The code point ranges *spans* as the inside of a character class
    (numbers here: two of them are direction marks, never source text)."""
    return "".join(f"{chr(first)}-{chr(last)}" for first, last in spans)


#: Characters outside the Arabic block, or of two letters in one, that fold to
#: an Arabic letter or a digit (NFKC): presentation forms, high-hamza letters,
#: raised, circled, fullwidth and mathematical forms. The word holding one is
#: folded whatever it looks like; ``tests/test_memory_lists_any_spelling.py`` holds this list
#: to every character there is.
_COMPAT = re.compile("[" + _span(
    (0xB2, 0xB3), (0xB9, 0xB9), (0xBC, 0xBE), (0x675, 0x678), (0x2070, 0x209C), (0x2150, 0x2189), (0x2460, 0x24FF),
    (0x3200, 0x33FF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF), (0xFF10, 0xFF19), (0x1CCF0, 0x1CCF9), (0x1D7CE, 0x1D7FF),
    (0x1EE00, 0x1EEFF), (0x1F100, 0x1F10A), (0x1FBF0, 0x1FBF9)) + "]")

#: What the fold drops between two letters: the marks, the stretch and the
#: direction marks.
_DROPPED = "[" + _span(
    (0x610, 0x61A), (0x61C, 0x61C), (0x640, 0x640), (0x64B, 0x65F), (0x670, 0x670), (0x6D6, 0x6ED),
    (0x200E, 0x200F), (0x202A, 0x202E), (0x2066, 0x2069)) + "]*"


@functools.lru_cache(maxsize=1)
def _spellings() -> dict[str, str]:
    """A folded Arabic letter, or a digit -> every character of the Arabic
    block (and the digit itself) that folds to it, read from the fold."""
    from kazma_core.documents.arabic import fold_for_search

    out: dict[str, str] = {}
    for code in (*range(0x30, 0x3A), *range(0x620, 0x700)):
        folded = fold_for_search(chr(code))
        if len(folded) == 1 and folded.isalnum():
            out[folded] = out.get(folded, "") + chr(code)
    return out


def _folds(term: str) -> bool:
    """Whether a text may hold *term* in another spelling than it is written
    in: an Arabic word, or a number. A Latin word, with digits or a hyphen in
    it or not, is in the index and in the text as it is written."""
    return any(ch.isalpha() and not ch.isascii() and ch in _spellings() for ch in term) or not any(
        ch.isalpha() for ch in term)


def _look(term: str, forms: Sequence[str]) -> re.Pattern[str]:
    """What an unfolded text that holds *term*, or one of its *forms*, holds
    somewhere: the letters they all begin with, in any spelling. Every text is
    looked at; only a word around a find is folded."""
    if not _folds(term):
        return re.compile(re.escape(term), re.IGNORECASE)
    begin = os.path.commonprefix([f[:-1] if f.endswith((_TAA, _MY)) and len(f) > 2 else f for f in forms]) or term[:1]
    spellings = _spellings()
    return re.compile(_DROPPED.join(f"[{spellings[ch]}]" if ch in spellings else re.escape(ch) for ch in begin),
                      re.IGNORECASE)


#: A taa marbuta, folded; what it is written as before whose it is; "my"; and
#: the endings that say whose it is.
_TAA, _OPEN_TAA, _MY = "ه", "ت", "ي"
_WHOSE = "(?:هما|كما|نا|كم|هم|ها|ي|ك|ه)"


def _forms(written: str, term: str) -> list[str]:
    """The folded *term*, and, when it was *written* with the ending "my", the
    word without it: "سيارتي" is "سياره", "بيتي" is "بيت", "مستشفاي" is
    "مستشفى". "منى" and "مستشفى" end in no "my"."""
    from kazma_core.memory.query_terms import content_terms

    if term.isascii() or len(term) < 3 or not written.endswith((_MY, "ی")) or not term.endswith(_MY):
        return [term]
    bare = term[:-1]
    if bare.endswith("ا") and len(bare) >= 4:  # an alef maqsura before "my"
        return [term, bare[:-1] + _MY]
    without = [bare, bare[:-1] + _TAA] if bare.endswith(_OPEN_TAA) and len(bare) >= 3 else [bare]
    # A word that says nothing alone ("من") is not what was asked for.
    return [term, *(f for f in without if content_terms(f))]


def _word_pattern(term: str, forms: Sequence[str], *, whole: bool) -> re.Pattern[str]:
    """``query_terms.mentions``' rule for one word: at the start of a word, an
    Arabic word of three letters or more with its article or a one-letter
    prefix before it, and -- either side -- with "my" at its end ("سيارتي" and
    "سيارة"). *whole*: to the word's end (a light plural or a possessive
    aside), not only its start; a word "my" was taken off is always whole."""
    from kazma_core.memory.query_terms import _AR_PROCLITIC

    def one(form: str) -> str:
        if form.isascii():
            return re.escape(form) + (r"(?:'?s)?(?!\w)" if whole else "")
        before = _AR_PROCLITIC if len(form) >= 3 else ""
        if form.endswith(_TAA) and len(form) >= 3:
            body = re.escape(form[:-1]) + f"(?:{_TAA}|{_OPEN_TAA}{_WHOSE})"
        elif form.endswith(_MY) and len(form) >= 4:
            body = re.escape(form[:-1]) + f"(?:{_MY}|ا{_MY})"
        else:
            body = re.escape(form) + f"{_MY}?" if whole or form != term else re.escape(form)
        return before + body + (r"(?!\w)" if whole or form != term else "")

    return re.compile(r"(?<!\w)(?:" + "|".join(one(f) for f in forms) + ")", re.UNICODE)


_fold_word = functools.lru_cache(maxsize=32768)(fold)


def _text(value: object) -> str:
    """*value* as text. A row's columns come as bytes (:func:`holds`): one
    stored with bytes that are no text is read as far as it is text, and does
    not end the search."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value or "")


@dataclass(frozen=True)
class _Word:
    look: re.Pattern[str]
    pattern: re.Pattern[str]
    folds: bool


@dataclass(frozen=True)
class Asked:
    """What was typed, read once: the folded whole and its content words."""

    phrase: str
    terms: tuple[str, ...]
    words: tuple[_Word, ...]
    #: The words typed as one ("user_memory_system"), by their place: together
    #: they are one thing that is asked for.
    together: tuple[tuple[int, ...], ...]

    @classmethod
    def of(cls, query: str, *, whole: bool = False, words: int = _WORDS_MAX) -> Asked:
        """*whole*: a word is found as a whole word (a list that answers
        with any of the words), else from its start (a list that wants all).
        *words*: the most words that are looked for."""
        from kazma_core.memory.query_terms import _TOKEN, content_terms

        terms: list[str] = []
        read: list[_Word] = []
        together: list[tuple[int, ...]] = []
        for piece in (query or "").split():
            here: list[int] = []
            for written in _TOKEN.findall("".join(ch for ch in piece if unicodedata.category(ch) != "Mn" and ch != "ـ")):
                for term in content_terms(written):
                    term = term[:_WORD_CHARS]
                    if term not in terms:
                        if len(terms) >= words:
                            continue
                        forms = _forms(written, term)
                        terms.append(term)
                        read.append(_Word(_look(term, forms), _word_pattern(term, forms, whole=whole), _folds(term)))
                    here.append(terms.index(term))
            if "_" in piece and len(here) > 1:
                together.append(tuple(here))
            else:
                together += [(n,) for n in here]
        return cls(fold(query), tuple(terms), tuple(read), tuple(dict.fromkeys(together)))

    @property
    def needs_folding(self) -> bool:
        """Whether a text may hold a word in another spelling than the index
        keeps (:func:`_folds`)."""
        return any(w.folds for w in self.words)

    def _held(self, parts: tuple[object, ...]) -> list[bool]:
        """Which of the words the texts *parts* hold."""
        raw = " ".join(_text(p) for p in parts if p)
        compat: list[str] | None = None
        flat = ""
        held: list[bool] = []
        for word in self.words:
            found, at = False, 0
            if word.look.search(raw):
                flat = flat or " ".join(raw.split())
                while not found and (m := word.look.search(flat, at)):
                    start = flat.rfind(" ", 0, m.start()) + 1
                    at = flat.find(" ", m.end())
                    at = len(flat) if at < 0 else at
                    found = word.pattern.search(_folded(flat[start:at])) is not None
            if not found and word.folds:
                if compat is None:
                    compat = [_folded(c) for c in raw.split() if _COMPAT.search(c)] if _COMPAT.search(raw) else []
                found = any(word.pattern.search(text) for text in compat)
            held.append(found)
        return held

    def every(self, *parts: object) -> bool:
        """The texts *parts* together hold every content word (and there is one)."""
        return bool(self.words) and all(self._held(parts))

    def any(self, *parts: object) -> bool:
        """They hold one of the things asked for: a word, or every word of
        one typed together."""
        if not self.words:
            return False
        held = self._held(parts)
        return any(all(held[n] for n in group) for group in self.together)

    def as_written(self, value: object) -> bool:
        """The whole query is in *value*, both folded."""
        return bool(self.phrase) and value is not None and self.phrase in fold(_text(value))


def _folded(chunk: str) -> str:
    """One run of a text without a space in it, folded; a slug's words are
    words (an underscore separates them)."""
    words = chunk.replace("_", " ")
    return _fold_word(words) if len(words) <= _WORD_CHARS else fold(words)


def _alias_names(raw: object) -> str:
    from kazma_core.memory.self_hub import parse_aliases

    return " ".join(parse_aliases(_text(raw)))


#: SQL: an entity's aliases as text (the outer row's name is ``e``).
ALIAS_NAMES = "kazma_alias_names(CAST(e.aliases_json AS BLOB))"


def holds(test: str, *columns: str) -> str:
    """SQL: the function ``kazma_asked_`` + *test* over *columns*, as bytes (a
    column SQLite cannot hand over as text would fail the whole statement).
    The columns are the caller's own SQL, never what was typed."""
    return f"kazma_asked_{test}({', '.join(f'CAST({c} AS BLOB)' for c in columns)})"


def register(conn: sqlite3.Connection, asked: Asked) -> None:
    """The functions of the module's docstring on *conn*, for *asked*."""
    conn.create_function("kazma_asked_every", -1, lambda *v: int(asked.every(*v)), deterministic=True)
    conn.create_function("kazma_asked_any", -1, lambda *v: int(asked.any(*v)), deterministic=True)
    conn.create_function("kazma_asked_phrase", 1, lambda v: int(asked.as_written(v)), deterministic=True)
    conn.create_function("kazma_folded", 1, lambda v: fold(_text(v)), deterministic=True)
    conn.create_function("kazma_alias_names", 1, _alias_names, deterministic=True)
