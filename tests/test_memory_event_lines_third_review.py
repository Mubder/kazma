"""The third independent review of dated event lines (the memory plan's W4, of
0bbecc655, 2026-11-25).

Each point fails on 0bbecc655, in English or in Arabic. Through the product's
own apply step, recall and forget, on real databases.

* **H1/M1** An event is left out only when its words stand in text that is
  clearly pasted or another person's (``memory/pasted_text.py``). ``own_words``,
  which is for trust, lost 12 of 135 ordinary English openings and 60 Arabic
  ones. The openings, the pasted texts and the messages that must keep their
  event are in ``tests/fixtures/memory_events/openings.json``, twins in both
  languages.
* **M2, finding 21** Each event is shown once before any event's other
  occasions; a list's siblings come in a meaningful order.
* **M3** A value's own clitics are read. **M4** "Sorry, I meant Omar" corrects.
* **M5** A plan told as done is a plan. **M6** A model's "9999" loses nothing.
* **Low** What Arabic says a day by; a "no" before a noun; "also"; a closed
  entry archived since; "missed" is an event.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest
from kazma_core.memory import belief_extractor, event_text, fact_days, pasted_text
from kazma_core.memory import recall as recall_mod

from tests.test_memory_event_lines_review import TOLD, at, event, mem  # noqa: F401 - the fixture
from tests.test_memory_event_lines_second_review import DAY, _habits, _tell

# ruff: noqa: F811
CASES = json.loads((Path(__file__).parent / "fixtures/memory_events/openings.json").read_text(encoding="utf-8"))
LANG = {"en": 0, "ar": 1}


def _days(mem) -> list[str]:
    return [fact_days.event_day(meta) for _o, meta in mem.days()]


def _kept(mem, openings, *, lang: int) -> tuple[int, list[str]]:
    """How many of the fixture's events stay after each of *openings*, with
    their day, and the messages that lost one."""
    kept, lost = 0, []
    for opening in openings:
        for told in CASES["events"]:
            mem.db.execute("DELETE FROM beliefs")
            said = opening[lang].format(told["said"][lang])
            mem.tell([event(told["predicate"], told["value"][lang], told["day"])], said)
            if _days(mem) == [told["day"] or ""]:
                kept += 1
            else:
                lost.append(said)
    return kept, lost


# ── H1 / M1: what is clearly not the user telling ────────────────────────


@pytest.mark.parametrize("lang", sorted(LANG))
@pytest.mark.parametrize(("which", "least"), [("review", 135), ("more", 177)])
def test_an_ordinary_opening_loses_no_event(mem, which, least, lang):
    """main keeps every one of them, in each language."""
    kept, lost = _kept(mem, CASES["openings"][which], lang=LANG[lang])
    assert kept >= least, lost


@pytest.mark.parametrize("lang", sorted(LANG))
def test_what_is_clearly_pasted_is_kept_out(mem, lang):
    kept, _lost = _kept(mem, CASES["pasted"], lang=LANG[lang])
    assert kept == 0
    for opening in CASES["pasted"]:
        for told in CASES["events"]:
            mem.db.execute("DELETE FROM beliefs")
            said = opening[LANG[lang]].format(told["said"][LANG[lang]])
            mem.tell([event(told["predicate"], told["value"][LANG[lang]], told["day"])], said)
            assert mem.facts() == [], said


@pytest.mark.parametrize("case", CASES["kept"], ids=lambda c: c["case"])
def test_what_the_user_tells_stays(mem, case):
    mem.tell([event(case["predicate"], case["value"], case["day"])], case["said"])
    assert _days(mem) == [case["day"] or ""], pasted_text.told_and_pasted(case["said"])


@pytest.mark.parametrize("words", [10, 22, 60])
def test_a_long_message_loses_no_event(mem, words):
    for said, value, filler in (
            ("I sold my car yesterday. ", "my car", "We did many small things around the house and the garden. "),
            ("بعت سيارتي أمس. ", "سيارتي", "وسوينا أشياء صغيرة كثيرة في البيت والحديقة وكان الجو جميل. ")):
        mem.db.execute("DELETE FROM beliefs")
        mem.tell([event("sold", value, "2026-05-30")], said + filler * words)
        assert _days(mem) == ["2026-05-30"], len(said + filler * words)


@pytest.mark.parametrize("said", [
    # A list's fields are no chat's speakers, though their labels repeat.
    "Here's the info for my shoes:\n\n* Type: Boots\n* Color: Black\n\n* Type: Sandals\n* Color: Red, bought in May",
    "هذي معلومات أحذيتي:\n\n* النوع: جزمة\n* اللون: أسود\n\n* النوع: صندل Sandals\n* اللون: أحمر",
    # A text to work on that tells the user's own doings.
    "Write my story in an engaging way: I bought Sandals by accident.",
    "رتب قصتي: شريت Sandals بالصدفة.",
    # A text of theirs named, not shown.
    "Can you review my CV?\n\nI bought Sandals in May.",
    "ممكن تراجع سيرتي الذاتية؟\n\nشريت Sandals في مايو.",
    # What they will say themself.
    'I\'ll just say something like, "Hey friends, I bought Sandals in May!"',
    'بقول لهم: "يا جماعة Sandals وصلت"',
])
def test_the_users_own_lists_and_stories_stay(mem, said):
    """Read in the pilots' cached replies: 7 of the 9 events the first form of
    the rule still dropped were the user's own."""
    mem.tell([event("bought", "Sandals", None)], said)
    assert mem.facts() == [("bought", "Sandals")], pasted_text.told_and_pasted(said)


@pytest.mark.parametrize(("said", "told"), [
    ("Omar: I bought a Rolex yesterday", True),  # one label alone says nothing: "The story: ..."
    ("عمر: شريت رولكس أمس", True),
    ("Monday: I bought a Rolex yesterday\nTuesday: I rested", True),
    ("الاثنين: شريت رولكس أمس\nالثلاثاء: ارتحت", True),
    ("London: I bought a Rolex yesterday\nParis: we walked", True),
    ("Omar: I bought a Rolex yesterday\nSara: congrats", False),
    ("عمر: شريت رولكس أمس\nسارة: مبروك", False),
    ("Omar: I bought a Rolex yesterday\nShould I believe Omar?", False),
    ("عمر: شريت رولكس أمس\nأصدق عمر؟", False),
    ("My brother: I bought a Rolex yesterday", False),
    ("أخوي: شريت رولكس أمس", False),
])
def test_a_label_is_a_speakers_when_the_message_shows_it(said, told):
    own, pasted = pasted_text.told_and_pasted(said)
    assert ("Rolex" in own or "رولكس" in own) is told, (own, pasted)


@pytest.mark.parametrize(("said", "own"), [
    ("Here's the mail:\n\nHi Ali,\n\nI bought a Rolex.\n\nBest,\nOmar\n\nYesterday I adopted a cat.", "I adopted a cat"),
    ("هذا الإيميل:\n\nهلا علي،\n\nشريت رولكس.\n\nتحياتي،\nعمر\n\nأمس تبنيت قطوة.", "تبنيت قطوة"),
    ("---------- Forwarded message ---------\nFrom: Omar\nI bought a Rolex.\n\nBy the way, I adopted a cat.",
     "I adopted a cat"),
    ("---------- رسالة محولة ---------\nمن: عمر\nشريت رولكس.\n\nبالمناسبة تبنيت قطوة.", "تبنيت قطوة"),
])
def test_a_letter_ends_at_its_sign_off_or_an_aside(said, own):
    told, pasted = pasted_text.told_and_pasted(said)
    assert own in told and "Rolex" not in told and "رولكس" not in told
    assert own not in pasted


@pytest.mark.parametrize("make", [
    lambda n: "a" * n, lambda n: "1 " * (n // 2), lambda n: "\n" * n, lambda n: '"I ' * (n // 3),
    lambda n: "والسبت " * (n // 7), lambda n: "> x\n" * (n // 4), lambda n: "Omar: hi\n" * (n // 9),
    lambda n: "Translate this: x\n\n" * (n // 19), lambda n: "قال: " * (n // 5), lambda n: "I " * (n // 2),
    lambda n: "From: a\n" * (n // 8), lambda n: "a, he says. " * (n // 12), lambda n: "```\n" * (n // 4),
    lambda n: "شريتها " * (n // 7), lambda n: "I bought " * (n // 9), lambda n: "12:30\n" * (n // 6),
    lambda n: " " * n + "x", lambda n: "Best,\n\n" * (n // 7), lambda n: "[12:01] a: " * (n // 11),
])
def test_parting_a_hostile_message_stays_fast(make):
    pasted_text.told_and_pasted.cache_clear()
    event_text.stands_elsewhere("a trip to Rome", make(16000))  # what is loaded once
    pasted_text.told_and_pasted.cache_clear()
    started = time.perf_counter()
    event_text.stands_elsewhere("a trip to Rome", make(64000), day="2019-03-03", said_on=TOLD)
    assert time.perf_counter() - started < 1.0


def test_control_own_words_as_the_gate_loses_the_openings(mem, monkeypatch):
    """Negative control (H1): the trust reading as the gate, as it was."""
    from kazma_core.memory.own_words import own_words

    def by_trust(text: str) -> tuple[str, str]:
        return own_words(text, questions=True), text

    monkeypatch.setattr(pasted_text, "told_and_pasted", by_trust)
    kept = {lang: _kept(mem, CASES["openings"]["review"], lang=n)[0] for lang, n in LANG.items()}
    assert kept["ar"] < 90 and kept["en"] < 135, kept


def test_an_events_verb_list_holds_what_the_pilots_named():
    for name in ("missed", "acquired", "rented", "wore", "experienced", "helped"):
        assert event_text.is_event_name(name), name


# ── M2 / finding 21: everything once, first ──────────────────────────────


def _ordered(mem, question: str, found: list[str], *, listing: bool = False, limit: int = 5):
    """What a question shows of facts ranked in the order *found* (their
    values): the hits, and their rows."""
    rows = {str(r["id"]): r for r in mem.db.execute("SELECT * FROM beliefs WHERE valid_until IS NULL")}
    ranked = []
    for value in found:  # recall ranks one occasion of an event: the first found
        bid = next(b for b, r in rows.items() if r["object"] == value)
        ranked.append(recall_mod.RecallHit(id=bid, content=value, score=1.0, kind="belief"))
    of_found = {h.id: rows[h.id] for h in ranked}
    ask = recall_mod._ASK.set(recall_mod._time_ask(question, at("2026-06-10")))
    lists = recall_mod._LISTING.set(listing)
    try:
        order = recall_mod._in_order(question, ranked, of_found, mem.db, "default", limit)
        return recall_mod._with_the_rest(recall_mod._finish_beliefs(order, of_found, None), of_found, limit), of_found
    finally:
        recall_mod._LISTING.reset(lists)
        recall_mod._ASK.reset(ask)


def _doctors(mem, other: str, dentist: str) -> None:
    mem.tell([event("visited", other, "2026-05-03")], other, turn=1, on="2026-05-04")
    for i in range(6):
        mem.tell([event("visited", dentist, f"2026-05-{i + 5:02d}")], dentist, turn=50 + i, on=f"2026-05-{i + 6:02d}")


@pytest.mark.parametrize(("dentist", "other", "question"), [
    ("the dentist", "the eye doctor", "Did I visit the dentist or the eye doctor last month?"),
    ("the dentist", "the eye doctor", "When did I visit the dentist and the eye doctor last month?"),
    ("دكتور الأسنان", "دكتور العيون", "هل زرت دكتور الأسنان أو دكتور العيون الشهر الماضي؟"),
    ("دكتور الأسنان", "دكتور العيون", "متى زرت دكتور الأسنان ودكتور العيون الشهر الماضي؟"),
])
def test_a_habit_never_hides_the_other_event_of_a_period(mem, dentist, other, question):
    _doctors(mem, other, dentist)
    hits, _rows = _ordered(mem, question, [dentist, other])
    assert [dentist in hits[0].content, other in hits[1].content] == [True, True], [h.content for h in hits]
    # The occasions the block has no room for are counted, not lost.
    shown = [h for h in hits if dentist in h.content]
    assert len(hits) == 5 and len(shown) + int(shown[0].metadata["more"]) == 6
    assert [h.metadata.get("more") for h in shown[1:]] == [None] * (len(shown) - 1)


def test_a_period_shows_its_own_occasions_only(mem):
    """The event's latest is of another month: the period's own stands first."""
    for turn, day in enumerate(("2026-04-02", "2026-04-20", "2026-05-25"), 1):
        mem.tell([event("visited", "the dentist", day)], "the dentist", turn=turn, on=day)
    shown = mem.block("What did I do at the dentist in April?", now="2026-06-10")
    assert "20 Apr" in shown and "2 Apr" in shown and "May" not in shown, shown


def _places(mem, gym: str, places: list[str], visits: int) -> None:
    for i, place in enumerate(places):
        mem.tell([event("visited", place, f"2026-05-{i + 3:02d}")], place, turn=i + 1, on="2026-05-20")
    for i in range(visits):
        mem.tell([event("visited", gym, f"2026-05-{i + 5:02d}")], gym, turn=50 + i, on=f"2026-05-{i + 6:02d}")


@pytest.mark.parametrize(("gym", "places", "question"), [
    ("the old gym", ["the Sadu House", "the Grand Mosque", "Failaka Island", "the Scientific Center", "Souq Mubarakiya"],
     "List all the places I visited, the gym too"),
    ("النادي القديم", ["بيت السدو", "المسجد الكبير", "جزيرة فيلكا", "المركز العلمي", "سوق المباركية"],
     "عدد كل الأماكن اللي زرتها والنادي"),
])
def test_a_list_shows_every_place_before_a_habits_occasions(mem, gym, places, question):
    """A habit the question does not name: its occasions come last."""
    _places(mem, gym, places, 20)
    named = " ".join(w for w in question.split() if "gym" not in w and "النادي" not in w)
    hits, _rows = _ordered(mem, named, [gym], listing=True, limit=12)
    contents = [h.content for h in hits]
    assert [any(p in c for c in contents[:6]) for p in places] == [True] * 5, contents
    assert len({h.id for h in hits}) == 12 and int(hits[0].metadata["more"]) == 20 - (12 - 5)


def test_a_lists_siblings_are_those_asked_about_then_the_newest(mem):
    """Finding 21: they were read in the order of their ids."""
    told = [("a red betta fish", "2026-01-05"), ("jazz", "2026-02-05"), ("neon tetra fish", "2026-03-05"),
            ("chess", "2026-04-05")]
    for turn, (value, on) in enumerate(told, 1):
        mem.tell([event("interested_in", value, None)], f"I'm interested in {value}.", turn=turn, on=on)
    hits, rows = _ordered(mem, "List all the fish I like", ["chess"], listing=True, limit=12)
    assert [rows[h.id]["object"] for h in hits] == ["chess", "neon tetra fish", "a red betta fish", "jazz"]


def test_a_lists_siblings_are_read_in_order_in_arabic_too(mem):
    told = [("سمكة بيتا حمراء", "2026-01-05"), ("الجاز", "2026-02-05"), ("سمكة نيون", "2026-03-05"),
            ("الشطرنج", "2026-04-05")]
    for turn, (value, on) in enumerate(told, 1):
        mem.tell([event("interested_in", value, None)], value, turn=turn, on=on)
    hits, rows = _ordered(mem, "عدد كل سمكة أحبها", ["الشطرنج"], listing=True, limit=12)
    assert [rows[h.id]["object"] for h in hits] == ["الشطرنج", "سمكة نيون", "سمكة بيتا حمراء", "الجاز"]


def test_control_siblings_in_the_order_of_their_ids(mem, monkeypatch):
    """Negative control (finding 21): neither the question nor the day orders them."""
    monkeypatch.setattr(recall_mod, "_named_by", lambda query: lambda row: False)
    monkeypatch.setattr(recall_mod, "_told_or_happened", lambda row: 0.0)
    for turn, (value, on) in enumerate([("a red betta fish", "2026-01-05"), ("jazz", "2026-02-05"),
                                        ("neon tetra fish", "2026-03-05"), ("chess", "2026-04-05")], 1):
        mem.tell([event("interested_in", value, None)], value, turn=turn, on=on)
    hits, rows = _ordered(mem, "List all the fish I like", ["chess"], listing=True, limit=12)
    assert [rows[h.id]["object"] for h in hits[1:]] == [
        r["object"] for r in sorted((rows[h.id] for h in hits[1:]), key=lambda r: str(r["id"]), reverse=True)]


def test_a_list_naming_the_habit_still_shows_its_occasions(mem, monkeypatch):
    _habits(mem, monkeypatch, 3, arabic=False)
    shown = [line for line in mem.block("List all my visits to the dentist", now="2026-06-01").splitlines()
             if "visited the dentist" in line]
    assert len(shown) == 3 and not any("more]" in line for line in shown), shown


def test_control_every_occasion_in_its_place_hides_the_other_event(mem, monkeypatch):
    """Negative control (M2): the occasions right after their first, as they were."""
    real = recall_mod._by_event

    def in_place(ranked, rows, conn, tenant_id):
        firsts, others = real(ranked, rows, conn, tenant_id)
        out = []
        for hit in firsts:
            key = recall_mod._event_of(rows.get(hit.id))
            out += [hit, *(o for o in others if key and recall_mod._event_of(rows.get(o.id)) == key)]
        return out, []

    monkeypatch.setattr(recall_mod, "_by_event", in_place)
    _doctors(mem, "the eye doctor", "the dentist")
    hits, _rows = _ordered(mem, "Did I visit the dentist or the eye doctor last month?",
                           ["the dentist", "the eye doctor"])
    assert not any("eye doctor" in hit.content for hit in hits)


# ── M3: the value's own clitics ──────────────────────────────────────────


@pytest.mark.parametrize(("value", "text", "stands"), [
    ("دراجة بـ٩٥ دينار", "كان سعرها ٩٥ دينار", True),
    ("عشاء بمطعم", "تعشينا في المطعم", True),
    ("سفر لدبي", "سافرنا إلى دبي", True),
    ("رحلة لدبي", "كنت بدبي", True),
    ("كلب", "علبة حليب", False),  # two letters left are in anything
    ("بيت", "كيت", False),
])
def test_a_values_own_clitics_are_read(value, text, stands):
    assert (event_text._share(event_text._wanted(value), text) >= event_text.EVENT_WORDS) is stands


@pytest.mark.parametrize(("first", "said", "then", "put_right", "kept"), [
    ("a bike for $900", "I bought a bike for $900 yesterday", "a bike for $950", "Sorry, it was $950",
     "a bike for $950"),
    ("دراجة بـ٩٠ دينار", "شريت دراجة بـ٩٠ دينار أمس", "دراجة بـ٩٥ دينار", "كان سعرها ٩٥", "دراجة بـ٩٥ دينار"),
])
def test_a_price_is_put_right_in_either_language(mem, first, said, then, put_right, kept):
    rows = _tell(mem, (TOLD, said, [event("bought", first, "2026-05-30")]),
                 ("2026-06-01", put_right, [event("bought", then, None, replaces="f1")]))
    assert rows == [("bought", kept, "2026-05-30")]


# ── M4 and the smaller denials ───────────────────────────────────────────


@pytest.mark.parametrize(("first", "said", "then", "put_right"), [
    ("Ali", "I met Ali yesterday", "Omar", "Sorry, I meant Omar"),
    ("علي", "قابلت علي أمس", "عمر", "آسف أقصد عمر"),
    ("Ali", "I met Ali yesterday", "Omar", "My mistake, it was Omar"),
    ("علي", "قابلت علي أمس", "عمر", "غلطت، كان عمر"),
])
def test_a_correction_need_not_name_what_was_wrong(mem, first, said, then, put_right):
    rows = _tell(mem, (TOLD, said, [event("met", first, "2026-05-30")]),
                 (TOLD, put_right, [event("met", then, None, replaces="f1")]))
    assert rows == [("met", then, "2026-05-30")]


@pytest.mark.parametrize(("first", "said", "then", "told"), [
    ("Ali", "I met Ali yesterday", "Omar", "No time today, I met Omar"),
    ("علي", "قابلت علي أمس", "عمر", "ما عندي وقت، قابلت عمر"),
])
def test_a_broad_no_needs_the_old_value_named(mem, first, said, then, told):
    rows = _tell(mem, (TOLD, said, [event("met", first, "2026-05-30")]),
                 (TOLD, told, [event("met", then, None, replaces="f1")]))
    assert [value for _p, value, _d in rows] == [first, then]


@pytest.mark.parametrize(("value", "said", "told"), [
    ("the dentist", "I visited the dentist yesterday", "I also visited the dentist on 2 May, no cavities"),
    ("the dentist", "I visited the dentist yesterday", "I visited the dentist on 2 May, no cavities"),
    ("the dentist", "I visited the dentist yesterday", "Actually I also visited the dentist on 2 May"),
    ("دكتور الأسنان", "زرت دكتور الأسنان أمس", "زرت دكتور الأسنان يوم ٢ مايو، لا تسوس ولا شي"),
    ("دكتور الأسنان", "زرت دكتور الأسنان أمس", "الصراحة زرت دكتور الأسنان كمان يوم ٢ مايو، مو مرة وحدة"),
])
def test_a_no_before_a_noun_or_beside_also_moves_no_day(mem, value, said, told):
    rows = _tell(mem, (TOLD, said, [event("visited", value, "2026-05-30")]),
                 ("2026-06-20", told, [event("visited", value, "2026-05-02", replaces="f1")]))
    assert sorted(d for _p, _v, d in rows) == ["2026-05-02", "2026-05-30"]


@pytest.mark.parametrize(("first", "said", "then", "told", "day"), [
    ("bananas", "I bought bananas yesterday", "a lamp", "I also bought a lamp yesterday", "2026-05-30"),
    ("موز", "شريت موز أمس", "لمبة", "وشريت بعد لمبة أمس", "2026-05-30"),
    ("Rome", "I visited Rome", "Milan", "I also visited Milan", None),
    ("روما", "زرت روما", "ميلان", "زرت ميلان كمان", None),
])
def test_one_more_closes_nothing(mem, first, said, then, told, day):
    rows = _tell(mem, (TOLD, said, [event("bought", first, day)]),
                 (TOLD, told, [event("bought", then, day, replaces="f1")]))
    assert [value for _p, value, _d in rows] == [first, then]


@pytest.mark.parametrize(("said", "denies", "corrects"), [
    ("No, it was Friday", True, False), ("لا، كان الجمعة", True, False),
    ("It was not Paris", True, False), ("ما كان باريس، مو باريس", True, False),
    ("Sorry, I meant Friday", True, True), ("آسف، أقصد الجمعة", True, True),
    ("I went to the gym, no pain", False, False), ("رحت النادي، لا ألم ولا شي", False, False),
    ("Not Paris, and I also went to Lyon", False, False), ("مو باريس، ورحت ليون كمان", False, False),
    ("> Sorry, I meant Friday\nWhat is this?", False, False), ("> آسف، أقصد الجمعة\nشنو هذا؟", False, False),
])
def test_what_denies_and_what_corrects(said, denies, corrects):
    assert (event_text.denies(said), event_text.corrects(said)) == (denies, corrects)


# ── M5: a plan told as done ──────────────────────────────────────────────


@pytest.mark.parametrize(("said", "predicate", "value", "day", "planned"), [
    ("I'm going to the jazz concert tomorrow", "attended", "the jazz concert", "2026-06-01", True),
    ("رايح حفلة الجاز بكرة", "attended", "حفلة الجاز", "2026-06-01", True),
    ("I will sell my car next Friday", "sold", "my car", "2026-06-05", True),
    ("راح أبيع سيارتي الجمعة الجاية", "sold", "سيارتي", "2026-06-05", True),
    ("I'm visiting Dubai on 3 June", "visited", "Dubai", "2026-06-03", True),
    ("بزور دبي يوم ٣ يونيو", "visited", "دبي", "2026-06-03", True),
    ("I'm visiting Dubai in August 2027", "visited", "Dubai", "2027-08", True),
    # The model's working out: the day is not told as one to come.
    ("I got a 50-inch TV on Black Friday", "bought", "a 50-inch TV", "2026-11-27", False),
    ("شريت تلفزيون في الجمعة البيضاء", "bought", "تلفزيون", "2026-11-27", False),
    ("I finished the book on 10 December", "finished", "the book", "2026-12-10", False),
    ("خلصت الكتاب يوم 10 ديسمبر", "finished", "الكتاب", "2026-12-10", False),
    ("I sold my car last Friday", "sold", "my car", "2026-06-05", False),
    ("بعت سيارتي الجمعة اللي فاتت", "sold", "سيارتي", "2026-06-05", False),
    # Told as done, the date ahead: a year left out, or a slip (the pilots').
    ("I finished the book on 20 June, it was great", "finished", "the book", "2026-06-20", False),
    ("خلصت الكتاب يوم ٢٠ يونيو وكان حلو", "finished", "الكتاب", "2026-06-20", False),
    ("I'm planning my week. I attended a workshop on 5 June.", "attended", "a workshop", "2026-06-05", False),
    ("ناوي أرتب أسبوعي. حضرت ورشة يوم ٥ يونيو.", "attended", "ورشة", "2026-06-05", False),
    ("> I'm going to the jazz concert tomorrow\nthe jazz concert sounds fun", "attended", "the jazz concert",
     "2026-06-01", False),
])
def test_a_plan_told_as_done_is_planned(mem, said, predicate, value, day, planned):
    mem.tell([event(predicate, value, day)], said)
    ((_value, meta),) = mem.days()
    assert (meta.get("planned"), bool(meta.get("happened"))) == (day if planned else None, False)


@pytest.mark.parametrize(("plan", "went"), [
    ("I'm going to the jazz concert tomorrow", "I went to the jazz concert"),
    ("رايح حفلة الجاز بكرة", "رحت حفلة الجاز"),
])
@pytest.mark.parametrize("day", ["2026-06-01", None])
def test_such_a_plan_comes_to_pass(mem, plan, went, day):
    value = "the jazz concert" if plan.startswith("I") else "حفلة الجاز"
    rows = _tell(mem, (TOLD, plan, [event("attended", value, "2026-06-01")]),
                 ("2026-06-02", went, [event("attended", value, day, replaces="f1")]))
    assert rows == [("attended", value, "2026-06-01")]
    assert mem.days()[0][1].get("happened") is True


@pytest.mark.parametrize(("said", "then"), [("I have the jazz concert on 3 June", "I missed the jazz concert"),
                                            ("عندي حفلة الجاز يوم ٣ يونيو", "فاتتني حفلة الجاز")])
def test_a_plan_missed_is_kept(mem, said, then):
    value = "the jazz concert" if said.startswith("I") else "حفلة الجاز"
    rows = _tell(mem, (TOLD, said, [event("scheduled_event", value, "2026-06-03")]),
                 ("2026-06-12", then, [event("missed", value, None, replaces="f1")]))
    assert rows == [("missed", value, "2026-06-03")]


# ── M6: a day past every calendar ────────────────────────────────────────


@pytest.mark.parametrize("day", ["9999", "9999-12", "9999-12-31", "2200-01-01"])
@pytest.mark.parametrize("predicate", ["scheduled_event", "attended"])
def test_a_day_past_every_calendar_loses_no_fact(mem, predicate, day):
    mem.tell([event(predicate, "the concert", day), event("bought", "a watch", None)],
             "I have the concert, and I bought a watch")
    assert sorted(mem.facts()) == sorted([(predicate, "the concert"), ("bought", "a watch")])
    assert _days(mem) == ["", ""]


@pytest.mark.parametrize("day", ["9999", "9999-12", "0001-01", "0001"])
def test_says_day_answers_for_any_year(day):
    from kazma_core.memory.time_words import says_day, says_to_come

    assert says_day("I have the concert tomorrow", day, TOLD) is False
    assert says_to_come("I have the concert tomorrow", day, TOLD) is False


# ── Low: what Arabic says a day by ───────────────────────────────────────


@pytest.mark.parametrize(("english", "arabic", "day"), [
    ("three days ago", "قبل ثلاث أيام", "2026-05-28"),
    ("three weeks ago", "قبل ثلاثة أسابيع", "2026-05-10"),
    ("a few days ago", "قبل كم يوم", "2026-05-28"),
    ("five months ago", "قبل خمسة أشهر", "2025-12-31"),
    ("on Wednesday", "الربوع", "2026-05-27"),
    ("last Thursday", "خميس الماضي", "2026-05-28"),
    ("the day before yesterday", "أول البارح", "2026-05-29"),
    ("in March", "شهر ٣", "2026-03"),
    ("this morning", "الصبح", "2026-05-31"),
    ("yesterday morning", "أمس الصبح", "2026-05-30"),
])
def test_a_day_is_said_in_arabic_as_in_english(english, arabic, day):
    from kazma_core.memory.time_words import says_day

    assert says_day(f"I finished it {english}", day, TOLD), english
    assert says_day(f"خلصته {arabic}", day, TOLD), arabic


@pytest.mark.parametrize("said", ["قابلت خميس أمس", "ما شفت أحد", "عندي اثنين", "قبل ثلاث ساعات"])
def test_a_name_or_a_number_is_no_day(said):
    from kazma_core.memory.time_words import says_day

    assert not says_day(said, "2026-05-28", TOLD)


# ── Low: a closed entry archived since ───────────────────────────────────


@pytest.mark.parametrize(("first", "then"), [
    ("a pottery exhibition", "the pottery exhibition at Dar Al Athar"), ("معرض فخار", "معرض فخار في دار الآثار")])
def test_forgetting_a_retelling_opens_an_archived_entry(mem, first, then):
    from kazma_core.memory.macro_sleep import run_macro_sleep

    mem.episode(1, "one")
    second = mem.episode(2, "two")
    mem.tell([event("attended", first, "2026-05-30")], first, turn=1)
    mem.tell([event("attended", then, "2026-05-30", replaces="f1")], then, turn=2, held=mem.held()[0])
    assert run_macro_sleep(mem.db, tenant_id="default", now=at(TOLD) + 400 * DAY)["archived_beliefs"] == 1
    mem.forget(second)
    assert mem.facts() == [("attended", first)] and _days(mem) == ["2026-05-30"]
    assert mem.db.execute("SELECT COUNT(*) FROM beliefs_archive").fetchone()[0] == 0


def test_an_archived_entry_whose_own_turn_is_forgotten_never_returns(mem):
    from kazma_core.memory.macro_sleep import run_macro_sleep

    one, two = mem.episode(1, "one"), mem.episode(2, "two")
    mem.tell([event("attended", "a pottery exhibition", "2026-05-30")], "a pottery exhibition", turn=1)
    mem.tell([event("attended", "the pottery exhibition at Dar Al Athar", "2026-05-30", replaces="f1")],
             "the pottery exhibition at Dar Al Athar", turn=2, held=mem.held()[0])
    run_macro_sleep(mem.db, tenant_id="default", now=at(TOLD) + 400 * DAY)
    mem.forget(one)
    mem.forget(two)
    assert mem.facts() == []
    for (blob,) in mem.db.execute("SELECT original_belief_json FROM beliefs_archive"):
        assert "pottery" not in blob


def test_control_without_the_archive_the_entry_is_lost(mem, monkeypatch):
    """Negative control (L5)."""
    from kazma_core.memory import forget
    from kazma_core.memory.macro_sleep import run_macro_sleep

    monkeypatch.setattr(forget, "_archived_closed", lambda conn, scope, params: {})
    mem.episode(1, "one")
    second = mem.episode(2, "two")
    mem.tell([event("attended", "a pottery exhibition", "2026-05-30")], "a pottery exhibition", turn=1)
    mem.tell([event("attended", "the pottery exhibition at Dar Al Athar", "2026-05-30", replaces="f1")],
             "the pottery exhibition at Dar Al Athar", turn=2, held=mem.held()[0])
    run_macro_sleep(mem.db, tenant_id="default", now=at(TOLD) + 400 * DAY)
    mem.forget(second)
    assert mem.facts() == []


def test_a_store_without_the_archive_still_forgets(tmp_path):
    """An old store: no table, no column."""
    from kazma_core.memory.forget import _archived_closed

    conn = sqlite3.connect(tmp_path / "old.db")
    assert _archived_closed(conn, "", []) == {}
    conn.close()


def test_in_users_words_is_still_asked_for_an_undated_event(mem):
    mem.tell([event("bought", "a watch", None)], "Translate: I bought a watch")
    assert mem.facts() == []
    assert not belief_extractor._in_users_words("a watch", "Translate: I bought a watch")
