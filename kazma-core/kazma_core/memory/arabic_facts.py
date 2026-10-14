"""The quick fact pass for Arabic (and Arabic mixed with English).

``belief_extractor.extract_beliefs_heuristic`` read English only: on the
extraction benchmark (Memory audit M24, 2026-10-14) it kept no Arabic fact
at all -- "اسمي ليلى وساكنة في دبي" gave neither the name nor the city, and
the deep pass, which reads Arabic, runs later and only when a model answers.
This module gives the same kinds of fact the English pass keeps -- name,
where the user lives, where they work, a favourite, a preference -- and an
allergy (both languages), read from the user's own words.

The rules come from Arabic grammar, not from example sentences, and refuse
what the English pass refuses by capitalisation, which Arabic does not have:

* the text is split into words, each compared folded
  (``documents.arabic.fold_for_search``: hamza forms, taa marbuta, alef
  maqsura and vowel marks do not matter) with an optional "و"/"ف" prefix;
  the value kept is the user's own spelling;
* a negated statement ("ما أحب", "مو ساكن") keeps nothing;
* a word that is not first person on its own -- "اسمي" (my name, also in
  "نسيت اسمي"), a participle ("ساكن", "شغال": "أخوي ساكن في دبي" is the
  brother) -- counts only where the user's own clause begins: the start of
  a sentence, after "أنا", a greeting, or joined by "و" to what came before;
  a verb without its hamza ("اشتغل", "استخدم") is the imperative the user
  gives Kazma unless "أنا" comes first;
* "أحب/أعشق" keep only a definite noun ("أحب الشاي"), never a verb
  ("أحب أعرف" -- "I'd like to know"); "أفضل" is also "the best", so it
  counts only as "أفضل X على Y";
* "أشتغل في X" is where OR what the user works, like "I work in X", which
  the English pass leaves alone: it counts only before an organisation
  ("شركة", "بنك" ...) or a Latin name; "عند/لدى X" is an employer;
* a place must END there -- end of sentence, a conjunction, a time word --
  so "انتقلت إلى وظيفة جديدة" (a new job) and "ساكن في شقة صغيرة" are not
  places.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["extract_arabic_facts"]

#: A word, or one punctuation mark of its own.
_TOKEN_RE = re.compile(r"[^\s.,!?،؛؟:;()\"«»]+|[.,!?،؛؟:;()]")
_PUNCT = set(".,!?،؛؟:;()")
_ARABIC_LETTER = re.compile(r"[ء-يٱ-ۓ]")
_LATIN_NAME = re.compile(r"^[A-Z][A-Za-z0-9&'.-]*$")
_LATIN_WORD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9&'.+-]*$")

# Every word list is written folded.
_NEGATIONS = {"ما", "لا", "مو", "مب", "مش", "ماني", "لم", "لن", "ليس", "لست", "مافي"}
_NAME_TRIGGERS = {"اسمي", "ناديني", "نادني"}
_NAME_SKIP = {"الكامل", "الاول", "الحقيقي", "المستعار", "هو", "هي"}
_NAME_HEADS = {"عبد", "ابو", "ام", "بن", "بنت", "ال"}
_HOME_TRIGGERS = {"ساكن", "ساكنه", "ساكنين", "اسكن", "نسكن", "اعيش", "نعيش", "عايش", "عايشه",
                  "عايشين", "مقيم", "مقيمه", "اقيم", "نقيم"}
_MOVE_TRIGGERS = {"انتقلت", "انتقلنا"}
_IN = {"في", "فى"}
_TO = {"الي"}
_PLACE_HEADS = {"ابو", "ام", "راس", "مدينه", "شرم", "خميس", "حفر", "جزيره"}
#: Words that end a place or a value: time, company, cause.
_ENDERS = {"الشهر", "السنه", "الاسبوع", "العام", "امس", "قبل", "من", "منذ", "هذا", "هذي", "هذه",
           "الحين", "الان", "مؤخرا", "اليوم", "مع", "حاليا", "بعد", "لان", "عشان", "بس", "لكن",
           "علي", "كل", "دايم", "دايما", "دائما", "كثير", "وايد", "جدا", "مره", "اذا", "لما", "يعني"}
_WORK_TRIGGERS = {"اشتغل", "نشتغل", "اعمل", "نعمل", "شغال", "شغاله", "اداوم", "موظف", "موظفه"}
#: Words that are first person only where the user's clause begins.
_PARTICIPLES = {"ساكن", "ساكنه", "ساكنين", "عايش", "عايشه", "عايشين", "مقيم", "مقيمه",
                "شغال", "شغاله", "موظف", "موظفه"}
#: Verbs that read as an imperative without their hamza ("اشتغل على الملف").
_HAMZA_VERBS = {"اشتغل", "اعمل", "استخدم", "استعمل"}
#: Words after which the user's own clause begins.
_OPENERS = {"انا", "وانا", "احنا", "نحن", "هلا", "مرحبا", "اهلا", "هاي", "السلام", "عليكم", "طيب",
            "اوكي", "حاليا", "الحين", "الان"}
_EMPLOYER_PREPS = {"عند", "لدي"}
_ORG_HEADS = {"شركه", "بنك", "مصرف", "مؤسسه", "مستشفي", "جامعه", "وزاره", "مدرسه", "مكتب", "هيئه",
              "مصنع", "مجموعه", "بلديه", "جمعيه", "معهد", "كليه", "مطعم", "فندق"}
_FAVOURITE = {"المفضل", "المفضله"}
_MINE = {"عندي", "لي", "لدي"}
#: A favourite's kind, folded, as the English pass names it ("favorite_city").
_KINDS = {
    "رياضه": "sport", "لون": "color", "اكله": "food", "اكل": "food", "طبخه": "dish", "وجبه": "meal",
    "مشروب": "drink", "قهوه": "coffee", "فيلم": "movie", "كتاب": "book", "كاتب": "author",
    "شاعر": "poet", "مدينه": "city", "دوله": "country", "بلد": "country", "فريق": "team",
    "نادي": "team", "لاعب": "player", "مسلسل": "series", "فنان": "artist", "مطرب": "singer",
    "اغنيه": "song", "رقم": "number", "حيوان": "animal", "سياره": "car", "مطعم": "restaurant",
    "هوايه": "hobby", "لعبه": "game", "فصل": "season", "موسم": "season", "يوم": "day",
}
_LIKE_TRIGGERS = {"احب", "اعشق"}
_USE_TRIGGERS = {"استخدم", "استعمل"}
_I = {"انا", "وانا", "احنا", "نحن"}
_HAVE = {"عندي", "لدي"}
_ALLERGY = {"حساسيه"}
_ALLERGY_PREPS = {"من", "ضد", "علي", "تجاه"}


class _Word:
    __slots__ = ("text", "folded", "bare")

    def __init__(self, text: str, folded: str) -> None:
        self.text = text
        self.folded = folded
        # Without a leading "and"/"so" ("وساكنة" -> "ساكنه").
        self.bare = folded[1:] if len(folded) > 3 and folded[0] in "وف" else folded

    @property
    def punct(self) -> bool:
        return self.text in _PUNCT

    @property
    def arabic(self) -> bool:
        return bool(_ARABIC_LETTER.search(self.text))


def _words(text: str) -> list[_Word]:
    from kazma_core.documents.arabic import fold_for_search

    return [_Word(t, fold_for_search(t)) for t in _TOKEN_RE.findall(text)]


def _is(word: _Word, names: set[str]) -> bool:
    return word.folded in names or word.bare in names


def _negated(words: list[_Word], i: int) -> bool:
    return i > 0 and words[i - 1].folded in _NEGATIONS


def _own_clause(words: list[_Word], i: int) -> bool:
    """Whether word *i* begins the user's own clause."""
    w = words[i]
    return i == 0 or words[i - 1].punct or words[i - 1].folded in _OPENERS or w.bare != w.folded


def _first_person(words: list[_Word], i: int) -> bool:
    """Whether word *i*, a trigger, speaks of the user."""
    w = words[i]
    if w.bare in _PARTICIPLES or w.folded in _NAME_TRIGGERS:
        return _own_clause(words, i)
    if w.bare in _HAMZA_VERBS:
        return w.text.lstrip("وف").startswith("أ") or (i > 0 and words[i - 1].folded in _I)
    return True


def _ends(words: list[_Word], i: int) -> bool:
    """Whether a value may end before word *i*."""
    if i >= len(words):
        return True
    w = words[i]
    return w.punct or w.folded in _ENDERS or (w.arabic and w.folded[0] in "وف" and len(w.folded) > 3)


def _latin_run(words: list[_Word], i: int, pattern: re.Pattern[str] = _LATIN_NAME) -> tuple[str, int]:
    """Up to three Latin words from *i* ("Kuwait City"); "" when none."""
    j = i
    while j < len(words) and j - i < 3 and pattern.match(words[j].text):
        j += 1
    return " ".join(w.text for w in words[i:j]), j


def _place(words: list[_Word], i: int) -> str:
    """A place starting at *i* that ends there, or ""."""
    if i >= len(words):
        return ""
    latin, j = _latin_run(words, i)
    if latin:
        return latin if _ends(words, j) else ""
    first = words[i]
    if first.punct or not first.arabic:
        return ""
    j = i + 1
    if first.folded in _PLACE_HEADS and j < len(words) and words[j].arabic and not words[j].punct:
        j += 1
    return " ".join(w.text for w in words[i:j]) if _ends(words, j) else ""


def _phrase(words: list[_Word], i: int, *, first_definite: bool) -> str:
    """Up to three Arabic words from *i*, stopping where a value ends."""
    if i >= len(words):
        return ""
    first = words[i]
    if first.punct or not first.arabic or (first_definite and not first.folded.startswith("ال")):
        return ""
    j = i + 1
    while j < len(words) and j - i < 3 and words[j].arabic and not _ends(words, j):
        j += 1
    return " ".join(w.text for w in words[i:j])


def _name(words: list[_Word], i: int) -> str:
    while i < len(words) and words[i].folded in _NAME_SKIP:
        i += 1
    if i >= len(words):
        return ""
    latin, _ = _latin_run(words, i)
    if latin:
        return latin
    first = words[i]
    if first.punct or not first.arabic or first.folded in _ENDERS | _IN | _TO | _MINE:
        return ""
    if first.folded in _NAME_HEADS and i + 1 < len(words) and words[i + 1].arabic and not words[i + 1].punct:
        return f"{first.text} {words[i + 1].text}"
    return first.text


def _employer(words: list[_Word], i: int) -> str:
    """Where the user works, from the word after the verb."""
    if i >= len(words):
        return ""
    prep = words[i]
    if prep.folded in _IN | _EMPLOYER_PREPS or prep.text.lower() in {"at", "for"}:
        latin, _ = _latin_run(words, i + 1)
        if latin:
            return latin
    if prep.folded not in _IN | _EMPLOYER_PREPS or i + 1 >= len(words):
        return ""
    head = words[i + 1]
    if head.folded in _ORG_HEADS:
        latin, _ = _latin_run(words, i + 2)
        if latin:
            return f"{head.text} {latin}"
        name = words[i + 2] if i + 2 < len(words) else None
        if name is None or name.punct or not name.arabic:
            return ""
        # The name goes on while one side is definite: "الملك فهد",
        # "الكويت الوطني", "أرامكو السعودية" -- not "أرامكو مهندسة" (a job).
        parts = [head.text, name.text]
        j = i + 3
        while (j < len(words) and len(parts) < 4 and words[j].arabic and not _ends(words, j)
               and (words[j - 1].folded.startswith("ال") or words[j].folded.startswith("ال"))):
            parts.append(words[j].text)
            j += 1
        return " ".join(parts)
    if prep.folded in _EMPLOYER_PREPS and head.arabic and not head.punct:
        return head.text
    return ""


def _kind(word: str) -> str:
    """The favourite's kind from "رياضتي" (my sport) or "اللون" (the colour)."""
    stem = word[2:] if word.startswith("ال") else word[:-1] if word.endswith("ي") else ""
    if stem.endswith("ت"):
        stem = stem[:-1] + "ه"  # رياضتي -> رياضه
    return _KINDS.get(stem, "")


def _fact(predicate: str, value: str, ptype: str, confidence: float, importance: int) -> dict[str, Any]:
    return {"subject": "user", "predicate": predicate, "predicate_type": ptype,
            "object": value.strip(), "confidence": confidence, "importance": importance}


def extract_arabic_facts(text: str) -> list[dict[str, Any]]:
    """The facts an Arabic (or mixed) message states about its writer.

    One fact per kind, in the shape ``extract_beliefs_heuristic`` returns.
    """
    if not text or not _ARABIC_LETTER.search(text):
        return []
    words = _words(text)
    found: dict[str, dict[str, Any]] = {}

    def keep(fact: dict[str, Any]) -> None:
        if fact["object"]:
            found.setdefault(fact["predicate"], fact)

    for i, w in enumerate(words):
        if w.punct or _negated(words, i) or not _first_person(words, i):
            continue
        nxt = words[i + 1] if i + 1 < len(words) else None
        if _is(w, _NAME_TRIGGERS):
            keep(_fact("name_is", _name(words, i + 1), "functional", 0.9, 5))
        elif _is(w, _HOME_TRIGGERS) and nxt is not None and nxt.folded in _IN:
            keep(_fact("lives_in", _place(words, i + 2), "functional", 0.85, 4))
        elif _is(w, _MOVE_TRIGGERS) and nxt is not None and nxt.folded in _TO:
            place = _place(words, i + 2)
            if place and _ARABIC_LETTER.search(place):  # "انتقلنا إلى Slack" is a tool
                keep(_fact("lives_in", place, "functional", 0.8, 4))
        elif _is(w, _WORK_TRIGGERS):
            keep(_fact("works_at", _employer(words, i + 1), "functional", 0.85, 4))
        elif nxt is not None and nxt.folded in _FAVOURITE:
            stem = w.folded if _kind(w.folded) else w.bare  # "فريقي" keeps its "ف"
            kind = _kind(stem)
            if not kind:
                continue
            j = i + 2
            if stem.startswith("ال"):
                if j >= len(words) or words[j].folded not in _MINE:
                    continue  # "the favourite colour" with no "mine"
                j += 1
            while j < len(words) and words[j].folded in {"هو", "هي"}:
                j += 1
            value = _phrase(words, j, first_definite=False) or _latin_run(words, j, _LATIN_WORD)[0]
            keep(_fact(f"favorite_{kind}", value, "functional", 0.8, 4))
        elif _is(w, _LIKE_TRIGGERS | _USE_TRIGGERS) or w.bare == "افضل":
            value = _phrase(words, i + 1, first_definite=True) or _latin_run(words, i + 1, _LATIN_WORD)[0]
            if w.bare == "افضل":
                end = i + 1 + len(value.split()) if value else i + 1
                if not value or end >= len(words) or words[end].folded != "علي":
                    continue  # "أفضل الطرق" is "the best ways"
            keep(_fact("prefers", value, "set", 0.7, 3))
        elif w.folded in _HAVE and nxt is not None and nxt.folded in _ALLERGY:
            j = i + 2
            if j < len(words) and words[j].folded in _ALLERGY_PREPS:
                j += 1
            value = _phrase(words, j, first_definite=False) or _latin_run(words, j, _LATIN_WORD)[0]
            if value.startswith("لل"):
                value = "ال" + value[2:]  # "حساسية للفول" -> "الفول"
            keep(_fact("allergic_to", value, "set", 0.85, 5))
    return list(found.values())
