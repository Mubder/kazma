"""Memory's lists find a word in any of its spellings (memory plan W6).

``memory_list`` folded the asked Arabic word and looked for it in the
unfolded text (SQLite's ``LIKE``), so a word ending in a taa marbuta did not
find itself; the Memory page asked the full-text index, which folds no Arabic
letter, so it found a word only as stored. English lost nothing. Measured
2026-11-25 over the eight memories below: ``memory_list`` 8 of 8 in English
and 0 in Arabic, in the same spelling.

Each list is asked here through its own route or tool, for eight memories told
in English, Gulf and Modern Standard Arabic, in four ways: the stored
spelling, the other usual spelling (in English: capitals), and the word as a
sentence carries it (the article or a prefix; in English a plural or a
possessive), and as the user's own ("my car", "سيارتي"). No list may answer with a memory the query does not name.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time
from types import SimpleNamespace

import pytest
from kazma_core.memory.schema_v2 import ensure_primary_schema

TENANT = "default"

#: (the stored words, asked as stored, asked in the other spelling, asked as a sentence carries it, asked as
#: "my X": one word in Arabic)
MEMORIES = {
    "english": (
        ("turkish coffee", "coffee", "Coffee", "coffees", "my coffee"),
        ("the hope hospital", "hospital", "HOSPITAL", "hospitals", "my hospital"),
        ("ahmad almutairi", "ahmad", "Ahmad", "ahmad's", "my ahmad"),
        ("a blue car", "car", "Car", "cars", "my car"),
        ("istanbul trip", "istanbul", "Istanbul", "trips", "my trip"),
        ("fish allergy", "allergy", "Allergy", "allergy's", "my allergy"),
        ("12 march", "march", "March", "12", "my march"),
        ("fintas beach", "fintas", "Fintas", "fintas's", "my beach"),
    ),
    "gulf": (
        ("قهوة كرك بالهيل", "قهوة", "قهوه", "القهوة", "قهوتي"),
        ("مستشفى العدان", "مستشفى", "مستشفي", "بالمستشفى", "مستشفاي"),
        ("ربعي أحمد العازمي", "أحمد", "احمد", "عازمي", "ربعي"),
        ("سيارة حمرة", "سيارة", "سياره", "السيارة", "سيارتي"),
        ("سفرة إسطنبول", "إسطنبول", "اسطنبول", "السفرة", "سفرتي"),
        ("حساسية من الروبيان", "حساسية", "حساسيه", "الحساسية", "حساسيتي"),
        ("موعد ١٢ مارس", "١٢", "12", "الموعد", "موعدي"),
        ("شاليه الخيران", "شاليه", "شالية", "الشاليه", "شاليهي"),
    ),
    "msa": (
        ("قهوة تركية", "قهوة", "قهوه", "القهوة", "قهوتي"),
        ("مستشفى الأمل", "مستشفى", "مستشفي", "المستشفى", "مستشفاي"),
        ("أحمد المطيري", "أحمد", "احمد", "مطيري", "أحمدي"),
        ("سيارة زرقاء", "سيارة", "سياره", "السيارة", "سيارتي"),
        ("رحلة إسطنبول", "إسطنبول", "اسطنبول", "الرحلة", "رحلتي"),
        ("حساسية من الأسماك", "حساسية", "حساسيه", "الحساسية", "حساسيتي"),
        ("موعد ١٢ مارس", "١٢", "12", "الموعد", "موعدي"),
        ("شاطئ الفنطاس", "شاطئ", "شاطي", "الشاطئ", "شاطئي"),
    ),
}
WAYS = ("as stored", "other spelling", "in a sentence", "my")

#: Memories no query above names.
_OTHERS = ("likes hiking on fridays", "works at the port", "owns a telescope", "reads old novels", "plays chess",
           "has two brothers", "يحب المشي كل جمعة", "يعمل في الميناء", "عنده تلسكوب", "يقرأ الروايات القديمة",
           "يلعب الشطرنج", "عنده أخوين")

PLACES = ("memory_list: facts", "memory_list: turns", "memory_list_beliefs", "memory_list_entities",
          "page: facts", "page: memories", "page: things")



def _id(n: int) -> str:
    """An id no query is found in."""
    return f"zz{chr(97 + n)}"


@pytest.fixture
def lists(tmp_path, monkeypatch):
    """``ask(place, query) -> ids`` over a store told in one language."""
    path = str(tmp_path / "memory_state.db")
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.paths.primary_memory_db", lambda: path)
    monkeypatch.setattr("kazma_core.safety.hitl.get_current_tenant_id", lambda: TENANT)
    from kazma_core.agent.tool_builtins.memory import register_memory_tools
    from kazma_core.memory.memory_list import list_memories
    from kazma_ui import memory_api
    from kazma_ui.routes_direct import memory as routes

    monkeypatch.setattr(routes, "_mem_tid", lambda: TENANT)
    monkeypatch.setattr(memory_api, "_memory_tenant_id", lambda: TENANT)
    from fastapi import FastAPI

    app = FastAPI()
    routes.register_memory_routes(SimpleNamespace(app=app))
    route = {r.path: r.endpoint for r in app.routes if "GET" in getattr(r, "methods", ())}
    tools: dict = {}

    class Registry:
        def register(self, **_kw):
            return lambda fn: tools.setdefault(fn.__name__, fn)

    register_memory_tools(Registry())

    def told(stored: tuple[str, ...], *, fts: bool = True) -> None:
        with sqlite3.connect(path) as conn:
            ensure_primary_schema(conn)
            for i, words in enumerate((*stored, *_OTHERS)):
                conn.execute(
                    "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, confidence, "
                    "structural_importance, source_trust_weight, valid_from, ingested_at, extraction_method) "
                    "VALUES (?, ?, 'user', 'noted', 'set', ?, 1, 2, 1, ?, ?, 'user_explicit')",
                    (_id(i), TENANT, words, float(i + 1), float(i + 1)))
                conn.execute(
                    "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
                    "created_at, tier) VALUES (?, ?, 's', ?, ?, 'ok', ?, 'episodic')",
                    (_id(i), TENANT, i, words, float(i + 1)))
                conn.execute("INSERT INTO entities (id, tenant_id, type, name, aliases_json) VALUES (?, ?, 'noted', ?, '[]')",
                             (_id(i), TENANT, words))
            if not fts:
                conn.execute("DROP TABLE beliefs_fts")
                conn.execute("DROP TABLE entities_fts")

    def listed(kind: str, query: str) -> set[str]:
        with sqlite3.connect(path) as conn:
            return {x["id"] for x in list_memories(tenant_id=TENANT, about=query, conn=conn).lines if x["kind"] == kind}

    def from_tool(name: str, key: str, query: str) -> set[str]:
        out = asyncio.run(tools[name](q=query))
        body = out.split("--- BEGIN OBSERVATION ---", 1)[1].split("--- END OBSERVATION ---", 1)[0]
        return {x["id"] for x in json.loads(body)[key]}

    def ask(place: str, query: str) -> set[str]:
        if place == "memory_list: facts":
            return listed("fact", query)
        if place == "memory_list: turns":
            return listed("conversation", query)
        if place == "memory_list_beliefs":
            return from_tool("memory_list_beliefs", "beliefs", query)
        if place == "memory_list_entities":
            return from_tool("memory_list_entities", "entities", query)
        if place == "page: facts":
            got = route["/api/memory/v2/beliefs"](q=query, limit=50, offset=0)
            assert "error" not in got, got
            assert got["total"] == len(got["beliefs"])
            return {x["id"] for x in got["beliefs"]}
        if place == "page: memories":
            got = route["/api/memory/v2/episodes"](q=query, limit=50, offset=0, tier="")
            assert got["ok"], got
            return {x["id"] for x in got["episodes"]}
        got = memory_api._list_entities_sync(query, 100, 0, False, False)
        assert got["ok"], got
        return {x["id"] for x in got["entities"]}

    ask.told = told
    ask.path = path
    return ask


def counted(ask, language: str) -> dict[tuple[str, str], tuple[int, int]]:
    """(place, way) -> (memories found, memories listed that were not asked for)."""
    table: dict[tuple[str, str], tuple[int, int]] = {}
    for place in PLACES:
        for n, way in enumerate(WAYS, start=1):
            hit = noise = 0
            for i, memory in enumerate(MEMORIES[language]):
                got = ask(place, memory[n])
                hit += _id(i) in got
                noise += len(got - {_id(i)})
            table[place, way] = (hit, noise)
    return table


@pytest.mark.parametrize("language", sorted(MEMORIES))
def test_every_list_finds_a_word_in_any_spelling_and_nothing_else(lists, language):
    lists.told(tuple(m[0] for m in MEMORIES[language]))
    table = counted(lists, language)
    for place in PLACES:  # the table, for a failure's report
        print(f"{language:8}{place:24}", *(table[place, way] for way in WAYS))
    assert {k: v for k, v in table.items() if v != (8, 0)} == {}


@pytest.mark.parametrize("place", ["page: facts", "page: things"])
@pytest.mark.parametrize("language", sorted(MEMORIES))
def test_the_page_finds_them_with_no_index(lists, language, place):
    """The index cannot be read: the words alone answer, in any spelling."""
    lists.told(tuple(m[0] for m in MEMORIES[language]), fts=False)
    for i, memory in enumerate(MEMORIES[language]):
        for asked in memory[1:]:
            assert lists(place, asked) == {_id(i)}, (place, asked)


# ── negative controls ────────────────────────────────────────────────────

def _lost(ask, language: str, way: str) -> list[str]:
    table = counted(ask, language)
    return sorted(p for p in PLACES if table[p, way][0] < 8)


def _without_fold(monkeypatch) -> None:
    from kazma_core.memory import word_search

    def lower(text):
        return " ".join(word_search._text(text).lower().split())

    monkeypatch.setattr(word_search, "fold", lower)
    monkeypatch.setattr(word_search, "_folded", lambda chunk: lower(chunk.replace("_", " ")))


@pytest.mark.parametrize("language", ["gulf", "msa"])
def test_without_the_my_ending_every_list_loses_it(lists, monkeypatch, language):
    from kazma_core.memory import word_search

    monkeypatch.setattr(word_search, "_forms", lambda _written, term: [term])
    lists.told(tuple(m[0] for m in MEMORIES[language]))
    assert _lost(lists, language, "my") == sorted(PLACES)


@pytest.mark.parametrize("language", ["gulf", "msa"])
def test_without_the_fold_every_list_loses_arabic_words(lists, monkeypatch, language):
    _without_fold(monkeypatch)
    lists.told(tuple(m[0] for m in MEMORIES[language]))
    assert _lost(lists, language, "other spelling") == sorted(PLACES)


def test_without_the_fold_english_loses_nothing(lists, monkeypatch):
    """Why nobody saw it."""
    _without_fold(monkeypatch)
    lists.told(tuple(m[0] for m in MEMORIES["english"]))
    assert [_lost(lists, "english", way) for way in WAYS] == [[], [], [], []]


@pytest.mark.parametrize("language", ["gulf", "msa"])
def test_without_the_article_rule_the_name_after_it_is_lost(lists, monkeypatch, language):
    from kazma_core.memory import query_terms

    monkeypatch.setattr(query_terms, "_AR_PROCLITIC", "")
    lists.told(tuple(m[0] for m in MEMORIES[language]))
    assert "page: facts" in _lost(lists, language, "in a sentence")


# ── what a word is ───────────────────────────────────────────────────────

@pytest.mark.parametrize("stored, query, held", [
    ("مقهوة", "قهوة", False),  # inside another word
    ("وبالقَهْوَةِ", "قهوه", True),  # a prefix, the article, every mark
    ("قهـــوة", "قهوة", True),  # stretched
    ("\ufed7\ufeeb\ufeee\ufe93", "قهوة", True),  # presentation forms (folded whole)
    ("رقم ٠٥٠١٢٣", "050123", True),
    ("number 050123", "٠٥٠١٢٣", True),
    ("number 050123", "٥٠١٢٣", False),
    ("carpet", "car", False),  # a whole word only, where a list answers with any of them
    ("the car's door", "car", True),
    # "my X": taken off what was typed, and off what was told.
    ("سيارة زرقاء", "سيارتي", True), ("سيارتي الزرقاء", "السيارة", True), ("سيارتي", "سيارتي", True),
    ("مستشفاي المفضل", "المستشفى", True), ("مستشفى الأمل", "مستشفاي", True),
    ("بيت في السالمية", "بيتي", True), ("بيتي في السالمية", "البيت", True), ("شقة", "شقتي", True),
    ("من هناك", "منى", False), ("ليل طويل", "ليلى", False), ("كرسي", "كرسي", True), ("مصر", "مستشفى", False),
    ("سيارتها بيضاء", "سيارة", True), ("ذهب من هنا", "مني", False),
    ("قهوجي", "قهوتي", False),  # a word "my" was taken off is whole
    ("blue_heron", "heron", True),
])
def test_a_word_is_found_as_a_word(stored, query, held):
    from kazma_core.memory.word_search import Asked

    assert Asked.of(query, whole=True).any(stored) is held


@pytest.mark.parametrize("stored, query, held", [
    # A letter typed as a letter and its mark (the last check of step 1).
    ("the café by the sea", "café", True), ("my résumé", "résumé", True),
    ("the cafe by the sea", "café", False), ("قرأت كتاب", "قرأت", True),
    # A digit in another script, inside a Latin word.
    ("٣G router", "٣G", True), ("bought an iphone١٥", "iphone١٥", True), ("bought an iPhone15", "iphone١٥", True),
    ("GPT٥ is out", "gpt5", True), ("gpt6 is out", "gpt5", False), ("٣G", "4g", False),
])
def test_a_word_is_found_as_it_was_typed(stored, query, held):
    from kazma_core.memory.word_search import Asked

    assert Asked.of(query).every(stored) is held


#: What is held, what is typed, the rows listed: a final ي that may be "my".
MY = [
    (("سامي اتصل", "سام زارنا"), "سامي", {0}), (("عادي جدا", "عاد من السفر"), "عادي", {0}),
    (("رامي في البيت", "رام الله مدينة"), "رامي", {0}), (("سيارة زرقاء", "بيت كبير"), "سيارتي", {0}),
    (("سيارتي تعطلت", "بيت كبير"), "سيارتي", {0}), (("حساسية من الفول", "بيت كبير"), "حساسيتي", {0}),
]


@pytest.mark.parametrize("held, typed, listed", MY)
@pytest.mark.parametrize("place", PLACES)
def test_my_is_taken_off_only_where_the_word_itself_is_nowhere(lists, place, held, typed, listed):
    lists.told(held)
    assert lists(place, typed) == {_id(n) for n in listed}


def test_control_my_taken_off_at_once_lists_another(lists, monkeypatch):
    from kazma_core.memory import word_search

    monkeypatch.setattr(word_search, "asked_in", lambda conn, query, places, **kw: (
        word_search.register(conn, asked := word_search.Asked.of(query, **kw)), asked)[1])
    lists.told(MY[0][0])
    assert lists("page: facts", "سامي") == {_id(0), _id(1)}


def test_a_list_that_wants_every_word_takes_a_words_start():
    from kazma_core.memory.word_search import Asked

    assert Asked.of("vacc").every("two vaccines in May")
    assert Asked.of("vaccine may").every("two vaccines", "in May")
    assert not Asked.of("vaccine june").every("two vaccines", "in May")
    assert not Asked.of("the of").every("the cup of tea"), "a query of no content word names nothing"


def test_the_look_before_the_fold_loses_nothing():
    """Every spelling of a word that the fold makes the same passes the look
    that saves folding a long text, over every character there is; and with a
    range taken out of what the look knows, one is lost (the control)."""
    from kazma_core.documents.arabic import fold_for_search
    from kazma_core.memory import word_search

    spellings = word_search._spellings()
    known = set("".join(spellings.values()))
    unknown = [c for c in range(0x110000) if not 0xD800 <= c <= 0xDFFF and chr(c) not in known
               and any(k in spellings for k in fold_for_search(chr(c)))]
    assert len(unknown) > 500
    assert [hex(c) for c in unknown if not word_search._COMPAT.search(chr(c))] == []
    assert any(0xFB50 <= c <= 0xFDFF for c in unknown), "the control: that range is needed"
    dropped = [c for c in range(0x110000) if not 0xD800 <= c <= 0xDFFF and fold_for_search(f"ق{chr(c)}ه") == "قه"]
    look = word_search._look("قهك", ["قهك"])
    assert dropped and [hex(c) for c in dropped
                        if not (look.search(f"ق{chr(c)}هك") or word_search._COMPAT.search(chr(c)))] == []


# ── the rest of what each list answers ───────────────────────────────────

def test_an_alias_is_found_in_arabic_however_it_was_written(lists):
    """Half the writers of ``aliases_json`` escape its Arabic: the index held
    the escapes, and an alias was found in English alone."""
    lists.told(())
    with sqlite3.connect(lists.path) as conn:
        for eid, aliases in (("zzx", json.dumps(["شركة الخليج", "Gulf Co"])),
                             ("zzy", json.dumps(["مؤسسة البحر"], ensure_ascii=False))):
            conn.execute("INSERT INTO entities (id, tenant_id, type, name, aliases_json) VALUES (?, ?, 'org', 'one', ?)",
                         (eid, TENANT, aliases))
    for place in ("page: things", "memory_list_entities"):
        assert lists(place, "شركه") == {"zzx"}, place
        assert lists(place, "Gulf") == {"zzx"}, place
        assert lists(place, "موسسة") == {"zzy"}, place


def test_the_thing_named_exactly_comes_first_in_either_spelling(lists):
    from kazma_ui import memory_api

    lists.told(())
    with sqlite3.connect(lists.path) as conn:
        for eid, name, n in (("zzx", "مكتبة الكويت الوطنية", 9), ("zzy", "مكتبة", 0)):
            conn.execute("INSERT INTO entities (id, tenant_id, type, name, aliases_json, belief_count, graph_degree) "
                         "VALUES (?, ?, 'place', ?, '[]', ?, 0)", (eid, TENANT, name, n))
        conn.execute("UPDATE entities SET belief_count = 0, graph_degree = 0 WHERE belief_count = -1")
    got = memory_api._list_entities_sync("مكتبه", 10, 0, False, False)
    assert [e["id"] for e in got["entities"]] == ["zzy", "zzx"]


@pytest.mark.parametrize("query", ["gpt5", "wi-fi", "covid19", "x-ray", "café"])
def test_a_latin_word_with_a_digit_a_hyphen_or_an_accent_is_found_in_every_list(lists, query):
    lists.told((f"about {query} today",))
    for place in PLACES:
        assert lists(place, query) == {_id(0)}, place


def test_an_id_typed_whole_is_one_thing_not_each_of_its_words(lists):
    """"user_memory_system" listed every fact: the subject of each is ``user``."""
    lists.told(("tea", "the memory system"))
    assert lists("page: facts", "user_memory_system") == {_id(1)}
    assert lists("page: facts", "memory tea") == {_id(0), _id(1)}


@pytest.mark.parametrize("query", ["memory system", "user memory system", "Memory-System"])
def test_the_tools_read_an_id_as_its_words(lists, query):
    lists.told(("tea",))
    with sqlite3.connect(lists.path) as conn:
        conn.execute("UPDATE beliefs SET subject = 'user_memory_system' WHERE id = ?", (_id(0),))
        conn.execute("UPDATE entities SET name = 'user_memory_system' WHERE id = ?", (_id(0),))
    for place in ("memory_list_beliefs", "memory_list_entities"):
        assert lists(place, query) == {_id(0)}, place


def test_the_panel_wants_every_word_of_a_long_query(lists):
    """Folded, only the first eight were looked for."""
    words = "قهوة سيارة رحلة موعد شاطئ مدرسة حديقة مكتبة سوق مسجد".split()
    lists.told((" ".join(words), " ".join(words[:8])))
    asked = " ".join(words).replace("ة", "ه")
    assert lists("page: memories", asked) == {_id(0)}


def test_a_row_whose_text_is_no_text_does_not_end_the_search(lists):
    lists.told(("قهوة تركية", "turkish coffee"))
    with sqlite3.connect(lists.path) as conn:
        conn.execute("UPDATE beliefs SET object = CAST(x'ff616263' AS TEXT) WHERE id = ?", (_id(3),))
        conn.execute("UPDATE episodes SET user_text = CAST(x'ff616263' AS TEXT) WHERE id = ?", (_id(3),))
        conn.execute("UPDATE entities SET name = CAST(x'ff616263' AS TEXT) WHERE id = ?", (_id(3),))
    for place in PLACES:
        assert lists(place, "قهوه") == {_id(0)}, place
        assert lists(place, "coffees") == {_id(1)}, place


def test_a_word_of_a_thousand_letters_is_no_thousand_letter_search():
    from kazma_core.memory.word_search import _WORD_CHARS, Asked

    asked = Asked.of("ق" * 1000 + " coffee", whole=True)
    assert [len(t) for t in asked.terms] == [_WORD_CHARS, 6]
    assert asked.any("turkish coffee") and not asked.every("turkish coffee")


def test_a_query_with_no_word_is_found_as_written(lists):
    lists.told(("50% sure", "q"))
    assert lists("page: facts", "%") == {_id(0)}
    assert lists("memory_list_beliefs", "%") == {_id(0)}
    assert _id(1) in lists("page: facts", "q")
    assert lists("page: facts", "ـ") == set(), "a stretch alone folds to nothing"


def test_another_space_and_a_forgotten_turn_are_not_listed(lists):
    lists.told(("قهوة تركية",))
    with sqlite3.connect(lists.path) as conn:
        conn.execute("UPDATE beliefs SET tenant_id = 'beta'")
        conn.execute("UPDATE entities SET tenant_id = 'beta'")
        conn.execute("UPDATE episodes SET tier = 'forgotten'")
    for place in ("memory_list_beliefs", "memory_list_entities", "memory_list: turns", "page: memories"):
        assert lists(place, "قهوه") == set(), place


def test_the_memories_panel_pages_and_counts_folded_matches(lists):
    from fastapi import FastAPI
    from kazma_ui.routes_direct import memory as routes

    lists.told(tuple(f"قهوة {n}" for n in ("أولى", "ثانية", "ثالثة", "رابعة", "خامسة")))
    app = FastAPI()
    routes.register_memory_routes(SimpleNamespace(app=app))
    route = next(r.endpoint for r in app.routes if r.path == "/api/memory/v2/episodes")
    pages = [route(q="قهوه", limit=2, offset=off, tier="") for off in (0, 2, 4, 6)]
    assert [p["total"] for p in pages] == [5] * 4
    assert [[e["id"] for e in p["episodes"]] for p in pages] == [
        [_id(4), _id(3)], [_id(2), _id(1)], [_id(0)], []]


@pytest.mark.parametrize(("query", "at_most"), [
    # A Latin word is looked for as it is written: no turn is folded for it.
    ("gpt5", 0.0), ("wi-fi", 0.0), ("covid19", 0.0), ("x-ray", 0.0), ("café", 0.0),
    # Two letters that begin, end and stand in many words: those words alone.
    ("عم", 0.12), ("قهوه", 0.02), ("050123", 0.0)])
def test_a_search_folds_the_words_that_may_be_it_and_no_turn_whole(monkeypatch, query, at_most):
    """Folding is what a search costs (24 seconds for "gpt5" in a memory of
    20,000 facts and 50,000 turns, 12 for "عم"): the share of the letters of a
    bilingual memory that a search folds is counted, and its time is bounded."""
    from kazma_core.memory import word_search

    folded = [0]
    fold = word_search.fold

    def counted(text):
        folded[0] += len(str(text))
        return fold(text)

    arabic = ("ذهبنا مع العم خالد إلى السوق ثم طعمنا الغداء، نعم كان الطعم طيب وعمر معنا والعمل كثير ولكن معظم الناس "
              "عملوا بجد وشربنا شاي ")
    english = "we went to the market with friends and bought tea, then worked on the garden until late in the evening "
    turns = [(arabic if n % 2 else english) * 12 + str(n) for n in range(4_000)]
    asked = word_search.Asked.of(query)
    monkeypatch.setattr(word_search, "fold", counted)
    monkeypatch.setattr(word_search, "_fold_word", counted)  # no cache: each word that is folded counts
    started = time.perf_counter()
    for turn in turns:
        asked.every(turn)
    took = time.perf_counter() - started
    assert folded[0] <= at_most * sum(map(len, turns)), folded[0] / sum(map(len, turns))
    assert took < 20  # 0.1 to 0.6 s measured; a slow runner's room


def test_control_a_turn_folded_whole_is_seen(monkeypatch):
    from kazma_core.memory import word_search

    whole = word_search.Asked._held

    def held(self, parts):
        word_search.fold(" ".join(map(str, parts)))
        return whole(self, parts)

    monkeypatch.setattr(word_search.Asked, "_held", held)
    with pytest.raises(AssertionError):
        test_a_search_folds_the_words_that_may_be_it_and_no_turn_whole(monkeypatch, "gpt5", 0.0)


# ── the class gate ───────────────────────────────────────────────────────

#: A statement that compares what was typed with memory's text as written.
_TEXT = r"(?:user_text|assistant_text|summary_text|subject|predicate|object|aliases_json|\bname|\bx)"
_RAW = re.compile(rf"{_TEXT}[\s),'_|-]*\b(?:LIKE|GLOB)\b|\w+_fts MATCH|instr\([^?]{{0,40}}{_TEXT}", re.IGNORECASE)

#: Where such a comparison is right, and why: (file, function) -> reason.
DECLARED = {
    ("kazma-core/kazma_core/memory/memory_list.py", "_every_word"):
        "a first look for plain English words; kazma_asked_every decides",
    ("kazma-ui/kazma_ui/routes_direct/memory.py", "_memory_v2_beliefs"): "the index, OR kazma_asked_any",
    ("kazma-ui/kazma_ui/routes_direct/memory.py", "_memory_v2_episodes"): "the index, OR kazma_asked_every",
    ("kazma-ui/kazma_ui/memory_api.py", "_list_entities_sync"): "the index, OR kazma_asked_any",
    ("kazma-core/kazma_core/memory/recall.py", "_episode_fts"):
        "recall's keyword channel; meaning finds the other spelling (46 Arabic questions, none lost, 2026-11-25)",
    ("kazma-core/kazma_core/memory/recall.py", "_episode_word_fallback"):
        "recall when the index cannot be read; reported with W6",
    ("kazma-core/kazma_core/memory/recall.py", "_belief_fts"): "recall's keyword channel, as above",
    ("kazma-core/kazma_core/memory/state_backend.py", "search_episodes"): "Postgres' copy; S4-01's, reported with W6",
    ("kazma-core/kazma_core/memory/state_backend.py", "search_beliefs"): "Postgres' copy; S4-01's, reported with W6",
    ("kazma-core/kazma_core/memory/forget.py", "_episodes_holding"):
        "one distinctive value (a code, a number), never a word; reported with W6",
    ("kazma-core/kazma_core/memory/forget.py", "_forget_departed_entries"): "the start of a member's own entry id",
    ("kazma-core/kazma_core/memory/forget.py", "_scrub_legacy_copies"): "the same text copied, never a typed query",
    ("kazma-core/kazma_core/memory/entity_resolution.py", "resolve_entity"): "an alias's hash, folded before hashing",
}


def _raw_searches(source: str) -> set[str]:
    """The functions of *source* with a statement that compares memory's text
    as written."""
    import ast

    found: set[str] = set()

    class Reader(ast.NodeVisitor):
        stack: list[str] = []

        def visit_FunctionDef(self, node):  # noqa: N802 - ast's name
            self.stack.append(node.name)
            body = node.body[1:] if ast.get_docstring(node) else node.body
            for child in body:
                self.visit(child)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815 - ast's name

        def visit_JoinedStr(self, node):  # noqa: N802
            self._text("".join(v.value if isinstance(v, ast.Constant) else " x " for v in node.values))

        def visit_Constant(self, node):  # noqa: N802
            if isinstance(node.value, str):
                self._text(node.value)

        def _text(self, text):
            if self.stack and _RAW.search(text):
                found.add(self.stack[-1])

    Reader().visit(ast.parse(source))
    return found


def _places() -> dict[str, str]:
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    files = [*(repo / "kazma-core/kazma_core/memory").glob("*.py"),
             repo / "kazma-core/kazma_core/agent/tool_builtins/memory.py",
             repo / "kazma-ui/kazma_ui/memory_api.py", *(repo / "kazma-ui/kazma_ui/routes_direct").glob("memory*.py")]
    # The chats' own search is another subject (chat_history, transcript_recall).
    return {f.relative_to(repo).as_posix(): f.read_text(encoding="utf-8") for f in files
            if f.name not in ("chat_history.py", "transcript_recall.py")}


def test_no_list_compares_memory_text_as_it_was_written():
    found = {(path, name) for path, source in _places().items() for name in _raw_searches(source)}
    assert found == set(DECLARED), f"undeclared: {found - set(DECLARED)}; gone: {set(DECLARED) - found}"


@pytest.mark.parametrize("old", [
    'def f(c, t):\n    return c.execute(f"SELECT id FROM beliefs WHERE LOWER(subject || object) LIKE ?", (t,))',
    'def f(c, q):\n    return c.execute("SELECT 1 FROM episodes WHERE user_text LIKE ?", (q,))',
    'def f(c, q):\n    return c.execute("SELECT 1 FROM x WHERE beliefs_fts MATCH ?", (q,))',
    'async def f(c, q):\n    return c.execute("SELECT 1 FROM entities e WHERE instr(e.name, ?) > 0", (q,))',
])
def test_the_gate_sees_the_old_searches(old):
    assert _raw_searches(old) == {"f"}


def test_the_gate_leaves_a_folded_search_and_a_key_alone():
    assert _raw_searches('def f(c, q):\n    """user_text LIKE ?"""\n'
                         '    return c.execute("SELECT 1 FROM beliefs WHERE kazma_asked_any(subject) AND id LIKE ?", (q,))'
                         ) == set()
