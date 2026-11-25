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
article, one-letter prefix, possessive or light plural is no difference.
:func:`register` puts the tests on a SQLite connection as functions, so a
statement asks them over the rows it already narrowed to: no index, nothing
stored. A list whose full-text index answered asks them beside it (``OR``), so
what the index finds is still found.

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
import re
import sqlite3
from dataclasses import dataclass

__all__ = ["Asked", "fold", "register"]

#: The most words of a query that are looked for: a list that wants every word
#: would find less with more of them.
_WORDS_MAX = 8


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
#: raised, circled, fullwidth and mathematical forms. A text holding one is
#: folded whole; ``tests/test_memory_lists_any_spelling.py`` holds this list
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


def _may_hold(term: str) -> re.Pattern[str] | None:
    """What a text that holds the folded *term* holds somewhere as written:
    its letters in any spelling. None for a term with a Latin letter (more
    spellings than one block holds). Most texts hold none of it, and looking
    costs a tenth of folding a long turn."""
    spellings = _spellings()
    if not all(ch in spellings for ch in term):
        return None
    return re.compile(_DROPPED.join(f"[{spellings[ch]}]" for ch in term))


def _word_pattern(term: str, *, whole: bool) -> re.Pattern[str]:
    """``query_terms.mentions``' rule for one word: at the start of a word, an
    Arabic word of three letters or more with its article or a one-letter
    prefix before it. *whole*: to the word's end (a light plural or a
    possessive aside), not only its start."""
    from kazma_core.memory.query_terms import _AR_PROCLITIC

    before = _AR_PROCLITIC if len(term) >= 3 else ""
    end = r"(?:'?s)?(?!\w)" if whole and term.isascii() else r"(?!\w)" if whole else ""
    return re.compile(r"(?<!\w)" + before + re.escape(term) + end, re.UNICODE)


@dataclass(frozen=True)
class Asked:
    """What was typed, read once: the folded whole and its content words."""

    phrase: str
    terms: tuple[str, ...]
    patterns: tuple[re.Pattern[str], ...]
    #: Per word, what a text must hold unfolded to be worth folding.
    looks: tuple[re.Pattern[str] | None, ...]

    @classmethod
    def of(cls, query: str, *, whole: bool = False) -> Asked:
        """*whole*: a word is found as a whole word (a list that answers
        with any of the words), else from its start (a list that wants all)."""
        from kazma_core.memory.query_terms import content_terms

        terms = tuple(content_terms(query or "")[:_WORDS_MAX])
        return cls(fold(query), terms, tuple(_word_pattern(t, whole=whole) for t in terms),
                   tuple(_may_hold(t) for t in terms))

    @property
    def needs_folding(self) -> bool:
        """Whether a text may hold a word in another spelling than the index
        keeps: not when every word is plain English letters."""
        return any(not (t.isascii() and t.isalpha()) for t in self.terms)

    def _found(self, parts: tuple[object, ...], *, every: bool) -> bool:
        raw = " ".join(str(p) for p in parts if p)
        if not self.terms or not raw:
            return False
        if not _COMPAT.search(raw):
            possible = [look is None or look.search(raw) is not None for look in self.looks]
            if not (all(possible) if every else any(possible)):
                return False
        # A slug's words are words: an underscore separates them.
        text = fold(raw.replace("_", " "))
        found = (p.search(text) is not None for p in self.patterns)
        return all(found) if every else any(found)

    def every(self, *parts: object) -> bool:
        """The texts *parts* together hold every content word (and there is one)."""
        return self._found(parts, every=True)

    def any(self, *parts: object) -> bool:
        """They hold one of the content words."""
        return self._found(parts, every=False)

    def as_written(self, value: object) -> bool:
        """The whole query is in *value*, both folded."""
        return bool(self.phrase) and value is not None and self.phrase in fold(value)


def _alias_names(raw: object) -> str:
    from kazma_core.memory.self_hub import parse_aliases

    return " ".join(parse_aliases(raw))


def register(conn: sqlite3.Connection, asked: Asked) -> None:
    """The functions of the module's docstring on *conn*, for *asked*."""
    conn.create_function("kazma_asked_every", -1, lambda *v: int(asked.every(*v)), deterministic=True)
    conn.create_function("kazma_asked_any", -1, lambda *v: int(asked.any(*v)), deterministic=True)
    conn.create_function("kazma_asked_phrase", 1, lambda v: int(asked.as_written(v)), deterministic=True)
    conn.create_function("kazma_folded", 1, fold, deterministic=True)
    conn.create_function("kazma_alias_names", 1, _alias_names, deterministic=True)
