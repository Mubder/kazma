"""The past-chats search finds a word however it is spelled, and checks every
chat that may hold it before it keeps the best (25 Nov 2026).

The question was folded (``documents.arabic``) and the stored text was not, so
"القهوة" did not find "القهوة" -- 14 of 21 spellings missed. And the store
returned its 15 best rows by substring before any was checked: 20 chats with
"resize" hid the one that said "my shoe size is 43".
"""

from __future__ import annotations

import json
import logging
import random
import re
import secrets
import sqlite3
import time
import unicodedata
from collections.abc import Iterator

import pytest
from kazma_core.documents import arabic
from kazma_core.memory import chat_history, transcript_recall
from kazma_core.memory.transcript_recall import search_transcripts

from tests._as_request import in_space

#: (what it is, the question, what the chat says)
SPELLINGS = [
    ("ta marbuta", "القهوة", "القهوة"),
    ("ta marbuta, no article", "قهوة", "قهوة"),
    ("ha asks ta marbuta", "قهوه", "قهوة"),
    ("ta marbuta asks ha", "قهوة", "قهوه"),
    ("hamza above", "أحمد", "أحمد"),
    ("bare asks hamza", "احمد", "أحمد"),
    ("hamza asks bare", "أحمد", "احمد"),
    ("hamza below", "إيجار", "إيجار"),
    ("madda", "آسيا", "آسيا"),
    ("wasla", "استقلال", "ٱستقلال"),
    ("alef maqsura", "مستشفى", "مستشفى"),
    ("ya asks maqsura", "مستشفي", "مستشفى"),
    ("waw hamza", "مؤتمر", "مؤتمر"),
    ("ya hamza", "طائرة", "طائرة"),
    ("two-character hamza", "مؤتمر", "مو\u0654تمر"),
    ("tatweel in the chat", "مبارك", "مبـــارك"),
    ("tatweel in the question", "مبـارك", "مبارك"),
    ("marks in the chat", "كتاب", "كِتَاب"),
    ("marks in the question", "كِتَاب", "كتاب"),
    ("a bidi mark inside", "كتاب", "كتا\u200fب"),
    ("Arabic digits asked", "٢٠٢٦", "2026"),
    ("Arabic digits in the chat", "2026", "٢٠٢٦"),
    ("eastern digits in the chat", "2026", "۲۰۲۶"),
    ("Farsi kaf asked", "کتاب", "كتاب"),
    ("Farsi kaf in the chat", "كتاب", "کتاب"),
    ("lam-alef in one character", "سلام", "سﻼم"),
    ("presentation forms", "بيت", "ﺑﻴﺖ"),
    ("the article and a preposition", "قهوة", "وبالقهوة"),
    ("an accent in capitals", "café", "CAFÉ"),
    ("Cyrillic in capitals", "привет", "ПРИВЕТ"),
    ("the Greek final sigma", "οδος", "η οδος"),
    ("the Greek final sigma, asked in capitals", "ΟΔΟΣ", "η οδος"),
    ("plain Arabic", "بيت", "بيت"),
    ("plain English", "passport", "Passport"),
]


@pytest.fixture()
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    return tmp_path


class Chats:
    """Saved as the web UI saves them, in a tenant of its own: the Postgres
    job's table is shared."""

    def __init__(self, data) -> None:
        self.path = str(data / "chat_sessions.db")
        self.tenant = f"t{secrets.token_hex(6)}"

    def save(self, session_id: str, *said: str, title: str = "", rows=None) -> None:
        from kazma_ui.session_manager import ChatSession, SessionManager

        store = SessionManager(db_path=self.path)
        try:
            store.put(ChatSession(
                session_id=f"{self.tenant}-{session_id}", tenant_id=self.tenant, title=title,
                thread_id=f"th-{self.tenant}-{session_id}",
                messages=rows or [{"role": "user", "content": s} for s in said],
            ))
        finally:
            store.close()

    def found(self, question: str, **kwargs) -> list[str]:
        return [h["session_id"].removeprefix(f"{self.tenant}-")
                for h in search_transcripts(question, tenant_id=self.tenant, **kwargs)]


@pytest.fixture()
def chats(data) -> Iterator[Chats]:
    made = Chats(data)
    with in_space(made.tenant):  # saved and searched in a turn of that tenant, as the chat page and the tool are
        yield made


# ── Every spelling ────────────────────────────────────────────────────────


@pytest.mark.postgres
@pytest.mark.parametrize(("asked", "said"), [s[1:] for s in SPELLINGS], ids=[s[0] for s in SPELLINGS])
def test_a_word_is_found_in_any_spelling(chats, asked, said):
    chats.save("other", "عن البحر والمطر, and nothing else 1999")
    chats.save("it", f"قال {said} امس", title="note")
    assert chats.found(asked) == ["it"]


@pytest.mark.postgres
def test_a_spelling_in_the_title_is_found_and_ranks_first(chats):
    chats.save("text", "كتبت عن القهوة", title="t")
    chats.save("title", "شي ثاني", title="القَهوة")
    assert chats.found("قهوه") == ["title", "text"]


def test_a_spooled_chat_is_found_in_any_spelling(chats):
    from kazma_ui.session_spool import SessionSpool, spool_path_for

    spool = SessionSpool(spool_path_for(chats.path))
    try:
        for name, _asked, said in SPELLINGS:
            spool.save(chats.tenant, name, {"session_id": name, "thread_id": f"th-{name}", "title": "",
                                            "messages": [{"role": "user", "content": f"قال {said} امس"}]})
    finally:
        spool.close()
    for name, asked, _said in SPELLINGS:
        assert name in [h["session_id"] for h in search_transcripts(asked, tenant_id=chats.tenant, limit=40)], name


@pytest.mark.postgres
def test_a_word_is_not_found_inside_another(chats):
    """The fold's confirmation holds for Arabic as for English."""
    chats.save("inside", "مكتبات كثيرة, sizeable")
    assert chats.found("تبات") == []
    assert chats.found("كتاب") == []
    assert chats.found("able") == []
    assert chats.found("مكتبات") == ["inside"]  # control


def test_the_snippet_shows_the_match(chats):
    filler = "word " * 500
    chats.save("it", f"{filler} في بيت مبـــارك أول امس {filler}")
    (hit,) = search_transcripts("مبارك", tenant_id=chats.tenant)
    assert "مبـــارك" in hit["snippet"]
    assert len(hit["snippet"]) < 600


def test_a_long_chat_shows_its_match_too(chats):
    """Past 200,000 characters the snippet was the title."""
    chats.save("it", "filler text here " * 15000 + "the falcon is in Wafra", title="t")
    (hit,) = search_transcripts("falcon", tenant_id=chats.tenant)
    assert "the falcon is in Wafra" in hit["snippet"]


def test_a_word_after_a_line_break_is_found(chats):
    """Stored messages are JSON: the break before the word is ``\\n``, and its
    letter n joined the word."""
    chats.save("it", "first line\nfalcon colours\tكتاب")
    assert chats.found("falcon") == ["it"]
    assert chats.found("كتاب") == ["it"]
    (hit,) = search_transcripts("falcon", tenant_id=chats.tenant)
    assert "first line\nfalcon" in hit["snippet"]


def test_a_path_keeps_its_folders_names(chats):
    chats.save("it", "look in C:\\new\\falcon")
    assert chats.found("falcon") == ["it"]
    assert chats.found("new folder falcon C") == ["it"]


# ── The pattern is the fold's ─────────────────────────────────────────────

_LETTERS = [chr(c) for c in range(0x621, 0x64B) if chr(c).isalpha()] + list("ٱکیھپچگ")
_EXTRA = ["", "", "ـ", "\u064e", "\u0651", "\u0654", "\u200f", "\u0670"]
_FORMS = [chr(c) for c in range(0xFE80, 0xFEFD)]


def _matches(term: str, text: str) -> bool:
    return re.search(arabic.spelling_pattern(term), text) is not None


def test_the_pattern_matches_exactly_what_folds_to_the_word():
    rng = random.Random(7)
    agreed = 0
    for _ in range(4000):
        word = "".join(rng.choice(_LETTERS) for _ in range(rng.randint(2, 6)))
        if rng.random() < 0.5:  # the same word, written otherwise
            text = "".join(rng.choice([c for c in (*_LETTERS, *_FORMS)
                                       if arabic.fold_for_search(c) == arabic.fold_for_search(w)] or [w])
                           + rng.choice(_EXTRA) for w in word)
        else:
            text = "".join(rng.choice(_LETTERS + _FORMS) + rng.choice(_EXTRA) for _ in range(len(word)))
        folds = arabic.fold_for_search(word) in arabic.fold_for_search(text)
        assert _matches(word, text) == folds, (word, text)
        agreed += folds
    assert 1000 < agreed < 3900, agreed  # both answers were tried


def test_every_character_that_folds_to_a_letter_is_in_its_pattern():
    seen = 0
    for point in (*range(0x600, 0x700), *range(0x750, 0x780), *range(0x8A0, 0x900), *range(0xFB50, 0xFF00)):
        char = chr(point)
        inside = arabic.fold_for_search(f"ب{char}ب")[1:-1]
        if len(inside) == 1 and inside.isalnum():
            assert _matches(inside, char), hex(point)
            seen += char != inside
        elif not inside:
            assert _matches("بب", f"ب{char}ب"), hex(point)
    assert seen > 200


def test_the_pattern_is_built_from_the_fold_itself(monkeypatch):
    """Take a class out of the fold's table and the pattern loses it too."""
    assert _matches("قهوه", "قهوة")
    monkeypatch.delitem(arabic._SEARCH_TRANSLATE, ord("ة"))
    arabic._spellings.cache_clear()
    arabic.spelling_pattern.cache_clear()
    try:
        assert not _matches("قهوه", "قهوة")
    finally:
        monkeypatch.undo()
        arabic._spellings.cache_clear()
        arabic.spelling_pattern.cache_clear()
    assert _matches("قهوه", "قهوة")


def test_the_pattern_reads_what_a_regex_would():
    assert arabic.spelling_pattern("") == ""
    assert _matches("الله", "ﷲ") and _matches("عبدالله", "عبدﷲ") and not _matches("الله", "لله")
    for term in ("wi-fi", "a.b", "c++", "[x]", "50%", "x^2"):
        assert _matches(term, f"the {term.upper()} one")
    assert not _matches("a.b", "acb")


def test_the_search_is_linear_on_one_long_word(chats):
    """Marks repeat between any two letters: no pattern of them backtracks."""
    chats.save("it", "ك" + "\u064e" * 200_000 + " " + "كـ" * 100_000)
    started = time.monotonic()
    assert chats.found("كتاب") == []
    assert time.monotonic() - started < 20


# ── Checked before the cut ────────────────────────────────────────────────


@pytest.mark.postgres
def test_chats_that_only_share_letters_do_not_hide_the_one(chats):
    for i in range(20):
        chats.save(f"d{i}", "resize resize resize horseshoe", title="resize horseshoe")
    chats.save("real", "my shoe size is 43")
    assert chats.found("What is my shoe size?", limit=1) == ["real"]


def test_forgotten_kept_out_and_copied_chats_do_not_hide_the_one(chats, monkeypatch):
    from kazma_core.memory import forget
    from kazma_core.spaces import threads

    for i in range(16):
        chats.save(f"c{i}", "tax form tax form tax form", title="tax form")
        chats.save(f"k{i}", "tax form tax form tax form", title="tax form")
    chats.save("real", "the tax form is in the blue folder")
    copied = {f"th-{chats.tenant}-c{i}": time.time() + 60 for i in range(16)}
    monkeypatch.setattr(threads, "copied_threads", lambda: copied)
    monkeypatch.setattr(forget, "forgotten_in_chat",
                        lambda _t, keys: ("-k" in str(list(keys)), set()))
    assert chats.found("tax form") == ["real"]


@pytest.mark.postgres
def test_the_best_is_still_first_and_the_cut_still_holds(chats):
    for i in range(40):
        chats.save(f"s{i:02d}", "a saffron latte")
    chats.save("best", "saffron and saffron", title="saffron")
    found = chats.found("saffron")
    assert found[0] == "best" and len(found) == 3


@pytest.mark.postgres
def test_few_texts_are_held_at_once_and_none_to_rank(chats, monkeypatch):
    for i in range(50):
        chats.save(f"d{i}", "a resize")
    chats.save("real", "the size")
    asked: list[int] = []
    real = chat_history._texts_of
    monkeypatch.setattr(chat_history, "_texts_of",
                        lambda ids, *a: asked.append(len(ids)) or real(ids, *a))
    assert chats.found("size") == ["real"]
    assert sum(asked) == 51 and max(asked) <= 15
    asked.clear()
    for i in range(50):
        chats.save(f"z{i}", "size", title="size")
    assert len(chats.found("size")) == 3
    assert asked == [15], "enough were found in the first texts"


def test_the_search_cut_before_its_checks_hid_it(chats, monkeypatch):
    """Control: the old order, 15 rows and then the checks."""
    real = chat_history._ranked
    monkeypatch.setattr(chat_history, "_ranked", lambda *a: real(*a)[:15])
    for i in range(20):
        chats.save(f"d{i}", "resize resize resize horseshoe", title="resize horseshoe")
    chats.save("real", "my shoe size is 43")
    assert chats.found("What is my shoe size?", limit=1) == []


# ── What is not conversation ──────────────────────────────────────────────


@pytest.mark.postgres
def test_a_system_row_is_not_searched(chats):
    """``/compact`` on the web stored the model's own prompt, "About me" and
    recalled memory as ``system`` rows until 25 Nov 2026: they stay, unread."""
    chats.save("old", rows=[{"role": "system", "content": "About me: the falcon is called Barq"},
                            {"role": "user", "content": "the system of the boat"}])
    assert chats.found("falcon Barq") == []
    assert chats.found("boat") == ["old"]
    (hit,) = search_transcripts("boat", tenant_id=chats.tenant)
    assert "falcon" not in hit["snippet"]


def test_a_chat_whose_words_are_all_forgotten_is_refused(chats, monkeypatch):
    from kazma_core.memory import forget

    chats.save("it", rows=[{"role": "user", "content": "the falcon is called Barq"},
                           {"role": "assistant", "content": "noted"},
                           {"role": "user", "content": "the boat"}], title="t")
    assert chats.found("falcon Barq") == ["it"]
    monkeypatch.setattr(forget, "forgotten_in_chat", lambda _t, _k: (False, {(1, "")}))
    assert chats.found("falcon Barq") == []
    assert chats.found("boat") == ["it"]


def test_reading_the_json_reads_only_its_escapes():
    stored = json.dumps([{"content": 'a "b"\n\\n C:\\new\té'}], ensure_ascii=False)
    assert 'a "b"\n\\n C:\\new\té' in transcript_recall._readable(stored)


# ── What only the fold reads (review M1, 25 Nov 2026) ─────────────────────

#: (what it is, the question, what the chat says): found before the spelling
#: pattern, and missed by it alone.
ONLY_THE_FOLD = [
    ("a decomposed accent", "café reservation", "the cafe\u0301 reservation is at 8"),
    ("a decomposed accent asked", "cafe\u0301 reservation", "the café reservation is at 8"),
    ("the ffi ligature", "office lease", "the oﬃce lease ends in May"),
    ("the fi ligature", "file backup", "the ﬁle backup ran"),
    ("full-width digits", "invoice 2026", "invoice ２０２６ is paid"),
    ("full-width letters", "kazma invoice", "ｋａｚｍａ invoice"),
    ("sharp s", "strasse parking", "Straße parking is free"),
    ("sharp s asked", "Straße parking", "strasse parking is free"),
    ("the final sigma", "οδοσ χαρτησ", "η οδος εχει χαρτης χαρτης"),
    ("the final sigma, capitals", "ΟΔΟΣ αθήνα", "οδος αθήνα"),
    ("half-width katakana", "カメラ batteries", "ｶﾒﾗ batteries"),
    ("a voiced half-width katakana", "ガラス window", "ｶﾞﾗｽ window"),
    ("decomposed Hangul", "한글 keyboard", "한글 keyboard"),
    ("the prayer ligature", "صلى محمد", "محمد ﷺ"),
    ("the name ligature", "محمد رسول", "ﷴ رسول"),
    ("a superscript", "x2 formula", "x² formula"),
    ("styled letters", "kazma formula", "𝐤𝐚𝐳𝐦𝐚 formula"),
]


@pytest.mark.postgres
@pytest.mark.parametrize(("asked", "said"), [s[1:] for s in ONLY_THE_FOLD], ids=[s[0] for s in ONLY_THE_FOLD])
def test_what_only_the_fold_reads_is_found(chats, asked, said):
    chats.save("other", "عن البحر والمطر, reservation lease backup only")
    chats.save("it", said, title="note")
    chats.save("wide", "ﬁ ß ２ only odd letters")  # control: an odd letter alone is no hit
    assert chats.found(asked) == ["it"]
    (hit,) = search_transcripts(asked, tenant_id=chats.tenant)
    assert hit["matched"]


def test_what_only_the_fold_reads_is_found_in_a_title_and_a_spooled_chat(chats):
    from kazma_ui.session_spool import SessionSpool, spool_path_for

    chats.save("title", "the lease ends in May", title="oﬃce")
    spool = SessionSpool(spool_path_for(chats.path))
    try:
        spool.save(chats.tenant, "spooled", {"session_id": "spooled", "thread_id": "th", "title": "",
                                             "messages": [{"role": "user", "content": "the oﬃce lease"}]})
    finally:
        spool.close()
    assert sorted(chats.found("office lease")) == ["spooled", "title"]


@pytest.mark.postgres
def test_the_score_alone_orders_the_hits(chats):
    """A chat that holds a word only the fold reads is not behind those whose
    words all have a spelling."""
    chats.save("plain", "the office lease", title="note")
    chats.save("best", "the oﬃce lease, the lease, the lease", title="lease")
    assert chats.found("office lease") == ["best", "plain"]
    assert chats.found("office lease", limit=1) == ["best"]


def test_a_chat_is_read_as_the_fold_reads_it():
    """The pattern and the folded words together answer as folding the whole
    text does, for letters of every script and every kind."""
    from kazma_core.memory.query_terms import content_terms, mentions, search_terms

    rng = random.Random(11)
    anything = [c for c in map(chr, range(0x21, 0x30000)) if unicodedata.category(c)[0] in "LNM"]
    odd = [*"ßςﬁﬃ２ｶﾞﷺﷴİſ™½²ǆＡـآأئﻻﷲ…’", "\u0301", "\u0327", "\u0651", "\u064e", "\u0654", "\u200f", "\u00ad", "\u00a0"]
    agreed = 0
    for _ in range(12_000):
        words = []
        for _word in range(rng.randint(1, 3)):
            plain = rng.choice(["abcefgiostu0123", "ابتةهىيولمك", anything])
            words.append("".join(rng.choice(odd if rng.random() < 0.25 else plain) for _ in range(rng.randint(2, 6))))
        text, word = " ".join(words), rng.choice(words)
        for term in content_terms(rng.choice([word, word.upper(), unicodedata.normalize("NFD", word), "abc"]))[:2]:
            folds = mentions(text, search_terms(term) or [term])
            assert (term in transcript_recall._present([term], text)) == folds, (ascii(term), ascii(text))
            agreed += folds
    assert agreed > 5000


def test_the_letters_only_the_fold_reads_are_asked_of_it(monkeypatch):
    """Control: with that class empty, what the pattern alone reads."""
    assert chat_history._has_folded_otherwise("oﬃce") and not chat_history._has_folded_otherwise("office … → 😀", "قهوة")
    monkeypatch.setattr(transcript_recall, "_otherwise", lambda: re.compile("(?!)"))
    assert transcript_recall._present(["office"], "the oﬃce") == {}
    monkeypatch.undo()
    assert transcript_recall._present(["office"], "the oﬃce") == {"office": 4}


# ── Long words and many chats (review M2, L2, L5) ─────────────────────────


@pytest.mark.postgres
@pytest.mark.parametrize("length", [65, 10_000, 70_000])
def test_a_long_word_is_found_and_costs_nothing(chats, length):
    token = "ab12cd34" * (length // 8) + "é"
    chats.save("it", f"the key is {token} ok")
    chats.save("other", "nothing here")
    started = time.monotonic()
    arabic.spelling_pattern.cache_clear()
    assert chats.found(token) == ["it"]
    assert chats.found(f"{token}x") == []
    assert time.monotonic() - started < 10
    assert arabic.spelling_pattern.cache_info().currsize == 0, "no pattern was made of it"


def test_the_longest_word_with_spellings(chats):
    word = "ق" + "هوة" * 21  # 64
    assert len(word) == chat_history.LONGEST_SPELLED
    chats.save("it", f"قال {word} امس")
    assert chats.found(word.replace("ة", "ه")) == ["it"]


def test_one_letter_many_times_over(chats):
    chats.save("it", "ا" * 2_000_000)
    started = time.monotonic()
    assert chats.found("ا" * 200) == ["it"]
    chats.found("ا" * 60 + "ب")
    assert time.monotonic() - started < 20


def test_a_search_reads_a_bounded_number_of_texts(chats, monkeypatch, caplog):
    monkeypatch.setattr(chat_history, "_TEXTS_READ", 20)
    for i in range(30):
        chats.save(f"d{i:02d}", "a resize")
    asked: list[int] = []
    real = chat_history._texts_of
    monkeypatch.setattr(chat_history, "_texts_of", lambda ids, *a: asked.append(len(ids)) or real(ids, *a))
    with caplog.at_level(logging.INFO, logger="kazma_core.memory.chat_history"):
        assert chats.found("size") == []
    assert asked == [15, 5]
    assert len([r for r in caplog.records if "20 best of 30" in r.getMessage()]) == 1
    # Equal ones are read newest first, and the better one before them.
    chats.save("new", "the size")
    chats.save("z-best", "resize size size", title="size")
    assert chats.found("size") == ["z-best", "new"]


def test_a_store_that_cannot_be_searched_is_said(chats, monkeypatch, caplog):
    chats.save("it", "a saffron latte")

    def refuses(*_a):
        raise sqlite3.OperationalError("disk I/O error at C:/secret/place")

    monkeypatch.setattr(chat_history, "_search_sqlite", refuses)
    monkeypatch.setattr(chat_history, "_search_postgres", refuses)
    with caplog.at_level(logging.DEBUG):
        assert chats.found("saffron") == []
    said = [r for r in caplog.records if "could not be searched" in r.getMessage()]
    assert [r.levelname for r in said] == ["WARNING"] and not said[0].exc_info
    caplog.clear()
    monkeypatch.setattr(chat_history, "_search_sqlite",
                        lambda *_a: (_ for _ in ()).throw(sqlite3.OperationalError("no such table: sessions")))
    monkeypatch.setattr(chat_history, "_search_postgres", chat_history._search_sqlite)
    with caplog.at_level(logging.DEBUG):
        assert chats.found("saffron") == []
    assert not [r for r in caplog.records if "could not be searched" in r.getMessage()]
