"""The deep pass is shown the fact a turn changes (the memory plan's W6, step 1).

``held_facts`` compared two LISTS of words with ``<=``, which is alphabetical
order: "What should I cook tonight?" was shown Acme's facts, an Arabic turn
every English-named subject's, and "أختي نورة صارت جراحة" was not shown Noura's
job. And the speaker's newest thirty facts took every place, so an older fact
of theirs, or anyone else's, could not be replaced: the old value and the new
one both stayed current. Now the list is the facts the turn is about
(``memory.held_facts``).

``benchmark_held.run_held_pairs`` tells eight changes in English, Gulf and MSA
to stores with none and with forty newer facts, through what the memory worker
reads for a turn and the one write path, with bge-m3's recorded vectors
(``scripts/memory_bench.py held-pairs-vectors``).

Negative controls: today's comparison of lists, and the newest facts alone,
lose the pairs; so does each language's when its way of choosing is taken away.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from kazma_core.memory import benchmark, benchmark_held, held_facts
from kazma_core.memory.belief_mutation import mutate_belief
from kazma_core.memory.limits import DEEP_PASS_HELD_CHARS, DEEP_PASS_HELD_FACTS
from kazma_core.memory.schema_v2 import ensure_primary_schema
from kazma_core.summarizer import estimate_text_tokens

FIXTURE = Path(__file__).parent / "fixtures" / "memory_bench" / benchmark_held.HELD_PAIRS_VECTORS

def _replay(monkeypatch: pytest.MonkeyPatch) -> None:
    replay = benchmark.ReplayEmbedder(FIXTURE)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: replay)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: replay.model)


def _run(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    _replay(monkeypatch)
    return benchmark_held.run_held_pairs()


def _same_language(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["held_in"] == ("en" if r["dialect"] == "en" else "ar")]


def _lost(rows: list[dict]) -> list[tuple]:
    return [(r["pair"], r["dialect"], r["held_in"], r["newer"]) for r in rows if not (r["shown"] and r["closed"])]


def test_the_fact_a_turn_changes_is_shown_and_closed_in_each_dialect(monkeypatch):
    rows = _run(monkeypatch)
    same = _same_language(rows)
    assert {d: sum(r["dialect"] == d for r in same) for d in ("en", "gulf", "msa")} == {"en": 16, "gulf": 16, "msa": 16}
    assert _lost(same) == []
    crossed = [r for r in rows if r not in same and r["subject"] == "as held"]
    assert len(crossed) == 32 and _lost(crossed) == []
    # Nobody the turn is not about; in another alphabet than the turn's, two.
    assert [r for r in same if r["unrelated"]] == []
    assert max(len(r["unrelated"]) for r in crossed) <= 2
    assert max(r["places"] for r in rows) == DEEP_PASS_HELD_FACTS


def test_a_reply_in_the_users_script_closes_a_persons_fact_held_in_the_other(monkeypatch):
    """The fact held as ``omar`` is shown to "عمر نقل الدوحة", and a reply that
    says "عمر", as the prompt asks, replaces it (W6, step 2:
    ``name_alphabets``). A project's name is nobody's in two alphabets: the
    reply's fact is written as it came."""
    said = [r for r in _run(monkeypatch) if r["subject"] == "as said"]
    assert len(said) == 32 and all(r["shown"] for r in said)
    assert {(r["pair"], r["dialect"]) for r in said if not r["closed"]} == {("project", "gulf"), ("project", "msa")}


def test_control_without_the_match_the_job_and_the_home_stay(monkeypatch):
    monkeypatch.setattr("kazma_core.memory.name_alphabets.same_name", lambda one, other: False)
    said = [r for r in _run(monkeypatch) if r["subject"] == "as said"]
    assert {(r["pair"], r["dialect"]) for r in said if not r["closed"]} == {
        (pair, dialect) for pair in ("job", "friend", "project") for dialect in ("gulf", "msa")}


def _crowds(monkeypatch) -> list[dict]:
    _replay(monkeypatch)
    return benchmark_held.run_held_crowds()


def test_a_fact_is_shown_among_many_nearer_ones_in_each_dialect(monkeypatch):
    """A friend's home among the user's facts about cities, a member's own
    habit among another member's: 60 facts were read for everybody, and
    theirs was not among them (the review of step 1)."""
    rows = _crowds(monkeypatch)
    assert {(r["case"], r["others"]) for r in rows} == {
        ("friend", n) for n in (0, 60, 120, 300)} | {("member", n) for n in (0, 80)}
    assert len(rows) == 18 and [r for r in rows if not r["shown"]] == []


def _no_names_across_alphabets(monkeypatch) -> None:
    """A friend held as ``omar`` is found near in meaning alone, as a thing is
    whose name Kazma does not know in both alphabets."""
    monkeypatch.setattr(held_facts._Said, "_in_the_other_alphabet", lambda self, subject: None)


def test_control_sixty_facts_read_lose_it(monkeypatch):
    _no_names_across_alphabets(monkeypatch)
    monkeypatch.setattr(held_facts, "_NEAR_READ", 60)
    lost = {(r["case"], r["dialect"], r["others"]) for r in _crowds(monkeypatch) if not r["shown"]}
    # The English ones hold a word of the turn ("swims", "Omar"); the Arabic
    # ones are found by meaning alone.
    assert lost == {("friend", "gulf", 120), ("friend", "gulf", 300), ("member", "gulf", 80), ("member", "msa", 80)}


def test_control_the_comparison_of_lists_and_the_newest_alone_lose_them(monkeypatch):
    """What was live: the named by alphabetical order, the newest first."""
    from kazma_core.memory.query_terms import content_terms

    told = held_facts._Said.__init__

    def keep_text(self, text):
        told(self, text)
        self._text = text

    def names(self, subject, *, person=False):
        wanted = content_terms(subject.replace("_", " "))
        return (1, 0) if wanted and wanted <= content_terms(self._text) else None

    monkeypatch.setattr(held_facts._Said, "__init__", keep_text)
    monkeypatch.setattr(held_facts._Said, "names", names)
    monkeypatch.setattr(held_facts, "_choose", lambda ways, places: [*ways["newest"], *ways["named"]][:places])
    rows = _run(monkeypatch)
    lost = _lost(_same_language(rows))
    for dialect in ("en", "gulf", "msa"):
        assert sum(1 for _p, d, _h, newer in lost if d == dialect and newer == 40) == 8, (dialect, lost)
    assert [r for r in rows if r["unrelated"]]


@pytest.mark.parametrize(("way", "dialects"), [("named", {"en", "gulf", "msa"}), ("near", {"en", "gulf", "msa"})])
def test_control_each_way_of_choosing_is_needed_in_each_dialect(monkeypatch, way, dialects):
    if way == "named":
        monkeypatch.setattr(held_facts._Said, "names", lambda *_a, **_kw: None)
        # Near in meaning finds a named person too: without both, it is lost.
        monkeypatch.setattr(held_facts, "_NEAR_ANOTHER", 2.0)
    else:
        monkeypatch.setattr(held_facts, "_nearest", lambda *_a: [])
    assert {d for _p, d, _h, newer in _lost(_same_language(_run(monkeypatch))) if newer == 40} == dialects


def test_control_a_name_in_the_other_alphabet_is_found_only_near(monkeypatch):
    """"عمر نقل الدوحة" and ``omar``, were the name not known in both: no word
    of the turn names him."""
    _no_names_across_alphabets(monkeypatch)
    monkeypatch.setattr(held_facts, "_OTHER_ALPHABET", 0)
    rows = [r for r in _run(monkeypatch) if r["subject"] == "as held"]
    assert {(pair, dialect) for pair, dialect, *_ in _lost(rows)} == {("friend", "gulf"), ("friend", "msa")}


@pytest.mark.parametrize(("subject", "text", "other"), [
    ("omar", "عمر نقل الدوحة", True), ("omar", "Omar moved", False), ("omar", "My café", False),
    ("عمر", "Omar moved", True), ("عمر", "عمر نقل", False), ("omar", "شريت iPhone حق عمر", True)])
def test_a_name_in_another_alphabet(subject, text, other):
    assert held_facts._in_another_alphabet(subject, text) is other


@pytest.mark.parametrize(("gone", "lost"), [
    (None, {"habit", "car"}), ("said", {"habit", "car", "allergy", "plan"}), ("one", {"habit", "car", "home"}),
    ("named", {"habit", "car", "job", "friend", "project"})])
def test_with_no_embedder_the_words_find_the_same_in_each_dialect(monkeypatch, gone, lost):
    """A habit and a car no word of the turn names need the meaning; every
    other pair is found by words, and each way of choosing by words is needed."""
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    choose = held_facts._choose
    if gone:
        monkeypatch.setattr(held_facts, "_choose", lambda ways, places: choose({**ways, gone: []}, places))
    rows = [r for r in _same_language(benchmark_held.run_held_pairs()) if r["newer"] == 40]
    assert {d: {p for p, dd, *_ in _lost(rows) if dd == d} for d in ("en", "gulf", "msa")} == dict.fromkeys(
        ("en", "gulf", "msa"), lost)


# ── Who a turn names ─────────────────────────────────────────────────────


@pytest.mark.parametrize(("text", "subject", "named"), [
    ("What should I cook tonight?", "acme", False), ("What should I cook tonight?", "berlin_office", False),
    ("My sister Noura is a surgeon now.", "noura", True), ("Noura's job changed", "noura", True),
    ("How is the Berlin office?", "berlin_office", True), ("Berlin was cold", "berlin_office", False),
    ("My sister moved", "user_sister", True), ("I maxed out the card", "max", False), ("Max called", "max", True),
    ("Will called me", "will", True), ("I will call you", "will", False),
    ("شنو أطبخ اليوم؟", "acme", False), ("شنو أطبخ اليوم؟", "سارة", False),
    ("أختي نورة صارت جراحة", "نورة", True), ("قلت لنورة وسارة", "نورة", True), ("قلت لنورة وسارة", "سارة", True),
    ("رحت مع منصور أمس", "منصور", True), ("مشروع فالكون تأجل", "مشروع_فالكون", True),
    ("تأجل المشروع", "مشروع_فالكون", False), ("كلمت علي أمس", "علي", True), ("الكتاب على الطاولة", "علي", False),
])
def test_a_subject_is_named_by_its_words(text, subject, named):
    assert (held_facts._Said(text).names(subject) is not None) is named


@pytest.mark.parametrize(("text", "subject", "named"), [
    # One name is not another: an Arabic name ends as it is written.
    ("كلمت كريمة", "كريم", False), ("كلمت كريم", "كريمة", False), ("كلمت خالدة", "خالد", False),
    ("كلمت خالد", "خالدة", False), ("أميرة وصلت", "أمير", False), ("أمير وصل", "أميرة", False),
    ("سمير وصل", "سميرة", False), ("سميرة وصلت", "سمير", False), ("فريدة وصلت", "فريد", False),
    ("منيرة وصلت", "منير", False), ("نورة وصلت", "نور", False), ("نور وصلت", "نورة", False),
    ("نوره وصلت", "نورة", True), ("نوره وصلت", "نور", False),
    # Nor is a plain word a name.
    ("سارت السيارة", "سارة", False), ("سافرت الهند", "هند", False), ("شريت وردة", "ورد", False),
    # A two-word name, joined and apart.
    ("كلمت عبدالله", "عبد_الله", True), ("كلمت عبد الله", "عبدالله", True), ("قلت لعبد الله", "عبدالله", True),
    ("وعبدالرحمن وصل", "عبد_الرحمن", True), ("عبد الرحمن وصل", "عبدالله", False),
    ("بوفهد وصل", "أبو_فهد", True), ("أبو فهد وصل", "بو_فهد", True), ("أبو فهد وصل", "بوفهد", True),
    ("رحت لبو فهد", "أبو_فهد", True), ("أبو علي وصل", "أبو_فهد", False), ("أبو علي وصل", "أبو_علي", True),
    ("كلمت علي", "أبو_علي", False), ("أحمد عازمي وصل", "أحمد_العازمي", True), ("والعازمي وصل", "العازمي", True),
    ("قَلَتُ لِنُورَةَ", "نورة", True),
])
def test_an_arabic_name_is_matched_as_a_name(text, subject, named):
    """The review of step 1, in the English way: "max" is not in "maxed"."""
    assert (held_facts._Said(text).names(subject, person=True) is not None) is named


#: A person called by what they are: said with the article and with whose they
#: are, as "my manager" and "the doctor" are. (English, Gulf, MSA, the subject.)
_ROLES = (
    ("My manager moved to Jabriya", "مديري نقل الجابرية", "انتقل مديري إلى الجابرية.", "manager", "مدير"),
    ("My neighbor retired", "جاري تقاعد", "تقاعد جاري.", "neighbor", "جار"),
    ("The doctor moved", "الدكتور نقل", "انتقل الطبيب.", "doctor", ("دكتور", "طبيب")),
    ("The driver went back to India", "السائق رجع الهند", "عاد السائق إلى الهند.", "driver", "سائق"),
    ("My wife became a manager", "زوجتي صارت مديرة", "أصبحت زوجتي مديرة.", "wife", "زوجة"),
    ("My sister graduated", "أختي تخرجت", "تخرجت أختي.", "sister", "أخت"),
    ("My father retired", "أبوي تقاعد", "تقاعد أبي.", "father", "أب"),
    ("My brother's job changed", "أخوي تغير دوامه", "تغير عمل أخي.", "brother", "أخ"),
)


@pytest.mark.parametrize(("text", "subject"), [
    (row[n], row[4] if isinstance(row[4], str) and n else row[3] if not n else row[4][n - 1])
    for row in _ROLES for n in range(3)])
def test_a_person_called_by_what_they_are_is_named_in_each_dialect(conn, text, subject):
    """The review of step 1's fixes: with a person's single word matched
    exactly, "مديري نقل" named no "مدير"."""
    assert held_facts._Said(text).names(subject, person=True) is not None
    _hold(conn, subject, "lives_in", "السالمية", 1.0)
    for n in range(40):
        _hold(conn, "user", "visited", f"city {n}", 100.0 + n)
    held = held_facts.held_facts(conn, tenant_id="default", text=text)
    assert subject in {h["subject"] for h in held}


@pytest.mark.parametrize(("text", "subject"), [
    ("المديرة وصلت", "مدير"), ("المدرسة سكرت", "مدرس"), ("الجارية طويلة", "جار"), ("سافرت الهند", "هند"),
    ("المدير وصل", "مديرة"), ("الكاتبة وصلت", "كاتب")])
def test_what_they_are_is_still_one_word(text, subject):
    assert held_facts._Said(text).names(subject, person=True) is None


def test_control_without_the_words_of_what_people_are_they_are_lost(monkeypatch):
    monkeypatch.setattr(held_facts, "_roles", frozenset)
    lost = [text for _en, gulf, msa, _s, ar in _ROLES for n, text in ((0, gulf), (1, msa))
            if held_facts._Said(text).names(ar if isinstance(ar, str) else ar[n], person=True) is None]
    assert len(lost) == 2 * len(_ROLES)


@pytest.mark.parametrize(("text", "subject", "named"), [
    ("سيارتي خربت", "سيارة", True), ("السيارة خربت", "سيارة", True), ("سيارتها بيضاء", "سيارة", True),
    ("رحت بالسيارة", "سيارة", True), ("مكتبنا انتقل", "مكتب", True), ("المكتبة سكرت", "مكتب", False),
    ("قلت للشركة", "الشركة", True), ("my office moved", "office", True), ("the official moved", "office", False)])
def test_a_thing_is_matched_with_its_article_and_whose_it_is(text, subject, named):
    assert (held_facts._Said(text).names(subject) is not None) is named


@pytest.mark.parametrize(("text", "named"), [
    ("Room 7 is booked now.", "room_7"), ("غرفة 7 محجوزة الحين", "غرفة_7"), ("الغرفة ٧ محجوزة", "غرفة_7")])
def test_a_number_of_one_digit_tells_things_apart(text, named):
    subjects = [f"{named.rsplit('_', 1)[0]}_{n}" for n in range(1, 10)]
    assert [s for s in subjects if held_facts._Said(text).names(s)] == [named]


@pytest.mark.parametrize(("predicate", "person"), [
    ("works_as", True), ("lives_in", True), ("sister_of", True), ("based_in", False), ("deadline", False)])
def test_a_person_is_told_by_the_facts_held(predicate, person):
    assert held_facts._a_person([{"predicate": "noted"}, {"predicate": predicate}]) is person


# ── The list's bounds ────────────────────────────────────────────────────


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    db = sqlite3.connect(":memory:", isolation_level=None)
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    yield db
    db.close()


def _hold(conn, subject, name, value, at, *, kind="set"):
    mutate_belief(conn, subject, name, value, predicate_type=kind, extraction_method="llm_inferred",
                  tenant_id="default", source_session="s", source_turn=1, now=at, private=True)


@pytest.mark.parametrize("word", ["word ", "كلمة "])
def test_the_list_is_bounded_in_both_languages(conn, word):
    for n in range(60):
        _hold(conn, "user", "noted", f"{n} {word * 60}", float(n))
    _hold(conn, "user", "likes", "tea", 0.5)
    held = held_facts.held_facts(conn, tenant_id="default", text="Do I like tea?")
    lines = "\n".join(held_facts.held_line(h) for h in held)
    assert len(lines) <= DEEP_PASS_HELD_CHARS
    # The most the list can cost: 1,000 tokens of English, 1,600 of Arabic.
    assert estimate_text_tokens(lines) <= (1000 if word.isascii() else 1600)
    assert 5 < len(held) <= DEEP_PASS_HELD_FACTS and [h["ref"] for h in held] == [f"f{n + 1}" for n in range(len(held))]
    assert any(h["object"] == "tea" for h in held)  # what the turn says is kept first


#: Long notes, in the two languages, letter for letter as long.
_LONG = {"en": ("note {n} " + "w" * 170, "Sorry, I meant the other one, Hana and the garden.", "hana", "garden"),
         "ar": ("بند  {n} " + "ك" * 170, "آسف قصدي الثانية، هناء وحديقة البيت", "هناء", "حديقة")}


def test_as_many_facts_are_shown_in_arabic_as_in_english_and_the_one_just_told(conn):
    """The bound was in tokens and the newest came last: of the same long
    facts, 19 in English and 13 in Arabic, the one just told not among them."""
    shown = {}
    for lang, (note, said, person, place) in _LONG.items():
        conn.execute("DELETE FROM beliefs")
        for n in range(12):
            _hold(conn, person, "noted", note.format(n=n), 100.0 + n)
            _hold(conn, "user", "noted", f"{place[:5]} {note.format(n=n)}", 200.0 + n)
        for n in range(20):
            _hold(conn, "user", "noted", f"other {note.format(n=n)}", 300.0 + n)
        for n in range(3):
            _hold(conn, "user", "noted", f"last {note.format(n=n)}", 900.0 + n)
        held = held_facts.held_facts(conn, tenant_id="default", text=said)
        assert sum(h["object"].startswith("last") for h in held) == 3, lang
        assert any(h["subject"] == person for h in held), lang
        assert len("\n".join(held_facts.held_line(h) for h in held)) <= DEEP_PASS_HELD_CHARS
        shown[lang] = len(held)
    assert shown["en"] == shown["ar"] and min(shown.values()) >= 18, shown


@pytest.mark.parametrize(("kept", "shown"), [(held_facts._NEWEST_KEPT, 3), (0, 0)])
def test_the_newest_are_kept_first_whatever_the_bound_drops(kept, shown):
    """(0: the control, and what it was.)"""
    facts = [{"id": f"b{n}", "shown": "user", "predicate": "noted", "object": "ك" * 190} for n in range(30)]
    newest = facts[-3:]
    within = held_facts._within(facts, newest[:kept], 2000)
    assert len(within) == 9 and len({f["id"] for f in newest} & within) == shown


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_control_a_bound_in_tokens_shows_fewer_in_arabic(lang):
    note = _LONG[lang][0]
    facts = [{"id": f"b{n}", "shown": "user", "predicate": "noted", "object": note.format(n=n)} for n in range(30)]
    used, in_tokens = 0, 0
    for fact in facts:
        used += estimate_text_tokens(held_facts.held_line({**fact, "ref": "f00"})) + 1
        in_tokens += used <= 1000
    assert (in_tokens, len(held_facts._within(facts, (), DEEP_PASS_HELD_CHARS))) == (
        {"en": 20, "ar": 13}[lang], {"en": 20, "ar": 20}[lang])


@pytest.mark.parametrize(("text", "named", "lost"), [
    ("Hana and Omar cheered: I left my company today.", ("hana", "omar"), "works_at"),
    ("هناء وعمر فرحوا لي، تركت الشركة اليوم", ("هناء", "عمر"), "works_at"),
    ("هناء وعمر صاروا ينادوني بدور", ("هناء", "عمر"), "name_is"),
    ("Hana and Omar call me Bader now", ("hana", "omar"), "name_is")])
def test_home_employer_and_name_have_their_places_before_resets_and_favourites(conn, text, named, lost):
    """Three services' next resets, newer, took the places of what Kazma keeps
    one value of (the review of step 1)."""
    for n, (name, value) in enumerate((("lives_in", "Kuwait"), ("works_at", "Acme"), ("name_is", "Sam"))):
        _hold(conn, "user", name, value, 1.0 + n, kind="functional")
    for person in named:
        for n in range(12):
            _hold(conn, person, "visited", f"place {n}", 100.0 + n)
    for n in range(20):
        _hold(conn, "user", "visited", f"city {n}", 200.0 + n)
    for n, name in enumerate(("grok_next_reset", "claude_next_reset", "cursor_next_reset", "favorite_color",
                              "favorite_ide", "favorite_language")):
        _hold(conn, "user", name, f"day {n}", 900.0 + n, kind="functional")
    held = {h["predicate"] for h in held_facts.held_facts(conn, tenant_id="default", text=text)}
    assert {"lives_in", "works_at", "name_is"} <= held and lost in held


def test_the_shares_are_the_places():
    assert sum(held_facts._SHARES.values()) == DEEP_PASS_HELD_FACTS


def test_one_person_with_many_facts_does_not_take_every_place(conn):
    for n in range(40):
        _hold(conn, "hana", "visited", f"place {n}", 100.0 + n)
        _hold(conn, "user", "visited", f"city {n}", 200.0 + n)
    _hold(conn, "omar", "works_as", "chef", 1.0)
    held = held_facts.held_facts(conn, tenant_id="default", text="Hana and Omar came over.")
    by = {s: sum(h["subject"] == s for h in held) for s in ("user", "hana", "omar")}
    assert by["omar"] == 1 and 0 < by["hana"] <= 10 and by["user"] >= 10 and len(held) == DEEP_PASS_HELD_FACTS


@pytest.mark.parametrize(("text", "people"), [
    ("Zain and Zain again, then Adam.", ("zain", "adam", "berta")),
    ("زين ثم زين مرة ثانية، وآدم", ("زين", "آدم", "بيرتا"))])
def test_who_is_named_most_and_first_has_the_places(conn, monkeypatch, text, people):
    """They went by the alphabet."""
    most, less, only_reply = people
    for person in people:
        for n in range(12):
            _hold(conn, person, "works_as", f"job {n}", 100.0 + n)
    said = [held_facts._Said(text), held_facts._Said(f"{text}\n{only_reply}"), held_facts._Said("")]
    others: dict = {}
    for s, p, o in conn.execute("SELECT subject, predicate, object FROM beliefs ORDER BY valid_from DESC").fetchall():
        others.setdefault(s, []).append({"id": f"{s}{o}", "subject": s, "predicate": p, "object": o})
    named, before = held_facts._named(said, others, text)
    assert [f["subject"] for f in named[:3]] == [most, less, only_reply] and before == []


_PRONOUNS = (("hana", "Hana is a pilot.", "She moved to Doha too."), ("omar", "I met Omar.", "He changed his job."),
             ("هناء", "هناء طيارة", "ونقلت الدوحة بعد"), ("عمر", "شفت عمر أمس", "وغير شغله"))


@pytest.mark.parametrize(("person", "before", "said"), _PRONOUNS)
def test_a_pronoun_speaks_of_who_the_turn_before_named(conn, person, before, said):
    _hold(conn, person, "works_as", "chef", 1.0)
    for n in range(40):
        _hold(conn, "user", "visited", f"city {n}", 100.0 + n)

    def shown(previous):
        return [h["subject"] for h in held_facts.held_facts(conn, tenant_id="default", text=said, previous=previous)
                if h["subject"] != "user"]

    assert shown((before, "Noted.")) == [person]
    assert shown(None) == []  # the control: nothing in the turn names them


def test_who_the_turn_before_named_comes_after_who_this_one_does(conn):
    for person in ("hana", "omar"):
        for n in range(12):
            _hold(conn, person, "works_as", f"{person} {n}", 100.0 + n)
    for n in range(40):
        _hold(conn, "user", "visited", f"city {n}", 200.0 + n)
    held = held_facts.held_facts(conn, tenant_id="default", text="Hana moved.", previous=("Omar called.", ""))
    by = {s: sum(h["subject"] == s for h in held) for s in ("hana", "omar", "user")}
    assert by["hana"] >= 7 and by["omar"] <= 1 and by["user"] >= 15, by


def test_eight_thousand_facts_of_one_subject_are_chosen_from_in_time(conn):
    import time

    facts = [{"id": f"b{n}", "subject": "hana", "predicate": "visited", "object": f"place {n}"} for n in range(8000)]
    said = [held_facts._Said("Hana and the place"), held_facts._Said(""), held_facts._Said("")]
    started = time.perf_counter()
    assert len(held_facts._named(said, {"hana": facts}, "Hana and the place")[0]) == held_facts._A_SUBJECT
    # Compared as dicts: 8,000 times 8,000. 0.1 s by id; a slow runner's room.
    assert time.perf_counter() - started < 5


def test_what_was_just_told_is_shown_whatever_the_turn_says(conn):
    """"Sorry, I meant the other one" holds no word of the fact it puts right."""
    for n in range(50):
        _hold(conn, "user", "visited", f"city {n}", float(n))
    for said in ("Sorry, I meant the other one.", "آسف، قصدي الثانية"):
        held = held_facts.held_facts(conn, tenant_id="default", text=said)
        assert {f"city {n}" for n in range(40, 50)} <= {h["object"] for h in held}


@pytest.mark.parametrize(("method", "told"), [("system_tool", set()), ("llm_inferred", {"related_to"})])
def test_the_link_to_a_subjects_hub_is_not_a_fact_to_replace(conn, method, told):
    mutate_belief(conn, "user", "related_to", "hana", predicate_type="set", extraction_method=method,
                  tenant_id="default", source_session="s", source_turn=1, now=5.0, private=True)
    _hold(conn, "user", "likes", "tea", 1.0)
    held = held_facts.held_facts(conn, tenant_id="default", text="Hana called.")
    assert {h["predicate"] for h in held} == {"likes"} | told


def test_the_hub_link_kazma_writes_is_the_one_left_out(conn):
    """The link the user told themself is a fact, and can be put right."""
    mutate_belief(conn, "user", "related_to", "hana", predicate_type="set", extraction_method="system_tool",
                  tenant_id="default", source_session="s", source_turn=1, now=5.0, private=True)
    mutate_belief(conn, "user", "related_to", "the Sabah family", predicate_type="set",
                  extraction_method="user_stated", tenant_id="default", source_session="s", source_turn=1, now=6.0,
                  private=True)
    held = held_facts.held_facts(conn, tenant_id="default", text="Hana called.")
    assert [h["object"] for h in held] == ["the Sabah family"]


def test_the_facts_are_named_from_what_the_model_reads(conn):
    """A name past the part of the turn the model is given names nobody."""
    from kazma_core.memory.limits import DEEP_PASS_QUESTION

    _hold(conn, "hana", "works_as", "pilot", 1.0)
    far = "x " * DEEP_PASS_QUESTION + "Hana is a captain now."
    assert held_facts.held_facts(conn, tenant_id="default", text=far) == []
    assert [h["subject"] for h in held_facts.held_facts(conn, tenant_id="default", text="Hi", answer="Hana called.")] \
        == ["hana"]


# ── The turn's stored vector ─────────────────────────────────────────────


def test_a_vector_of_another_model_is_not_compared(monkeypatch):
    from kazma_core.memory.embedder import serialize_f32_embedding
    from kazma_core.memory.worker_bootstrap import _stored_vector

    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: "m2")
    blob = serialize_f32_embedding([1.0, 0.5])
    assert _stored_vector({"embedding": blob, "embedding_model_version": "m2"}) == [1.0, 0.5]
    assert _stored_vector({"embedding": blob, "embedding_model_version": "m1"}) is None
    assert _stored_vector({"embedding": None, "embedding_model_version": "m2"}) is None


@pytest.mark.parametrize("stored", [b"abc", "text", 7, b"\x00" * 5])
def test_a_stored_vector_that_is_none_is_said_and_answers_none(monkeypatch, caplog, stored):
    from kazma_core.memory.worker_bootstrap import _stored_vector

    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: "m2")
    with caplog.at_level("WARNING", logger="kazma_core.memory.worker_bootstrap"):
        assert _stored_vector({"embedding": stored, "embedding_model_version": "m2"}) is None
    assert "could not be read" in caplog.text


def test_a_failure_while_the_turn_is_read_closes_both_connections(tmp_path, monkeypatch):
    """``keep`` was set before the facts were chosen, so a failure there left
    the two databases open."""
    from kazma_core.memory import worker_bootstrap
    from kazma_core.memory.schema_v2 import ensure_ops_schema

    for name, ensure in (("primary", ensure_primary_schema), ("ops", ensure_ops_schema)):
        with sqlite3.connect(tmp_path / f"{name}.db") as db:
            ensure(db)
            if name == "primary":
                db.execute("INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
                           "created_at, tier) VALUES ('e1', 'default', 's', 1, 'Hana moved to Doha', 'ok', 1, 'episodic')")
    monkeypatch.setattr("kazma_core.paths.primary_memory_db", lambda: str(tmp_path / "primary.db"))
    monkeypatch.setattr("kazma_core.paths.memory_ops_db", lambda: str(tmp_path / "ops.db"))
    opened: list[sqlite3.Connection] = []
    connect = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: opened.append(connect(*a, **kw)) or opened[-1])

    def broken(*_a):
        raise ValueError("no")

    monkeypatch.setattr(worker_bootstrap, "_turn_context", broken)
    with pytest.raises(ValueError, match="no"):
        worker_bootstrap._micro_consolidation_prepare("e1")
    assert len(opened) >= 2
    for conn in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")


def test_a_vector_search_that_fails_is_said_and_the_rest_is_shown(conn, monkeypatch, caplog):
    _hold(conn, "user", "likes", "tea", 1.0)

    def broken(*_a, **_kw):
        raise sqlite3.OperationalError("no")

    monkeypatch.setattr("kazma_core.memory.vector_engine.VectorEngine.search_beliefs", broken)
    with caplog.at_level("WARNING", logger="kazma_core.memory.held_facts"):
        held = held_facts.held_facts(conn, tenant_id="default", text="hi", vector=[0.1, 0.2])
    assert [h["object"] for h in held] == ["tea"] and "could not be read" in caplog.text
