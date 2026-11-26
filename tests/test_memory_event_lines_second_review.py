"""The second independent review of dated event lines (the memory plan's W4,
of 174057e52, 2026-11-24).

Each point fails on 174057e52, in English or in Arabic. Through the product's
own apply step, recall and block, and forget, on real databases.

* **N1/N6** An event is somebody else's only when its words stand in what
  ``own_words`` removed: compared as search compares words, so "لدبي" holds
  "دبي" -- 30 of 30 English travel sentences kept their event, 12 of 30 Arabic.
* **N2** One line for an event, however often it happened: five visits to the
  dentist hid "my dentist is Dr Sara".
* **N3** ``replaces`` on the same day is a correction whatever the words.
* **N4** A single-valued label holds unless the name is an event's verb.
* **N7** A name that says it happened keeps no day to come; a plan attended is
  what happened.
* **Choices 6 and 1** A day is the user's own words' day, and one they counted
  ("yesterday") stands unless they deny it.
* **N8** The held list shows an event's day.
* **Low** One shared word is a kind of thing; forgetting a retelling opens the
  event it closed; the chat's forget counts the days it took back; a write
  reads a bounded number of entries. And the two mutations no test caught.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from datetime import UTC, datetime

import pytest
from kazma_core.memory import belief_extractor, belief_mutation, fact_days
from kazma_core.memory import recall as recall_mod
from kazma_core.memory.belief_mutation import mutate_belief

from tests.test_memory_event_lines_review import TOLD, at, event, mem  # noqa: F401 - the fixture

# ruff: noqa: F811
DAY = 86400


def _single(predicate: str, value: str, on: str | None = None, label: str = "functional") -> dict:
    return event(predicate, value, on, predicate_type=label)


def _tell(mem, *steps) -> list[tuple]:
    """Each step (day, said, beliefs) told in turn, with the facts held shown."""
    for turn, (on, said, beliefs) in enumerate(steps, 1):
        mem.tell([dict(b) for b in beliefs], said, turn=turn, on=on, held=mem.held()[0])
    return [(r["predicate"], r["object"], fact_days.event_day(json.loads(r["metadata_json"] or "{}")))
            for r in mem._current()]


# ── N1 / N6: whose words an event is ─────────────────────────────────────

PLACES = [("Dubai", "دبي"), ("Qatar", "قطر"), ("Turkey", "تركيا"), ("Istanbul", "إسطنبول"), ("Paris", "باريس"),
          ("London", "لندن"), ("Cairo", "القاهرة"), ("Riyadh", "الرياض"), ("Bahrain", "البحرين"), ("Jordan", "الأردن")]
TRAVEL = [("I travelled to {} last week", "سافرت ل{} الأسبوع الماضي"), ("I was in {} yesterday", "كنت ب{} أمس"),
          ("I went with my family to {} on Friday", "رحت مع أهلي ل{} يوم الجمعة")]


def _joined(text: str, place: str) -> str:
    """The preposition written onto the place, as Arabic writes it ("للرياض")."""
    before, after = text.split("{}")
    if before.endswith("ل") and place.startswith("ال"):
        return before + "ل" + place[2:] + after
    return before + place + after


@pytest.mark.parametrize(("english", "arabic"), PLACES)
def test_a_trip_is_kept_in_either_language(mem, english, arabic):
    for said_en, said_ar in TRAVEL:
        for said, place in ((said_en.format(english), english), (_joined(said_ar, arabic), arabic)):
            mem.db.execute("DELETE FROM beliefs")
            mem.tell([event("travelled_to", place, "2026-05-28")], said)
            assert mem.facts() == [("travelled_to", place)], said


EVERYDAY = [
    ("We had dinner at a Lebanese restaurant yesterday", "dinner at a Lebanese restaurant",
     "تعشينا أمس بمطعم لبناني", "عشاء في مطعم لبناني"),
    ("I went to the gym on Monday", "the gym", "رحت للنادي يوم الاثنين", "النادي"),
    ("I travelled to Istanbul with my family last week", "Istanbul with my family",
     "سافرت لإسطنبول مع أهلي الأسبوع الماضي", "إسطنبول مع الأهل"),
    ("I sold my car yesterday", "my car", "بعت سيارتي أمس", "السيارة"),
    ("I lost my phone yesterday", "my phone", "ضيعت تلفوني أمس", "التلفون"),
    ("I finished my book yesterday", "the book", "كملت كتابي أمس", "الكتاب"),
    ("I had lunch at a restaurant", "a restaurant", "تغديت بمطعم أمس", "مطعم"),
    ("I joined a club yesterday", "a club", "اشتركت بنادي أمس", "نادي"),
    ("I took my kids to the zoo on Friday", "my kids to the zoo", "وديت عيالي لحديقة الحيوان الجمعة",
     "العيال إلى حديقة الحيوان"),
    ("I had a checkup at the clinic", "a checkup at the clinic", "سويت فحص بالعيادة اليوم", "فحص في العيادة"),
    ("I planted a lemon tree in the garden", "a lemon tree in the garden", "زرعت شجرة ليمون بالحديقة أمس",
     "شجرة ليمون في الحديقة"),
    ("I got a promotion at work two weeks ago", "a promotion at work", "ترقيت بالشغل قبل أسبوعين", "ترقية في الشغل"),
    # A paraphrase: its words in neither the text removed nor the words kept.
    ("I finally decluttered and organized my closet today", "decluttering and organizing my closet",
     "رتبت خزانتي اليوم", "ترتيب الخزانة"),
    ("I checked in on Friday afternoon at the lobby bar", "the Hilton LA Downtown",
     "سجلت دخولي الجمعة العصر عند اللوبي", "فندق هيلتون"),
]


@pytest.mark.parametrize(("english", "value", "arabic", "value_ar"), EVERYDAY)
def test_an_everyday_event_is_the_users_in_either_language(english, value, arabic, value_ar):
    for said, obj in ((english, value), (arabic, value_ar)):
        assert belief_extractor._in_users_words(obj, said, "", "2026-05-29", TOLD), said


@pytest.mark.parametrize(("english", "value", "arabic", "value_ar"), [
    ("Fix the grammar of this email from my client:\n> I bought a Rolex for $9,000 on 12 May 2026.",
     "a Rolex for $9,000", "صحح إيميل العميل:\n> اشتريت ساعة رولكس يوم 12 مايو 2026.", "ساعة رولكس"),
    ("My sister said: I sold my car yesterday.", "my car", "أختي قالت: بعت سيارتي أمس.", "السيارة"),
])
def test_what_the_user_did_not_tell_is_kept_out_in_either_language(english, value, arabic, value_ar):
    for said, obj in ((english, value), (arabic, value_ar)):
        assert not belief_extractor._in_users_words(obj, said, "", "2026-05-30", TOLD), said


@pytest.mark.parametrize(("said", "value"), [
    ("Translate to Dutch: I received a prayer plant in the mail.", "a prayer plant"),
    ("ترجم: وصلتني نبتة بالبريد.", "نبتة"),
])
def test_an_undated_event_of_a_paste_is_kept_out_too(mem, said, value):
    """Finding 20: the check read dated events only."""
    mem.tell([event("received", value, None)], said)
    assert mem.facts() == []
    mem.tell([event("received", value, None)], said.split(":", 1)[1].strip(), turn=2)
    assert mem.facts() == [("received", value)]


# ── N2: one line for an event ────────────────────────────────────────────


class _Words:
    """An embedder of words: texts share meaning as they share words."""

    dim = 256

    def encode(self, text: str) -> list[float]:
        from kazma_core.memory.query_terms import content_terms

        vec = [0.0] * self.dim
        for word in content_terms(text):
            vec[int(hashlib.sha256(word.encode()).hexdigest(), 16) % self.dim] += 1.0
        size = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / size for x in vec]


STATED = [("dentist_is", "Dr Sara"), ("lives_in", "Kuwait City"), ("works_at", "Gulf Bank"), ("allergic_to", "peanuts"),
          ("has_child", "a daughter named Noor"), ("favorite_color", "teal"), ("gym_is", "Platinum Gym"),
          ("drives", "a Toyota Prado"), ("goes_to", "yoga with Huda as the teacher"),
          ("has_appointment_with", "Dr Sara every six months")]
STATED_AR = [("dentist_is", "دكتور الأسنان سارة"), ("lives_in", "مدينة الكويت"), ("works_at", "بنك الخليج"),
             ("allergic_to", "الفول السوداني"), ("has_child", "بنت اسمها نور"), ("favorite_color", "أزرق"),
             ("gym_is", "نادي بلاتينيوم"), ("drives", "برادو"), ("goes_to", "يوغا مع هدى"),
             ("has_appointment_with", "سارة كل ستة شهور")]
VISITS = [("visited", "the dentist"), ("attended", "a yoga class"), ("went_to", "the gym")]
VISITS_AR = [("visited", "دكتور الأسنان"), ("attended", "حصة يوغا"), ("went_to", "النادي")]
ASKED = [("Who is my dentist?", "Dr Sara"), ("Which gym do I go to?", "Platinum"), ("Who teaches my yoga?", "Huda")]
ASKED_AR = [("منو دكتور الأسنان؟", "سارة"), ("أي نادي أروح؟", "بلاتينيوم"), ("مع منو اليوغا؟", "هدى")]


def _iso(when: float) -> str:
    return datetime.fromtimestamp(when, UTC).date().isoformat()


def _habits(mem, monkeypatch, visits: int, *, arabic: bool) -> None:
    words = _Words()
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: words)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob",
                        lambda text: struct.pack(f"<{words.dim}f", *words.encode(text)))
    now = at(TOLD)
    for i, (predicate, value) in enumerate(STATED_AR if arabic else STATED):
        mutate_belief(mem.db, "user", predicate, value, predicate_type="set", tenant_id="default", private=True,
                      extraction_method="user_stated", now=now - 400 * DAY + i, source_session="c0", source_turn=i)
    for i in range(visits):
        when = now - (visits - i) * 7 * DAY
        for j, (predicate, value) in enumerate(VISITS_AR if arabic else VISITS):
            mem.tell([event(predicate, value, _iso(when))], value, turn=10 + i * 3 + j, on=_iso(when + DAY))


def _lines(mem, question: str) -> list[str]:
    return [line for line in mem.block(question, now="2026-06-01").splitlines() if line.startswith("- ")]


@pytest.mark.parametrize("arabic", [False, True])
@pytest.mark.parametrize("visits", [5, 12, 26])
def test_a_habits_occasions_never_hide_a_stated_fact(mem, monkeypatch, visits, arabic):
    _habits(mem, monkeypatch, visits, arabic=arabic)
    for question, answer in ASKED_AR if arabic else ASKED:
        shown = _lines(mem, question)
        assert any(answer in line for line in shown), (question, shown)
        assert sum(" more]" in line for line in shown) <= 3, shown


def test_the_latest_occasion_stands_for_them_all(mem, monkeypatch):
    _habits(mem, monkeypatch, 12, arabic=False)
    shown = [line for line in _lines(mem, "When did I visit the dentist?") if "visited" in line]
    assert len(shown) == 1 and "[on 24 May 2026, and 11 more] user visited the dentist" in shown[0], shown


@pytest.mark.parametrize("question", ["What did I do at the dentist last month?", "List all my visits to the dentist"])
def test_a_list_or_a_period_shows_every_occasion(mem, monkeypatch, question):
    _habits(mem, monkeypatch, 3, arabic=False)
    shown = [line for line in _lines(mem, question) if "visited the dentist" in line]
    assert len(shown) == 3 and not any("more]" in line for line in shown), shown
    # ... the latest first: they say the same words, so nothing else orders them.
    assert ["24 May" in shown[0], "17 May" in shown[1], "10 May" in shown[2]] == [True, True, True], shown


def test_control_every_occasion_a_candidate_hides_the_dentist(mem, monkeypatch):
    """Negative control (N2): the search as it was."""
    monkeypatch.setattr(recall_mod, "_event_of", lambda row: None)
    _habits(mem, monkeypatch, 26, arabic=False)
    assert not any("Dr Sara" in line for line in _lines(mem, "Who is my dentist?"))


def test_the_latest_of_one_event_comes_first(mem):
    """The mutation no test caught: ``_latest_first`` answering what it was given."""
    ranked = [recall_mod.RecallHit(id=i, content="user visited the dentist", score=1.0, kind="belief") for i in "ab"]
    rows = {i: {"subject": "user", "predicate": "visited", "object": "the dentist",
                "metadata_json": json.dumps({"started": day, "happened": True})}
            for i, day in (("a", "2026-05-03"), ("b", "2026-05-30"))}
    assert [h.id for h in recall_mod._latest_first(ranked, rows)] == ["b", "a"]
    rows["b"]["object"] = "the bank"  # another event keeps its own place
    assert [h.id for h in recall_mod._latest_first(ranked, rows)] == ["a", "b"]


# ── N3 / N4 / N7: corrections, single values, plans ──────────────────────


@pytest.mark.parametrize(("first", "said", "then", "put_right", "kept"), [
    ([event("travelled_to", "Paris", "2026-05-24")], "I went to Paris last week",
     [event("travelled_to", "Lyon", "2026-05-24", replaces="f1")], "Sorry, not Paris, I went to Lyon last week", "Lyon"),
    ([event("travelled_to", "باريس", "2026-05-24")], "رحت باريس الأسبوع الماضي",
     [event("travelled_to", "ليون", "2026-05-24", replaces="f1")], "عفوا، مو باريس، رحت ليون الأسبوع الماضي", "ليون"),
    ([event("met", "Ali", "2026-05-30")], "I met Ali yesterday",
     [event("met", "Omar", "2026-05-30", replaces="f1")], "Omar, not Ali", "Omar"),
    ([event("met", "علي", "2026-05-30")], "قابلت علي أمس",
     [event("met", "عمر", "2026-05-30", replaces="f1")], "لا، قابلت عمر أمس الأول مو علي", "عمر"),
    # One of them without a day: a switch, or what the user says it was not.
    ([event("uses_tool", "vim", None)], "I use vim",
     [event("uses_tool", "emacs", "2026-05-30", replaces="f1")], "I switched to emacs yesterday", "emacs"),
    ([event("uses_tool", "فيم", None)], "أستخدم فيم",
     [event("uses_tool", "إيماكس", "2026-05-30", replaces="f1")], "غيرت إلى إيماكس أمس", "إيماكس"),
    ([event("met", "Ali", "2026-05-30")], "I met Ali yesterday",
     [event("met", "Omar", None, replaces="f1")], "I meant Omar, not Ali", "Omar"),
    ([event("met", "علي", "2026-05-30")], "قابلت علي أمس",
     [event("met", "عمر", None, replaces="f1")], "أقصد عمر مو علي", "عمر"),
])
def test_a_correction_needs_no_shared_word(mem, first, said, then, put_right, kept):
    assert [o for _p, o, _d in _tell(mem, (TOLD, said, first), ("2026-06-01", put_right, then))] == [kept]


@pytest.mark.parametrize(("steps", "kept"), [
    ([("2026-05-01", "I weigh 80 kg", [_single("weight", "80 kg")]),
      ("2026-05-08", "I weigh 78 kg today", [_single("weight", "78 kg", "2026-05-08")]),
      ("2026-06-08", "I weigh 75 kg", [_single("weight", "75 kg")])], "75 kg"),
    ([("2026-05-01", "وزني 80 كيلو", [_single("weight", "80 كيلو")]),
      ("2026-05-08", "وزني 78 كيلو اليوم", [_single("weight", "78 كيلو", "2026-05-08")]),
      ("2026-06-08", "وزني 75 كيلو", [_single("weight", "75 كيلو")])], "75 كيلو"),
    ([("2026-05-01", "my order shipped yesterday", [_single("order_status", "shipped", "2026-04-30", "state")]),
      ("2026-05-05", "my order was delivered yesterday", [_single("order_status", "delivered", "2026-05-04", "state")])],
     "delivered"),
    ([("2026-05-01", "انشحن طلبي أمس", [_single("order_status", "انشحن", "2026-04-30", "state")]),
      ("2026-05-05", "وصل طلبي أمس", [_single("order_status", "وصل", "2026-05-04", "state")])], "وصل"),
    ([("2026-05-01", "I have 420 followers", [_single("follower_count", "420")]),
      ("2026-05-08", "427 followers today", [_single("follower_count", "427", "2026-05-08")])], "427"),
    ([("2026-05-01", "The car has 40,000 km", [_single("mileage", "40,000 km", "2026-05-01")]),
      ("2026-05-08", "The car has 41,000 km", [_single("mileage", "41,000 km", "2026-05-08")])], "41,000 km"),
])
def test_a_single_value_with_a_day_stays_single(mem, steps, kept):
    assert [o for _p, o, _d in _tell(mem, *steps)] == [kept]


@pytest.mark.parametrize(("predicate", "said", "value", "day"), [
    ("bought", "I got a 50-inch TV on Black Friday", "a 50-inch TV", "2026-11-27"),
    ("finished", "خلصت كتاب يوم 10 ديسمبر", "كتاب", "2026-12-10"),
])
def test_what_happened_has_no_day_to_come(mem, predicate, said, value, day):
    assert _tell(mem, (TOLD, said, [event(predicate, value, day)])) == [(predicate, value, "")]


@pytest.mark.parametrize("day", ["2026-06-03", None])
@pytest.mark.parametrize(("plan", "said", "then", "went"), [
    ("Amr Diab concert", "I have the Amr Diab concert on 3 June", "the Amr Diab concert",
     "I went to the Amr Diab concert yesterday"),
    ("حفلة عمرو دياب", "عندي حفلة عمرو دياب يوم 3 يونيو", "حفلة عمرو دياب", "رحت حفلة عمرو دياب أمس"),
])
def test_a_plan_attended_is_what_happened(mem, plan, said, then, went, day):
    rows = _tell(mem, (TOLD, said, [event("scheduled_event", plan, "2026-06-03")]),
                 ("2026-06-04", went, [event("attended", then, day, replaces="f1")]))
    assert rows == [("attended", then, "2026-06-03")]
    assert mem.days()[0][1].get("happened") is True


@pytest.mark.parametrize(("said", "value"), [("I called Omar about the car", "Omar"),
                                             ("اتصلت على عمر عشان السيارة", "عمر")])
def test_an_undated_call_is_no_name(mem, said, value):
    mem.tell([event("called", value, None)], said)
    assert mem.facts() == [("called", value)]


def test_what_the_user_is_called_is_their_name_whatever_the_label(mem):
    mem.tell([event("called", "Sam", None)], "I'm called Sam.")
    assert mem.facts() == [("name_is", "Sam")]


# ── Choices 6 and 1: whose day it is ─────────────────────────────────────

BIKE = ("2026-05-01", "I bought a road bike yesterday", [event("bought", "a road bike", "2026-04-30")])
BIKE_AR = ("2026-05-01", "شريت دراجة أمس", [event("bought", "دراجة", "2026-04-30")])


@pytest.mark.parametrize(("first", "said"), [
    (BIKE, "Can you check this email I got last week?\n> I bought a road bike on 3 March 2019."),
    (BIKE, "Fix these 2 sentences:\n> I bought a road bike on 3 March 2019."),
    (BIKE, "My road bike email is ready.\n> I bought it on 3 March 2019."),
    (BIKE_AR, "صحح هذا الإيميل اللي وصلني اليوم:\n> اشتريت دراجة يوم 3 مارس 2019"),
    (BIKE_AR, "صحح هالجملتين 2:\n> اشتريت دراجة يوم 3 مارس 2019"),
    (BIKE_AR, "إيميل الدراجة جاهز.\n> اشتريتها يوم 3 مارس 2019"),
])
def test_a_pasted_day_never_becomes_the_users(mem, first, said):
    value = first[2][0]["object"]
    rows = _tell(mem, first, (TOLD, said, [event("bought", value, "2019-03-03", replaces="f1")]))
    assert rows == [("bought", value, "2026-04-30")]


@pytest.mark.parametrize(("value", "said", "then"), [("the dentist", "I went to the dentist", "It was two weeks ago"),
                                                     ("دكتور الأسنان", "رحت دكتور الأسنان", "كانت من أسبوعين")])
def test_the_users_own_day_counts(mem, value, said, then):
    rows = _tell(mem, (TOLD, said, [event("visited", value, None)]),
                 ("2026-06-01", then, [event("visited", value, "2026-05-18", replaces="f1")]))
    assert rows == [("visited", value, "2026-05-18")]


@pytest.mark.parametrize(("value", "said", "again", "denied"), [
    ("the dentist", "I went to the dentist yesterday", "the dentist on Saturday hurt",
     "No, the dentist was on Friday"),
    ("دكتور الأسنان", "رحت دكتور الأسنان أمس", "دكتور الأسنان يوم السبت عورني", "لا، دكتور الأسنان كان الجمعة"),
])
def test_a_day_the_user_counted_stands_unless_they_deny_it(mem, value, said, again, denied):
    """The model works out the 29th for "Saturday" (the 30th): the last guess never wins."""
    first = (TOLD, said, [event("visited", value, "2026-05-30")])
    guessed = [event("visited", value, "2026-05-29", replaces="f1")]
    assert _tell(mem, first, ("2026-06-02", again, guessed)) == [("visited", value, "2026-05-30")]
    mem.db.execute("DELETE FROM beliefs")
    other = [event("visited", f"{value} 2", "2026-05-29", replaces="f1")]  # told in other words
    assert _tell(mem, first, ("2026-06-02", again, other)) == [("visited", f"{value} 2", "2026-05-30")]
    mem.db.execute("DELETE FROM beliefs")
    assert _tell(mem, first, ("2026-06-02", denied, guessed)) == [("visited", value, "2026-05-29")]


@pytest.mark.parametrize(("value", "said", "too"), [
    ("the dentist", "I went to the dentist yesterday", "I went to the dentist on Friday too"),
    ("دكتور الأسنان", "رحت دكتور الأسنان أمس", "ورحت دكتور الأسنان الجمعة بعد"),
])
def test_a_day_the_user_says_beside_one_they_counted_is_another_occasion(mem, value, said, too):
    rows = _tell(mem, (TOLD, said, [event("visited", value, "2026-05-30")]),
                 ("2026-06-02", too, [event("visited", value, "2026-05-29", replaces="f1")]))
    assert sorted(d for _p, _o, d in rows) == ["2026-05-29", "2026-05-30"]


# ── N8: the held list shows the day ──────────────────────────────────────


def test_the_held_list_shows_an_events_day(mem):
    _tell(mem, (TOLD, "I went to the dentist yesterday", [event("visited", "the dentist", "2026-05-30")]),
          (TOLD, "the concert is on 3 June", [event("scheduled_event", "the concert", "2026-06-03")]),
          (TOLD, "أنا في الكويت", [_single("lives_in", "الكويت", "2026-01-02")]))
    message = belief_extractor.turn_message("The dentist on Saturday hurt", held=mem.held()[0])
    assert "user visited the dentist (on 2026-05-30)" in message
    assert "user scheduled_event the concert (planned for 2026-06-03)" in message
    assert "user lives_in الكويت" in message and "الكويت (" not in message
    assert belief_extractor.MESSAGE_SHAPE == 5


# ── The smaller points ───────────────────────────────────────────────────


@pytest.mark.parametrize(("a", "b", "same"), [
    ("a new phone, a Samsung Galaxy S22", "a black silicone phone case", False),
    ("تلفون جديد سامسونج جالاكسي", "غطا تلفون أسود سيليكون", False),
    ("a pottery exhibition", "the pottery show", True), ("معرض فخار", "صالة الفخار", True),
    ("new Converse sneakers", "a pair of black Converse sneakers", True),
    ("bananas", "fresh vegetables", False), ("Paris", "Paris", True),
])
def test_one_shared_word_is_a_kind_of_thing(a, b, same):
    assert fact_days.same_thing(a, b) is same


@pytest.mark.parametrize(("phone", "got", "case", "then"), [
    ("a new phone, a Samsung Galaxy S22", "I got a new phone, a Samsung Galaxy S22", "a black silicone phone case",
     "I got a black silicone phone case"),
    ("تلفون جديد سامسونج جالاكسي", "شريت تلفون جديد سامسونج جالاكسي", "غطا تلفون أسود سيليكون",
     "شريت غطا تلفون أسود سيليكون"),
])
def test_a_phone_case_closes_no_phone(mem, phone, got, case, then):
    rows = _tell(mem, (TOLD, got, [event("got", phone, "2026-05-01")]),
                 ("2026-06-01", then, [event("got", case, "2026-05-30", replaces="f1")]))
    assert len(rows) == 2


def test_what_was_told_by_then_bounds_an_event_told_in_other_words(mem, monkeypatch):
    """The mutation no test caught: ``could_have_happened_by`` on the path of other words."""
    first = (TOLD, "I went to the charity spin class", [event("attended", "a charity spin class", "2026-05-24")])
    then = ("2026-11-11", "The charity spin class at the gym was on Tuesday",
            [event("attended", "a charity spin class at the gym", "2026-11-10", replaces="f1")])
    assert [d for _p, _o, d in _tell(mem, first, then)] == ["2026-05-24", "2026-11-10"]
    mem.db.execute("DELETE FROM beliefs")
    monkeypatch.setattr(fact_days, "could_have_happened_by", lambda day, told_at: True)
    assert [d for _p, _o, d in _tell(mem, first, then)] == ["2026-11-10"]


@pytest.mark.parametrize(("first", "said", "again", "then"), [
    ("a pottery exhibition", "I went to a pottery exhibition yesterday", "the pottery exhibition with Dana",
     "I loved the pottery exhibition with Dana"),
    ("معرض فخار", "رحت معرض فخار أمس", "معرض الفخار مع دانة", "عجبني معرض الفخار مع دانة"),
])
def test_forgetting_a_retelling_opens_the_event_it_closed(mem, first, said, again, then):
    rows = _tell(mem, (TOLD, said, [event("attended", first, "2026-05-30")]),
                 ("2026-06-01", then, [event("attended", again, None, replaces="f1")]))
    assert rows == [("attended", again, "2026-05-30")]
    mem.forget(mem.episode(2, then))
    assert [(o, m.get("started"), "closed_by" in m) for o, m in mem.days()] == [(first, "2026-05-30", False)]


def test_a_retelling_replaced_by_one_that_stands_opens_nothing(mem):
    mem.tell([event("attended", "a pottery exhibition", "2026-05-30")], "I went to a pottery exhibition yesterday")
    mem.tell([event("attended", "the pottery show", None, replaces="f1")], "the pottery show was fun", turn=2,
             held=mem.held()[0])
    mem.tell([event("attended", "the pottery exhibition show", None, replaces="f1")], "that pottery exhibition show",
             turn=3, held=mem.held()[0])
    assert [o for o, _m in mem.days()] == ["the pottery exhibition show"]
    mem.forget(mem.episode(2, "the pottery show was fun"))
    assert [o for o, _m in mem.days()] == ["the pottery exhibition show"]
    mem.forget(mem.episode(3, "that pottery exhibition show"))
    assert [o for o, _m in mem.days()] == ["a pottery exhibition"]


def test_forgetting_the_chat_counts_every_day_it_took_back(mem):
    from kazma_core.memory.forget import forget_chat

    mem.tell([event("attended", "the pottery exhibition", None)], "I went to the pottery exhibition.")
    mem.db.execute("UPDATE beliefs SET source_session = 'chat-0'")
    mem.tell([event("attended", "the pottery exhibition", "2026-05-30", replaces="f1")],
             "The pottery exhibition was yesterday.", turn=2, held=mem.held()[0])
    mem.episode(2, "The pottery exhibition was yesterday.")
    answer = forget_chat("chat-1", tenant_id="default", conn=mem.db)
    assert answer["days_taken_back"] == 1, answer


def test_a_write_reads_a_bounded_number_of_entries(mem, monkeypatch):
    """52 ms under the mutation lock at 5,000 entries of one name."""
    rows = [(f"b{i}", "default", "user", "bought", "set", f"thing number {i}", 0.9, 2, 1.0, float(i), float(i),
             "llm_inferred", "{}") for i in range(5000)]
    mem.db.executemany(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, confidence, "
        "structural_importance, source_trust_weight, valid_from, ingested_at, extraction_method, metadata_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    read: list[int] = []
    real = belief_mutation._same_value
    monkeypatch.setattr(belief_mutation, "_same_value", lambda old, new: read.append(1) or real(old, new))
    token = belief_mutation._bind_write_context(None, "2026-05-30", True)
    try:
        # The oldest entry, in its own spelling, is found among all of them.
        assert belief_mutation._entry_told_again(mem.db, "user", "bought", "thing number 3", "default", None) == (
            None, False)  # told in 1970, it did not happen in 2026
        assert belief_mutation._entry_told_again(mem.db, "user", "bought", "a Thing", "default", None) == (None, False)
    finally:
        belief_mutation._unbind_write_context(token)
    assert 0 < len(read) <= 2 * belief_mutation._FOLDED_ENTRIES
    assert belief_mutation._entry_told_again(mem.db, "user", "bought", "thing number 3", "default", None)[0] == "b3"


def test_the_mirrors_facts_carry_their_day(monkeypatch):
    """M3 on the Postgres-primary path: ``fetch_beliefs`` reads the metadata."""
    from kazma_core.memory.state_backend import PostgresStateBackend

    asked: list[str] = []

    class Cursor:
        description = [(c,) for c in ("id", "metadata_json")]

        def execute(self, sql, params=()):
            asked.append(" ".join(sql.split()))

        def fetchall(self):
            return [("b1", '{"started": "2026-05-30", "happened": true}')]

        def close(self):
            pass

    class Conn:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    backend = PostgresStateBackend("postgresql://unused")
    monkeypatch.setattr(backend, "_connect", Conn)
    monkeypatch.setattr(backend, "_ensure", lambda: None)
    (row,) = backend.fetch_beliefs(["b1"], tenant_id="default")
    assert fact_days.event_day(fact_days.metadata_of(row["metadata_json"])) == "2026-05-30"
    assert "metadata_json FROM kazma_beliefs" in asked[-1]


def test_an_events_verb_names_what_happened():
    from kazma_core.memory import event_text

    for name in ("attended", "travelled_to", "bought", "bought from", "Went To"):
        assert event_text.is_event_name(name), name
    for name in ("weight", "order_status", "lives_in", "uses_tool", "scheduled_event", "has_appointment", ""):
        assert not event_text.is_event_name(name), name


def test_control_the_exact_words_drop_the_arabic_trips():
    """Negative control (N1): words compared exactly, as they were."""
    from kazma_core.memory.query_terms import content_terms

    kept = {"en": 0, "ar": 0}
    for english, arabic in PLACES:
        for said_en, said_ar in TRAVEL:
            for lang, said, place in (("en", said_en.format(english), english), ("ar", _joined(said_ar, arabic), arabic)):
                kept[lang] += set(content_terms(place)) <= set(content_terms(said))
    assert kept["en"] == 30 and kept["ar"] < 15, kept
