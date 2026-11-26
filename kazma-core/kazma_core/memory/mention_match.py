"""Does a stored text mention what the user asked to forget? One answer.

The memory framework audit's F5 (part D, 2026-11-21). ``forget_mentions``
compared with SQLite's ``LIKE``: ASCII case only, so "Ahmad" missed "AHMAD"
in Arabic-script neighbours, an Arabic word written with a taa marbuta missed
the same word written with a haa, and a word with harakat missed it without.
Here both sides are folded with ``documents.arabic.fold_for_search`` -- the
fold the search indexes use (letter forms, marks, tatweel, digits, case) --
and the folded text is searched for the folded needle.

An id (a fact's subject or predicate, an entity's id: a slug such as
``blue_heron_42``) is matched by whole words: the needle's words, in order,
among the id's (``_`` and every other non-letter separate them), so
"BLUE-HERON-42" finds the entity Kazma named ``blue_heron_42`` and
"diagnosed" the predicate ``diagnosed_with``, while "live" is not
``lives_in``. Kazma's own
ids for the person (``user``, a member's entry) and itself are never a
mention: "forget everything that mentions user" is not "forget every fact
about the user". A person's id is found by their name in either alphabet
("خالد" finds ``khaled``); a text by the letters it is written in: "علي" is a
name and "on".

:func:`register` puts the three tests on a SQLite connection as functions, so
a statement can ask them over every row of a table.
"""

from __future__ import annotations

import functools
import re
import sqlite3
from dataclasses import dataclass

__all__ = ["Needle", "register"]


def _fold(text: object) -> str:
    from kazma_core.documents.arabic import fold_for_search

    return fold_for_search(str(text or ""))


#: What separates the words of an id: anything but a letter, mark or digit
#: (``_`` included).
_ID_WORD_BREAK = re.compile(r"[\W_]+")


def _words(text: str) -> tuple[str, ...]:
    return tuple(w for w in _ID_WORD_BREAK.split(text) if w)


def _slug(text: str) -> str:
    from kazma_core.memory.entity_resolution import slug

    try:
        return slug(text)
    except ValueError:  # a text with no letter or digit has no id
        return ""


@functools.lru_cache(maxsize=32768)
def _holds_the_name(name: str, value: str) -> bool:
    """Whether some words of the id *value*, side by side, are *name* in the
    other alphabet (a name of two words may be three there: "عبدالله")."""
    from kazma_core.memory.name_alphabets import same_name

    if name.isascii() == value.isascii():
        return False
    have, most = [w for w in _ID_WORD_BREAK.split(value) if w], len(name.split()) + 2
    return any(same_name(name, " ".join(have[i:j])) for i in range(len(have))
               for j in range(i + 1, min(len(have), i + most) + 1))


@dataclass(frozen=True)
class Needle:
    """What the user asked to forget, folded once."""

    text: str
    folded: str
    #: Its words as an id (its slug's, folded).
    words: tuple[str, ...]

    @classmethod
    def of(cls, text: str) -> Needle:
        raw = (text or "").strip()
        slug = _slug(raw)
        return cls(raw, _fold(raw), _words(_fold(slug)) if slug else ())

    def in_text(self, value: object) -> bool:
        """True when the text *value* mentions it (folded on both sides)."""
        return bool(self.folded) and value is not None and self.folded in _fold(value)

    def in_id(self, value: object) -> bool:
        """True when the id *value* holds its words, whole and in order."""
        if value is None or not self.words:
            return False
        have = _words(_fold(value))
        n = len(self.words)
        return any(have[i:i + n] == self.words for i in range(len(have) - n + 1))

    def in_name(self, value: object) -> bool:
        """:meth:`in_id`, except for Kazma's own ids for the person and itself
        (``entity_protection.is_core_entity``): those name no one the user
        wrote about."""
        from kazma_core.memory.entity_protection import is_core_entity

        if value is None or is_core_entity(str(value)):
            return False
        return self.in_id(value) or self._names(str(value))

    def _names(self, value: str) -> bool:
        """Whether the id *value* holds the name the user wrote, as the other
        alphabet writes it: "خالد" and the subject ``khaled_alotaibi``, "Khaled"
        and ``خالد`` (``name_alphabets``). Facts told of خالد are filed under
        ``khaled`` where that is who was meant (``belief_extractor._who_is_meant``),
        and are his under either name when he is forgotten."""
        return bool(self.words) and _holds_the_name(" ".join(self.words), value)


def register(conn: sqlite3.Connection, needle: Needle) -> None:
    """``kazma_mentions(text)``, ``kazma_mentions_id(id)`` and
    ``kazma_mentions_name(id)`` on *conn*: 1 when the value mentions
    *needle*, else 0."""
    conn.create_function("kazma_mentions", 1, lambda v: int(needle.in_text(v)), deterministic=True)
    conn.create_function("kazma_mentions_id", 1, lambda v: int(needle.in_id(v)), deterministic=True)
    conn.create_function("kazma_mentions_name", 1, lambda v: int(needle.in_name(v)), deterministic=True)
